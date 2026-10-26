"""Statistical validation of the cost model (civicequity/cost_model.py).

    python analysis/validate_cost_model.py

Writes analysis/cost_model_validation.json and analysis/cost_model_validation.md. Fixed random seeds,
so the numbers reproduce. Tests:

  1. In-sample fit: OLS with heteroskedasticity-robust (HC3) standard errors, Type II ANOVA
     (how much each term explains), Wald tests for scope and borough.
  2. Does borough matter? Robust Wald test, plus a permutation test that shuffles borough labels
     within each scope (no distribution assumptions), plus bootstrap 95% intervals for the
     borough multipliers.
  3. Out-of-sample accuracy: repeated stratified 10-fold cross-validation against two simpler
     baselines (one citywide median; scope only), with paired tests on the errors.
  4. Are the ranges honest? Coverage of the 50% and 80% ranges on held-out projects, overall and
     per scope (each scope has its own range, shrunk toward the citywide spread when small).
  4b. Could the ranges be narrower? Out-of-sample test of extra predictors (managing agency,
     size words in the project name, number of districts).
  5. Residual diagnostics: normality (Jarque-Bera), constant variance (Breusch-Pagan),
     influential projects (Cook's distance).
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy import stats
from statsmodels.stats.diagnostic import het_breuschpagan
from statsmodels.stats.stattools import jarque_bera

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from civicequity import data  # noqa: E402
from civicequity.cost_model import BOROUGHS, SCOPES, CostModel, scope_ranges  # noqa: E402

OUT = Path(__file__).resolve().parent
SEED = 20261026
FOLDS, REPEATS = 10, 10
N_PERMUTATIONS, N_BOOTSTRAP = 2000, 2000


def design(df: pd.DataFrame, scopes: list[str], use_scope=True, use_borough=True) -> np.ndarray:
    """Dummy-coded design matrix with fixed category levels (first level is the baseline)."""
    cols = [np.ones(len(df))]
    if use_scope:
        cols += [(df["scope"] == s).to_numpy(float) for s in scopes[1:]]
    if use_borough:
        cols += [(df["borough"] == b).to_numpy(float) for b in BOROUGHS[1:]]
    return np.column_stack(cols)


def fit_predict(train: pd.DataFrame, test: pd.DataFrame, scopes, **kw):
    A, y = design(train, scopes, **kw), np.log(train["total_budget"].to_numpy())
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ coef
    return design(test, scopes, **kw) @ coef, resid


def nested_f(df, scopes):
    """F statistic for adding borough to a scope-only model."""
    y = np.log(df["total_budget"].to_numpy())
    A1, A0 = design(df, scopes), design(df, scopes, use_borough=False)
    r1 = y - A1 @ np.linalg.lstsq(A1, y, rcond=None)[0]
    r0 = y - A0 @ np.linalg.lstsq(A0, y, rcond=None)[0]
    return ((r0 @ r0 - r1 @ r1) / (A1.shape[1] - A0.shape[1])) / ((r1 @ r1) / (len(y) - A1.shape[1]))


def multipliers(df, scopes):
    y = np.log(df["total_budget"].to_numpy())
    coef = np.linalg.lstsq(design(df, scopes), y, rcond=None)[0]
    beta = np.concatenate([[0.0], coef[-(len(BOROUGHS) - 1):]])
    return np.exp(beta - beta.mean())


def main():
    rng = np.random.default_rng(SEED)
    model = CostModel(data.load_snapshot())
    df = model.train[["scope", "borough", "total_budget", "fms_project_name"]].reset_index(drop=True)
    df["log_budget"] = np.log(df["total_budget"])
    scopes = df["scope"].value_counts().index.tolist()  # baseline = most common scope
    code = {s: f"S{i}" for i, s in enumerate(scopes)}
    df["scope_code"] = df["scope"].map(code)
    n = len(df)
    results = {"projects": n, "scopes": len(scopes),
               "scope_sizes": {SCOPES[s][0]: int(c) for s, c in df["scope"].value_counts().items()},
               "data_cleaning": model.cleaning_log}

    # ---------- 1. In-sample fit with robust standard errors ----------
    ols = smf.ols("log_budget ~ C(scope_code) + C(borough, Treatment('Bronx'))", data=df).fit()
    robust = ols.get_robustcov_results(cov_type="HC3")
    anova = sm.stats.anova_lm(ols, typ=2)
    ss_total = ((df["log_budget"] - df["log_budget"].mean()) ** 2).sum()
    results["in_sample"] = {
        "r2": round(ols.rsquared, 3), "adj_r2": round(ols.rsquared_adj, 3),
        "overall_F": round(ols.fvalue, 2), "overall_p": float(ols.f_pvalue),
        "share_of_variation_explained": {
            "scope": round(anova.loc["C(scope_code)", "sum_sq"] / ss_total, 3),
            "borough": round(anova.loc["C(borough, Treatment('Bronx'))", "sum_sq"] / ss_total, 3),
        },
    }
    names = robust.model.exog_names
    b_idx = [i for i, nm in enumerate(names) if "borough" in nm]
    s_idx = [i for i, nm in enumerate(names) if "scope_code" in nm]
    for label, idx in (("scope", s_idx), ("borough", b_idx)):
        R = np.zeros((len(idx), len(names)))
        for r, i in enumerate(idx):
            R[r, i] = 1
        w = robust.wald_test(R, scalar=True)
        results["in_sample"][f"robust_wald_{label}"] = {"F": round(float(w.statistic), 2), "p": float(w.pvalue)}
    results["in_sample"]["borough_coefficients_vs_bronx_HC3"] = {
        names[i].split("T.")[-1].rstrip("]"): {
            "multiplier": round(float(np.exp(robust.params[i])), 3),
            "ci95": [round(float(np.exp(robust.params[i] - 1.96 * robust.bse[i])), 3),
                     round(float(np.exp(robust.params[i] + 1.96 * robust.bse[i])), 3)],
            "p": float(robust.pvalues[i])} for i in b_idx}

    # ---------- 2. Does borough matter? Permutation test and bootstrap ----------
    f_obs = nested_f(df, scopes)
    perm = np.empty(N_PERMUTATIONS)
    shuffled = df.copy()
    for k in range(N_PERMUTATIONS):
        shuffled["borough"] = df.groupby("scope")["borough"].transform(lambda s: rng.permutation(s.to_numpy()))
        perm[k] = nested_f(shuffled, scopes)
    results["borough_permutation_test"] = {
        "observed_F": round(float(f_obs), 2), "permutations": N_PERMUTATIONS,
        "p": float((1 + (perm >= f_obs).sum()) / (N_PERMUTATIONS + 1)),
        "note": "borough labels shuffled within each scope, so scope mix is held fixed"}

    boots = np.empty((N_BOOTSTRAP, len(BOROUGHS)))
    for k in range(N_BOOTSTRAP):
        sample = df.iloc[rng.integers(0, n, n)]
        if sample["scope"].nunique() < len(scopes):
            sample = pd.concat([sample, df.drop_duplicates("scope")])  # keep every scope estimable
        boots[k] = multipliers(sample, scopes)
    point = multipliers(df, scopes)
    results["borough_multipliers_bootstrap"] = {
        b: {"multiplier": round(float(point[i]), 3),
            "ci95": [round(float(np.percentile(boots[:, i], 2.5)), 3), round(float(np.percentile(boots[:, i], 97.5)), 3)]}
        for i, b in enumerate(BOROUGHS)}

    # ---------- 3 & 4. Repeated stratified cross-validation and range coverage ----------
    variants = {"citywide median": dict(use_scope=False, use_borough=False),
                "scope only": dict(use_scope=True, use_borough=False),
                "scope + borough (the model)": dict(use_scope=True, use_borough=True)}
    errors = {v: [] for v in variants}
    covered = {"50%": [], "80%": []}
    per_scope = {s: {"n": 0, "in50": 0, "width50": []} for s in scopes}
    for rep in range(REPEATS):
        fold = np.empty(n, int)
        for _, idx in df.groupby("scope").indices.items():  # stratify by scope
            idx = rng.permutation(idx)
            fold[idx] = np.arange(len(idx)) % FOLDS
        for f in range(FOLDS):
            train, test = df[fold != f], df[fold == f]
            y_test = test["log_budget"].to_numpy()
            for v, kw in variants.items():
                pred, resid = fit_predict(train, test, scopes, **kw)
                errors[v].append(y_test - pred)
                if v == "scope + borough (the model)":
                    # Same per-scope ranges the app uses, rebuilt from the training fold only.
                    ranges = scope_ranges(train["scope"].to_numpy(), resid)
                    for name, lv in {"50%": (25, 75), "80%": (10, 90)}.items():
                        qlo = np.array([ranges[s][lv][0] for s in test["scope"]])
                        qhi = np.array([ranges[s][lv][1] for s in test["scope"]])
                        inside = (y_test >= pred + qlo) & (y_test <= pred + qhi)
                        covered[name].append(inside.sum())
                        if name == "50%":
                            for s, ok, lo_, hi_ in zip(test["scope"], inside, qlo, qhi):
                                per_scope[s]["n"] += 1
                                per_scope[s]["in50"] += int(ok)
                                per_scope[s]["width50"].append(np.exp(hi_ - lo_))
    cv = {}
    sst = ((df["log_budget"] - df["log_budget"].mean()) ** 2).sum() * REPEATS
    for v, errs in errors.items():
        e = np.concatenate(errs)
        cv[v] = {"rmse_log": round(float(np.sqrt((e ** 2).mean())), 3),
                 "r2_out_of_sample": round(float(1 - (e ** 2).sum() / sst), 3),
                 "median_abs_pct_error": round(float(np.median(np.abs(np.exp(-e) - 1))), 3),
                 "share_within_2x": round(float((np.abs(e) < np.log(2)).mean()), 3)}
    e_scope = np.concatenate(errors["scope only"]) ** 2
    e_full = np.concatenate(errors["scope + borough (the model)"]) ** 2
    e_base = np.concatenate(errors["citywide median"]) ** 2
    results["cross_validation"] = {
        "design": f"{REPEATS} x stratified {FOLDS}-fold, {n} projects, each predicted {REPEATS} times",
        "models": cv,
        "model_vs_citywide_median": {"paired_t_p": float(stats.ttest_rel(e_base, e_full).pvalue),
                                     "wilcoxon_p": float(stats.wilcoxon(e_base, e_full).pvalue)},
        "borough_adds_out_of_sample": {"mean_sq_error_change": round(float((e_full - e_scope).mean()), 4),
                                       "paired_t_p": float(stats.ttest_rel(e_scope, e_full).pvalue),
                                       "wilcoxon_p": float(stats.wilcoxon(e_scope, e_full).pvalue)},
        "range_coverage": {k: round(sum(v) / (n * REPEATS), 3) for k, v in covered.items()},
        "per_scope_50pct_range": {SCOPES[s][0]: {"coverage": round(v["in50"] / v["n"], 3),
                                                 "width_high_over_low": round(float(np.mean(v["width50"])), 1),
                                                 "projects": int((df["scope"] == s).sum())}
                                  for s, v in per_scope.items()},
    }

    # ---------- 4b. Could extra predictors narrow the ranges? ----------
    nm = df["fms_project_name"].str.lower()
    df["size_words"] = nm.str.contains(r"area|phase|multi|hub|grid|citywide|various|corridor|program|group|priority").astype(float)
    df["single_site"] = nm.str.contains(r"\bat\b|intersection|triangle|corner|plaza|square|\blot\b").astype(float)
    n_districts = model.train.reset_index(drop=True)["fms_id"].map(data.load_snapshot()["allocations"].groupby("fms_id").size())
    df["log_districts"] = np.log(n_districts.fillna(1).to_numpy())
    df["managing_agency"] = model.train.reset_index(drop=True)["managing_agency"]
    agencies = sorted(df["managing_agency"].unique())

    def extra_design(frame, extra):
        A = [design(frame, scopes)]
        if "agency" in extra:
            A.append(np.column_stack([(frame["managing_agency"] == a).to_numpy(float) for a in agencies[1:]]))
        for c in ("size_words", "single_site", "log_districts"):
            if c in extra:
                A.append(frame[[c]].to_numpy(float))
        return np.column_stack(A)

    experiments = {"scope + borough": [], "+ managing agency": ["agency"],
                   "+ size words in name": ["size_words", "single_site"], "+ number of districts": ["log_districts"],
                   "+ all three": ["agency", "size_words", "single_site", "log_districts"]}
    exp_out = {}
    for label, extra in experiments.items():
        errs, widths = [], []
        for rep_ in range(5):
            fold = np.empty(n, int)
            for _, idx in df.groupby("scope").indices.items():
                idx = rng.permutation(idx)
                fold[idx] = np.arange(len(idx)) % FOLDS
            for f in range(FOLDS):
                tr, te = df[fold != f], df[fold == f]
                A = extra_design(tr, extra)
                coef = np.linalg.lstsq(A, tr["log_budget"].to_numpy(), rcond=None)[0]
                r = tr["log_budget"].to_numpy() - A @ coef
                q = np.percentile(r, [25, 75])
                errs.append(te["log_budget"].to_numpy() - extra_design(te, extra) @ coef)
                widths.append(np.exp(q[1] - q[0]))
        e = np.concatenate(errs)
        sst_ = ((df["log_budget"] - df["log_budget"].mean()) ** 2).sum() * 5
        exp_out[label] = {"r2_out_of_sample": round(float(1 - (e ** 2).sum() / sst_), 3),
                          "range_width_high_over_low": round(float(np.mean(widths)), 2)}
    results["narrower_ranges_experiment"] = {
        "results": exp_out,
        "note": "Agency and project names are unknown for a problem that has no project yet, so these "
                "predictors could not be used for new 311 needs even where they help."}

    # ---------- 5. Residual diagnostics ----------
    jb, jb_p, skew, kurt = jarque_bera(ols.resid)
    bp_lm, bp_p, _, _ = het_breuschpagan(ols.resid, ols.model.exog)
    cooks = ols.get_influence().cooks_distance[0]
    top = df.assign(cooks=cooks).nlargest(3, "cooks")
    results["diagnostics"] = {
        "jarque_bera": {"stat": round(float(jb), 1), "p": float(jb_p), "skew": round(float(skew), 2),
                        "kurtosis": round(float(kurt), 2)},
        "breusch_pagan": {"LM": round(float(bp_lm), 1), "p": float(bp_p)},
        "influential_projects_cooks_gt_4_over_n": int((cooks > 4 / n).sum()),
        "most_influential": [{"project": r.fms_project_name, "budget": float(r.total_budget),
                              "scope": SCOPES[r.scope][0], "cooks_d": round(float(r.cooks), 3)} for r in top.itertuples()],
    }
    # Refit without the influential projects: do the borough conclusions survive?
    keep = cooks <= 4 / n
    results["diagnostics"]["borough_multipliers_without_influential"] = {
        b: round(float(m), 3) for b, m in zip(BOROUGHS, multipliers(df[keep].reset_index(drop=True), scopes))}

    (OUT / "cost_model_validation.json").write_text(json.dumps(results, indent=2) + "\n")
    write_markdown(results)
    print(json.dumps(results, indent=1))


def fmt_p(p: float) -> str:
    return "< 0.001" if p < 0.001 else f"{p:.3f}"


def write_markdown(r: dict) -> None:
    s, cv, d = r["in_sample"], r["cross_validation"], r["diagnostics"]
    lines = [
        "# Cost model validation", "",
        f"Model: log(budget) ~ scope + borough, fit to {r['projects']} site-specific projects in NYC's current "
        f"capital plan across {r['scopes']} scopes. Reproduce with `python analysis/validate_cost_model.py`.", "",
        "## Fit", "",
        f"- R² = {s['r2']} (adjusted {s['adj_r2']}); overall F = {s['overall_F']}, p {fmt_p(s['overall_p'])}",
        f"- Scope explains {s['share_of_variation_explained']['scope']:.1%} of the variation in log budgets; "
        f"borough explains {s['share_of_variation_explained']['borough']:.1%} on top of scope.",
        f"- Robust (HC3) Wald test, scope: F = {s['robust_wald_scope']['F']}, p {fmt_p(s['robust_wald_scope']['p'])}",
        f"- Robust (HC3) Wald test, borough: F = {s['robust_wald_borough']['F']}, p {fmt_p(s['robust_wald_borough']['p'])}",
        f"- Permutation test, borough ({r['borough_permutation_test']['permutations']:,} shuffles within scope): "
        f"p {fmt_p(r['borough_permutation_test']['p'])}", "",
        "## Borough multipliers (1.0 = five-borough average)", "",
        "| Borough | Multiplier | Bootstrap 95% CI | Without influential projects |", "|---|---|---|---|"]
    for b, v in r["borough_multipliers_bootstrap"].items():
        lines.append(f"| {b} | {v['multiplier']:.2f} | {v['ci95'][0]:.2f} – {v['ci95'][1]:.2f} | "
                     f"{d['borough_multipliers_without_influential'][b]:.2f} |")
    lines += ["", f"## Out-of-sample accuracy ({cv['design']})", "",
              "| Model | Out-of-sample R² | RMSE (log) | Median abs. % error | Within 2× of actual |", "|---|---|---|---|---|"]
    for v, m in cv["models"].items():
        lines.append(f"| {v} | {m['r2_out_of_sample']} | {m['rmse_log']} | {m['median_abs_pct_error']:.0%} | "
                     f"{m['share_within_2x']:.0%} |")
    b = cv["borough_adds_out_of_sample"]
    lines += ["",
              f"- Model vs. a single citywide median: paired t-test p {fmt_p(cv['model_vs_citywide_median']['paired_t_p'])}, "
              f"Wilcoxon p {fmt_p(cv['model_vs_citywide_median']['wilcoxon_p'])}",
              f"- Adding borough to scope, out of sample: change in mean squared error {b['mean_sq_error_change']:+.4f} "
              f"(paired t p {fmt_p(b['paired_t_p'])}, Wilcoxon p {fmt_p(b['wilcoxon_p'])})", "",
              "## Are the ranges honest?", "",
              f"- The 50% range contained {cv['range_coverage']['50%']:.1%} of held-out projects (target 50%).",
              f"- The 80% range contained {cv['range_coverage']['80%']:.1%} of held-out projects (target 80%).", "",
              "| Scope | Projects | 50% range covered | Range width (high ÷ low) |", "|---|---|---|---|"]
    for sc, v in sorted(cv["per_scope_50pct_range"].items(), key=lambda kv: -kv[1]["projects"]):
        lines.append(f"| {sc} | {v['projects']} | {v['coverage']:.0%} | {v['width_high_over_low']}× |")
    ex = r["narrower_ranges_experiment"]["results"]
    lines += ["", "## Could the ranges be narrower?", "",
              "| Predictors | Out-of-sample R² | 50% range width (high ÷ low) |", "|---|---|---|"]
    lines += [f"| {k} | {v['r2_out_of_sample']} | {v['range_width_high_over_low']}× |" for k, v in ex.items()]
    lines += ["", r["narrower_ranges_experiment"]["note"], ""]
    lines += [
              "## Diagnostics", "",
              f"- Residual normality (Jarque-Bera): stat {d['jarque_bera']['stat']}, p {fmt_p(d['jarque_bera']['p'])}, "
              f"skew {d['jarque_bera']['skew']}, kurtosis {d['jarque_bera']['kurtosis']}",
              f"- Constant variance (Breusch-Pagan): LM {d['breusch_pagan']['LM']}, p {fmt_p(d['breusch_pagan']['p'])}",
              f"- Influential projects (Cook's D > 4/n): {d['influential_projects_cooks_gt_4_over_n']}", "",
              "## Data cleaning", "",
              f"- {r['data_cleaning']['dropped_below_capital_minimum']} projects under the $50,000 capital minimum dropped.",
              f"- {r['data_cleaning']['reclassified']} projects reclassified because their names contradict their "
              "capital plan category. Largest examples:"]
    lines += [f"  - {e}" for e in r["data_cleaning"]["examples"]]
    lines += ["- Scopes left with too few real projects to price (now handled as existing citywide programs "
              "or not offered): " + ", ".join(f"{k} ({v})" for k, v in r["data_cleaning"]["dropped_scopes_too_few_projects"].items())]
    (OUT / "cost_model_validation.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
