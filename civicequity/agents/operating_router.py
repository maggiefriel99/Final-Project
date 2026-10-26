"""Operating Router agent: drafts an alert to the City agency that handles a triaged problem.

It never sees the constituent's original text: only fields Triage produced and code checked, plus
facts from the 311 data. Code picks the agency (from the complaint type), and code checks the draft:
every number in it must come from the facts, and it must pass the same output checks as Triage.
Every alert waits for a person to approve it (civicequity.alerts); nothing is sent automatically.
"""

import json
import re
import time
import uuid
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent

from civicequity import alerts, audit, config, harness, rules, safety
from civicequity.agents.triage import TriageResult

AGENCY_NAMES = {  # full names come from the 311 data (agency_name); these cover the snapshot without it
    "DOT": "NYC Department of Transportation",
    "DEP": "NYC Department of Environmental Protection",
    "DPR": "NYC Department of Parks & Recreation",
    "DSNY": "NYC Department of Sanitation",
}
_catalog = config.ROOT / "data" / "311_catalog_all.csv"
if _catalog.exists():
    import pandas as _pd
    AGENCY_NAMES = {**_pd.read_csv(_catalog).groupby("agency")["agency_name"].first().to_dict(), **AGENCY_NAMES}
MAX_BODY_WORDS = 150


class AlertDraft(BaseModel):
    subject: str
    body: str
    priority: Literal["routine", "elevated", "urgent"]
    priority_reason: str
    requested_action: str


def build_agent() -> Agent:
    return harness.build("operating_router", AlertDraft)


def alert_facts(t: TriageResult) -> dict:
    """The only information the router sees: checked fields and 311 facts, no original text.

    Street history is written here as finished sentences, counting every complaint type in the same
    capital group (e.g. "Sewer" and "Sewer Maintenance"), so the agent repeats facts rather than
    paraphrasing counts into something they are not.
    """
    d = t.decision
    related = {d.complaint_type}
    if t.capital_group:
        related |= {r["complaint_type"] for r in t.street_record if rules.crosswalk_group(r["complaint_type"]) == t.capital_group}
    history = [f"{r['complaints_past_year']} {r['complaint_type']} complaints on {d.street_name.title()} in {r['district']} "
               "in the past year" for r in t.street_record
               if r["complaint_type"] in related and r["boro_cd"] == t.boro_cd]
    facts = {
        "agency": t.agency,
        "agency_name": AGENCY_NAMES.get(t.agency, t.agency),
        "complaint_type": d.complaint_type,
        "descriptor": d.descriptor,
        "street": d.street_name,
        "cross_street": d.cross_street,
        "borough": d.borough,
        "district": t.district_label,
        "district_confirmed": t.district_note in ("chosen by staff", "from this street's complaint history"),
        "summary": d.summary,
        "writer_says_it_recurs": d.repeat_problem_mentioned,
        "capital_candidate": t.route == "capital",
        "capital_reason": t.route_reason if t.route == "capital" else None,
        "urgent_hazard": getattr(t, "urgent_hazard", None),
    }
    if history:
        facts["street_history"] = history
    return facts


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*", text or "")}


def check_draft(draft: AlertDraft, facts: dict) -> list[str]:
    """Reasons the draft can't go to a person for approval as-is. Empty list = clean."""
    problems = []
    allowed = {"311"}  # "contact the resident through 311" is part of the instructions
    for v in facts.values():
        allowed |= _numbers(str(v))
    text = " ".join([draft.subject, draft.body, draft.priority_reason, draft.requested_action])
    invented = sorted(_numbers(text) - allowed)
    if invented:
        problems.append("numbers not in the facts: " + ", ".join(invented))
    if len(draft.body.split()) > MAX_BODY_WORDS:
        problems.append(f"body is longer than {MAX_BODY_WORDS} words")
    facts_text = " ".join(str(v) for v in facts.values())
    for field, value in (("subject", draft.subject), ("body", draft.body), ("action", draft.requested_action),
                         ("priority reason", draft.priority_reason)):
        problems += [f"{field}: {p}" for p in safety.fairness_problems(value, facts_text)]
        for p in safety.output_problems(value, None, None):
            if "longer than" not in p:  # length is checked above, per field
                problems.append(f"{field}: {p}")
    return problems


def route_to_agency(t: TriageResult, run_id: str | None = None) -> dict:
    """Draft an agency alert for a triaged request and queue it for approval. Returns the queued alert."""
    if t.route not in ("operating", "capital") or not t.agency or not t.decision:
        raise ValueError(f"Only operating or capital requests with an agency get alerts (route was {t.route}).")
    run_id = run_id or f"router-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    facts = alert_facts(t)
    stopped, draft, problems = None, None, []

    r = harness.run("operating_router", build_agent(), f"<facts>\n{json.dumps(facts, indent=1)}\n</facts>", run_id)
    draft, usage, stopped = r.output, r.usage, r.stopped
    if draft:
        problems = check_draft(draft, facts)
        if facts["urgent_hazard"]:
            # Code, not the agent, decides that a hazard to people gets urgent priority.
            draft.priority = "urgent"
            draft.priority_reason = "Life-Safety Screener: a hazard that could hurt someone if not fixed soon."

    alert = alerts.queue(
        triage_run_id=t.run_id, router_run_id=run_id, agency=t.agency, agency_name=facts["agency_name"],
        district=t.district_label, facts=facts, draft=draft and draft.model_dump(), problems=problems,
        stopped=stopped, usage=usage,
    )
    audit.record({
        "run_id": run_id, "flow": "operating_router", "triage_run_id": t.run_id, "alert_id": alert["alert_id"],
        "facts_sent_to_agent": facts,
        "safety_checks": {"draft_checks": {"passed": not problems and draft is not None, "problems": problems}},
        "stopped": stopped,
        "draft": alert["draft"],
        "status": alert["status"],
        "tokens": {k: usage.get(k, 0) for k in ("requests", "input_tokens", "cached_input_tokens",
                                                 "cache_write_tokens", "output_tokens")},
        "cost_usd": usage.get("cost_usd", 0.0),
        "seconds": round(time.monotonic() - started, 2),
    })
    return alert
