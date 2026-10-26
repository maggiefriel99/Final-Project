"""Stress test: real resident complaints from other US cities, run through Triage and the Life-Safety Screener.

    python analysis/other_cities_check.py FETCH_DIR [--per-city 100] [--label NAME]

FETCH_DIR holds one JSON file per city of public SeeClickFix issues (category + resident's own description;
no reporter fields), e.g. made by fetching https://seeclickfix.com/api/v2/issues?place_url=<city> (public,
read-only API). Raw text is never written into this repository: only the aggregate results
(analysis/other_cities_check_<label>.md/.json) and short ids for follow-up.

What it measures (none of these cities is NYC, so every request is a fair test of robustness):
  - crashes and stopped runs;
  - how often ordinary text trips the manipulation checks (false alarms) or is held, and why;
  - invented NYC locations: a borough set when the text names no NYC borough;
  - emergency escalations (each listed for a person to judge);
  - complaint type vs. the city's own category, through a category -> acceptable NYC types mapping written
    for this test (CATEGORY_MAP); categories with no clear NYC equivalent are not scored.
Fresh AI calls, request bank off. About $0.0006 per request.
"""

import argparse
import json
import random
import re
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from civicequity import data, needs  # noqa: E402
from civicequity.agents.triage import triage  # noqa: E402

HPD_DOB = {"GENERAL", "UNSANITARY CONDITION", "PLUMBING", "PAINT/PLASTER", "HEAT/HOT WATER", "DOOR/WINDOW",
           "ELECTRIC", "FLOORING/STAIRS", "OUTSIDE BUILDING", "SAFETY", "Mold", "WATER LEAK", "APPLIANCE",
           "General Construction/Plumbing", "Building/Use", "Electrical", "Plumbing", "Scaffold Safety"}
WASTE = {"Missed Collection", "Residential Disposal Complaint", "Commercial Disposal Complaint", "Dirty Condition",
         "Illegal Dumping", "Litter Basket Complaint", "Dumpster Complaint", "Lot Condition"}
TREES = {"Damaged Tree", "Dead/Dying Tree", "Overgrown Tree/Branches", "New Tree Request", "Uprooted Stump",
         "Root/Sewer/Sidewalk Condition", "Illegal Tree Damage", "Wood Pile Remaining"}
# Category keywords (other city) -> NYC complaint types a 311 operator could reasonably choose. Written for
# this test; first match wins.
CATEGORY_MAP = [
    (r"pothole|street condition|cave in|sink ?hole|street hazard", {"Street Condition", "Highway Condition", "Sewer",
                                                                    "Curb Condition"}),
    (r"street ?light|street lamp", {"Street Light Condition"}),
    (r"graffiti", {"Graffiti"}),
    (r"traffic signal|pedestrian signal", {"Traffic Signal Condition"}),
    (r"\bsigns?\b|sign repair|traffic sign", {"Street Sign - Damaged", "Street Sign - Missing", "Street Sign - Dangling",
                                             "Traffic Signal Condition", "Illegal Posting"}),
    (r"illegal dump|dumping|dump site", {"Illegal Dumping", "Dirty Condition", "Lot Condition"}),
    (r"trash|garbage|recycl|solid waste|heavy trash|yard waste|large item|container|debris", WASTE | {"Obstruction"}),
    (r"abandoned vehicle|junk motor", {"Abandoned Vehicle", "Derelict Vehicles"}),
    (r"scooter", {"E-Scooter", "Obstruction", "Illegal Parking"}),
    (r"bike lane|parking|valet|vehicle sticker", {"Illegal Parking", "Blocked Driveway", "Abandoned Vehicle",
                                                  "Derelict Vehicles"}),
    (r"tree", TREES),
    (r"sidewalk|curb|accessibility", {"Sidewalk Condition", "Curb Condition", "Root/Sewer/Sidewalk Condition",
                                      "Obstruction", "Street Condition"}),
    (r"rodent", {"Rodent"}),
    (r"dead animal|deceased animal", {"Dead Animal"}),
    (r"encampment|homeless", {"Encampment", "Homeless Person Assistance"}),
    (r"water leak|water service|service leak|fire hydrant", {"Water System", "Water Maintenance", "WATER LEAK",
                                                             "Water Conservation", "Water Quality", "PLUMBING"}),
    (r"drainage|sewer", {"Sewer", "Sewer Maintenance", "Street Condition", "Standing Water", "Indoor Sewage"}),
    (r"restaurant", {"Food Establishment", "Food Poisoning"}),
    (r"housing code|building code|building violation", HPD_DOB),
    (r"tall grass|weed", {"Lot Condition", "Dirty Condition", "Overgrown Tree/Branches", "Maintenance or Facility",
                          "Plant", "Obstruction", "Illegal Dumping"}),
    (r"snow|ice", {"Snow or Ice"}),
    (r"human waste", {"Dirty Condition", "Urinating in Public", "UNSANITARY CONDITION"}),
    (r"scooter", {"E-Scooter", "Obstruction"}),
    (r"obstruction", {"Obstruction", "Dirty Condition", "Illegal Dumping"}),
]
NOISE = re.compile(r"noise", re.I)
NYC_PLACE = re.compile(r"\b(manhattan|bronx|brooklyn|queens|staten island|new york|nyc)\b", re.I)


