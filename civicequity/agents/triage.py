"""Triage agent: maps one constituent request to an official 311 complaint type and location.

The agent only reads and maps, in a single model call with no tools. Code then looks up the street's
complaint history and decides the route (operating, capital, not a City matter, or human review)
from definitions.yaml, so the text of a request cannot talk its way into a capital classification
or a funding decision. Repeats of earlier requests are answered from the request bank for free.

Order of steps for every request:
  1. clean the text           4. request bank (skipped if flagged)    7. route by the rules
  2. remove personal info     5. agent (one call, token budget)        8. bank the answer if clean
  3. pattern check            6. validate the agent's answer          9. life-safety escalation
                                                                      10. audit log record
The Life-Safety Screener (civicequity.agents.life_safety) reads the same cleaned text at the same time
as step 5, on every request, including ones answered from the bank. Escalation (step 9): if the screener
OR this agent says the request describes danger to life or safety, the route becomes "emergency"; if
neither does but the text has danger words (emergencies.yaml), the request is held for a person.
"""

import contextvars
import re
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import cache
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from civicequity import (audit, config, emergency, fact_check, harness, limits, llm, privacy, request_bank, rules,
                         safety)
from civicequity.agents import life_safety
from civicequity.agents.life_safety import IncidentType

Borough = Literal["Manhattan", "Bronx", "Brooklyn", "Queens", "Staten Island"]
BORO_CODE = {"Manhattan": 1, "Bronx": 2, "Brooklyn": 3, "Queens": 4, "Staten Island": 5}
CATALOG_MIN_REQUESTS = 100  # complaint types rarer than this are left out of the prompt
CATALOG_DESCRIPTORS = 8
HELD = "Held for a person to check before anything happens."


class TriageDecision(BaseModel):
    is_city_infrastructure_request: bool
    complaint_type: str | None
    descriptor: str | None
    borough: Borough | None
    street_name: str | None
    cross_street: str | None
    summary: str = Field(description="One neutral sentence describing the problem.")
    repeat_problem_mentioned: bool
    suspicious_instructions: bool
    confidence: Literal["high", "medium", "low"]
    reasoning: str
    emergency_type: IncidentType | None = Field(None, description="Set if the request describes danger to "
                                                "life, health, or property right now.")
    advice_topics: list[str] = Field(default_factory=list, description="Ids from the advice library that fit.")
    other_problems: list[str] = Field(default_factory=list, description="Each other separate problem the request "
                                      "reports, as one short sentence with its location.")


@dataclass
class TriageResult:
    run_id: str
    request_text: str  # after cleaning and personal-information removal; the only version kept
    decision: TriageDecision | None  # None when the agent never answered
    route: Literal["emergency", "capital", "operating", "advice", "not_city", "human_review"]
    route_reason: str
    complaint_class: str | None = None
    agency: str | None = None
    capital_group: str | None = None
    boro_cd: int | None = None
    district_label: str | None = None
    district_note: str = ""
    street_record: list[dict] = field(default_factory=list)
    matched_candidate: dict | None = None
    review_reasons: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)  # result of every safety check, for the audit log
    stopped: dict | None = None  # set when the run was blocked or cut off, with the reason
    usage: dict = field(default_factory=dict)
    bank_match: dict | None = None  # set when the agent's reading was reused from an earlier request
    life_safety: dict | None = None  # the Life-Safety Screener's answer
    emergency: dict | None = None  # set when escalated, or held because of danger words
    urgent_hazard: str | None = None  # the screener's reason, when it found a hazard to fix soon (not 911)


