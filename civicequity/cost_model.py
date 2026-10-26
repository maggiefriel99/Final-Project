"""Simple, explainable cost model: log(budget) ~ scope + borough.

Trained on site-specific projects in the City's current capital plan (Capital Projects Dashboard,
latest reporting period) in the categories that address 311 complaints: about 1,150 projects,
budgeted in today's dollars, each with a scope category (street reconstruction, sewer replacement,
lampposts, ...) and a borough.

  - Scope terms: what kind of fix. This explains most of what the model can explain.
  - Borough terms (the location dummies): pooled across scopes, since single scopes are too small
    to estimate their own. They are statistically real in this data, but they likely reflect the
    size of typical projects in each borough as well as construction prices; the data has no size
    measure to separate the two.

Estimates are for a typical project of that scope and come with a 25th-75th percentile range,
because budgets for the same kind of work vary far more than the averages differ.

The older completed-projects file (Street and Highway Capital Reconstruction) is not used here: many
of its costs are partial (sewer work listed at $1 to $200K inside larger street jobs) and most date
from 2006-2017.
"""

import os
import re

import numpy as np
import pandas as pd

from civicequity import config, rules

# If the R model comparison (analysis/compare_cost_models.R) finds a model that beats this one under the
# agreed rule, it writes that model's estimate for every scope and borough here, and estimate() uses it.
# Without the file, the app uses the regression below. The app never runs R.
WINNER_ESTIMATES = config.ROOT / "analysis" / "cost_model_estimates.csv"

BOROUGHS = ["Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island"]

# Plain-language names for the capital plan categories the agent and the screens show.
SCOPES = {
    "PRIMARY STREET RECONSTRUCTION": ("Full street reconstruction",
                                      "Rebuild the roadbed, curbs, and usually the sewer and water lines beneath."),
    "PRIMARY STREET RESURFACING": ("Street resurfacing", "Mill and repave the top layer of the road."),
    "LOCAL STREET RECONSTRUCTION": ("Local street reconstruction", "Rebuild a smaller residential street."),
    "RECONSTRUCTION OF STEP STREETS": ("Step street reconstruction", "Rebuild a public stairway street."),
    "REPLACEMENT OR AUGMENTATION OF EXISTING SYSTEMS": ("Sewer construction or upgrade",
                                                        "Build, replace, or enlarge the sewers and catch basins under a street."),
    "GREEN INFRASTRUCTURE PROGRAM": ("Green and natural drainage",
                                     "Cloudburst areas, bluebelts, rain gardens, and bioswales that hold and soak up stormwater."),
    "TRUNK AND DISTRIBUTION MAIN REPLACEMENT": ("Water main replacement", "Replace aging water mains under a street."),
    "TRUNK AND DISTRIBUTION MAIN EXTENSION": ("Water main extension", "Extend water mains to new areas."),
    "PEDESTRIAN RAMP CONSTRUCTION": ("Pedestrian ramp upgrades", "Rebuild corner ramps to accessibility standards."),
    "SIDEWALK RECONSTRUCTION": ("Sidewalk reconstruction", "Rebuild City-owned sidewalks along a corridor."),
    "INSTALLATION OF LAMPPOSTS AND LUMINAIRES": ("Street light replacement", "Replace lampposts and fixtures."),
    "SIGNAL INSTALLATION AND COMPUTERIZATION": ("Traffic signal upgrade", "Install or modernize traffic signals."),
    "Land Acquisition, Tree Planting and Green Infrastructure": ("Street tree planting",
                                                                  "Plant and replace street trees in a defined area."),
    "Neighborhood Parks, Playgrounds and Ballfields": ("Park reconstruction",
                                                       "Rebuild playgrounds, ballfields, or park structures."),
}
MIN_COMPARABLES = 4  # scopes with fewer site-specific projects are not offered
MIN_CAPITAL_COST = 50_000  # NYC Comptroller Directive 10 minimum; smaller lines are budget fragments
RANGE_POOLING = 20  # a scope's own spread counts as much as the citywide spread once it has 20 projects

