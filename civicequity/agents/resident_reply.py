"""Resident Reply agent: drafts a reply to the person who made the request, with advice when there is
something they can do themselves or when the problem is outside City purview.

Advice can only come from advice.yaml, where every entry is tied to an official source checked word for
word. The agent picks which entries apply and writes plain language; code checks that it used only real
entries, wrote no links or numbers beyond what those entries contain, and adds the source links itself.
Every reply waits for a person to approve it (civicequity.alerts), like agency alerts.
"""

import json
import time
import uuid
from functools import cache

import yaml
from pydantic import BaseModel
from civicequity import alerts, audit, config, fact_check, harness, safety
from civicequity.agents.operating_router import AGENCY_NAMES
from civicequity.agents.triage import TriageResult


class ReplyDraft(BaseModel):
    body: str
    advice_ids: list[str]


@cache
def advice_library() -> dict:
    return yaml.safe_load((config.ROOT / "advice.yaml").read_text())["entries"]


def next_step(t: TriageResult) -> str:
    """What happens next, decided by code from the route (the agent may not promise anything else)."""
    agency = AGENCY_NAMES.get(t.agency, t.agency)
    if t.route in ("operating", "capital"):
        step = f"Your report is being shared with the {agency}, which handles this kind of problem."
        if t.route == "capital":
            step += " Because similar problems have been reported here before, it is also being reviewed for a longer-term repair."
        return step
    if t.route == "human_review":
        return "A staff member is reviewing your request to make sure it reaches the right place."
    if t.route == "advice":
        return "There is no 311 service request for this, so it has not been sent to a City agency. The guidance below is from official City sources."
    return "This does not appear to be something a City agency handles through 311."


@cache
def _prompt_with_library() -> str:
    """The reply prompt with the advice library after it. The library is the same on every run, so it sits in the
    system prompt, where the provider can cache it (repeat runs pay a tenth of the input price for it)."""
    lib = [{"id": k, "applies_when": v["applies_when"], "guidance": v["guidance"]} for k, v in advice_library().items()]
    return harness.agent_prompt("resident_reply") + "\n## Advice library\n" + json.dumps(lib, indent=1) + "\n"


def reply_facts(t: TriageResult) -> dict:
    d = t.decision
    return {
        "problem_summary": d.summary,
        "complaint_type": d.complaint_type,
        "descriptor": d.descriptor,
        "agency": AGENCY_NAMES.get(t.agency, t.agency) if t.agency else None,
        "next_step": next_step(t),
    }


def check_reply(draft: ReplyDraft, facts: dict, request_text: str) -> list[str]:
    lib = advice_library()
    problems = [f'advice id "{a}" is not in the library' for a in draft.advice_ids if a not in lib]
    used = [lib[a]["guidance"] for a in draft.advice_ids if a in lib]
    # Numbers must come from the advice used, the next step, or the resident's own request.
    allowed = {fact_check.canonical(n) for n in fact_check.numbers_in(" ".join(used + [facts["next_step"], request_text]))}
    extra = [n for n in fact_check.numbers_in(draft.body) if fact_check.canonical(n) not in allowed]
    if extra:
        problems.append("numbers not in the advice or the request: " + ", ".join(sorted(set(extra))))
    if len(draft.body.split()) > 170:
        problems.append("reply is longer than 170 words")
    problems += safety.output_problems(draft.body, None, None, allow_funding_words=True, max_chars=1500)
    problems += safety.fairness_problems(draft.body, " ".join([request_text, json.dumps(facts)]))
    return problems


def suspected_manipulation(t: TriageResult) -> bool:
    c = t.checks
    pf = c.get("provider_filter") or {}
    return bool(t.stopped or c["pattern_check"]["flagged"] or (c.get("agent_flag") or {}).get("suspicious_instructions")
                or (c.get("life_safety") or {}).get("suspicious_instructions")
                or pf.get("blocked") or pf.get("jailbreak_detected") or pf.get("indirect_attack_detected")
                or not (c.get("output_validation") or {"passed": True})["passed"])


def draft_reply(t: TriageResult, run_id: str | None = None) -> dict | None:
    """Draft the resident reply and queue it for approval.

    No reply is drafted for requests held because they look like manipulation attempts or were blocked;
    a request held only because the AI was unsure (e.g. a gas smell, which has no 311 complaint type)
    still gets a draft, so staff can approve safety advice quickly."""
    if not t.decision or t.route == "emergency" or (t.route == "human_review" and suspected_manipulation(t)):
        return None  # emergencies get the fixed 911 message instead (civicequity.emergency)
    run_id = run_id or f"reply-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    facts = reply_facts(t)
    stopped, draft, problems = None, None, []
    r = harness.run("resident_reply", harness.build("resident_reply", ReplyDraft, prompt=_prompt_with_library()),
                    f"<facts>\n{json.dumps(facts, indent=1)}\n</facts>", run_id)
    draft, usage, stopped = r.output, r.usage, r.stopped
    if draft:
        problems = check_reply(draft, facts, t.request_text)

    lib = advice_library()
    sources = [{"title": s["source_title"], "url": s["url"]}
               for a in (draft.advice_ids if draft else []) if a in lib
               for s in [lib[a], *lib[a].get("more_sources", [])]]
    reply = alerts.queue(
        kind="resident_reply", triage_run_id=t.run_id, router_run_id=run_id, agency=t.agency or "",
        agency_name="Resident (reply to the person who made the request)", district=t.district_label, facts=facts,
        draft=draft and {"subject": "Your 311 request", "body": draft.body, "priority": "routine",
                         "priority_reason": "", "requested_action": "", "advice_ids": draft.advice_ids,
                         "sources": sources},
        problems=problems, stopped=stopped, usage=usage,
    )
    audit.record({
        "run_id": run_id, "flow": "resident_reply", "triage_run_id": t.run_id, "alert_id": reply["alert_id"],
        "advice_used": draft.advice_ids if draft else [], "safety_checks": {"reply_checks": {"passed": not problems,
                                                                                            "problems": problems}},
        "stopped": stopped, "status": reply["status"],
        "tokens": {k: usage.get(k, 0) for k in ("requests", "input_tokens", "cached_input_tokens", "output_tokens")},
        "cost_usd": usage.get("cost_usd", 0.0), "seconds": round(time.monotonic() - started, 2),
    })
    return reply