def build_catalog(requests: pd.DataFrame) -> tuple[str, dict[str, str], dict[str, set[str]]]:
    """The complaint types the agent may choose from, built from the real 311 data: every agency when the
    full catalog is in the snapshot, otherwise the infrastructure agencies' complaints."""
    totals = requests.groupby(["complaint_type", "agency"])["n"].sum().reset_index()
    totals = totals[totals["n"] >= CATALOG_MIN_REQUESTS].sort_values("n", ascending=False)
    totals = totals.drop_duplicates("complaint_type")  # a type filed under two agencies goes to the main one
    lines, agency, descriptors = [], {}, {}
    for r in totals.itertuples():
        d = (requests[requests["complaint_type"] == r.complaint_type].groupby("descriptor")["n"].sum()
             .sort_values(ascending=False).head(CATALOG_DESCRIPTORS).index.tolist())
        lines.append(f"- {r.complaint_type} [{r.agency}]: " + "; ".join(d))
        agency[r.complaint_type] = r.agency
        # Validation accepts every descriptor 311 has used for the type, not only the ones shown in the prompt.
        descriptors[r.complaint_type] = set(requests.loc[requests["complaint_type"] == r.complaint_type, "descriptor"]
                                            .dropna())
    return "\n".join(lines), agency, descriptors


@cache
def _prompt_template() -> str:
    from civicequity.agents.resident_reply import advice_library  # late import: resident_reply imports triage
    lib = advice_library()
    return ((config.ROOT / "prompts" / "triage.md").read_text()
            .replace("{incident_types}", "\n".join(f"  - {t}" for t in emergency.incident_types()))
            .replace("{advice_library}", "\n".join(f"- {k}: {v['applies_when']}" for k, v in lib.items())))


def build_agent(catalog_text: str) -> Agent:
    # The catalog is the same on every run, so it sits in the system prompt where the provider can
    # cache it: repeat runs pay the cached-input price for it. The harness puts the shared rules first.
    return harness.build("triage", TriageDecision, prompt=_prompt_template() + "\n" + catalog_text)


