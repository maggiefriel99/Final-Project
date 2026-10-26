"""Measured tokenomics and scalability for CivicEquity as a live 311 system.

    python analysis/tokenomics.py            # 40 records + 1 memo, about 5 minutes and a few cents
    python analysis/tokenomics.py --n 80

In real use CivicEquity would process every 311 request as it arrives. This script measures what that costs:
  1. Volume, from real data: all 311 requests in the year (data/311_catalog_all.csv) and daily counts back to
     2020 from NYC Open Data (saved to analysis/311_daily_counts.csv).
  2. Workload: N real 311 records from September 2026, picked at random moments, so the mix of complaint
     types matches what 311 actually receives. Public 311 data has no resident-written text, so each record's
     complaint type, descriptor, and location are turned into a short message (labeled synthetic text).
  3. Each record goes through the live pipeline (Triage -> Cost Estimator for capital needs -> agency alert
     -> resident reply) with Portkey's response cache bypassed, so every call is a real, billed call.
     Logs go to a separate folder, not the app's.
  4. One fresh memo.
  5. Monthly cost at 311 volume, peak-day cost against the daily cap, cost per user at 100 and 10,000 users,
     human-approval workload, throughput, and timing of the two parts that grow with use.
Writes analysis/tokenomics.json and analysis/tokenomics.md. Assumptions are labeled as assumptions.
"""

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent
RUN_LOGS = Path(tempfile.mkdtemp(prefix="civicequity-tokenomics-"))
os.environ["CIVICEQUITY_FRESH_CALLS"] = "1"  # bypass Portkey's response cache
os.environ["CIVICEQUITY_LOG_DIR"] = str(RUN_LOGS)  # keep test traffic out of the app's logs
sys.path.insert(0, str(ROOT))

from civicequity import alerts, config, data, emergency, needs, request_bank  # noqa: E402
from civicequity.agents.cost_estimator import estimate_fix  # noqa: E402
from civicequity.agents.memo import run_memo  # noqa: E402
from civicequity.agents.operating_router import route_to_agency  # noqa: E402
from civicequity.agents.resident_reply import draft_reply  # noqa: E402
from civicequity.agents.triage import triage  # noqa: E402
from civicequity.cost_model import CostModel  # noqa: E402

BASE = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"

# ---------- assumptions (not measured; stated so the reader can change them) ----------
ASSUMPTIONS = {
    "memos_per_user_per_month": 4,          # one per week per staff user
    "seconds_to_review_one_draft": 30,       # staff time to read and approve an alert or reply
    "staff_work_hours_per_month": 140,       # one full-time employee
    "peak_hour_share_of_day": 0.10,          # share of a day's requests arriving in its busiest hour
    "qa_sample_share_of_auto_sent": 0.02,    # tiered design: random share of auto-sent drafts a person still checks
}


def needs_person(draft: dict, route: str) -> bool:
    """Tiered approval for live use: a person approves anything with advice, capital or urgent/elevated alerts,
    anything that failed a check or had no draft, and anything from a request held for review."""
    d = draft.get("draft") or {}
    if draft.get("status", "").startswith("shown"):
        return False  # the fixed 911 message: verified text written by code, shown at once
    if draft.get("kind") == "emergency_alert":
        return True  # every emergency alert waits for a person
    if draft.get("problems") or not draft.get("draft") or route == "human_review":
        return True
    if draft.get("kind") == "resident_reply":
        return bool(d.get("advice_ids"))
    return route == "capital" or d.get("priority") in ("urgent", "elevated")


def get(params, tries=4):
    for i in range(tries):
        try:
            r = requests.get(BASE, params=params, timeout=300)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(10 * (i + 1))


def daily_counts() -> pd.DataFrame:
    cache = OUT / "311_daily_counts.csv"
    if cache.exists():
        return pd.read_csv(cache)
    def year(y):
        return get({"$select": "date_trunc_ymd(created_date) as day,count(*) as n",
                    "$where": f"created_date >= '{y}-01-01' AND created_date < '{y + 1}-01-01'",
                    "$group": "day", "$limit": 400})
    with ThreadPoolExecutor(7) as pool:
        rows = [r for rows in pool.map(year, range(2020, 2027)) for r in rows]
    d = pd.DataFrame(rows).assign(day=lambda x: x["day"].str[:10], n=lambda x: x["n"].astype(int)).sort_values("day")
    d.to_csv(cache, index=False)
    return d