# The capital plan's categories are often wrong for the project (15 of the 18 "pedestrian ramp"
# projects are sewer jobs; a $92M rec-center roof is filed under street lights). A project is moved
# only when its name contradicts its category: if any keyword for its own category matches (or none
# match at all), it stays. Parks are recognized by the Parks Department managing the project, not the
# word "park", which is usually a place name (Park Avenue, Central Park North).
RECLASSIFY = [
    ("REPLACEMENT OR AUGMENTATION OF EXISTING SYSTEMS",
     r"sewer|\bswr\b|\bse\b|\bhlss\b|high level storm|catch basin|sanitary|\bsan\.?\b|\bstrm\b|storm ?water flood"),
    ("GREEN INFRASTRUCTURE PROGRAM", r"cloudburst|bluebelt|green infrastructure|\bgi\b|bioswale|rain garden"),
    ("TRUNK AND DISTRIBUTION MAIN REPLACEMENT", r"water main|\bwm\b|trunk main|distribution main"),
    ("INSTALLATION OF LAMPPOSTS AND LUMINAIRES", r"\blamps?\b|\blight(?:ing|s)?\b|light ?poles?|luminaire"),
    ("PEDESTRIAN RAMP CONSTRUCTION", r"pedestrian ramps?|ped\.? ramps?"),
    ("SIDEWALK RECONSTRUCTION", r"sidewalk"),
    ("Land Acquisition, Tree Planting and Green Infrastructure", r"\btrees?\b|forestry|planting"),
    ("PRIMARY STREET RECONSTRUCTION", r"reconstruct|street|avenue|\bave?\b|blvd|boulevard|road|safety|plaza|streetscape"),
    ("Neighborhood Parks, Playgrounds and Ballfields", r"playground|ballfield|\bpool\b|rec\.? ?c(?:en)?te?r|recreation"),
]
MERGE = {"EXTENSIONS TO ACCOMMODATE NEW DEVELOPMENT": "REPLACEMENT OR AUGMENTATION OF EXISTING SYSTEMS",
         "BLUEBELT PROGRAM": "GREEN INFRASTRUCTURE PROGRAM"}


def clean_scope(category: str, name: str, agency: str) -> str:
    """The scope a project actually belongs to, judged by its name when its category doesn't fit."""
    n = (name or "").lower()
    matches = [scope for scope, pattern in RECLASSIFY if re.search(pattern, n)]
    if agency == "DPR":
        matches.append("Neighborhood Parks, Playgrounds and Ballfields")
    own = MERGE.get(category, category)
    if not matches or own in matches:
        return own
    return matches[0]


