"""Equity measures for the Equity dashboard. Every number is computed here by code; no AI is involved.

One row per community district (59), then the same measures for groups of districts ("Compare by"):
income quarter, limited-English quarter, or the Health Department's Heat Vulnerability Index (1-5).

Measures
  - capital per resident, and the existing raw / income-adjusted gaps (memo_data);
  - concentration index: does capital per resident lean toward districts ranked low or high on a
    variable (-1 to +1, population-weighted; 0 = no lean), with a bootstrap 95% interval;
  - Gini: how unevenly capital per resident is spread across residents (0 = equal);
  - share of capital in high heat-vulnerability districts (HVI 4-5, the rule NYC OMB's climate budgeting
    uses) vs. their share of residents;
  - capital per resident by asset type and group;
  - repeat-problem streets (capital candidates) per 100,000 residents vs. capital per resident;
  - follow-through on site-specific projects: share of budget already spent, share of budget in
    construction or later.
"""

import numpy as np
import pandas as pd

from civicequity import capital, memo_data, rules

ASSET_LABELS = {
    "streets": "Streets", "sewers_and_flooding": "Sewers & flooding", "water_mains": "Water mains",
    "parks": "Parks", "street_trees": "Street trees", "sidewalks_and_ramps": "Sidewalks & ramps",
    "street_lights_and_signals": "Street lights & signals",
}
# Capital plan phases: construction or later vs. still being planned or designed. Anything else
# (cancelled, on hold, pass-through funds, equipment...) is left out of the follow-through measures.
BUILDING = {"Construction", "(Construction)", "Close-out", "(Closeout)", "(Completed)"}
PLANNING = {"Pre-Design", "(Pre-Design)", "Design", "(Design)", "Construction Procurement", "Construction procurement",
            "(Initiation)", "(Pending)"}

COMPARE = {
    "Income": ("median_household_income", ["Lowest-income quarter", "Second quarter", "Third quarter",
                                           "Highest-income quarter"]),
    "Limited English": ("pct_limited_english", ["Least limited-English quarter", "Second quarter", "Third quarter",
                                                "Most limited-English quarter"]),
    "Heat vulnerability": ("heat_vulnerability_index", ["HVI 1 (lowest risk)", "HVI 2", "HVI 3", "HVI 4",
                                                        "HVI 5 (highest risk)"]),
}


def district_table(snap: dict, cands: pd.DataFrame, model, use_borough_costs: bool = True) -> tuple[pd.DataFrame, dict]:
    """Every measure per district, plus notes on what the capital dollars include."""
    t, unassigned, income_fit = memo_data._district_table(snap, cands, model, use_borough_costs)
    dollars, _ = capital.district_capital(snap, model, use_borough_costs)
    by_asset = dollars.pivot_table(index="boro_cd", columns="group", values="dollars", aggfunc="sum").fillna(0)
    t = t.join(by_asset.add_prefix("dollars_"))
    lump = dollars[dollars["method"] != "site-specific project"].groupby("boro_cd")["dollars"].sum()
    t["lump_sum_dollars"] = lump.reindex(t.index).fillna(0)
    t["candidates_per_100k"] = t["candidates"] / t["population"] * 100_000

    # Follow-through: site-specific projects placed in each district (share of each project's budget).
    groups = rules.category_groups()
    p = snap["projects"].assign(group=snap["projects"]["ten_year_plan_category"].map(groups))
    p = p[p["group"].notna()]
    a = snap["allocations"].merge(p[["fms_id", "total_budget", "spend_to_date", "current_phase"]], on="fms_id")
    a["budget"] = a["share"] * a["total_budget"]
    a["spent"] = a["share"] * a["spend_to_date"].fillna(0)
    a["stage"] = np.where(a["current_phase"].isin(BUILDING), "building",
                          np.where(a["current_phase"].isin(PLANNING), "planning", "other"))
    staged = a[a["stage"] != "other"]
    f = pd.DataFrame({
        "site_budget": a.groupby("boro_cd")["budget"].sum(),
        "site_spent": a.groupby("boro_cd")["spent"].sum(),
        "staged_budget": staged.groupby("boro_cd")["budget"].sum(),
        "building_budget": staged[staged["stage"] == "building"].groupby("boro_cd")["budget"].sum(),
        "site_projects": a.groupby("boro_cd")["fms_id"].nunique(),
    })
    t = t.join(f).fillna({c: 0 for c in f.columns})
    t["share_spent"] = t["site_spent"] / t["site_budget"].replace(0, np.nan)
    t["share_building"] = t["building_budget"] / t["staged_budget"].replace(0, np.nan)
    notes = {
        "districts": len(t),
        "capital_total": float(t["dollars"].sum()),
        "lump_sum_share": float(t["lump_sum_dollars"].sum() / t["dollars"].sum()),
        "unassigned_total": float(unassigned["dollars"].sum()) if len(unassigned) else 0.0,
        "site_projects": int(a["fms_id"].nunique()),
        "phase_left_out_share": float(a.loc[a["stage"] == "other", "budget"].sum() / a["budget"].sum()),
        "income_elasticity": income_fit["slope"],
    }
    return t, notes