def sample_records(n: int, seed: int = 26) -> list[dict]:
    """Real 311 records at random moments in September 2026 (so the type mix matches real volume)."""
    rng = np.random.default_rng(seed)
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    moments = sorted(start + timedelta(seconds=float(s)) for s in rng.uniform(0, 30 * 86400, n))

    def one(ts):
        rows = get({"$select": "unique_key,agency,complaint_type,descriptor,incident_address,street_name,"
                               "cross_street_1,borough",
                    "$where": f"created_date >= '{ts:%Y-%m-%dT%H:%M:%S}'", "$order": "created_date", "$limit": 1})
        return rows[0] if rows else None

    with ThreadPoolExecutor(8) as pool:
        return [r for r in pool.map(one, moments) if r]


def message(r: dict) -> str:
    """A short resident-style message built from a real record's fields (synthetic text)."""
    where = r.get("incident_address") or " and ".join(filter(None, [r.get("street_name"), r.get("cross_street_1")]))
    boro = (r.get("borough") or "").title()
    what = r.get("descriptor") or r.get("complaint_type")
    return f"I want to report a problem: {r['complaint_type']} ({what}) at {where}, {boro}. Please help."


def run_record(r, snap, cands, model):
    t0 = time.monotonic()
    out = {"unique_key": r["unique_key"], "agency_in_311": r["agency"], "complaint_type_in_311": r["complaint_type"]}
    t = triage(message(r), snap, cands, use_bank=False)
    out.update(route=t.route, triage_type=t.decision.complaint_type if t.decision else None,
               urgent_hazard=bool(t.urgent_hazard), emergency=(t.emergency or {}).get("incident_type"))
    triage_usage = dict(t.usage)
    usage = {"life_safety_screener": triage_usage.pop("life_safety", {}) or {}, "triage": triage_usage}
    if t.route == "emergency":
        # Code only, no AI: urgent alerts to the CIMS agencies and the fixed 911 message.
        emergency.escalate(t, t.emergency["incident_type"], t.emergency["detected_by"])
    if t.route == "capital" and t.capital_group:
        try:
            usage["cost_estimator"] = estimate_fix(t, model).usage
        except ValueError:
            pass
    if t.route in ("operating", "capital") and t.agency:
        usage["agency_alert"] = route_to_agency(t)["usage"]
    reply = draft_reply(t)
    if reply:
        usage["resident_reply"] = reply["usage"]
    out["usage"] = usage
    out["seconds"] = round(time.monotonic() - t0, 2)
    out["cost_usd"] = sum(u.get("cost_usd", 0) for u in usage.values())
    drafts = [d for d in alerts.all_alerts() if d["triage_run_id"] == t.run_id]
    out["approvals_needed"] = sum(not d.get("status", "").startswith("shown") for d in drafts)
    out["drafts"] = [{"kind": d.get("kind", "agency_alert"), "needs_person": needs_person(d, t.route),
                      "priority": (d.get("draft") or {}).get("priority"),
                      "advice": (d.get("draft") or {}).get("advice_ids", [])} for d in drafts]
    return out


def bench_bank(sizes=(1_000, 10_000, 50_000)) -> dict:
    """Time one request-bank lookup as the bank grows (it scans every entry)."""
    res = {}
    for n in sizes:
        f = RUN_LOGS / f"bank_{n}.jsonl"
        with f.open("w") as fh:
            for i in range(n):
                fh.write(json.dumps({"key": f"k{i}", "text": f"Pothole on {i} Street near {i + 3} Avenue in Queens, "
                                     f"it has been there for weeks and cars swerve around it", "decision": {}}) + "\n")
        request_bank.BANK_FILE = f
        t0 = time.monotonic()
        request_bank.find("Pothole on 999999 Street near 3 Avenue in Queens, it has been there for weeks and cars swerve")
        res[n] = round(time.monotonic() - t0, 3)
    return res


def bench_daily_cap(sizes=(10_000, 100_000, 300_000)) -> dict:
    """Time the daily-spend check (it reads the whole usage log) as the log grows."""
    from civicequity import llm
    res = {}
    for n in sizes:
        f = RUN_LOGS / f"usage_{n}.jsonl"
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with f.open("w") as fh:
            for _ in range(n):
                fh.write(json.dumps({"time": now, "cost_usd": 0.0001}) + "\n")
        llm.USAGE_LOG = f
        t0 = time.monotonic()
        llm.spent_today()
        res[n] = round(time.monotonic() - t0, 3)
    return res