def accepted(category: str) -> set | None:
    c = (category or "").lower()
    if NOISE.search(c):
        return {"*noise*"}
    for pattern, types in CATEGORY_MAP:
        if re.search(pattern, c):
            return types
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fetch_dir")
    ap.add_argument("--per-city", type=int, default=100)
    ap.add_argument("--label", default="run1")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--exclude", help="a results file from an earlier run: skip the complaints it already used")
    ap.add_argument("--results", help="where to write per-request rows (outside the repo)", default=None)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    used = {x["id"] for x in json.loads(Path(a.exclude).read_text())} if a.exclude else set()
    sample = []
    for f in sorted(Path(a.fetch_dir).glob("*.json")):
        rows = [r for r in json.loads(f.read_text()) if r["id"] not in used]
        sample += rng.sample(rows, min(a.per_city, len(rows)))
    snap = data.load_snapshot()
    cand = needs.capital_candidates(snap)
    out = []
    for i, r in enumerate(sample):
        cat = r.get("request_type") or r.get("category") or ""
        started = time.monotonic()
        try:
            t = triage(r["text"], snap, cand, use_bank=False)
            d = t.decision
            c = t.checks
            row = {
                "city": r["city"], "id": r["id"], "category": cat, "route": t.route,
                "complaint_type": d and d.complaint_type, "agency": t.agency, "confidence": d and d.confidence,
                "borough": d and d.borough, "street": d and d.street_name, "district": t.district_label,
                "emergency": (t.emergency or {}).get("incident_type"),
                "emergency_by": (t.emergency or {}).get("detected_by"),
                "possible_emergency_hold": bool(t.emergency and t.emergency.get("possible")),
                "urgent_hazard": bool(t.urgent_hazard),
                "pattern_flags": c["pattern_check"]["phrases"],
                "agent_flag": (c.get("agent_flag") or {}).get("suspicious_instructions"),
                "screener_flag": (c.get("life_safety") or {}).get("suspicious_instructions"),
                "provider_blocked": (c.get("provider_filter") or {}).get("blocked"),
                "danger_words": c.get("danger_words"), "personal_info": c.get("personal_info_removed"),
                "output_issues": (c.get("output_validation") or {}).get("output_issues"),
                "fixes": (c.get("output_validation") or {}).get("fixes"),
                "review_reasons": t.review_reasons, "stopped": (t.stopped or {}).get("by"),
                "names_nyc_place": bool(NYC_PLACE.search(r["text"])), "seconds": round(time.monotonic() - started, 1),
                "crash": None,
            }
        except Exception:
            row = {"city": r["city"], "id": r["id"], "category": cat, "crash": traceback.format_exc()[-800:]}
        acc = accepted(cat)
        if acc and not row.get("crash"):
            ct = row["complaint_type"]
            row["scored"] = True
            row["type_ok"] = bool(ct) and (ct in acc or ("*noise*" in acc and ct.startswith("Noise")))
        else:
            row["scored"] = False
        out.append(row)
        print(f"{i + 1:4d} {row['city']:11s} {str(row.get('route')):13s} {str(row.get('complaint_type')):28s} "
              f"{'OK ' if row.get('type_ok') else '-- ' if row['scored'] else '   '}{cat[:40]}", flush=True)
    if a.results:
        Path(a.results).write_text(json.dumps(out, indent=1))

    ok = [x for x in out if not x.get("crash")]
    n = len(ok)
    scored = [x for x in ok if x["scored"]]
    held = [x for x in ok if x["route"] == "human_review"]
    reasons = Counter()
    for x in held:
        for rr in x["review_reasons"]:
            reasons[re.sub(r'"[^"]*"', '"…"', rr)[:110]] += 1
    invented = [x for x in ok if x["borough"] and not x["names_nyc_place"]]
    summary = {
        "requests": len(out), "crashes": len(out) - n,
        "routes": dict(Counter(x["route"] for x in ok)),
        "held": len(held), "held_reasons": dict(reasons.most_common(12)),
        "pattern_false_alarms": sum(bool(x["pattern_flags"]) for x in ok),
        "agent_or_screener_manipulation_flags": sum(bool(x["agent_flag"] or x["screener_flag"]) for x in ok),
        "provider_blocked": sum(bool(x["provider_blocked"]) for x in ok),
        "invented_nyc_borough": len(invented),
        "district_assigned": sum(bool(x["district"]) for x in ok),
        "emergencies": sum(bool(x["emergency"]) for x in ok),
        "urgent_hazards": sum(bool(x.get("urgent_hazard")) for x in ok),
        "possible_emergency_holds": sum(x["possible_emergency_hold"] for x in ok),
        "type_scored": len(scored), "type_ok": sum(x["type_ok"] for x in scored),
        "type_accuracy_by_city": {c: f"{sum(x['type_ok'] for x in scored if x['city'] == c)} of "
                                     f"{sum(x['city'] == c for x in scored)}" for c in sorted({x['city'] for x in scored})},
        "personal_info_removed": sum(bool(x["personal_info"]) for x in ok),
        "median_seconds": sorted(x["seconds"] for x in ok)[n // 2] if n else None,
    }
    (OUT / f"other_cities_check_{a.label}.json").write_text(json.dumps({
        "summary": summary,
        "misses": [{k: x.get(k) for k in ("city", "id", "category", "complaint_type", "route")}
                   for x in scored if not x["type_ok"]],
        "emergencies": [{k: x.get(k) for k in ("city", "id", "category", "emergency", "emergency_by")}
                        for x in ok if x["emergency"]],
        "invented_borough": [{k: x.get(k) for k in ("city", "id", "category", "borough", "street", "district")}
                             for x in invented],
        "pattern_false_alarms": [{k: x.get(k) for k in ("city", "id", "pattern_flags")} for x in ok if x["pattern_flags"]],
        "crashes": [x for x in out if x.get("crash")],
    }, indent=1))
    L = [f"# Other cities' 311 complaints ({a.label})", "",
         f"{summary['requests']} public complaints from {len({x['city'] for x in out})} cities (SeeClickFix), "
         "resident wording, run through Triage and the Life-Safety Screener.", "",
         f"- Crashes: {summary['crashes']}",
         f"- Routes: {summary['routes']}",
         f"- Complaint type plausible for the city's own category: {summary['type_ok']} of {summary['type_scored']} "
         f"scored ({summary['type_accuracy_by_city']})",
         f"- Ordinary text flagged by the manipulation patterns: {summary['pattern_false_alarms']}; by an AI agent: "
         f"{summary['agent_or_screener_manipulation_flags']}; blocked by the provider: {summary['provider_blocked']}",
         f"- NYC borough invented for a non-NYC complaint: {summary['invented_nyc_borough']}; NYC district assigned: "
         f"{summary['district_assigned']}",
         f"- Escalated as possible emergencies: {summary['emergencies']}; urgent hazards (alert marked urgent, no "
         f"911): {summary['urgent_hazards']}; held for danger words only: "
         f"{summary['possible_emergency_holds']}",
         f"- Personal information removed in: {summary['personal_info_removed']}",
         f"- Median seconds per request: {summary['median_seconds']}", "",
         "Why requests were held:", ""] + [f"- {k}: {v}" for k, v in summary["held_reasons"].items()]
    (OUT / f"other_cities_check_{a.label}.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