def add_groups(t: pd.DataFrame, compare: str) -> pd.Series:
    col, labels = COMPARE[compare]
    if compare == "Heat vulnerability":
        return t[col].map(lambda v: labels[int(v) - 1]).astype(pd.CategoricalDtype(labels, ordered=True))
    return pd.qcut(t[col], 4, labels=labels)


def group_table(t: pd.DataFrame, compare: str) -> pd.DataFrame:
    """Measures for each group: sums first, then ratios (so big and small districts count by their residents)."""
    g = t.assign(group=add_groups(t, compare)).groupby("group", observed=True)
    asset_cols = [c for c in t.columns if c.startswith("dollars_")]
    s = g[["population", "dollars", "requests", "candidates", "site_budget", "site_spent", "staged_budget",
           "building_budget", *asset_cols]].sum()
    out = pd.DataFrame({
        "districts": g.size(),
        "residents": s["population"],
        "capital_per_resident": s["dollars"] / s["population"],
        "complaints_per_1k": s["requests"] / s["population"] * 1000,
        "candidates_per_100k": s["candidates"] / s["population"] * 100_000,
        "share_spent": s["site_spent"] / s["site_budget"],
        "share_building": s["building_budget"] / s["staged_budget"],
    })
    for c in asset_cols:
        out[c.replace("dollars_", "per_resident_")] = s[c] / s["population"]
    return out


