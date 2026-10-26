"""Reality check: can the stretch of street that 311 complaints cover stand in for a project's size?

    python analysis/size_proxy_check.py             # reuses the saved complaint pull
    python analysis/size_proxy_check.py --refresh   # pulls the complaints again (NYC updates past 311 records)

For a new capital candidate the app can't know the future project's size, so it would estimate it from
how far the complaints on that street stretch. This script tests that proxy on real City projects that
have a mapped footprint (CPDB polygons):
  1. pull the year's individual street, sewer, and water complaints with coordinates;
  2. find the street each project footprint sits on (the most common street among complaints inside it);
  3. measure the complaint stretch on that street in that district, the same way the app would
     (distance between the outermost complaint locations);
  4. compare the stretch with the project's real length, and test whether it predicts the budget out of
     sample as well as the real footprint does.
Writes analysis/size_proxy_check.json and .md. The decision rule agreed with the project owner: if the
proxy is weak, the app keeps typical-size estimates and the report says so.
"""

import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent
BASE = "https://data.cityofnewyork.us/resource"
GROUP_TYPES = {
    "Full street reconstruction": ["Street Condition", "Curb Condition"],
    "Sewer construction or upgrade": ["Sewer", "Sewer Maintenance"],
    "Water main replacement": ["Water System", "Water Maintenance"],
    "Green and natural drainage": ["Sewer", "Sewer Maintenance"],
}
NEAR_FEET = 75  # a complaint counts as on the project if within this distance of its footprint
MIN_COMPLAINTS = 3


def soql(dataset, params):
    rows, offset = [], 0
    while True:
        for attempt in range(4):  # NYC Open Data sometimes returns 503 for a moment
            try:
                r = requests.get(f"{BASE}/{dataset}.json", params={**params, "$limit": 50000, "$offset": offset},
                                 timeout=300)
                r.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(10 * (attempt + 1))
        batch = r.json()
        rows += batch
        if len(batch) < 50000:
            return rows
        offset += 50000


def footprint_length(geom) -> float:
    """Length of an elongated footprint: for each part, the long side of a rectangle with the same
    area and perimeter (L = (P/2 + sqrt((P/2)^2 - 4A)) / 2)."""
    total = 0.0
    for g in getattr(geom, "geoms", [geom]):
        p, a = g.length, g.area
        half = p / 2
        total += (half + np.sqrt(max(half ** 2 - 4 * a, 0))) / 2
    return total


def stretch_feet(pts: gpd.GeoDataFrame) -> float:
    """Distance between the outermost complaints: the bounding-box diagonal, which the app can compute
    from min/max latitude and longitude in one NYC Open Data query."""
    x0, y0, x1, y1 = pts.total_bounds
    return float(np.hypot(x1 - x0, y1 - y0))