def money(x):
    return f"${x:,.2f}" if x >= 1 else f"${x:.4f}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=40)
    args = p.parse_args()

    # ---------- 1. volume ----------
    catalog = pd.read_csv(ROOT / "data" / "311_catalog_all.csv")
    year_total = int(catalog["n"].sum())
    infra_total = int(catalog[catalog["agency"].isin(["DOT", "DEP", "DPR", "DSNY"])]["n"].sum())
    days = daily_counts()
    window = days[(days["day"] >= "2025-10-01") & (days["day"] < "2026-10-01")]
    record = days.loc[days["n"].idxmax()]
    volume = {
        "all_311_requests_per_year": year_total, "all_311_requests_per_month_avg": round(year_total / 12),
        "infrastructure_agencies_per_year": infra_total,
        "daily_avg_in_window": round(window["n"].mean()), "peak_day_in_window": {"day": window.loc[window["n"].idxmax(), "day"],
                                                                                 "requests": int(window["n"].max())},
        "record_day_since_2020": {"day": record["day"], "requests": int(record["n"])},
    }
    print(json.dumps(volume, indent=1))

    # ---------- 2-3. measured pipeline ----------
    snap = data.load_snapshot()
    cands = needs.capital_candidates(snap)
    model = CostModel(snap)
    recs = sample_records(args.n)
    print(f"Running {len(recs)} real 311 records through the live pipeline (fresh calls) ...")
    rows = []
    for i, r in enumerate(recs):
        rows.append(run_record(r, snap, cands, model))
        print(f"  {i + 1:3d} {r['agency']:6s} {r['complaint_type'][:28]:28s} -> {rows[-1]['route']:12s} "
              f"${rows[-1]['cost_usd']:.5f} {rows[-1]['seconds']}s")

    per_agent = {}
    for row in rows:
        for agent, u in row["usage"].items():
            per_agent.setdefault(agent, []).append(u)
    agents = {}
    for agent, us in per_agent.items():
        agents[agent] = {
            "runs": len(us), "share_of_records": round(len(us) / len(rows), 3),
            "input_tokens_median": int(statistics.median(u.get("input_tokens", 0) for u in us)),
            "cached_input_tokens_median": int(statistics.median(u.get("cached_input_tokens", 0) for u in us)),
            "output_tokens_median": int(statistics.median(u.get("output_tokens", 0) for u in us)),
            "cost_usd_mean": round(statistics.mean(u.get("cost_usd", 0) for u in us), 7),
            "seconds_median": round(statistics.median(c["seconds"] for u in us for c in u.get("calls", []) if c.get("seconds")), 2),
        }
    costs = [r["cost_usd"] for r in rows]
    per_record = {"mean_usd": round(statistics.mean(costs), 7), "p90_usd": round(float(np.percentile(costs, 90)), 7),
                  "seconds_median": round(statistics.median(r["seconds"] for r in rows), 1),
                  "approvals_per_record": round(statistics.mean(r["approvals_needed"] for r in rows), 3),
                  "routes": pd.Series([r["route"] for r in rows]).value_counts().to_dict(),
                  "emergency_share": round(sum(bool(r["emergency"]) for r in rows) / len(rows), 3),
                  "urgent_hazard_share": round(sum(r["urgent_hazard"] for r in rows) / len(rows), 3)}

    # ---------- 4. one fresh memo ----------
    print("Running one fresh memo ...")
    memo = run_memo(snap, model)
    memo_cost = memo["total"]["cost_usd"]

    # ---------- 5. scale ----------
    month = volume["all_311_requests_per_month_avg"]
    a = ASSUMPTIONS
    processing_month = per_record["mean_usd"] * month
    users = {}
    for n in (1, 100, 10_000):
        memos = n * a["memos_per_user_per_month"] * memo_cost
        users[n] = {"processing": processing_month, "memos": memos, "total": processing_month + memos,
                    "per_user": (processing_month + memos) / n}
    approvals_month = per_record["approvals_per_record"] * month
    review_hours = approvals_month * a["seconds_to_review_one_draft"] / 3600
    all_drafts = [d for r in rows for d in r["drafts"]]
    person_share = sum(d["needs_person"] for d in all_drafts) / max(1, len(all_drafts))
    tier_share = person_share + (1 - person_share) * a["qa_sample_share_of_auto_sent"]
    tiered_hours = approvals_month * tier_share * a["seconds_to_review_one_draft"] / 3600
    peak_req = volume["record_day_since_2020"]["requests"]
    peak_hour_rate = peak_req * a["peak_hour_share_of_day"] / 3600
    scale = {
        "monthly_processing_cost_all_311": round(processing_month, 2),
        "yearly_processing_cost_all_311": round(processing_month * 12, 2),
        "daily_cost_avg_day": round(per_record["mean_usd"] * volume["daily_avg_in_window"], 2),
        "daily_cost_record_day": round(per_record["mean_usd"] * peak_req, 2),
        "daily_cost_record_day_p90_mix": round(per_record["p90_usd"] * peak_req, 2),
        "current_daily_cap": config.DAILY_BUDGET_USD,
        "safety_reserve": config.SAFETY_RESERVE_USD,
        "life_safety_screener_record_day": round(agents.get("life_safety_screener", {}).get("cost_usd_mean", 0)
                                                 * peak_req, 2),
        "recommended_daily_cap_live": float(np.ceil(per_record["p90_usd"] * peak_req * 2)),
        "memo_cost": memo_cost,
        "users": {str(k): {kk: round(vv, 4) for kk, vv in v.items()} for k, v in users.items()},
        "approvals_per_month": round(approvals_month),
        "staff_review_hours_per_month": round(review_hours),
        "staff_fte_for_review": round(review_hours / a["staff_work_hours_per_month"], 1),
        "tiered": {"drafts_measured": len(all_drafts), "share_needing_a_person": round(person_share, 3),
                   "share_reviewed_incl_qa_sample": round(tier_share, 3),
                   "reviews_per_month": round(approvals_month * tier_share), "staff_hours_per_month": round(tiered_hours),
                   "staff_fte": round(tiered_hours / a["staff_work_hours_per_month"], 1)},
        "peak_hour_requests_per_second": round(peak_hour_rate, 2),
        "parallel_workers_needed_at_peak_hour": int(np.ceil(peak_hour_rate * per_record["seconds_median"])),
        "request_bank_lookup_seconds_by_size": bench_bank(),
        "daily_cap_check_seconds_by_log_size": bench_daily_cap(),
    }
    result = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "records_measured": len(rows),
              "volume": volume, "agents": agents, "per_record": per_record, "memo": {
                  "cost_usd": memo_cost, "tokens": memo["total"], "verified": memo["verified"]},
              "scale": scale, "assumptions": ASSUMPTIONS, "records": rows}
    (OUT / "tokenomics.json").write_text(json.dumps(result, indent=1, default=str))
    write_md(result)
    print(json.dumps({k: v for k, v in result.items() if k not in ("records",)}, indent=1, default=str)[:6000])


