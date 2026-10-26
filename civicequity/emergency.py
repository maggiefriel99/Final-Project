"""Emergency escalation: what happens when a request may report danger to life or safety.

SIMULATION ONLY. Nothing here contacts 911 or any agency. "Alerts" are drafts written into the local
approval queue (logs/alerts.jsonl), and approving one only changes its status there.

Everything in this module is code, not AI: which agencies get an alert comes from emergencies.yaml
(the City's CIMS charts, checked word for word by analysis/verify_sources.py), and the alert and the
message to the resident are fixed templates. The only AI-written text that appears is the request
summary, which has already passed the output checks.
"""

import re
import time
import uuid
from functools import cache

import yaml

from civicequity import alerts, audit, config

SIMULATION_NOTE = "SIMULATION: CivicEquity is a class project. Nothing was sent to 911 or to any agency."
OTHER = "Other danger to life or safety"


@cache
def plan() -> dict:
    return yaml.safe_load((config.ROOT / "emergencies.yaml").read_text())


def incident_types() -> list[str]:
    return list(plan()["incidents"])


def keyword_hits(text: str) -> list[str]:
    """Words that can signal danger to life. Routine phrases (fire hydrant, smoke detector...) are ignored."""
    b = plan()["backstop"]
    t = (text or "").lower()
    for phrase in b["ignore"]:
        t = t.replace(phrase, " ")
    hits = []
    for p in b["patterns"]:
        m = re.search(p, t)
        if m:
            hits.append(m.group(0).strip())
    return hits


def agency_name(code: str) -> str:
    from civicequity.agents.operating_router import AGENCY_NAMES  # late import: router imports triage
    return plan()["agency_names"].get(code) or AGENCY_NAMES.get(code, code)


def agencies_for(incident: str, complaint_agency: str | None = None, complaint_type: str | None = None) -> list[dict]:
    """Who gets an alert, and each agency's role in CIMS words. Order: primary, support, coordinator, 311 agency."""
    p = plan()
    inc = p["incidents"].get(incident) or p["incidents"][OTHER]
    out: dict[str, dict] = {}
    for a in inc["primary"]:
        out[a] = {"agency": a, "role": f"Primary agency for \"{incident}\" in the CIMS Primary Agency Matrix "
                                      f"({inc['command']}).", "basis": "CIMS Annex B"}
    for a, comp in inc.get("support", []):
        out.setdefault(a, {"agency": a, "role": f"Supporting agency. CIMS core competency: {p['competencies'][comp]}.",
                           "basis": "CIMS Annex A (chosen by CivicEquity)"})
    co = p["coordinating_agency"]
    out.setdefault(co["agency"], {"agency": co["agency"], "role": f"Coordinating agency. CIMS core competency: "
                                                                  f"{co['competency']}.", "basis": "CIMS"})
    if complaint_agency:
        out.setdefault(complaint_agency, {"agency": complaint_agency,
                                          "role": f"Handles the 311 complaint type \"{complaint_type}\".",
                                          "basis": "311 data"})
    for v in out.values():
        v["agency_name"] = agency_name(v["agency"])
    return list(out.values())


def resident_notice(incident: str, advice_ids: list[str]) -> dict:
    """The fixed message to the resident: call 911, plus any verified advice that fits, word for word."""
    from civicequity.agents.resident_reply import advice_library  # late import (avoids a cycle)
    p, lib = plan(), advice_library()
    ids = list(dict.fromkeys(p["incidents"].get(incident, {}).get("advice", []) + advice_ids))
    ids = [a for a in ids if a in lib]
    body = "\n\n".join([p["resident_message"]["text"]] + [lib[a]["guidance"] for a in ids])
    src = p["sources"][p["resident_message"]["source"]]
    sources = [{"title": src["title"], "url": src["url"]}] + [{"title": s["source_title"], "url": s["url"]}
                                                              for a in ids for s in [lib[a], *lib[a].get("more_sources", [])]]
    return {"subject": "If anyone is in danger, call 911", "body": body, "priority": "urgent", "priority_reason": "",
            "requested_action": "", "advice_ids": ids, "sources": sources}