def lookup_street(street_hotspots: pd.DataFrame, labels: dict[int, str], street_name: str,
                  borough: str | None) -> list[dict]:
    """Past-year 311 complaints on this street in this borough, by complaint type and district."""
    name = (street_name or "").upper().strip()
    rows = street_hotspots[street_hotspots["street_name"] == name]
    if borough in BORO_CODE:
        rows = rows[rows["boro_cd"] // 100 == BORO_CODE[borough]]
    return [{"district": labels.get(int(r.boro_cd), str(r.boro_cd)), "boro_cd": int(r.boro_cd),
             "complaint_type": r.complaint_type, "complaints_past_year": int(r.n)}
            for r in rows.sort_values("n", ascending=False).head(15).itertuples()]


def triage(text: str, snap: dict, candidates: pd.DataFrame, district_hint: int | None = None,
           run_id: str | None = None, use_bank: bool = True,
           progress: Callable[[str], None] | None = None, sender: str | None = None) -> TriageResult:
    """progress, if given, is called with "screen", "ai" or "bank", then "rules" as the run moves along."""
    run_id = run_id or f"triage-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    progress = progress or (lambda step: None)
    progress("screen")

    cleaned, cleaning_notes = safety.clean_request(text)
    redacted, personal_info = privacy.redact(cleaned)
    flags = safety.injection_flags(redacted)
    checks = {
        "input_cleaning": {"changed": bool(cleaning_notes), "notes": cleaning_notes},
        "personal_info_removed": personal_info,
        "pattern_check": {"flagged": bool(flags), "phrases": flags},
    }
    review = []
    if flags:
        review.append("Request contains text that looks like instructions to the system: "
                      + "; ".join(f'"{f}"' for f in flags))

    checks["danger_words"] = emergency.keyword_hits(redacted)
    if len(redacted.strip()) < 3:
        # Nothing to read (empty, only spaces, or only hidden characters): no AI call.
        review.append("The request is empty after cleaning.")
        res = TriageResult(run_id=run_id, request_text=redacted, decision=None, route="human_review",
                           route_reason=HELD, review_reasons=review, checks=checks,
                           stopped={"by": "empty request", "detail": "Nothing left to read after cleaning."},
                           usage=llm.log_usage(run_id, "triage", llm.empty_usage(), note="stopped: empty request"))
        return _audit(res, started)
    rate = limits.check_and_record(sender)
    checks["sender_limit"] = rate

    if not rate["allowed"]:
        # Over the sender's limit: no AI at all. The danger-word check above still applies in _escalate.
        review.append(f"This sender has sent {rate['last_hour']} requests in the past hour and {rate['last_day']} "
                      f"in the past day (limits {rate['limit_per_hour']} and {rate['limit_per_day']}). Held without "
                      "using AI so one source can't use up the day's budget.")
        res = TriageResult(run_id=run_id, request_text=redacted, decision=None, route="human_review",
                           route_reason=HELD, review_reasons=review, checks=checks,
                           stopped={"by": "sender limit", "detail": review[-1]},
                           usage=llm.log_usage(run_id, "triage", llm.empty_usage(), note="stopped: sender limit"))
        skipped = {"danger": None, "incident_type": None, "reason": "", "suspicious_instructions": False,
                   "problems": [], "stopped": {"by": "sender limit"}, "usage": {}}
        progress("safety")
        return _audit(_escalate(res, skipped), started)

    # Life-Safety Screener: runs on every request, at the same time as the rest of triage.
    pool = ThreadPoolExecutor(max_workers=1)
    pending = pool.submit(contextvars.copy_context().run, life_safety.screen, redacted, run_id)
    pool.shutdown(wait=False)

    def finish(res: TriageResult) -> TriageResult:
        progress("safety")
        return _audit(_escalate(res, pending.result()), started)

    catalog_text, agency_by_type, descriptors_by_type = build_catalog(
        snap["catalog_all"] if snap.get("catalog_all") is not None else snap["requests"])
    labels = snap["districts"].set_index("boro_cd")["district_label"].to_dict()

    # A flagged request is never answered from the bank: it always gets a fresh look and human review.
    banked = None if flags or not use_bank else request_bank.find(redacted)
    checks["request_bank"] = {
        "used": bool(banked),
        "match": banked and {k: banked[k] for k in ("match", "similarity", "time", "run_id")},
        "skipped_because": "request was flagged" if flags else ("staff asked for a fresh run" if not use_bank else None),
    }
    if banked:
        progress("bank")
        progress("rules")
        usage = llm.log_usage(run_id, "triage", llm.empty_usage(), note=f"reused ({banked['match']})")
        res = _route(TriageDecision.model_validate(banked["decision"]), run_id, redacted, review, checks, usage,
                     snap, candidates, labels, agency_by_type, descriptors_by_type, district_hint,
                     bank_match={k: banked[k] for k in ("match", "similarity", "time", "run_id", "text")})
        return finish(res)

    # Daily spending cap: once reached, requests wait for a person instead of calling the model.
    if llm.daily_budget_reached():
        stopped = {"by": "daily budget", "detail": f"${llm.spent_today():.2f} spent today; cap is "
                                                   f"${config.DAILY_BUDGET_USD:.2f}."}
        review.append("Today's AI spending cap was reached, so this request was not sent to the agent.")
        checks["limits"] = {"daily_budget_usd": config.DAILY_BUDGET_USD, "exceeded": True}
        usage = llm.log_usage(run_id, "triage", llm.empty_usage(), note="stopped: daily budget")
        res = TriageResult(run_id=run_id, request_text=redacted, decision=None, route="human_review",
                           route_reason=HELD, review_reasons=review, checks=checks, stopped=stopped, usage=usage)
        return finish(res)

    progress("ai")
    screening = []
    r = harness.run("triage", build_agent(catalog_text), f"<constituent_request>\n{redacted}\n</constituent_request>",
                    run_id, screening=screening)
    usage = r.usage
    if r.stopped:
        # The model provider's safety filter, or our own token budget, stopped the run. Nothing is
        # classified; a person looks at it.
        stopped = r.stopped
        review.append({"token budget": "The run hit its token or call budget and was stopped.",
                       "provider content filter": "The model provider's safety filter blocked this request.",
                       "daily budget": "Today's AI spending cap was reached, so this request was not sent to the agent.",
                       }.get(stopped["by"], "The model could not answer; held for a person."))
        checks["provider_filter"] = _screening(screening, blocked=stopped["by"] == "provider content filter")
        checks["limits"] = {"token_budget": config.RUN_TOKEN_BUDGET, "call_limit": config.RUN_REQUEST_LIMIT,
                            "exceeded": stopped["by"] == "token budget"}
        res = TriageResult(run_id=run_id, request_text=redacted, decision=None, route="human_review",
                           route_reason=HELD, review_reasons=review, checks=checks, stopped=stopped, usage=usage)
        return finish(res)

    progress("rules")
    checks["provider_filter"] = _screening(screening, blocked=False)
    if checks["provider_filter"]["jailbreak_detected"] or checks["provider_filter"]["indirect_attack_detected"]:
        review.append("The model provider detected an attack in this request (it was not blocked).")
    total = usage["input_tokens"] + usage["output_tokens"]
    checks["limits"] = {"token_budget": config.RUN_TOKEN_BUDGET, "tokens_used": total,
                        "call_limit": config.RUN_REQUEST_LIMIT, "calls": usage["requests"], "exceeded": False}
    res = _route(r.output, run_id, redacted, review, checks, usage, snap, candidates, labels,
                 agency_by_type, descriptors_by_type, district_hint, bank_match=None)
    return finish(res)


def _escalate(res: TriageResult, ls: dict) -> TriageResult:
    """Apply the life-safety rule: any AI check escalates; danger words alone hold for a person."""
    res.life_safety = ls
    res.usage = {**res.usage, "life_safety": ls["usage"]}
    c = res.checks
    c["life_safety"] = {k: ls.get(k) for k in ("danger", "hazard", "incident_type", "reason",
                                               "suspicious_instructions", "problems", "stopped")}
    if ls["suspicious_instructions"] and not c["pattern_check"]["flagged"] and not (
            c.get("agent_flag") or {}).get("suspicious_instructions"):
        res.review_reasons.append("The Life-Safety Screener flagged text that looks like instructions to the system.")
        if res.route not in ("human_review",):
            res.route, res.route_reason = "human_review", HELD
    d = res.decision
    votes = (["Life-Safety Screener"] if ls["danger"] else []) + (["Triage agent"] if d and d.emergency_type else [])
    words = c["danger_words"]
    if votes:
        incident = ls["incident_type"] or (d and d.emergency_type) or emergency.OTHER
        res.emergency = {"incident_type": incident, "detected_by": votes + (["danger words"] if words else []),
                         "reason": ls["reason"], "danger_words": words}
        res.route = "emergency"
        res.route_reason = ("Possible danger to life or safety. The resident is told to call 911, and urgent alerts "
                            "are drafted for the emergency agencies. Simulation: nothing is sent.")
    elif ls.get("hazard"):
        # A dangerous condition to fix soon, not a 911 emergency: normal routing, alert marked urgent by code.
        res.urgent_hazard = ls["reason"] or "The Life-Safety Screener found a hazard that could hurt someone."
    elif words:
        res.emergency = {"incident_type": None, "possible": True, "detected_by": ["danger words"],
                         "reason": ls["reason"], "danger_words": words}
        res.review_reasons.append(
            "The request uses words that can signal danger to life (" + ", ".join(f'"{w}"' for w in words)
            + ")" + (", but neither AI check found an emergency." if ls["danger"] is False
                     else ", and the Life-Safety Screener could not answer.")
            + " A person should decide whether to flag it as an emergency.")
        res.route, res.route_reason = "human_review", HELD
    c["life_safety"]["escalated"] = res.route == "emergency"
    c["life_safety"]["urgent_hazard"] = bool(res.urgent_hazard)
    return res


def _route(d: TriageDecision, run_id: str, redacted: str, review: list[str], checks: dict, usage: dict,
           snap: dict, candidates: pd.DataFrame, labels: dict[int, str], agency_by_type: dict[str, str],
           descriptors_by_type: dict[str, set[str]], district_hint: int | None,
           bank_match: dict | None) -> TriageResult:
    """Check the agent's answer, find the district, and decide the route from the rules."""
    checks["agent_flag"] = {"suspicious_instructions": d.suspicious_instructions}
    if d.suspicious_instructions and not checks["pattern_check"]["flagged"]:
        review.append("The agent flagged text that looks like instructions to the system.")

    fixes = []
    # The catalog lists "Type [AGENCY]"; an agent that copies the tag along means the same official type.
    if d.complaint_type and d.complaint_type not in agency_by_type:
        bare = re.sub(r"\s*\[[A-Z0-9-]+\]\s*$", "", d.complaint_type)
        if bare in agency_by_type:
            fixes.append(f'removed the agency tag from "{d.complaint_type}"')
            d.complaint_type = bare
    if d.complaint_type and d.complaint_type not in agency_by_type:
        fixes.append(f'rejected complaint type "{d.complaint_type}" (not an official type)')
        review.append(f'Agent chose "{d.complaint_type}", which is not an official complaint type.')
        d.complaint_type, d.descriptor = None, None
    if d.complaint_type and d.descriptor and d.descriptor not in descriptors_by_type[d.complaint_type]:
        # Not a 311 descriptor for this type: treated as unspecified. No hold: the type alone decides the route
        # unless a listed failed-asset descriptor is present.
        fixes.append(f'dropped descriptor "{d.descriptor}" (not listed for {d.complaint_type})')
        d.descriptor = None
    from civicequity.agents.resident_reply import advice_library
    unknown = [a for a in d.advice_topics if a not in advice_library()]
    if unknown:
        fixes.append("dropped unknown advice ids: " + ", ".join(unknown))
        d.advice_topics = [a for a in d.advice_topics if a in advice_library()]
    if d.confidence == "low":
        review.append("The agent had low confidence in its reading of the request.")
    # The agent's summary and street fields travel on to the Operating Router and agency alerts, so
    # anything that looks like smuggled instructions, links, money, or a non-street value stops here.
    output_issues = safety.output_problems(d.summary, d.street_name, d.cross_street)
    output_issues += safety.fairness_problems(d.summary, redacted)
    checks["fairness"] = {"request_targets_group": safety.group_blame(redacted)}
    # The agent has no data beyond the request, so any number it writes must come from the request.
    # Street numbers written as words ("Eighth Avenue", "第八大道") come back as digits in 311 style ("8 AVENUE"),
    # so numbers in the agent's own street fields (which pass the strict street format check) are allowed too.
    street_text = " ".join(filter(None, [d.street_name, d.cross_street]))
    # Clock times written without a colon ("630am") come back as "6:30 a.m.": allow the hour and minutes.
    times = " ".join(f"{m.group(1)} {m.group(2)}" for m in re.finditer(r"\b(\d{1,2})(\d{2})\s?(?:am|pm|a\.m|p\.m)",
                                                                         redacted, flags=re.I))
    req_numbers = ({fact_check.canonical(n) for n in fact_check.numbers_in(f"{redacted} {street_text} {times}")}
                   | {("num", 311.0), ("num", 911.0)})  # the services themselves
    invented = [n for n in fact_check.numbers_in(f"{d.summary} {d.reasoning}")
                if fact_check.canonical(n) not in req_numbers]
    if invented:
        output_issues.append("numbers not in the request: " + ", ".join(sorted(set(invented))))
    # Other problems in the same request each need their own request; they pass the same checks as the summary.
    kept = []
    for item in d.other_problems[:5]:
        bad = (safety.output_problems(item, None, None) + safety.fairness_problems(item, redacted)
               + [n for n in fact_check.numbers_in(item) if fact_check.canonical(n) not in req_numbers])
        if bad:
            fixes.append(f"dropped an additional problem that failed a check ({'; '.join(map(str, bad))[:80]})")
        else:
            kept.append(item)
    d.other_problems = kept
    if output_issues:
        review.append("The agent's answer can't be passed on as-is: " + "; ".join(output_issues))
    checks["output_validation"] = {"passed": not output_issues and not any(f.startswith("rejected") for f in fixes),
                                   "fixes": fixes,
                                   "output_issues": output_issues, "confidence": d.confidence}

    # Which district: staff's choice if given, else from the street's complaint history.
    record = lookup_street(snap["street_hotspots"], labels, d.street_name, d.borough) if d.street_name else []
    boro_cd, district_note = district_hint, "chosen by staff" if district_hint else ""
    if not boro_cd:
        same_type = [r for r in record if r["complaint_type"] == d.complaint_type] or record
        cds = sorted({r["boro_cd"] for r in same_type})
        if len(cds) == 1:
            boro_cd, district_note = cds[0], "from this street's complaint history"
        elif cds:
            boro_cd = same_type[0]["boro_cd"]
            district_note = (f"street runs through {len(cds)} districts; picked the one with the most "
                             "complaints of this type. Confirm with the cross street.")
        else:
            district_note = "unknown: no past complaints found on this street"

    # The route is decided by the rules, not the agent.
    complaint_class = rules.classify(d.complaint_type, d.descriptor) if d.complaint_type else None
    group = rules.crosswalk_group(d.complaint_type) if d.complaint_type else None
    match = None
    if d.complaint_type and d.street_name and boro_cd:
        m = candidates[(candidates["boro_cd"] == boro_cd) & (candidates["complaint_type"] == d.complaint_type)
                       & (candidates["location"] == d.street_name.upper())]
        match = (m.iloc[0][["location", "reason", "complaints", "signal_complaints", "signal_descriptors"]]
                 .fillna("").to_dict() if len(m) else None)

    if review:
        route, why = "human_review", HELD
    elif not d.complaint_type and d.advice_topics:
        route, why = "advice", ("No 311 complaint type covers this, but official City guidance does. A reply with "
                                "that guidance is drafted for approval.")
    elif d.is_city_infrastructure_request and not d.complaint_type:
        # A City matter that no 311 complaint type fits (for example a request for a new bridge): never filtered
        # out as "not a City matter"; a person decides where it goes.
        review.append("A City matter with no matching 311 complaint type: a person should decide where it goes.")
        route, why = "human_review", HELD
    elif not d.is_city_infrastructure_request or not d.complaint_type:
        route, why = "not_city", "Not a City matter that matches a 311 complaint type."
    elif complaint_class == rules.CAPITAL_SIGNAL and group:
        route, why = "capital", "The complaint describes a failed asset, which City rules treat as a capital need."
    elif match and group:
        route, why = "capital", f"This street already qualifies as a capital candidate ({match['reason']})."
    else:
        route, why = "operating", "A repair or service the agency handles from its operating budget."
    checks["route_by_rules"] = {"complaint_class": complaint_class, "route": route}

    # Bank only clean answers the agent actually produced.
    banked_now = bank_match is None and route != "human_review"
    if banked_now:
        request_bank.add(redacted, d.model_dump(), run_id)
    checks["request_bank"]["stored"] = banked_now

    return TriageResult(
        run_id=run_id, request_text=redacted, decision=d, route=route, route_reason=why,
        complaint_class=complaint_class, agency=agency_by_type.get(d.complaint_type), capital_group=group,
        boro_cd=boro_cd, district_label=labels.get(boro_cd), district_note=district_note, street_record=record,
        matched_candidate=match, review_reasons=review, checks=checks, usage=usage, bank_match=bank_match,
    )


def _audit(res: TriageResult, started: float) -> TriageResult:
    d = res.decision
    audit.record({
        "run_id": res.run_id,
        "flow": "triage",
        "request_text": res.request_text,
        "safety_checks": res.checks,
        "stopped": res.stopped,
        "agent_answer": d and {"complaint_type": d.complaint_type, "descriptor": d.descriptor,
                               "borough": d.borough, "street_name": d.street_name, "confidence": d.confidence,
                               "summary": d.summary, "other_problems": d.other_problems},
        "route": res.route,
        "route_reason": res.route_reason,
        "emergency": res.emergency,
        "urgent_hazard": res.urgent_hazard,
        "review_reasons": res.review_reasons,
        "agency": res.agency,
        "district": res.district_label,
        "tokens": {k: res.usage.get(k, 0) for k in ("requests", "input_tokens", "cached_input_tokens",
                                                     "cache_write_tokens", "output_tokens")},
        "cost_usd": res.usage.get("cost_usd", 0.0),
        "seconds": round(time.monotonic() - started, 2),
    })
    return res


def _screening(results: list[dict], blocked: bool) -> dict:
    """Azure's prompt screening for this run, merged across calls."""
    return {
        "blocked": blocked,
        "jailbreak_detected": any(r["jailbreak_detected"] for r in results),
        "indirect_attack_detected": any(r["indirect_attack_detected"] for r in results),
        "screened_calls": len(results),
    }