def write_md(r):
    v, s, pr, a = r["volume"], r["scale"], r["per_record"], r["assumptions"]
    L = ["# Tokenomics and scale (measured)", "",
         f"Measured {r['run_at'][:10]} on {r['records_measured']} real 311 records sampled from September 2026 "
         "(complaint-type mix as received), each run through the live pipeline with fresh, billed model calls. "
         "Model: gpt-6-luna at $0.10 per million input tokens, $0.01 cached, $0.50 output. "
         "Reproduce: `python analysis/tokenomics.py`.", "",
         "## Volume (real data)", "",
         f"- All 311 requests, Oct 2025 – Sep 2026: {v['all_311_requests_per_year']:,} "
         f"(about {v['all_311_requests_per_month_avg']:,} a month; {v['daily_avg_in_window']:,} a day on average)",
         f"- Busiest day in that year: {v['peak_day_in_window']['day']} ({v['peak_day_in_window']['requests']:,})",
         f"- Busiest day since 2020: {v['record_day_since_2020']['day']} ({v['record_day_since_2020']['requests']:,})", "",
         "## Tokens and cost per agent (median tokens, mean billed cost)", "",
         "| Agent | Runs on share of records | Input | Cached input | Output | Cost per run | Seconds |",
         "|---|---|---|---|---|---|---|"]
    for k, g in r["agents"].items():
        L.append(f"| {k.replace('_', ' ')} | {g['share_of_records']:.0%} | {g['input_tokens_median']:,} | "
                 f"{g['cached_input_tokens_median']:,} | {g['output_tokens_median']:,} | ${g['cost_usd_mean']:.5f} | "
                 f"{g['seconds_median']} |")
    L += ["", f"**Per 311 request (all agents):** mean ${pr['mean_usd']:.5f}, 90th percentile ${pr['p90_usd']:.5f}, "
              f"median {pr['seconds_median']} s end to end. Routes: {pr['routes']}. Escalated as emergencies: "
              f"{pr['emergency_share']:.0%} (alerts written by code, no AI cost); urgent hazards: "
              f"{pr['urgent_hazard_share']:.0%}.",
          f"**One memo:** ${r['memo']['cost_usd']:.4f} ({r['memo']['tokens']['input_tokens']:,} input and "
          f"{r['memo']['tokens']['output_tokens']:,} output tokens; verified: {r['memo']['verified']}).", "",
          "## At scale", "",
          f"- Processing every 311 request: **{money(s['monthly_processing_cost_all_311'])} a month** "
          f"({money(s['yearly_processing_cost_all_311'])} a year).",
          f"- Average day: {money(s['daily_cost_avg_day'])}. Record day: {money(s['daily_cost_record_day'])} "
          f"({money(s['daily_cost_record_day_p90_mix'])} if every request cost as much as the 90th percentile). "
          f"Current daily cap: ${s['current_daily_cap']:.0f}; recommended for live use: ${s['recommended_daily_cap_live']:.0f}. "
          f"Past the cap, only the Life-Safety Screener keeps running, on a ${s['safety_reserve']:.0f} reserve; on the "
          f"record day the screener alone would cost {money(s['life_safety_screener_record_day'])}, so the reserve "
          "covers emergency detection for every request.", "",
          f"| Staff users | 311 processing | Memos ({a['memos_per_user_per_month']}/user/month, assumed) | Total per month | Per user per month |",
          "|---|---|---|---|---|"]
    for n, u in s["users"].items():
        L.append(f"| {int(n):,} | {money(u['processing'])} | {money(u['memos'])} | {money(u['total'])} | {money(u['per_user'])} |")
    L += ["", "## What limits scale", "",
          f"- **Human approval, not AI cost.** About {s['approvals_per_month']:,} drafts a month need a person. At "
          f"{a['seconds_to_review_one_draft']} seconds each (assumed) that is about {s['staff_review_hours_per_month']:,} "
          f"staff hours, or {s['staff_fte_for_review']} full-time staff, if a person approves every draft.",
          f"- **Tiered approval (the live design):** a person approves replies that give advice, capital, urgent, "
          f"and elevated alerts, every emergency alert, and anything flagged (the fixed 911 message is shown at once); "
          f"the rest is sent automatically, with a "
          f"{a['qa_sample_share_of_auto_sent']:.0%} random quality sample. In the measured drafts "
          f"{s['tiered']['share_needing_a_person']:.0%} needed a person, so about {s['tiered']['reviews_per_month']:,} "
          f"reviews a month ({s['tiered']['staff_hours_per_month']:,} hours, {s['tiered']['staff_fte']} full-time staff).",
          f"- **Throughput.** In the busiest hour of the record day ({a['peak_hour_share_of_day']:.0%} of the day, assumed), "
          f"about {s['peak_hour_requests_per_second']} requests arrive per second; at the measured median time per "
          f"request that needs about {s['parallel_workers_needed_at_peak_hour']} requests processed in parallel.",
          f"- **Request bank lookup** (scans every saved request): " +
          ", ".join(f"{int(k):,} saved: {v}s" for k, v in s["request_bank_lookup_seconds_by_size"].items()) + ".",
          f"- **Daily-cap check** (reads the whole usage log): " +
          ", ".join(f"{int(k):,} log lines: {v}s" for k, v in s["daily_cap_check_seconds_by_log_size"].items()) + ".", "",
          "Assumptions (not measured): " + "; ".join(f"{k.replace('_', ' ')} = {v}" for k, v in a.items()) + "."]
    before = OUT / "tokenomics_before.json"
    if before.exists():
        b = json.loads(before.read_text())
        bs, bp = b["scale"], b["per_record"]
        L += ["", "## Compared with the previous measurement", "",
              f"Previous run: {b['run_at'][:10]}, {b['records_measured']} records, before the Life-Safety Screener, "
              "the shared harness rules, and the longer Triage prompt (`analysis/tokenomics_before.md`).", "",
              "| Measure | Before | Now |", "|---|---|---|",
              f"| Cost per 311 request (mean) | ${bp['mean_usd']:.5f} | ${pr['mean_usd']:.5f} |",
              f"| 90th percentile | ${bp['p90_usd']:.5f} | ${pr['p90_usd']:.5f} |",
              f"| All 311, per month | {money(bs['monthly_processing_cost_all_311'])} | "
              f"{money(s['monthly_processing_cost_all_311'])} |",
              f"| Record day | {money(bs['daily_cost_record_day'])} | {money(s['daily_cost_record_day'])} |",
              f"| Recommended live daily cap | ${bs['recommended_daily_cap_live']:.0f} | ${s['recommended_daily_cap_live']:.0f} |",
              f"| One memo | ${b['memo']['cost_usd']:.4f} | ${r['memo']['cost_usd']:.4f} |",
              f"| Median seconds per request | {bp['seconds_median']} | {pr['seconds_median']} |",
              f"| Tiered review, full-time staff | {bs['tiered']['staff_fte']} | {s['tiered']['staff_fte']} |"]
    (OUT / "tokenomics.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