def main():
    meta = json.loads((ROOT / "data" / "metadata.json").read_text())
    start, end = meta["window_start"], meta["window_end_exclusive"]
    types = sorted({t for v in GROUP_TYPES.values() for t in v})
    cache = OUT / "size_proxy_complaints.csv.gz"
    if cache.exists() and "--refresh" not in sys.argv:
        c = pd.read_csv(cache)
        print(f"Using saved complaint pull ({cache.name})")
    else:
        print(f"Pulling individual complaints {start} to {end}: {', '.join(types)} ...")
        c = pd.DataFrame(soql("erm2-nwe9", {
            "$select": "complaint_type,street_name,community_board,latitude,longitude",
            "$where": f"created_date >= '{start}' AND created_date < '{end}' AND complaint_type in("
                      + ",".join(f"'{t}'" for t in types) + ") AND latitude IS NOT NULL AND street_name IS NOT NULL",
        }))
        c.to_csv(cache, index=False)
        (OUT / "size_proxy_complaints_pulled.txt").write_text(pd.Timestamp.now(tz="UTC").isoformat() + "\n")
    c = gpd.GeoDataFrame(c, geometry=gpd.points_from_xy(c["longitude"].astype(float), c["latitude"].astype(float)),
                         crs=4326).to_crs(2263)
    print(f"  {len(c):,} complaints")

    train = pd.read_csv(OUT / "cost_training_data.csv")
    train = train[train["scope_label"].isin(GROUP_TYPES)]
    poly = soql("9jkp-n57r", {"$select": "maprojid,the_geom"})
    poly = gpd.GeoDataFrame({"fms_id": [p["maprojid"][3:] for p in poly if p.get("the_geom")]},
                            geometry=[shape(p["the_geom"]) for p in poly if p.get("the_geom")], crs=4326).to_crs(2263)
    poly = poly.dissolve("fms_id").reset_index().merge(train, on="fms_id")
    poly["area_sqft"] = poly.area
    poly["length_ft"] = poly.geometry.apply(footprint_length)
    print(f"  {len(poly)} infrastructure projects with footprints")

    rows = []
    for p in poly.itertuples():
        near = c[c["complaint_type"].isin(GROUP_TYPES[p.scope_label])]
        near = near[near.geometry.distance(p.geometry) <= NEAR_FEET]
        if len(near) < MIN_COMPLAINTS:
            continue
        street = near["street_name"].mode().iloc[0]
        cb = near.loc[near["street_name"] == street, "community_board"].mode().iloc[0]
        on_street = c[(c["street_name"] == street) & (c["community_board"] == cb)
                      & c["complaint_type"].isin(GROUP_TYPES[p.scope_label])]
        rows.append({"fms_id": p.fms_id, "project": p.fms_project_name, "scope": p.scope_label, "borough": p.borough,
                     "budget": p.total_budget, "area_sqft": p.area_sqft, "length_ft": p.length_ft,
                     "street": street, "complaints_on_street": len(on_street), "stretch_ft": stretch_feet(on_street)})
    m = pd.DataFrame(rows)
    m = m[m["stretch_ft"] > 0]
    print(f"  {len(m)} projects matched to a street with {MIN_COMPLAINTS}+ nearby complaints")

    lr = lambda a, b: float(np.log(m[a]).corr(np.log(m[b]), method="spearman"))
    result = {
        "projects_with_footprint": int(len(poly)), "projects_matched": int(len(m)),
        "by_scope": m["scope"].value_counts().to_dict(),
        "rank_corr_stretch_vs_project_length": round(lr("stretch_ft", "length_ft"), 3),
        "rank_corr_stretch_vs_budget": round(lr("stretch_ft", "budget"), 3),
        "rank_corr_footprint_area_vs_budget": round(lr("area_sqft", "budget"), 3),
        "median_stretch_over_length": round(float((m["stretch_ft"] / m["length_ft"]).median()), 2),
        "typical_width_ft_by_scope": (m.assign(w=m["area_sqft"] / m["length_ft"]).groupby("scope")["w"].median()
                                      .round(0).to_dict()),
    }

    # Out-of-sample test on the matched projects: no size vs. real footprint vs. complaint-stretch proxy
    rng = np.random.default_rng(11)
    m = m.reset_index(drop=True)
    m["y"] = np.log(m["budget"])
    scopes, boros = sorted(m["scope"].unique()), sorted(m["borough"].unique())

    def X(f, size_col):
        cols = [np.ones(len(f))] + [(f["scope"] == s).to_numpy(float) for s in scopes[1:]] + \
               [(f["borough"] == b).to_numpy(float) for b in boros[1:]]
        if size_col:
            cols.append(np.log(f[size_col].to_numpy()))
        return np.column_stack(cols)

    def cv(size_col, reps=20, k=10):
        E, W50, W80, C50, C80 = [], [], [], [], []
        for _ in range(reps):
            fold = rng.permutation(len(m)) % k
            for f in range(k):
                a, b = m[fold != f], m[fold == f]
                A = X(a, size_col)
                coef = np.linalg.lstsq(A, a["y"], rcond=None)[0]
                r = a["y"] - A @ coef
                q = np.percentile(r, [10, 25, 75, 90])
                e = b["y"].to_numpy() - X(b, size_col) @ coef
                E.append(e)
                W50.append(np.exp(q[2] - q[1])); W80.append(np.exp(q[3] - q[0]))
                C50.append(((e >= q[1]) & (e <= q[2])).mean()); C80.append(((e >= q[0]) & (e <= q[3])).mean())
        e = np.concatenate(E)
        sst = ((m["y"] - m["y"].mean()) ** 2).sum() * reps
        return {"r2_out_of_sample": round(float(1 - (e ** 2).sum() / sst), 3), "width50": round(float(np.mean(W50)), 2),
                "width80": round(float(np.mean(W80)), 1), "coverage50": round(float(np.mean(C50)), 3),
                "coverage80": round(float(np.mean(C80)), 3)}

    result["cross_validation"] = {"no size": cv(None), "real footprint area": cv("area_sqft"),
                                  "complaint stretch (the app's proxy)": cv("stretch_ft")}
    # Same test on every infrastructure project with a footprint (not only those matched to a complaint street):
    # how much would real size data help if the City published it?
    matched = m
    m = poly.assign(budget=poly["total_budget"], scope=poly["scope_label"]).reset_index(drop=True)
    m["y"] = np.log(m["budget"])
    result["all_footprints"] = {"projects": int(len(m)), "no size": cv(None), "real footprint area": cv("area_sqft")}
    m = matched
    proxy, base = result["cross_validation"]["complaint stretch (the app's proxy)"], result["cross_validation"]["no size"]
    strong = (result["rank_corr_stretch_vs_project_length"] >= 0.3 and
              proxy["r2_out_of_sample"] > base["r2_out_of_sample"] + 0.05 and proxy["width80"] < base["width80"])
    result["verdict"] = ("strong enough: use the complaint stretch to size-adjust estimates" if strong else
                         "too weak: keep typical-size estimates in the app and report the finding")
    (OUT / "size_proxy_check.json").write_text(json.dumps(result, indent=2))
    m.drop(columns=["y"]).to_csv(OUT / "size_proxy_matches.csv", index=False)

    cvr = result["cross_validation"]
    md = ["# Can complaint stretch stand in for project size?", "",
          f"{result['projects_matched']} of {result['projects_with_footprint']} street, sewer, water, and drainage projects "
          "with a mapped footprint sit on a street with nearby complaints in the same category.", "",
          f"- Rank correlation, complaint stretch vs. real project length: **{result['rank_corr_stretch_vs_project_length']}**",
          f"- Rank correlation, complaint stretch vs. budget: {result['rank_corr_stretch_vs_budget']} "
          f"(real footprint area vs. budget: {result['rank_corr_footprint_area_vs_budget']})",
          f"- Median complaint stretch ÷ project length: {result['median_stretch_over_length']}", "",
          "| Size measure | Out-of-sample R² | 50% range | 80% range | 50% coverage | 80% coverage |", "|---|---|---|---|---|---|"]
    md += [f"| {k} | {v['r2_out_of_sample']} | {v['width50']}x | {v['width80']}x | {v['coverage50']:.0%} | {v['coverage80']:.0%} |"
           for k, v in cvr.items()]
    af = result["all_footprints"]
    md += ["", f"**If the City published project size:** on all {af['projects']} infrastructure projects with a footprint, "
               f"real footprint area raises out-of-sample R² from {af['no size']['r2_out_of_sample']} to "
               f"{af['real footprint area']['r2_out_of_sample']} and narrows the 80% range from {af['no size']['width80']}x "
               f"to {af['real footprint area']['width80']}x (50% range {af['no size']['width50']}x to "
               f"{af['real footprint area']['width50']}x).", "",
           f"**Verdict:** {result['verdict']}."]
    (OUT / "size_proxy_check.md").write_text("\n".join(md) + "\n")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
