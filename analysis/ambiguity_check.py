"""How Triage handles loosely worded requests and emergencies.

    python analysis/ambiguity_check.py [label]

Runs every case in analysis/ambiguity_cases.json through Triage (fresh AI call, request bank off; logs go
to a separate folder so the app's audit log and daily spending are untouched) and scores it:
  - ambiguous: passes if the request is NOT held and gets an accepted complaint type, or the accepted
    advice entry when no complaint type fits;
  - emergency: passes if it is flagged as an emergency of an accepted incident type;
  - not_emergency: passes if it is NOT flagged as an emergency (false alarms).
Nothing is sent anywhere: Triage only reads, and emergency alerts are not drafted by this check.
Writes analysis/ambiguity_check_<label>.json and .md.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent
os.environ.setdefault("CIVICEQUITY_LOG_DIR", tempfile.mkdtemp(prefix="civicequity-ambiguity-"))
sys.path.insert(0, str(ROOT))

from civicequity import data, needs  # noqa: E402
from civicequity.agents.triage import triage  # noqa: E402


def main(label: str):
    cases = json.loads((OUT / "ambiguity_cases.json").read_text())
    snap = data.load_snapshot()
    cand = needs.capital_candidates(snap)
    rows = []
    for group in ("ambiguous", "emergency", "not_emergency"):
        for c in cases[group]:
            t = triage(c["text"], snap, cand, use_bank=False)
            d = t.decision
            em = getattr(t, "emergency", None) or {}
            advice = list(getattr(d, "advice_topics", None) or []) if d else []
            got = {"route": t.route, "complaint_type": d and d.complaint_type, "agency": t.agency,
                   "confidence": d and d.confidence, "advice": advice,
                   "emergency_type": em.get("incident_type"), "emergency_source": em.get("detected_by"),
                   "urgent_hazard": bool(getattr(t, "urgent_hazard", None)),
                   "review_reasons": t.review_reasons}
            if group == "ambiguous":
                ok = t.route != "human_review" and (
                    got["complaint_type"] in c["accept"] or any(f"advice:{a}" in c["accept"] for a in advice))
            elif group == "emergency":
                ok = got["emergency_type"] in c["accept"]
            else:
                ok = not got["emergency_type"]
            rows.append({"group": group, "text": c["text"], "accept": c.get("accept"), "pass": ok, **got})
            print(f"{'PASS' if ok else 'FAIL'} {group:13s} {t.route:13s} {str(got['complaint_type'] or advice):32s} "
                  f"{got['emergency_type'] or '':28s} {c['text'][:60]}")
    summary = {g: f"{sum(r['pass'] for r in rows if r['group'] == g)} of {sum(r['group'] == g for r in rows)}"
               for g in ("ambiguous", "emergency", "not_emergency")}
    summary["ambiguous_escalated"] = (f"{sum(r['route'] == 'emergency' for r in rows if r['group'] == 'ambiguous')} of "
                                      f"{sum(r['group'] == 'ambiguous' for r in rows)}")
    (OUT / f"ambiguity_check_{label}.json").write_text(json.dumps({"summary": summary, "cases": rows}, indent=1))
    L = [f"# Loosely worded requests and emergencies ({label})", "",
         f"- Loosely worded requests sorted correctly (not held): {summary['ambiguous']}",
         f"- Emergencies flagged with an accepted incident type: {summary['emergency']}",
         f"- Look-alike non-emergencies NOT flagged: {summary['not_emergency']}",
         f"- Loosely worded requests that were escalated as possible emergencies: {summary['ambiguous_escalated']} "
         "(the screener is told to escalate when in doubt; a person checks every escalation)", "",
         "| Group | Request | Route | Type / advice | Emergency | Pass |", "|---|---|---|---|---|---|"]
    L += [f"| {r['group']} | {r['text']} | {r['route']} | {r['complaint_type'] or ', '.join(r['advice']) or ''} | "
          f"{r['emergency_type'] or ''} | {'yes' if r['pass'] else 'no'} |" for r in rows]
    (OUT / f"ambiguity_check_{label}.md").write_text("\n".join(L) + "\n")
    print("\n" + "\n".join(L[2:5]))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "after")