def escalate(t, incident: str, detected_by: list[str], reason: str = "", reviewer: str | None = None) -> dict:
    """Draft urgent alerts to every agency for this incident type and the 911 message to the resident.

    t is a TriageResult. Requests that also looked like manipulation are still escalated (a person checks
    every alert anyway), but their alerts are marked as needing a closer look."""
    from civicequity.agents.resident_reply import suspected_manipulation
    started = time.monotonic()
    run_id = f"emergency-{uuid.uuid4().hex[:8]}"
    d = t.decision
    incident = incident if incident in plan()["incidents"] else OTHER
    agencies = agencies_for(incident, t.agency, d and d.complaint_type)
    where = ", ".join(filter(None, [d and " & ".join(filter(None, [d.street_name, d.cross_street])),
                                    t.district_label or (d and d.borough)])) or "not given in the request"
    summary = (d and d.summary) or reason or "See the original request."
    flagged = suspected_manipulation(t)
    problems = (["The request also looked like an attempt to manipulate the system. Check it before approving."]
                if flagged else [])
    by = ", ".join(detected_by) + (f" (flagged by {reviewer})" if reviewer else "")
    ids = []
    for a in agencies:
        others = [x["agency"] for x in agencies if x["agency"] != a["agency"]]
        body = (f"{SIMULATION_NOTE}\n\nA 311 request may report an emergency: {incident}.\n"
                f"What the request reports: {summary}\nWhere: {where}\n"
                f"Your agency's role: {a['role']}\n"
                f"Also alerted: {', '.join(others) or 'no other agency'}.\n"
                "The resident was told to call 911 if anyone is in danger.\n"
                f"Flagged by: {by}.")
        alert = alerts.queue(
            kind="emergency_alert", triage_run_id=t.run_id, router_run_id=run_id, agency=a["agency"],
            agency_name=a["agency_name"], district=t.district_label, incident_type=incident,
            facts={"incident_type": incident, "role": a["role"], "basis": a["basis"], "detected_by": detected_by,
                   "summary": summary, "where": where},
            draft={"subject": f"[SIMULATION, NOT SENT] URGENT: possible {incident.lower()}"
                              + (f", {d.borough}" if d and d.borough else ""),
                   "body": body, "priority": "urgent",
                   "priority_reason": f"Possible danger to life or safety. Flagged by: {by}.",
                   "requested_action": "Check whether 911 already has this incident; if it is real, respond "
                                       "under CIMS."},
            problems=problems, stopped=None, usage={})
        ids.append(alert["alert_id"])
    advice_ids = list(d.advice_topics) if d else []
    notice = alerts.queue(
        kind="resident_reply", triage_run_id=t.run_id, router_run_id=run_id, agency="",
        agency_name="Resident (reply to the person who made the request)", district=t.district_label,
        facts={"incident_type": incident}, draft=resident_notice(incident, advice_ids), problems=[], stopped=None,
        usage={}, status="shown to resident (simulated)")
    result = {"run_id": run_id, "incident_type": incident, "detected_by": detected_by, "reviewer": reviewer,
              "agencies": agencies, "alert_ids": ids, "notice_id": notice["alert_id"], "manipulation_suspected": flagged}
    audit.record({"run_id": run_id, "flow": "emergency_escalation", "triage_run_id": t.run_id,
                  "incident_type": incident, "detected_by": detected_by, "flagged_by_staff": reviewer,
                  "agencies": [a["agency"] for a in agencies], "alert_ids": ids, "notice_id": notice["alert_id"],
                  "simulation": True, "tokens": {}, "cost_usd": 0.0,
                  "seconds": round(time.monotonic() - started, 2)})
    return result
