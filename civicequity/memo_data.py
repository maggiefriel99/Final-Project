"""Every fact the quarterly memo may use, computed by code.

The memo agents receive these facts and choose, explain, and write; they never compute numbers.
Each number comes with the exact display text the memo must use, so the fact check (fact_check.py)
can confirm every number in the memo came from here.
"""

from functools import cache

import numpy as np
import pandas as pd
import yaml

from civicequity import capital, config, needs, rules
from civicequity.cost_model import CostModel, price_candidates

GROUP_LABELS = {
    "streets": "Streets", "sewers_and_flooding": "Sewers and flooding", "water_mains": "Water mains",
    "sidewalks_and_ramps": "Sidewalks and ramps", "street_lights_and_signals": "Street lights and signals",
    "street_trees": "Street trees", "parks": "Parks",
}
# For trees and parks, most complaints are pruning or upkeep (operating work), which a capital project
# does not fix; only the failed-asset complaints (fallen or dead trees, new tree requests, repeated
# structure problems) count toward a capital project's impact. Rebuilding a street, sewer, water main,
# or light fixes all of its complaints.
IMPACT_FROM_SIGNALS_ONLY = {"street_trees", "parks"}
MAX_PER_CATEGORY = 3


def money(x: float) -> str:
    if x >= 1e9:
        return f"${x / 1e9:,.1f}B"
    if x >= 1e6:
        return f"${x / 1e6:,.1f}M"
    return f"${x / 1e3:,.0f}K"


def dollars(x: float) -> str:
    return f"${x:,.0f}"


def place(location: str) -> str:
    return " ".join(location.split()).title()


def count(n) -> str:
    return f"{int(round(n)):,}"


@cache
def priorities() -> dict:
    return yaml.safe_load((config.ROOT / "priorities.yaml").read_text())


def _district_table(snap, cands, model, use_borough_costs):
    d = snap["districts"].set_index("boro_cd")
    need = needs.district_need(snap, cands).groupby("boro_cd")[["requests", "signal_requests", "candidates"]].sum()
    dollars, unassigned = capital.district_capital(snap, model, use_borough_costs)
    t = d.join(need).join(dollars.groupby("boro_cd")["dollars"].sum()).fillna({"dollars": 0})
    t["requests_per_1k"] = t["requests"] / t["population"] * 1000
    t["capital_per_resident"] = t["dollars"] / t["population"]

    # Raw view: complaint rate rank minus capital-per-resident rank (0-1 percentiles).
    t["raw_gap"] = t["requests_per_1k"].rank(pct=True) - t["capital_per_resident"].rank(pct=True)
    # Income-adjusted view: richer districts file more complaints per resident, so compare each
    # district's complaint rate with what districts of similar income file (log-log regression).
    x, y = np.log(t["median_household_income"]), np.log(t["requests_per_1k"])
    slope, intercept = np.polyfit(x, y, 1)
    t["expected_requests_per_1k"] = np.exp(intercept + slope * x)
    t["need_vs_similar_income"] = t["requests_per_1k"] / t["expected_requests_per_1k"]
    t["adjusted_gap"] = t["need_vs_similar_income"].rank(pct=True) - t["capital_per_resident"].rank(pct=True)
    top_raw = set(t["raw_gap"].nlargest(15).index)
    top_adj = set(t["adjusted_gap"].nlargest(15).index)
    t["underfunded_raw"] = t.index.isin(top_raw)
    t["underfunded_adjusted"] = t.index.isin(top_adj)
    t["underfunded_both"] = t["underfunded_raw"] & t["underfunded_adjusted"]
    return t, unassigned, {"slope": float(slope)}


def _equity_measures(snap, cands, model, use_borough_costs) -> dict:
    from civicequity import equity  # late import: equity builds on this module's district table
    t, notes = equity.district_table(snap, cands, model, use_borough_costs)
    return equity.memo_facts(t, notes)


