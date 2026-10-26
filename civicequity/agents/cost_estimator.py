"""Cost Estimator agent: picks the scope of the lasting fix for a capital need and explains it.

The agent chooses among scope options backed by current capital projects, and can look up real
comparable projects (its one tool). Code supplies every dollar figure from the cost model, checks
that the scope is a real option and that cited projects came from the tool, and falls back to a
rule-based scope if the agent fails. Nothing here commits money; it is an estimate for planning.
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from civicequity import audit, harness, safety
from civicequity.agents.triage import TriageResult
from civicequity.cost_model import CostModel, default_scope


class CostChoice(BaseModel):
    scope: str
    what_the_fix_involves: str
    reason: str
    cited_projects: list[str]
    confidence: Literal["high", "medium", "low"]


@dataclass
class EstimatorDeps:
    model: CostModel
    borough: str
    looked_up: dict = field(default_factory=dict)  # fms_id -> project, for everything the tool returned


@dataclass
class CostResult:
    run_id: str
    scope: str
    scope_label: str
    what_the_fix_involves: str
    reason: str
    confidence: str
    estimate: dict
    comparables: list[dict]
    comparables_cited_by_ai: bool
    chosen_by: Literal["agent", "rules"]
    problems: list[str]
    stopped: dict | None
    usage: dict


def comparable_projects(ctx: RunContext[EstimatorDeps], scope: str) -> list[dict]:
    """Current NYC capital projects of this scope in the request's borough (name, budget, phase)."""
    try:
        found = ctx.deps.model.comparables(scope, ctx.deps.borough)
    except ValueError:
        return []
    for p in found:
        ctx.deps.looked_up[p["fms_id"]] = p
    return found


def build_agent() -> Agent:
    # Its one tool is read-only: it looks up current City capital projects and records what it returned.
    return harness.build("cost_estimator", CostChoice, tools=[comparable_projects], deps_type=EstimatorDeps)


def estimator_facts(t: TriageResult, options: list[dict]) -> dict:
    d = t.decision
    same_street = [f"{r['complaints_past_year']} {r['complaint_type']} complaints in the past year"
                   for r in t.street_record if r["boro_cd"] == t.boro_cd][:6]
    return {
        "complaint_type": d.complaint_type,
        "descriptor": d.descriptor,
        "summary": d.summary,
        "writer_says_it_recurs": d.repeat_problem_mentioned,
        "street": d.street_name,
        "borough": d.borough,
        "district": t.district_label,
        "street_history": same_street,
        "capital_candidate": t.matched_candidate,
        "why_capital": t.route_reason,
        "scope_options": [{k: o[k] for k in ("scope", "label", "description", "comparable_projects",
                                              "funded_by_existing_program")} for o in options],
    }


def estimate_fix(t: TriageResult, model: CostModel, run_id: str | None = None) -> CostResult:
    """Choose the lasting fix for a capital request and attach the cost model's estimate."""
    if t.route != "capital" or not t.capital_group or not t.decision:
        raise ValueError("Only capital requests get a cost estimate.")
    run_id = run_id or f"cost-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    borough = t.decision.borough or (t.district_label or "").rsplit(" CD", 1)[0]
    if borough not in ("Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island"):
        raise ValueError("Need a borough to estimate cost.")
    options = model.scopes(t.capital_group)
    allowed = {o["scope"] for o in options}
    facts = estimator_facts(t, options)
    signals = "; ".join(filter(None, [t.decision.descriptor, (t.matched_candidate or {}).get("signal_descriptors")]))
    rule_scope = default_scope(t.capital_group, signals)
    deps = EstimatorDeps(model, borough)
    choice, stopped, problems = None, None, []

    r = harness.run("cost_estimator", build_agent(), f"<facts>\n{json.dumps(facts, indent=1)}\n</facts>", run_id,
                    deps=deps)
    choice, usage, stopped = r.output, r.usage, r.stopped

    # Check the agent's answer; fall back to the rule-based scope if it can't be used.
    if choice:
        if choice.scope not in allowed:
            problems.append(f'chose "{choice.scope}", which is not one of the scope options')
        bad_cites = [c for c in choice.cited_projects if c not in deps.looked_up]
        if bad_cites:
            problems.append("cited projects the tool never returned: " + ", ".join(bad_cites))
            choice.cited_projects = [c for c in choice.cited_projects if c in deps.looked_up]
        for label, text in (("reason", choice.reason), ("fix description", choice.what_the_fix_involves)):
            # Funding is this agent's topic ("already funded by the resurfacing program"), so those words
            # are allowed; links, money figures, and instruction-like text are not.
            problems += [f"{label}: {p}" for p in safety.output_problems(text, None, None, allow_funding_words=True,
                                                                              max_chars=500)]
    use_agent = choice is not None and choice.scope in allowed and not any(
        p.startswith(("reason", "fix description")) for p in problems)

    scope = choice.scope if use_agent else rule_scope
    est = model.estimate(scope, borough)
    if use_agent:
        cited = [deps.looked_up[c] for c in choice.cited_projects]
        what, reason, confidence = choice.what_the_fix_involves, choice.reason, choice.confidence
    else:
        cited = []
        what = est["scope_description"]
        reason = "Chosen by the City-rules default for this kind of complaint because the AI's answer could not be used."
        confidence = "low"
    cited_by_ai = bool(cited)
    if not cited:  # show the closest real projects anyway, labeled as the model's picks
        cited = model.comparables(scope, borough, n=3)

    result = CostResult(run_id=run_id, scope=scope, scope_label=est["scope_label"], what_the_fix_involves=what,
                        reason=reason, confidence=confidence, estimate=est, comparables=cited,
                        comparables_cited_by_ai=cited_by_ai,
                        chosen_by="agent" if use_agent else "rules", problems=problems, stopped=stopped, usage=usage)
    audit.record({
        "run_id": run_id, "flow": "cost_estimator", "triage_run_id": t.run_id,
        "facts_sent_to_agent": facts,
        "tool_calls": {"comparable_projects": len(deps.looked_up)},
        "safety_checks": {"answer_checks": {"passed": not problems, "problems": problems}},
        "stopped": stopped, "chosen_by": result.chosen_by, "scope": scope,
        "estimate": {k: est[k] for k in ("typical", "low", "high", "comparable_projects", "funded_by_existing_program")},
        "tokens": {k: usage.get(k, 0) for k in ("requests", "input_tokens", "cached_input_tokens",
                                                 "cache_write_tokens", "output_tokens")},
        "cost_usd": usage.get("cost_usd", 0.0),
        "seconds": round(time.monotonic() - started, 2),
    })
    return result
