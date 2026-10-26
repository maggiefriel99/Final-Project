"""Does training the cost model on every capital-plan snapshot (May 2023 - May 2026, budgets converted to May 2026
dollars with the BLS construction input index) predict CURRENT projects better than the latest snapshot alone?

    python analysis/cost_history_check.py

Fair test: 10 x stratified 10-fold cross-validation over current projects. In each fold both models are scored on
the same held-out current projects; the "with history" model also trains on projects that appear only in older
snapshots. Writes analysis/cost_history_check.md and .json.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent

from civicequity import data  # noqa: E402
from civicequity.cost_model import BOROUGHS, SCOPES, CostModel, scope_ranges  # noqa: E402


def main():
    s = data.load_snapshot()
    cur = CostModel(s, use_history=False).train.copy()
    his = CostModel(s, use_history=True).train.copy()
    for d in (cur, his):
        d["y"] = np.log(d["total_budget"])
    added = his[~his["fms_id"].isin(cur["fms_id"])]
    scopes = sorted(set(cur["scope"]) | set(his["scope"]))

    def X(f):
        return np.column_stack([np.ones(len(f))] + [(f["scope"] == x).to_numpy(float) for x in scopes[1:]]
                               + [(f["borough"] == b).to_numpy(float) for b in BOROUGHS[1:]])

    rng = np.random.default_rng(5)
    res = {"latest snapshot only": [], "with older snapshots (BLS-adjusted)": []}
    for _ in range(10):
        fold = np.empty(len(cur), int)
        for _, idx in cur.groupby("scope").indices.items():
            idx = rng.permutation(idx)
            fold[idx] = np.arange(len(idx)) % 10
        for f in range(10):
            test, train = cur[fold == f], cur[fold != f]
            for name, tr in (("latest snapshot only", train),
                             ("with older snapshots (BLS-adjusted)", pd.concat([train, added]))):
                A = X(tr)
                coef = np.linalg.lstsq(A, tr["y"], rcond=None)[0]
                R = scope_ranges(tr["scope"].to_numpy(), tr["y"].to_numpy() - A @ coef)
                e = test["y"].to_numpy() - X(test) @ coef
                q = lambda lv, i: np.array([R[x][lv][i] for x in test["scope"]])
                res[name].append(pd.DataFrame({
                    "scope": test["scope"].values, "e": e,
                    "in50": (e >= q((25, 75), 0)) & (e <= q((25, 75), 1)),
                    "in80": (e >= q((10, 90), 0)) & (e <= q((10, 90), 1)),
                    "w50": np.exp(q((25, 75), 1) - q((25, 75), 0))}))
    sst = ((cur["y"] - cur["y"].mean()) ** 2).sum() * 10
    out = {}
    for k, v in res.items():
        d = pd.concat(v)
        out[k] = {"r2_out_of_sample": round(float(1 - (d["e"] ** 2).sum() / sst), 3),
                  "coverage50": round(float(d["in50"].mean()), 3), "coverage80": round(float(d["in80"].mean()), 3),
                  "width50": round(float(d["w50"].mean()), 2),
                  "coverage50_by_scope": {SCOPES[sc][0]: round(float(g["in50"].mean()), 2) for sc, g in d.groupby("scope")},
                  "_sq": (d["e"] ** 2).to_numpy()}
    a, b = out["latest snapshot only"].pop("_sq"), out["with older snapshots (BLS-adjusted)"].pop("_sq")
    medians = (his.assign(group=np.where(his["fms_id"].isin(cur["fms_id"]), "current", "left the plan"))
                  .groupby(["scope", "group"])["total_budget"].median().unstack())
    result = {
        "current_projects": len(cur), "projects_added_from_older_snapshots": len(added),
        "added_by_scope": {SCOPES[k][0]: int(v) for k, v in added["scope"].value_counts().items()},
        "models": out, "mean_squared_error_change": round(float((b - a).mean()), 4),
        "paired_t_p": float(stats.ttest_rel(b, a).pvalue),
        "median_budget_millions": {SCOPES[k][0]: {g: round(float(v) / 1e6, 2) for g, v in r.items()}
                                   for k, r in medians.iterrows()},
        "added_by_phase": added["current_phase"].value_counts().to_dict(),
    }
    (OUT / "cost_history_check.json").write_text(json.dumps(result, indent=2))
    L = ["# Does more history improve the cost model?", "",
         f"{result['projects_added_from_older_snapshots']} projects appear only in older capital-plan snapshots "
         f"(May 2023 - Jan 2026); their budgets were converted to May 2026 dollars with the BLS PPI for net inputs to "
         f"other nonresidential construction (WPUIP231230). Test: predict the {result['current_projects']} current "
         "projects out of sample, with and without them.", "",
         "| Training data | Out-of-sample R² | 50% coverage | 80% coverage | 50% range width |", "|---|---|---|---|---|"]
    L += [f"| {k} | {v['r2_out_of_sample']} | {v['coverage50']:.1%} | {v['coverage80']:.1%} | {v['width50']}x |"
          for k, v in out.items()]
    L += ["", f"Adding the older projects raised the squared error on current projects by {result['mean_squared_error_change']:+.4f} "
              f"(paired t p = {result['paired_t_p']:.2g}).", "",
          "Why: projects that left the plan are not like current ones (median budget, $ millions):", "",
          "| Scope | Current | Left the plan |", "|---|---|---|"]
    L += [f"| {k} | {v.get('current', '')} | {v.get('left the plan', '')} |" for k, v in result["median_budget_millions"].items()]
    L += ["", "**Decision:** the app keeps training on the latest snapshot; the history and BLS conversion stay available "
              "(`CIVICEQUITY_COST_HISTORY=1`)."]
    (OUT / "cost_history_check.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
