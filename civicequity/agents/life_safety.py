"""Life-Safety Screener agent: does this request describe danger to life or safety?

It runs on every request, alongside Triage, and answers only that one question. Triage's job is to find
the closest 311 complaint type, which pulls emergencies into routine categories (before this agent
existed, "smoke and flames coming out of a window" was filed as a Non-Emergency Police Matter). A second,
single-purpose reader catches what Triage misses.

It answers with one of three levels: emergency (911-level: escalate), urgent_hazard (a dangerous condition
to fix soon: the agency alert is marked urgent by code, no 911 message), or none.

Escalation rule (applied in civicequity.agents.triage): the request is escalated if this agent OR Triage
says it is an emergency. If neither does and this agent finds no hazard, but the request uses danger words
(emergencies.yaml backstop), it is held for a person. Only a person can downgrade an escalation.
"""

from functools import cache
from typing import Literal

from pydantic import BaseModel
from civicequity import config, emergency, fact_check, harness, llm, safety

IncidentType = Literal[tuple(emergency.incident_types())]


class LifeSafetyCheck(BaseModel):
    level: Literal["emergency", "urgent_hazard", "none"]
    incident_type: IncidentType | None
    reason: str
    suspicious_instructions: bool


@cache
def _prompt() -> str:
    types = "\n".join(f"  - {t}" for t in emergency.incident_types())
    return (config.ROOT / "prompts" / "life_safety.md").read_text().replace("{incident_types}", types)


def screen(redacted: str, run_id: str) -> dict:
    token = llm.CALL_LOG.set([])  # its own call log, since it runs at the same time as Triage
    try:
        return _screen(redacted, run_id)
    finally:
        llm.CALL_LOG.reset(token)


def _screen(redacted: str, run_id: str) -> dict:
    """Run the screener on text that has already been cleaned and had personal information removed.

    Returns {"danger", "incident_type", "reason", "suspicious_instructions", "problems", "stopped", "usage"}.
    If the agent cannot answer (budget, filter, error), danger is None: the code backstop still applies."""
    out = {"danger": None, "hazard": None, "incident_type": None, "reason": "", "suspicious_instructions": False,
           "problems": [], "stopped": None, "usage": {}}
    r = harness.run("life_safety", harness.build("life_safety", LifeSafetyCheck, prompt=_prompt()),
                    f"<constituent_request>\n{redacted}\n</constituent_request>", run_id)
    out["usage"], out["stopped"] = r.usage, r.stopped
    if r.stopped:
        return out
    c = r.output
    # The reason is shown to staff; it gets the same checks as Triage's summary. A failed check never
    # cancels an escalation: the reason is just withheld.
    problems = safety.output_problems(c.reason, None, None) + safety.fairness_problems(c.reason, redacted)
    allowed = {fact_check.canonical(n) for n in fact_check.numbers_in(redacted)} | {("num", 911.0), ("num", 311.0)}
    extra = [n for n in fact_check.numbers_in(c.reason) if fact_check.canonical(n) not in allowed]
    if extra:
        problems.append("numbers not in the request: " + ", ".join(sorted(set(extra))))
    out.update(danger=c.level == "emergency", hazard=c.level == "urgent_hazard",
               incident_type=(c.incident_type or emergency.OTHER) if c.level == "emergency" else None,
               reason=c.reason if not problems else "(withheld: the AI's reason failed an output check)",
               suspicious_instructions=c.suspicious_instructions, problems=problems)
    return out