def clean_training_data(projects: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Site-specific crosswalk projects with corrected scopes, plus a log of every change.

    Takes either the latest snapshot (capital_projects.csv) or the all-snapshot history
    (capital_projects_history.csv, budgets already converted to latest-snapshot dollars with the BLS index)."""
    p = projects.assign(group=projects["ten_year_plan_category"].map(rules.category_groups()))
    if "budget_latest_dollars" in p:  # history: site-specific = tagged to a district or mapped in CPDB
        site_specific = p["has_district_tag"] | p["has_map_location"]
        p = p.assign(total_budget=p["budget_latest_dollars"])
    else:
        site_specific = ~p["district_placement"].str.startswith("not")
    p = p[p["group"].notna() & site_specific & (p["total_budget"] > 0) & p["borough"].isin(BOROUGHS)].copy()
    log = {"site_specific_projects": len(p)}
    small = p["total_budget"] < MIN_CAPITAL_COST
    log["dropped_below_capital_minimum"] = int(small.sum())
    p = p[~small].copy()
    p["scope"] = [clean_scope(c, nm, ag) for c, nm, ag in
                  zip(p["ten_year_plan_category"], p["fms_project_name"], p["managing_agency"])]
    p["group"] = p["scope"].map(rules.category_groups())
    moved = p[p["scope"] != p["ten_year_plan_category"].map(lambda c: MERGE.get(c, c))]
    log["reclassified"] = len(moved)
    log["reclassified_by_move"] = (moved.groupby(["ten_year_plan_category", "scope"]).size()
                                   .rename("projects").reset_index().sort_values("projects", ascending=False)
                                   .assign(move=lambda d: d["ten_year_plan_category"].map(lambda c: SCOPES.get(c, (c,))[0])
                                           + " -> " + d["scope"].map(lambda s: SCOPES[s][0]))
                                   .set_index("move")["projects"].to_dict())
    log["examples"] = moved.nlargest(5, "total_budget").apply(
        lambda r: f"{r.fms_project_name} (${r.total_budget / 1e6:.1f}M): {SCOPES.get(r.ten_year_plan_category, (r.ten_year_plan_category,))[0]}"
                  f" -> {SCOPES[r.scope][0]}", axis=1).tolist()
    counts = p["scope"].value_counts()
    log["dropped_scopes_too_few_projects"] = {SCOPES.get(s, (s,))[0]: int(c) for s, c in counts.items() if c < MIN_COMPARABLES}
    p = p[p["scope"].map(counts) >= MIN_COMPARABLES]
    log["training_projects"] = len(p)
    return p, log

# Resurfacing is paid from citywide lump sums, so the capital plan has almost no site-specific
# resurfacing projects to price (one, and it is a highway ramp). For a street whose road base is
# still sound, the realistic fix is to get it onto DOT's resurfacing schedule: no new project.
RESURFACING_PROGRAM = "RESURFACING PROGRAM"
SIDEWALK_RAMP_PROGRAM = "SIDEWALK AND RAMP PROGRAMS"
PROGRAM_SCOPES = {
    SIDEWALK_RAMP_PROGRAM: {
        "group": "sidewalks_and_ramps", "label": "Add to the City's sidewalk and ramp programs",
        "description": "Repair through existing citywide programs: DOT's pedestrian ramp program, DOT's sidewalk "
                       "program for City-owned frontage, and Parks' Trees & Sidewalks program for tree damage. "
                       "Already funded through lump sums, so no new capital project is needed.",
    },
    RESURFACING_PROGRAM: {
        "group": "streets", "label": "Add to DOT's resurfacing schedule",
        "description": "Repave through the City's existing citywide resurfacing program. Already funded, so no "
                       "new capital project or budget is needed; DOT decides the schedule.",
    },
}


class CostModel:
    def __init__(self, snap: dict, use_history: bool | None = None):
        # Default: the latest snapshot only. Adding older snapshots (BLS-adjusted) made predictions of current
        # projects worse in a held-out test (analysis/cost_history_check.md). CIVICEQUITY_COST_HISTORY=1 uses it.
        if use_history is None:
            use_history = os.getenv("CIVICEQUITY_COST_HISTORY", "0") == "1"
        history = snap.get("projects_history")
        self.source = "all capital-plan snapshots (BLS-adjusted)" if use_history and history is not None \
            else "latest capital-plan snapshot"
        self.train, self.cleaning_log = clean_training_data(history if use_history and history is not None
                                                            else snap["projects"])

        X = pd.get_dummies(self.train[["scope", "borough"]], drop_first=True).astype(float)
        X["const"] = 1.0
        y = np.log(self.train["total_budget"].to_numpy())
        A = X.to_numpy()
        self.columns = list(X.columns)
        self.coef, *_ = np.linalg.lstsq(A, y, rcond=None)
        self.residuals = y - A @ self.coef
        self.r2 = float(1 - self.residuals @ self.residuals / ((y - y.mean()) @ (y - y.mean())))

        # Joint F test: do the borough terms add anything beyond scope?
        b_cols = [i for i, c in enumerate(self.columns) if c.startswith("borough_")]
        A0 = np.delete(A, b_cols, axis=1)
        c0, *_ = np.linalg.lstsq(A0, y, rcond=None)
        r0 = y - A0 @ c0
        dof = len(y) - A.shape[1]
        rss = self.residuals @ self.residuals
        self.borough_f = float(((r0 @ r0 - rss) / len(b_cols)) / (rss / dof))
        self.range_quantiles = scope_ranges(self.train["scope"].to_numpy(), self.residuals)
        self.winner = None
        if WINNER_ESTIMATES.exists():
            w = pd.read_csv(WINNER_ESTIMATES)
            label_to_scope = {v[0]: k for k, v in SCOPES.items()}
            w["scope_key"] = w["scope"].map(label_to_scope)
            self.winner = w.set_index(["scope_key", "borough"])
        self.model_name = (f"{self.winner['model'].iloc[0]} (R model comparison winner)" if self.winner is not None
                           else "scope + borough regression")

    # ---------- what the agent and screens use ----------

    def scopes(self, group: str) -> list[dict]:
        """Fix options for a capital group, with how many current projects back each one."""
        t = self.train[self.train["group"] == group]
        out = [{"scope": k, "label": v["label"], "description": v["description"], "comparable_projects": None,
                "median_budget": None, "funded_by_existing_program": True}
               for k, v in PROGRAM_SCOPES.items() if v["group"] == group]
        for scope, g in t.groupby("scope"):
            label, desc = SCOPES.get(scope, (scope.title(), ""))
            out.append({"scope": scope, "label": label, "description": desc, "comparable_projects": len(g),
                        "median_budget": float(g["total_budget"].median()), "funded_by_existing_program": False})
        return out

    def estimate(self, scope: str, borough: str) -> dict:
        """Typical budget for one project of this scope in this borough, with a 25th-75th percentile range.
        Program scopes (already funded) come back with no dollar figures."""
        if scope in PROGRAM_SCOPES:
            p = PROGRAM_SCOPES[scope]
            return {"scope": scope, "scope_label": p["label"], "scope_description": p["description"], "borough": borough,
                    "typical": None, "low": None, "high": None, "comparable_projects": 0, "comparable_in_borough": 0,
                    "borough_multiplier": None, "funded_by_existing_program": True}
        if scope not in set(self.train["scope"]):
            raise ValueError(f"No cost data for scope {scope!r}.")
        x = dict.fromkeys(self.columns, 0.0)
        x["const"] = 1.0
        x[f"scope_{scope}"] = 1.0
        x[f"borough_{borough}"] = 1.0
        mid = float(np.dot([x[c] for c in self.columns], self.coef))
        lo, hi = self.range_quantiles[scope][(25, 75)]
        center = self.range_quantiles[scope]["median"]
        label, desc = SCOPES.get(scope, (scope.title(), ""))
        typical, low, high = float(np.exp(mid + center)), float(np.exp(mid + lo)), float(np.exp(mid + hi))
        if self.winner is not None and (scope, borough) in self.winner.index:
            row = self.winner.loc[(scope, borough)]
            typical, low, high = float(row["typical"]), float(row["low"]), float(row["high"])
        return {
            "scope": scope, "scope_label": label, "scope_description": desc, "borough": borough,
            "typical": typical, "low": low, "high": high, "model": self.model_name,
            "comparable_projects": int((self.train["scope"] == scope).sum()),
            "comparable_in_borough": int(((self.train["scope"] == scope) & (self.train["borough"] == borough)).sum()),
            "borough_multiplier": self.borough_multipliers()[borough], "funded_by_existing_program": False,
        }

    def comparables(self, scope: str, borough: str, n: int = 5) -> list[dict]:
        """Real current capital projects of this scope in this borough, closest to the typical budget."""
        if scope in PROGRAM_SCOPES:
            return []
        t = self.train[(self.train["scope"] == scope) & (self.train["borough"] == borough)]
        if t.empty:
            t = self.train[self.train["scope"] == scope]
        target = np.log(self.estimate(scope, borough)["typical"])
        t = t.assign(gap=(np.log(t["total_budget"]) - target).abs()).nsmallest(n, "gap")
        return [{"fms_id": r.fms_id, "project": r.fms_project_name, "borough": r.borough,
                 "budget": float(r.total_budget), "phase": r.current_phase} for r in t.itertuples()]

    def borough_multipliers(self) -> dict[str, float]:
        """Budget for the same scope in each borough, relative to the five-borough average (1.0)."""
        beta = {b: dict(zip(self.columns, self.coef)).get(f"borough_{b}", 0.0) for b in BOROUGHS}
        mean = np.mean(list(beta.values()))
        return {b: float(np.exp(v - mean)) for b, v in beta.items()}

    def summary(self) -> dict:
        return {"source": f"site-specific projects, {self.source}, scopes cleaned",
                "range_width_high_over_low": {SCOPES[s][0]: round(float(np.exp(q[(25, 75)][1] - q[(25, 75)][0])), 1)
                                              for s, q in self.range_quantiles.items()},
                "projects": int(len(self.train)), "scopes": int(self.train["scope"].nunique()),
                "r2": round(self.r2, 3), "borough_f_statistic": round(self.borough_f, 2),
                "borough_multipliers": {b: round(m, 2) for b, m in self.borough_multipliers().items()}}


def scope_ranges(scopes: np.ndarray, residuals: np.ndarray, pooling: int = RANGE_POOLING,
                 levels=((25, 75), (10, 90))) -> dict:
    """Per-scope error quantiles: the scope's own spread, shrunk toward the citywide spread, but never
    narrower than the scope's own spread.

    Different kinds of work vary by different amounts, so each scope gets its own range. A scope with
    n projects gets weight n / (n + pooling) on its own spread and the rest on the citywide spread;
    then, so a small, unusually variable scope (street tree contracts cover 1 to 7 community boards)
    is not given a falsely narrow range, the range is widened to the scope's own spread if that is
    wider. Cross-validation showed plain shrinkage covered only 30% of held-out tree projects with its
    "50%" range; this rule brings it to 44% while keeping overall coverage at about 50%.
    """
    pooled = {lv: np.percentile(residuals, lv) for lv in levels}
    out = {}
    for s in np.unique(scopes):
        r = residuals[scopes == s]
        w = len(r) / (len(r) + pooling)
        # The middle: the scope's median error, shrunk the same way. Errors are skewed, so the plain
        # regression prediction sits below the median project; this centers "typical" on the median.
        out[s] = {"median": w * np.percentile(r, 50) + (1 - w) * np.percentile(residuals, 50)}
        for lv in levels:
            own = np.percentile(r, lv)
            shrunk = w * own + (1 - w) * pooled[lv]
            out[s][lv] = np.array([min(shrunk[0], own[0]), max(shrunk[1], own[1])])
    return out


# ---------- default scope for batch pricing (the memo's long list; the agent chooses for single requests) ----------

DEFAULT_SCOPE = {
    "streets": "PRIMARY STREET RECONSTRUCTION",  # resurfacing program is chosen by default_scope when it fits
    "sewers_and_flooding": "REPLACEMENT OR AUGMENTATION OF EXISTING SYSTEMS",
    "water_mains": "TRUNK AND DISTRIBUTION MAIN REPLACEMENT",
    "sidewalks_and_ramps": SIDEWALK_RAMP_PROGRAM,  # almost no site-specific sidewalk/ramp projects exist
    "street_lights_and_signals": "INSTALLATION OF LAMPPOSTS AND LUMINAIRES",
    "street_trees": "Land Acquisition, Tree Planting and Green Infrastructure",
    "parks": "Neighborhood Parks, Playgrounds and Ballfields",
}


def default_scope(group: str, signal_descriptors: str | None) -> str:
    """Rule-based scope: resurfacing program unless the road base has failed; ramps when ramps are the complaint."""
    sig = signal_descriptors.lower() if isinstance(signal_descriptors, str) else ""
    if group == "streets" and not any(w in sig for w in ("cave-in", "failed street repair")):
        return RESURFACING_PROGRAM
    return DEFAULT_SCOPE[group]


def price_candidates(model: CostModel, candidates: pd.DataFrame, districts: pd.DataFrame) -> pd.DataFrame:
    """Typical cost and complaints-per-$1M for every capital candidate (no AI; for ranking)."""
    boro = dict(zip(districts["boro_cd"], districts["borough"]))
    est = {}
    rows = []
    for r in candidates.itertuples():
        scope = default_scope(r.group, r.signal_descriptors)
        key = (scope, boro[r.boro_cd])
        if key not in est:
            est[key] = model.estimate(*key)
        e = est[key]
        rows.append({"scope": scope, "scope_label": e["scope_label"], "typical_cost": e["typical"],
                     "cost_low": e["low"], "cost_high": e["high"],
                     "funded_by_existing_program": e["funded_by_existing_program"],
                     "complaints_per_million": r.complaints / (e["typical"] / 1e6) if e["typical"] else None})
    return pd.concat([candidates.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