def _weighted_ranks(order_values: np.ndarray, weights: np.ndarray, tiebreak: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    idx = np.lexsort((tiebreak, order_values))
    w = weights[idx] / weights.sum()
    return idx, np.cumsum(w) - w / 2


def concentration_index(y: np.ndarray, rank_by: np.ndarray, pop: np.ndarray, tiebreak: np.ndarray) -> float:
    """Population-weighted concentration index of y (per resident) ranked by rank_by, low to high.
    -1 to +1: negative = more per resident where rank_by is low; 0 = no lean."""
    idx, r = _weighted_ranks(rank_by, pop, tiebreak)
    w = pop[idx] / pop.sum()
    yy = y[idx]
    mu = (w * yy).sum()
    return float(2 * (w * (yy - mu) * (r - 0.5)).sum() / mu)


def gini(y: np.ndarray, pop: np.ndarray) -> float:
    """Population-weighted Gini of y per resident (0 = everyone gets the same, 1 = one district gets it all)."""
    return concentration_index(y, y, pop, np.zeros(len(y)))


def curves(t: pd.DataFrame, compare: str) -> dict:
    """Lorenz curve (districts sorted by capital per resident) and concentration curve (sorted by the
    compare-by variable, low to high): cumulative share of residents vs. cumulative share of capital."""
    col = COMPARE[compare][0]
    pop, dol = t["population"].to_numpy(float), t["dollars"].to_numpy(float)

    def cum(order):
        o = np.asarray(order)
        return (np.r_[0, np.cumsum(pop[o]) / pop.sum()], np.r_[0, np.cumsum(dol[o]) / dol.sum()],
                [None] + list(t.index[o]))
    lorenz = cum(np.argsort(dol / pop, kind="stable"))
    conc = cum(np.lexsort((t["median_household_income"].to_numpy(), t[col].to_numpy())))
    return {"lorenz": lorenz, "concentration": conc}


def headline(t: pd.DataFrame, compare: str, n_boot: int = 2000, seed: int = 7) -> dict:
    col = COMPARE[compare][0]
    y = (t["dollars"] / t["population"]).to_numpy(float)
    pop = t["population"].to_numpy(float)
    rank_by = t[col].to_numpy(float)
    tie = t["median_household_income"].to_numpy(float)
    ci = concentration_index(y, rank_by, pop, tie)
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(n_boot):  # resample districts to see whether the lean could be chance
        i = rng.integers(0, len(y), len(y))
        boots.append(concentration_index(y[i], rank_by[i], pop[i], tie[i]))
    lo, hi = np.percentile(boots, [2.5, 97.5])
    high_heat = t["heat_vulnerability_index"] >= 4
    return {
        "concentration_index": ci, "ci_low": float(lo), "ci_high": float(hi),
        "lean_is_clear": bool(lo > 0 or hi < 0),
        "complaints_concentration_index": concentration_index(
            (t["requests"] / t["population"]).to_numpy(float), rank_by, pop, tie),
        "gini": gini(y, pop),
        "heat_capital_share": float(t.loc[high_heat, "dollars"].sum() / t["dollars"].sum()),
        "heat_resident_share": float(t.loc[high_heat, "population"].sum() / t["population"].sum()),
        "heat_districts": int(high_heat.sum()),
    }


def memo_facts(t: pd.DataFrame, notes: dict) -> dict:
    """The dashboard's main measures as finished strings for the memo (by income), so the memo's agents copy
    numbers rather than compute them. Differences that are not statistically clear are labeled as such."""
    h = headline(t, "Income")
    g = group_table(t, "Income")
    lo, hi = g.index[0], g.index[-1]
    pct = lambda v: f"{v:.0%}"
    usd = lambda v: f"${v:,.0f} per resident"
    spent, building = trend(t, "Income", "share_spent"), trend(t, "Income", "share_building")
    clear = spent["p"] < 0.05 or building["p"] < 0.05
    return {
        "lean_of_capital_by_income": {
            "concentration_index": f"{h['concentration_index']:+.2f}",
            "range_95": f"{h['ci_low']:+.2f} to {h['ci_high']:+.2f}",
            "meaning": ("No clear lean of capital per resident toward lower- or higher-income districts."
                        if not h["lean_is_clear"] else
                        f"Capital per resident leans toward {'lower' if h['concentration_index'] < 0 else 'higher'}"
                        "-income districts."),
            "complaints_concentration_index": f"{h['complaints_concentration_index']:+.2f}",
            "complaints_meaning": "311 complaints per resident lean toward higher-income districts."
                                  if h["complaints_concentration_index"] > 0 else
                                  "311 complaints per resident lean toward lower-income districts.",
        },
        "gini_of_capital_per_resident": f"{h['gini']:.2f}",
        "high_heat_risk_districts": {
            "districts": str(h["heat_districts"]), "share_of_capital": pct(h["heat_capital_share"]),
            "share_of_residents": pct(h["heat_resident_share"]),
            "definition": "Heat Vulnerability Index 4 or 5 (NYC Health); NYC OMB's climate budgeting treats these "
                          "community districts as high heat risk.",
        },
        "capital_by_asset_type": [
            {"asset": label, "lowest_income_quarter": usd(g.loc[lo, f"per_resident_{k}"]),
             "highest_income_quarter": usd(g.loc[hi, f"per_resident_{k}"])}
            for k, label in ASSET_LABELS.items() if f"per_resident_{k}" in g.columns and k != "street_lights_and_signals"],
        "follow_through": {
            "lowest_income_quarter": {"budget_spent": pct(g.loc[lo, "share_spent"]),
                                      "budget_in_construction_or_finished": pct(g.loc[lo, "share_building"])},
            "highest_income_quarter": {"budget_spent": pct(g.loc[hi, "share_spent"]),
                                       "budget_in_construction_or_finished": pct(g.loc[hi, "share_building"])},
            "statistically_clear": clear,
            "note": ("The difference is statistically clear across districts." if clear else
                     "Across the 59 districts this difference is not statistically clear; do not present it as a "
                     "finding."),
        },
        "caveat": f"{pct(notes['lump_sum_share'])} of the capital placed in districts is citywide program money spread "
                  "by where each program worked, not money assigned to a district.",
    }


def trend(t: pd.DataFrame, compare: str, metric: str) -> dict:
    """District-level Spearman rank correlation between a measure and the compare-by variable, so a
    difference between groups is not read as real when it could be chance (59 districts)."""
    from scipy.stats import spearmanr
    d = t[[COMPARE[compare][0], metric]].dropna()
    rho, p = spearmanr(d.iloc[:, 0], d.iloc[:, 1])
    return {"rho": float(rho), "p": float(p), "n": len(d)}
