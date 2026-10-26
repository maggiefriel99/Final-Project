"""Stress test for everything after Triage: agency alerts, resident replies, cost estimates, and emergency
escalation, on real resident wording.

    python analysis/pipeline_check.py TEXTS.json [--limit 80] [--label NAME]

TEXTS.json is a list of {"text": ...} (for example public complaints from other cities, kept outside the
repository). Each text goes through the same steps as the New request page. The request bank is on, so
texts already triaged in the same log folder cost nothing at the Triage step. Records crashes, drafts that
fail their checks ("needs editing", with the reasons), stopped runs, and which agencies emergencies reach.
Nothing is sent anywhere: alerts and replies are drafts in the log folder's queue.
Writes analysis/pipeline_check_<label>.md/.json (aggregates and reasons only, no request text).
"""

import argparse
import json
import re
import sys
import traceback
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from civicequity import data, emergency, needs  # noqa: E402
from civicequity.agents.cost_estimator import estimate_fix  # noqa: E402
from civicequity.agents.operating_router import route_to_agency  # noqa: E402
from civicequity.agents.resident_reply import draft_reply  # noqa: E402
from civicequity.agents.triage import triage  # noqa: E402
from civicequity.cost_model import CostModel  # noqa: E402


def norm(problem: str) -> str:
    return re.sub(r'"[^"]*"|\d[\d,.]*', "…", problem)[:100]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("texts")
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--label", default="run1")
    a = ap.parse_args()
    texts = [x["text"] for x in json.loads(Path(a.texts).read_text())][: a.limit]
    snap = data.load_snapshot()
    cand = needs.capital_candidates(snap)
    model = CostModel(snap)
    rows = []
    for i, text in enumerate(texts):
        row = {"i": i, "crash": None, "steps": {}}
        try:
            t = triage(text, snap, cand, use_bank=True)
            row["route"] = t.route
            if t.route == "emergency":
                e = emergency.escalate(t, t.emergency["incident_type"], t.emergency["detected_by"])
                row["steps"]["emergency"] = {"incident": e["incident_type"], "agencies": [x["agency"] for x in e["agencies"]]}
            if t.route == "capital" and t.capital_group:
                try:
                    c = estimate_fix(t, model)
                    row["steps"]["cost"] = {"problems": c.problems, "chosen_by": c.chosen_by,
                                            "stopped": (c.stopped or {}).get("by") if hasattr(c, "stopped") else None}
                except ValueError as ve:
                    row["steps"]["cost"] = {"skipped": str(ve)}
            if t.route in ("operating", "capital") and t.agency:
                al = route_to_agency(t)
                row["steps"]["alert"] = {"status": al["status"], "problems": al["problems"],
                                         "stopped": (al["stopped"] or {}).get("by")}
            rp = draft_reply(t)
            if rp:
                row["steps"]["reply"] = {"status": rp["status"], "problems": rp["problems"],
                                         "stopped": (rp["stopped"] or {}).get("by")}
        except Exception:
            row["crash"] = traceback.format_exc()[-1200:]
        rows.append(row)
        print(f"{i + 1:3d} {str(row.get('route')):13s} " + " ".join(
            f"{k}:{v.get('status') or v.get('incident') or v.get('chosen_by') or v.get('skipped', '')[:20]}"
            for k, v in row["steps"].items()) + ("  CRASH" if row["crash"] else ""), flush=True)

    alerts = [r["steps"]["alert"] for r in rows if "alert" in r["steps"]]
    replies = [r["steps"]["reply"] for r in rows if "reply" in r["steps"]]
    costs = [r["steps"]["cost"] for r in rows if "cost" in r["steps"]]
    summary = {
        "requests": len(rows), "crashes": sum(bool(r["crash"]) for r in rows),
        "routes": dict(Counter(r.get("route") for r in rows)),
        "alerts": len(alerts), "alerts_needing_edits": sum(a["status"] == "needs editing" for a in alerts),
        "alert_problems": dict(Counter(norm(p) for a in alerts for p in a["problems"]).most_common(10)),
        "replies": len(replies), "replies_needing_edits": sum(r["status"] == "needs editing" for r in replies),
        "reply_problems": dict(Counter(norm(p) for r in replies for p in r["problems"]).most_common(10)),
        "cost_estimates": len(costs), "cost_estimates_with_problems": sum(bool(c.get("problems")) for c in costs),
        "cost_problems": dict(Counter(norm(p) for c in costs for p in c.get("problems", [])).most_common(10)),
        "emergencies": [r["steps"]["emergency"] for r in rows if "emergency" in r["steps"]],
    }
    (OUT / f"pipeline_check_{a.label}.json").write_text(json.dumps(
        {"summary": summary, "crashes": [r["crash"] for r in rows if r["crash"]]}, indent=1))
    L = [f"# Full pipeline on real resident wording ({a.label})", "",
         f"- Requests: {summary['requests']}; crashes: {summary['crashes']}; routes: {summary['routes']}",
         f"- Agency alerts: {summary['alerts']}, needing edits: {summary['alerts_needing_edits']}",
         f"- Resident replies: {summary['replies']}, needing edits: {summary['replies_needing_edits']}",
         f"- Cost estimates: {summary['cost_estimates']}, with problems: {summary['cost_estimates_with_problems']}",
         f"- Emergencies escalated: {len(summary['emergencies'])}", "",
         "Most common draft problems:", ""]
    L += [f"- alert: {k} ({v})" for k, v in summary["alert_problems"].items()]
    L += [f"- reply: {k} ({v})" for k, v in summary["reply_problems"].items()]
    L += [f"- cost: {k} ({v})" for k, v in summary["cost_problems"].items()]
    (OUT / f"pipeline_check_{a.label}.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