def build_facts(snap: dict, model: CostModel, use_borough_costs: bool = True) -> dict:
    meta = snap.get("metadata", {})
    cands = needs.capital_candidates(snap)
    labels = snap["districts"].set_index("boro_cd")["district_label"].to_dict()
    names = snap["districts"].set_index("boro_cd")["district_name"].to_dict()
    table, unassigned, income_fit = _district_table(snap, cands, model, use_borough_costs)

    # ---------- citywide: what New Yorkers reported ----------
    req = needs.label_requests(snap["requests"])
    infra = req[(req["class"] != rules.NOT_INFRASTRUCTURE) & req["group"].notna()]
    by_group = infra.groupby("group").agg(requests=("n", "sum"))
    by_group["failed_asset"] = infra[infra["class"] == rules.CAPITAL_SIGNAL].groupby("group")["n"].sum()
    by_group = by_group.fillna(0).sort_values("requests", ascending=False)
    quarterly = infra.groupby(["quarter", "group"])["n"].sum().unstack(fill_value=0)
    top_types = infra.groupby("complaint_type")["n"].sum().nlargest(5)
    totals = quarterly.sum(axis=1).sort_index()
    peaks = [{"category": GROUP_LABELS[g], "peak_quarter": quarterly[g].idxmax(),
              "peak_requests": count(quarterly[g].max())} for g in quarterly.columns]
    citywide = {
        "infrastructure_requests": count(infra["n"].sum()),
        "failed_asset_requests": count(infra.loc[infra["class"] == rules.CAPITAL_SIGNAL, "n"].sum()),
        "capital_candidates": count(len(cands)),
        "by_category": [{"category": GROUP_LABELS[g], "requests": count(r.requests),
                         "failed_asset_requests": count(r.failed_asset)} for g, r in by_group.iterrows()],
        "top_complaint_types": [{"type": k, "requests": count(v)} for k, v in top_types.items()],
        "by_quarter": {q: count(v) for q, v in totals.items()},
        "trend": {
            "total_rose_every_quarter": bool((totals.diff().dropna() > 0).all()),
            "first_quarter_total": count(totals.iloc[0]), "last_quarter_total": count(totals.iloc[-1]),
            "change_first_to_last_quarter": f"{(totals.iloc[-1] / totals.iloc[0] - 1) * 100:+.0f}%",
            "peak_quarter_by_category": peaks,
            "note": "Categories peak in different seasons; do not describe a category's trend beyond its peak quarter.",
        },
    }

    # ---------- best value: complaints addressed per $1M ----------
    priced = price_candidates(model, cands, snap["districts"])
    priced["impact"] = np.where(priced["group"].isin(IMPACT_FROM_SIGNALS_ONLY),
                                priced["signal_complaints"], priced["complaints"])
    capital_cands = priced[~priced["funded_by_existing_program"] & (priced["impact"] > 0)].copy()
    capital_cands["per_million"] = capital_cands["impact"] / (capital_cands["typical_cost"] / 1e6)
    capital_cands = capital_cands.sort_values("per_million", ascending=False)
    picked = (capital_cands.groupby("group", group_keys=False).head(MAX_PER_CATEGORY)
              .sort_values("per_million", ascending=False).head(10))

    def card(r, prefix, i):
        t_ = table.loc[r.boro_cd]
        return {
            "id": f"{prefix}{i}", "where": f"{place(r.location)}, {labels[r.boro_cd]} ({names[r.boro_cd]})",
            "category": GROUP_LABELS[r.group], "complaint_type": r.complaint_type,
            "complaints": count(r.complaints), "failed_asset_complaints": count(r.signal_complaints),
            "complaints_counted": count(r.impact), "why_counted": "failed-asset complaints only"
            if r.group in IMPACT_FROM_SIGNALS_ONLY else "all complaints on this street",
            "fix": r.scope_label,
            "fix_description": model.estimate(r.scope, labels[r.boro_cd].rsplit(" CD", 1)[0])["scope_description"],
            "typical_cost": None if pd.isna(r.typical_cost) else money(r.typical_cost),
            "cost_range": None if pd.isna(r.typical_cost) else f"{money(r.cost_low)} to {money(r.cost_high)}",
            "complaints_per_million": None if pd.isna(r.typical_cost) else f"{r.impact / (r.typical_cost / 1e6):.0f}",
            "district_income": money(t_["median_household_income"]),
            "district_underfunded": "both views" if t_["underfunded_both"] else (
                "raw view only" if t_["underfunded_raw"] else "adjusted view only" if t_["underfunded_adjusted"] else "no"),
        }

    best_value = [card(r, "V", i + 1) for i, r in enumerate(picked.itertuples())]
    best_per_category = [card(r, "B", i + 1) for i, r in
                         enumerate(capital_cands.groupby("group").head(1).itertuples())]
    quick = priced[priced["funded_by_existing_program"]].sort_values("complaints", ascending=False).head(6)
    quick_wins = [card(r, "Q", i + 1) for i, r in enumerate(quick.itertuples())]

    # ---------- equity: both views ----------
    def drow(cd):
        r = table.loc[cd]
        return {"district": f"{labels[cd]} ({names[cd]})", "median_income": money(r["median_household_income"]),
                "complaints_per_1k_residents": f"{r['requests_per_1k']:.0f} per 1,000 residents",
                "complaints_vs_similar_income_districts": f"{r['need_vs_similar_income']:.1f}x",
                "capital_per_resident": dollars(r["capital_per_resident"]),
                "underfunded": "both views" if r["underfunded_both"] else "raw view only"
                if r["underfunded_raw"] else "adjusted view only" if r["underfunded_adjusted"] else "no"}

    q = pd.qcut(table["median_household_income"], 4, labels=["lowest", "second", "third", "highest"])
    quartiles = table.groupby(q, observed=True).agg(income=("median_household_income", "median"),
                                                   rate=("requests_per_1k", "median"),
                                                   cap=("capital_per_resident", "median"))
    low, high = quartiles.loc["lowest"], quartiles.loc["highest"]
    equity = {
        "views": {
            "raw_view": "Districts with many 311 infrastructure complaints per resident but few capital dollars per resident.",
            "income_adjusted_view": "The same comparison, but each district's complaint rate is compared with districts of "
                                    "similar median household income, because higher-income districts file more complaints.",
            "underfunded_on_both_views": "Among the 15 largest gaps on the raw view and among the 15 largest on the "
                                         "income-adjusted view.",
        },
        "income_reporting_pattern": {
            "lowest_income_quarter": {"median_income": money(low["income"]), "complaints_per_1k": f"{low['rate']:.0f} per 1,000 residents",
                                      "capital_per_resident": dollars(low["cap"])},
            "highest_income_quarter": {"median_income": money(high["income"]), "complaints_per_1k": f"{high['rate']:.0f} per 1,000 residents",
                                       "capital_per_resident": dollars(high["cap"])},
            "ratio_high_to_low_complaints": f"{high['rate'] / low['rate']:.1f}x",
            "meaning": "In NYC's 59 community districts, higher-income districts file more 311 infrastructure complaints "
                       "per resident, while capital dollars per resident are about the same at every income level.",
            "related_research": "A study of Kansas City 311 data (Kontokosta and Hong, Sustainable Cities and Society, 2021) "
                                "found low-income and minority neighborhoods were less likely to report street-condition "
                                "problems despite greater need. NYC's pattern is consistent with that, but this analysis "
                                "does not measure actual street conditions.",
        },
        "underfunded_both_views": [drow(cd) for cd in
                                   table[table["underfunded_both"]].sort_values("adjusted_gap", ascending=False).index],
        "underfunded_adjusted_only": [drow(cd) for cd in table[table["underfunded_adjusted"] & ~table["underfunded_raw"]]
                                      .sort_values("adjusted_gap", ascending=False).index[:6]],
        "underfunded_raw_only": [drow(cd) for cd in table[table["underfunded_raw"] & ~table["underfunded_adjusted"]]
                                 .sort_values("raw_gap", ascending=False).index[:6]],
        "capital_not_assigned_to_districts": money(unassigned["dollars"].sum()) if len(unassigned) else "$0K",
        "more_measures": _equity_measures(snap, cands, model, use_borough_costs),
        "borough_cost_adjustment": "on" if use_borough_costs else "off",
    }

    # ---------- City priorities ----------
    pr = priorities()
    alignment = []
    for key, p in pr["priorities"].items():
        groups = p.get("capital_groups", [])
        if groups:
            sub = priced[priced["group"].isin(groups)]
            if key == "street_safety":
                hazard = priced[(priced["group"] == "streets") & priced["signal_descriptors"].fillna("")
                                .str.contains("Cave-in|Failed Street Repair")]
                sub = pd.concat([sub, hazard])
        else:  # every_neighborhood
            sub = priced[priced["boro_cd"].isin(table.index[table["underfunded_both"]])]
        top = sub.sort_values(["signal_complaints", "complaints"], ascending=False).head(3)
        alignment.append({
            "priority": p["name"],
            "evidence": [{"text": e.get("quote") or e.get("fact"), "is_quote": "quote" in e,
                          "source": pr["sources"][e["source"]]["title"]} for e in p["evidence"]],
            "candidates": count(len(sub)), "complaints_on_those_streets": count(sub["complaints"].sum()),
            "top_places": [f"{place(r.location)}, {labels[r.boro_cd]} ({names[r.boro_cd]}): {count(r.complaints)} "
                           f"complaints, fix: {r.scope_label}" for r in top.itertuples()],
        })

    return {
        "period": {"from": meta.get("window_start", "2025-10-01"), "to_exclusive": meta.get("window_end_exclusive", "2026-10-01"),
                   "label": "October 2025 to September 2026"},
        "citywide": citywide,
        "best_value_capital": best_value,
        "best_in_each_category": best_per_category,
        "quick_wins_already_funded": quick_wins,
        "equity": equity,
        "priorities": alignment,
        "method_notes": {
            "cost_estimates": "Typical budget of current City capital projects of the same kind and borough; the range is "
                              "the middle half of similar budgets (about 5 times wide). Planning estimates, not budgets.",
            "complaints_per_million": "Complaints the fix would address per $1 million of typical cost. For trees and parks "
                                      "only failed-asset complaints count; pruning and upkeep are operating work.",
            "income_adjustment": f"Complaint rates rise with income (elasticity {income_fit['slope']:.2f}); the adjusted view "
                                 "compares each district with districts of similar income.",
        },
        "_tables": {"districts": table, "priced": priced, "quarterly": quarterly,
                    "income_fit": income_fit},  # for charts; never sent to an agent
    }
