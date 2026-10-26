"""Build the CivicEquity data snapshot from NYC Open Data.

Pulls 311 service requests, capital projects, community district boundaries,
population, and located work from four capital programs (street resurfacing,
pedestrian ramps, street trees, street/sewer/water reconstruction), then writes
small files to data/ that the app reads offline. Takes about 3 minutes.

Usage:
    python prepare_data.py               # last 4 complete quarters
    python prepare_data.py --quarters 8  # longer history

Files are built in a temp folder and only copied into data/ if every step
succeeds, so a failed refresh never breaks the existing snapshot.
"""

import argparse
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from dotenv import load_dotenv
from shapely.geometry import shape

BASE = "https://data.cityofnewyork.us/resource"
DATASETS = {
    "311": "erm2-nwe9",               # 311 Service Requests from 2010 to Present
    "capital_dashboard": "fb86-vt7u",  # Capital Projects Dashboard - Citywide Budget and Schedule
    "cpdb_points": "h2ic-zdws",        # Capital Projects Database (CPDB) - Projects (Points)
    "cpdb_polygons": "9jkp-n57r",      # Capital Projects Database (CPDB) - Projects (Polygons)
    "districts": "5crt-au7u",          # Community Districts
    "population": "9ji4-nien",         # Population by Community District (ACS 2015-2019)
    "pluto": "64uk-42ks",              # PLUTO tax lots: which lots the City owns
    "resurfacing": "ffaf-8mrv",        # DOT In-house Street Resurfacing Projects
    "ped_ramps": "e7gc-ub6z",          # Pedestrian Ramp - Program Progress
    "forestry_work_orders": "bdjm-n7q4",   # Parks Forestry Work Orders
    "street_reconstruction": "97nd-ff3i",  # Street and Highway Capital Reconstruction Projects - Intersection
}

# City agencies that own physical infrastructure that 311 complaints point at.
INFRA_AGENCIES = ["DOT", "DEP", "DPR", "DSNY"]

BORO_CODES = {"MANHATTAN": 1, "BRONX": 2, "BROOKLYN": 3, "QUEENS": 4, "STATEN ISLAND": 5}
BORO_NAMES = {v: k.title() for k, v in BORO_CODES.items()}

# Hotspots are streets (or parks) where the same problem keeps coming back. These floors
# are deliberately low; definitions.yaml sets the real thresholds, so they can change
# without pulling data again.
STREET_HOTSPOT_FLOOR = 5       # complaints of one type on one street
DESCRIPTOR_HOTSPOT_FLOOR = 2   # complaints with one descriptor on one street or at one park
REPAIR_AGENCIES = ["DOT", "DEP", "DPR"]  # sanitation complaints are never capital

# Where lump-sum programs actually worked over the last few years shows where their money goes.
PROGRAM_YEARS = 3

PAGE = 50_000
DATA_DIR = Path(__file__).parent / "data"
for _env in (Path(__file__).parent / ".env", Path(__file__).parent.parent / ".env"):
    if _env.exists():
        load_dotenv(_env, override=False)

# Median household income by community district, from the Census Bureau's American Community Survey
# (5-year estimates, table B19013). Census publishes it for PUMAs; in NYC each PUMA is one community
# district, except four PUMAs that each cover two districts (both districts get the shared figure).
# Needs a free CENSUS_API_KEY, used only here: the app reads the bundled file and never needs the key.
ACS_YEAR = 2024

# Construction cost converter for older capital-plan snapshots: U.S. Bureau of Labor Statistics, PPI by Commodity,
# Inputs to Industries: Net Inputs to Other Nonresidential Construction, Excluding Capital Investment, Labor, and
# Imports (Dec 2014=100, not seasonally adjusted). Covers highways and streets, power and communication
# structures, and other nonresidential public works. It excludes labor, which the report notes as a limitation.
BLS_SERIES = "WPUIP231230"
BLS_TITLE = ("PPI by Commodity: Inputs to Industries: Net Inputs to Other Nonresidential Construction, "
             "Excluding Capital Investment, Labor, and Imports")
# Statuses whose budgets don't describe a real, site-specific project.
EXCLUDED_PHASES = {"(Cancelled)", "(Terminated)", "(Capitally Ineligible)", "(Transferred)", "(Inactive)",
                   "(Lump Sum)", "(Holding Code)", "(Pass-Through Fund)"}
ACS_URL = f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"


# ---------- helpers ----------

def soql(dataset, params, timeout=180):
    """Fetch every row of a SoQL query, paging past the 50k row limit."""
    headers = {}
    token = os.getenv("SOCRATA_APP_TOKEN")  # optional, raises rate limits
    if token:
        headers["X-App-Token"] = token
    rows, offset = [], 0
    while True:
        page_params = {**params, "$limit": PAGE, "$offset": offset}
        for attempt in range(3):
            try:
                r = requests.get(f"{BASE}/{dataset}.json", params=page_params,
                                 headers=headers, timeout=timeout)
                r.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        batch = r.json()
        rows.extend(batch)
        if len(batch) < PAGE:
            return rows
        offset += PAGE


def is_real_district(boro_cd):
    """Community districts are 1-18 within a borough; higher codes are parks/airports."""
    return boro_cd is not None and 1 <= boro_cd % 100 <= 18


def cd_from_311(value):
    """'10 BRONX' -> 210. Returns None for 'Unspecified' and similar."""
    m = re.fullmatch(r"(\d{2}) (MANHATTAN|BRONX|BROOKLYN|QUEENS|STATEN ISLAND)", (value or "").strip())
    if not m:
        return None
    code = BORO_CODES[m.group(2)] * 100 + int(m.group(1))
    return code if is_real_district(code) else None


def cds_from_dashboard(value):
    """'Queens, Queens 07' -> [407]. 'Bronx 01, Bronx 02' -> [201, 202]. 'Citywide' -> []."""
    codes = []
    for boro, num in re.findall(r"(Manhattan|Bronx|Brooklyn|Queens|Staten Island) (\d{1,2})", value or ""):
        code = BORO_CODES[boro.upper()] * 100 + int(num)
        if is_real_district(code) and code not in codes:
            codes.append(code)
    return codes


def quarter_window(n_quarters, today=None):
    """Start and end dates covering the last n complete calendar quarters."""
    today = today or date.today()
    q_start_month = 3 * ((today.month - 1) // 3) + 1
    end = date(today.year, q_start_month, 1)
    months_back = 3 * n_quarters
    y, m = end.year, end.month - months_back
    while m < 1:
        m += 12
        y -= 1
    return date(y, m, 1), end


def to_gdf(rows, geom_field="the_geom"):
    rows = [r for r in rows if r.get(geom_field)]
    geoms = [shape(r.pop(geom_field)) for r in rows]
    return gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")


# ---------- steps ----------

def build_districts():
    print("Community districts + population ...")
    gdf = to_gdf(soql(DATASETS["districts"], {"$select": "boro_cd,the_geom"}))
    gdf["boro_cd"] = gdf["boro_cd"].astype(int)
    gdf = gdf[gdf["boro_cd"].map(is_real_district)].copy()

    pop = pd.DataFrame(soql(DATASETS["population"], {
        "$select": "borough_cd_code,community_district_name,community_district_population,of_population_that_is_lep",
    }))
    pop = pop.rename(columns={
        "borough_cd_code": "boro_cd",
        "community_district_name": "district_name",
        "community_district_population": "population",
        "of_population_that_is_lep": "pct_limited_english",
    })
    pop["boro_cd"] = pop["boro_cd"].astype(int)
    pop["population"] = pd.to_numeric(pop["population"])
    pop["pct_limited_english"] = pd.to_numeric(pop["pct_limited_english"])

    gdf = gdf.merge(pop, on="boro_cd", how="left")
    gdf["borough"] = (gdf["boro_cd"] // 100).map(BORO_NAMES)
    gdf["district_label"] = gdf["borough"] + " CD " + (gdf["boro_cd"] % 100).astype(str)
    missing = gdf[gdf["population"].isna()]["boro_cd"].tolist()
    if missing:
        print(f"  warning: no population for districts {missing}")
    print(f"  {len(gdf)} districts")
    return gdf


def next_month(d):
    return date(d.year + d.month // 12, d.month % 12 + 1, 1)


def month_starts(start, end):
    months, d = [], start
    while d < end:
        months.append(d)
        d = next_month(d)
    return months


def query_311_month(month_start, agencies):
    rows = soql(DATASETS["311"], {
        "$select": "community_board,agency,complaint_type,descriptor,count(*) as n",
        "$where": f"created_date >= '{month_start}' AND created_date < '{next_month(month_start)}' AND agency in({agencies})",
        "$group": "community_board,agency,complaint_type,descriptor",
        "$order": "community_board,agency,complaint_type,descriptor",
    })
    for r in rows:
        r["month"] = month_start.strftime("%Y-%m")
    return rows


HOTSPOT_STATS = ("count(*) as n,avg(latitude) as lat,avg(longitude) as lon,"
                 "min(created_date) as first_seen,max(created_date) as last_seen")


def query_street_hotspots(where, agency, by_descriptor):
    group = "community_board,agency,complaint_type" + (",descriptor" if by_descriptor else "") + ",street_name"
    floor = DESCRIPTOR_HOTSPOT_FLOOR if by_descriptor else STREET_HOTSPOT_FLOOR
    return soql(DATASETS["311"], {
        "$select": f"{group},{HOTSPOT_STATS}",
        "$where": f"{where} AND agency='{agency}' AND street_name IS NOT NULL",
        "$group": group,
        "$having": f"count(*) >= {floor}",
        "$order": "n DESC",
    }, timeout=300)


def query_park_hotspots(where):
    group = "community_board,park_facility_name,complaint_type,descriptor"
    return soql(DATASETS["311"], {
        "$select": f"{group},{HOTSPOT_STATS}",
        "$where": f"{where} AND agency='DPR' AND complaint_type='Maintenance or Facility' "
                  "AND park_facility_name IS NOT NULL AND park_facility_name != 'Unspecified'",
        "$group": group,
        "$having": f"count(*) >= {DESCRIPTOR_HOTSPOT_FLOOR}",
        "$order": "n DESC",
    }, timeout=300)


def query_sidewalk_lots(where):
    return soql(DATASETS["311"], {
        "$select": "community_board,bbl,descriptor,count(*) as n,avg(latitude) as lat,avg(longitude) as lon",
        "$where": f"{where} AND complaint_type='Sidewalk Condition' AND bbl IS NOT NULL",
        "$group": "community_board,bbl,descriptor",
    }, timeout=300)


def query_city_owned_lots():
    return soql(DATASETS["pluto"], {"$select": "bbl,ownername", "$where": "ownertype='C'"})


def clean_hotspots(rows, cols):
    df = pd.DataFrame(rows)
    df["boro_cd"] = df["community_board"].map(cd_from_311)
    df = df.dropna(subset=["boro_cd"]).copy()
    df["boro_cd"] = df["boro_cd"].astype(int)
    df["n"] = pd.to_numeric(df["n"]).astype(int)
    for col in ("lat", "lon"):
        df[col] = pd.to_numeric(df[col])
    for col in ("first_seen", "last_seen"):
        if col in df:
            df[col] = pd.to_datetime(df[col]).dt.strftime("%Y-%m-%d")
    return df[cols]


def build_311(start, end):
    print(f"311 requests {start} to {end} for {', '.join(INFRA_AGENCIES)} ...")
    agencies = ",".join(f"'{a}'" for a in INFRA_AGENCIES)
    where = f"created_date >= '{start}' AND created_date < '{end}' AND agency in({agencies})"

    # One grouped query over a whole year takes minutes per page on the city's
    # servers; one query per month takes seconds, so run them side by side.
    # The year-long hotspot queries are slower (about a minute) and run alongside them.
    with ThreadPoolExecutor(max_workers=8) as pool:
        street_f = [pool.submit(query_street_hotspots, where, a, False) for a in REPAIR_AGENCIES]
        desc_f = [pool.submit(query_street_hotspots, where, a, True) for a in REPAIR_AGENCIES]
        park_f = pool.submit(query_park_hotspots, where)
        sidewalk_f = pool.submit(query_sidewalk_lots, where)
        city_lots_f = pool.submit(query_city_owned_lots)
        month_rows = list(pool.map(lambda m: query_311_month(m, agencies), month_starts(start, end)))
        street_rows = [r for fut in street_f for r in fut.result()]
        desc_rows = [r for fut in desc_f for r in fut.result()]
        park_rows, sidewalk_rows, city_lots = park_f.result(), sidewalk_f.result(), city_lots_f.result()

    counts = pd.DataFrame([r for rows in month_rows for r in rows])
    counts["boro_cd"] = counts["community_board"].map(cd_from_311)
    unplaced = pd.to_numeric(counts.loc[counts["boro_cd"].isna(), "n"]).sum()
    counts = counts.dropna(subset=["boro_cd"]).copy()
    counts["boro_cd"] = counts["boro_cd"].astype(int)
    counts["n"] = pd.to_numeric(counts["n"]).astype(int)
    counts["quarter"] = pd.PeriodIndex(counts["month"], freq="Q").astype(str)
    counts["descriptor"] = counts["descriptor"].fillna("Unspecified")
    counts = counts[["boro_cd", "agency", "complaint_type", "descriptor", "month", "quarter", "n"]]
    print(f"  {counts['n'].sum():,} requests in {len(counts):,} rows ({unplaced:,} had no district)")

    stats = ["n", "lat", "lon", "first_seen", "last_seen"]
    hot = {
        "street": clean_hotspots(street_rows, ["boro_cd", "agency", "complaint_type", "street_name", *stats]),
        "street_descriptor": clean_hotspots(
            desc_rows, ["boro_cd", "agency", "complaint_type", "descriptor", "street_name", *stats]),
        "park": clean_hotspots(park_rows, ["boro_cd", "park_facility_name", "complaint_type", "descriptor", *stats]),
    }
    print(f"  hotspots: {len(hot['street']):,} streets ({STREET_HOTSPOT_FLOOR}+ of one type), "
          f"{len(hot['street_descriptor']):,} street-descriptor pairs, {len(hot['park']):,} park-descriptor pairs")

    # Sidewalk complaints in front of City-owned lots: the City, not a private owner, must fix those.
    owners = {str(r["bbl"]).split(".")[0]: r.get("ownername", "") for r in city_lots}
    side = pd.DataFrame(sidewalk_rows)
    side["bbl"] = side["bbl"].astype(str).str.split(".").str[0]
    side = side[side["bbl"].isin(owners)].copy()
    side["owner_name"] = side["bbl"].map(owners)
    side = clean_hotspots(side.to_dict("records"), ["boro_cd", "bbl", "owner_name", "descriptor", "n", "lat", "lon"])
    print(f"  {side['n'].sum():,} sidewalk complaints in front of {side['bbl'].nunique():,} City-owned lots "
          f"(of {len(owners):,} City-owned lots in PLUTO)")
    return counts, hot, side


def build_programs(districts, end):
    """Located work from lump-sum capital programs, counted per community district."""
    start = date(end.year - PROGRAM_YEARS, end.month, 1)
    print(f"Capital program work {start} to {end} ...")
    rows = []

    res = soql(DATASETS["resurfacing"], {
        "$select": "location_community_board as cb,sum(location_actual_paving_square) as units,count(*) as segments",
        "$where": f"location_actual_paving_start_date >= '{start}' AND location_actual_paving_start_date < '{end}'",
        "$group": "cb",
    })
    for r in res:
        cd = int(float(r["cb"])) if r.get("cb") else None
        if is_real_district(cd):
            rows.append({"program": "street_resurfacing", "boro_cd": cd, "units": float(r["units"]),
                         "unit": "square yards paved"})

    ramps = to_gdf(soql(DATASETS["ped_ramps"], {
        "$select": "the_geom",
        "$where": f"construc_2 = 'Constructed' AND construc_1 >= '{start:%Y/%m/%d}' AND construc_1 < '{end:%Y/%m/%d}'",
    }))
    in_cd = gpd.sjoin(ramps, districts[["boro_cd", "geometry"]], predicate="within")
    for cd, n in in_cd.groupby("boro_cd").size().items():
        rows.append({"program": "pedestrian_ramps", "boro_cd": int(cd), "units": float(n), "unit": "ramps built"})

    trees = soql(DATASETS["forestry_work_orders"], {
        "$select": "communityboard as cb,wocategory,count(*) as n",
        "$where": "woentity = 'Capital' AND wocategory in('Tree Planting','Tree and Sidewalk Repair') "
                  f"AND wostatus not in('Cancel','Alternate') AND createddate >= '{start}' AND createddate < '{end}'",
        "$group": "cb,wocategory",
    })
    for r in trees:
        cd = int(float(r["cb"])) if r.get("cb") else None
        if is_real_district(cd):
            program = "street_tree_planting" if r["wocategory"] == "Tree Planting" else "tree_sidewalk_repair"
            rows.append({"program": program, "boro_cd": cd, "units": float(r["n"]), "unit": "capital work orders"})

    units = pd.DataFrame(rows).groupby(["program", "boro_cd", "unit"], as_index=False)["units"].sum()
    for program, g in units.groupby("program"):
        print(f"  {program}: {g['units'].sum():,.0f} {g['unit'].iloc[0]} in {len(g)} districts")
    return units, str(start)


def build_reconstruction(districts):
    """Completed and active street, sewer, and water reconstruction projects with real costs."""
    print("Street and highway capital reconstruction projects ...")
    df = pd.DataFrame(soql(DATASETS["street_reconstruction"], {
        "$select": "projectid,fmsid,projtitle,projectt_1,projectsta,projectcos,constructi,boroughnam,leadagency,"
                   "latitude,longitude",
    }))
    df["projectcos"] = pd.to_numeric(df["projectcos"], errors="coerce")
    for col in ("latitude", "longitude"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Each row is one intersection; the project cost repeats on every row.
    projects = df.groupby("projectid", as_index=False).agg(
        fms_id=("fmsid", "first"), title=("projtitle", "first"), project_type=("projectt_1", "first"),
        status=("projectsta", "first"), cost=("projectcos", "first"), construction_year=("constructi", "first"),
        borough=("boroughnam", "first"), lead_agency=("leadagency", "first"), n_locations=("projectid", "size"),
    )
    projects["borough"] = projects["borough"].str.title()

    pts = df.dropna(subset=["latitude", "longitude"])
    pts = gpd.GeoDataFrame(pts, geometry=gpd.points_from_xy(pts["longitude"], pts["latitude"]), crs="EPSG:4326")
    j = gpd.sjoin(pts, districts[["boro_cd", "geometry"]], predicate="within")
    locations = j.groupby(["projectid", "fmsid", "boro_cd"]).size().rename("n_locations").reset_index()
    locations = locations.rename(columns={"fmsid": "fms_id"})
    print(f"  {len(projects):,} projects, ${projects['cost'].sum() / 1e9:,.1f}B, "
          f"{projects['project_type'].nunique()} project types")
    return projects, locations


def build_capital(districts, recon_locations):
    print("Capital Projects Dashboard (latest reporting period) ...")
    latest = soql(DATASETS["capital_dashboard"], {"$select": "max(reporting_period) as p"})[0]["p"]
    dash = pd.DataFrame(soql(DATASETS["capital_dashboard"], {
        "$select": "fms_id,fms_project_name,agency_project_description,managing_agency,sponsor_agency,"
                   "ten_year_plan_category,borough,community_board,total_budget,spend_to_date,"
                   "current_phase,forecast_completion,budget_line",
        "$where": f"reporting_period = '{latest}'",
        "$order": "fms_id",
    }))
    for col in ("total_budget", "spend_to_date"):
        dash[col] = pd.to_numeric(dash[col], errors="coerce").fillna(0.0)
    dash["community_board"] = dash["community_board"].fillna("")
    # A few projects appear once per budget line; collapse to one row per project.
    dash = (dash.groupby("fms_id", as_index=False)
                .agg({**{c: "first" for c in dash.columns if c not in ("fms_id", "total_budget", "spend_to_date")},
                      "total_budget": "sum", "spend_to_date": "sum"}))
    dash["reporting_period"] = latest
    print(f"  period {latest}: {len(dash):,} projects, ${dash['total_budget'].sum() / 1e9:,.1f}B")

    print("CPDB project geometries ...")
    geoms = []
    for key in ("cpdb_polygons", "cpdb_points"):  # polygons first: they carry more detail
        g = to_gdf(soql(DATASETS[key], {"$select": "maprojid,the_geom"}))
        g["fms_id"] = g["maprojid"].str[3:]  # maprojid = 3-digit agency code + FMS id
        geoms.append(g[["fms_id", "geometry"]])
    geo = pd.concat(geoms).drop_duplicates("fms_id", keep="first")
    geo = gpd.GeoDataFrame(geo, crs="EPSG:4326")
    geo = geo[geo["fms_id"].isin(dash["fms_id"])]

    # Overlay in a feet-based projection so area shares are meaningful.
    cd = districts[["boro_cd", "geometry"]].to_crs("EPSG:2263")
    geo = geo.to_crs("EPSG:2263")
    alloc = []
    parts = geo.explode(index_parts=False)
    is_poly = parts.geom_type.isin(["Polygon", "MultiPolygon"])
    # Polygon projects split across districts by overlapping area ...
    poly = gpd.overlay(parts[is_poly], cd, how="intersection")
    poly["weight"] = poly.geometry.area
    # ... point projects (possibly several points) split by how many points land in each district.
    pts = gpd.sjoin(parts[~is_poly & ~parts["fms_id"].isin(poly["fms_id"])], cd, predicate="within")
    pts["weight"] = 1.0
    pieces = pd.concat([poly[["fms_id", "boro_cd", "weight"]], pts[["fms_id", "boro_cd", "weight"]]])
    shares = pieces.groupby(["fms_id", "boro_cd"])["weight"].sum().reset_index()
    shares["share"] = shares["weight"] / shares.groupby("fms_id")["weight"].transform("sum")
    for r in shares.itertuples():
        alloc.append({"fms_id": r.fms_id, "boro_cd": int(r.boro_cd), "share": r.share, "method": "map_location"})
    geo_ids = set(shares["fms_id"])

    # Next best: the street reconstruction dataset lists every intersection a project touches.
    recon = recon_locations[recon_locations["fms_id"].isin(dash["fms_id"]) & ~recon_locations["fms_id"].isin(geo_ids)]
    recon = recon.groupby(["fms_id", "boro_cd"])["n_locations"].sum().reset_index()
    recon["share"] = recon["n_locations"] / recon.groupby("fms_id")["n_locations"].transform("sum")
    for r in recon.itertuples():
        alloc.append({"fms_id": r.fms_id, "boro_cd": int(r.boro_cd), "share": r.share,
                      "method": "street_reconstruction_locations"})
    geo_ids |= set(recon["fms_id"])

    for r in dash.itertuples():
        if r.fms_id in geo_ids:
            continue
        codes = cds_from_dashboard(r.community_board)
        for c in codes:
            alloc.append({"fms_id": r.fms_id, "boro_cd": c, "share": 1 / len(codes), "method": "district_tag"})
    alloc = pd.DataFrame(alloc)

    placed = dash["fms_id"].isin(alloc["fms_id"])
    dash["district_placement"] = "not placed (borough-wide or citywide)"
    by_method = alloc.drop_duplicates("fms_id").set_index("fms_id")["method"]
    dash.loc[placed, "district_placement"] = dash.loc[placed, "fms_id"].map(by_method)

    coverage = {
        "projects_total": int(len(dash)),
        "budget_total": float(dash["total_budget"].sum()),
        "projects_placed": int(placed.sum()),
        "budget_placed": float(dash.loc[placed, "total_budget"].sum()),
        "placed_by_map_location": int(dash["district_placement"].eq("map_location").sum()),
        "placed_by_district_tag": int(dash["district_placement"].eq("district_tag").sum()),
        "placed_by_street_reconstruction": int(dash["district_placement"].eq("street_reconstruction_locations").sum()),
    }
    print(f"  placed in a district: {coverage['projects_placed']:,} of {coverage['projects_total']:,} projects "
          f"({coverage['projects_placed'] / coverage['projects_total']:.0%}), "
          f"{coverage['budget_placed'] / coverage['budget_total']:.0%} of dollars")
    print(f"    by map location: {coverage['placed_by_map_location']:,}, "
          f"by street reconstruction locations: {coverage['placed_by_street_reconstruction']:,}, "
          f"by district tag: {coverage['placed_by_district_tag']:,}")
    return dash, alloc, latest, coverage


def query_catalog_month(month_start):
    rows = soql(DATASETS["311"], {
        "$select": "agency,agency_name,complaint_type,descriptor,count(*) as n",
        "$where": f"created_date >= '{month_start}' AND created_date < '{next_month(month_start)}'",
        "$group": "agency,agency_name,complaint_type,descriptor",
        "$order": "agency,complaint_type,descriptor",
    }, timeout=300)
    return rows


def build_catalog(start, end) -> pd.DataFrame:
    """Every 311 complaint type and descriptor, with its agency and yearly count (all agencies).
    Triage uses it so it can route any request (housing, noise, parking ...) to the right agency."""
    print(f"311 complaint catalog, all agencies, {start} to {end} ...")
    with ThreadPoolExecutor(max_workers=6) as pool:
        months = list(pool.map(query_catalog_month, month_starts(start, end)))
    cat = pd.DataFrame([r for rows in months for r in rows])
    cat["n"] = pd.to_numeric(cat["n"]).astype(int)
    cat["descriptor"] = cat["descriptor"].fillna("Unspecified")
    cat = cat.groupby(["agency", "agency_name", "complaint_type", "descriptor"], as_index=False)["n"].sum()
    print(f"  {cat['n'].sum():,} requests, {cat['agency'].nunique()} agencies, {cat['complaint_type'].nunique()} complaint types")
    return cat


def bls_index(periods: list[str]) -> pd.DataFrame:
    """Monthly index values for each snapshot month (YYYYMM), from the BLS public API (no key needed)."""
    years = sorted({p[:4] for p in periods})
    r = requests.post("https://api.bls.gov/publicAPI/v1/timeseries/data/",
                      json={"seriesid": [BLS_SERIES], "startyear": years[0], "endyear": years[-1]}, timeout=120)
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "REQUEST_SUCCEEDED":
        raise requests.RequestException(f"BLS API: {body.get('message')}")
    vals = {f"{d['year']}{d['period'][1:]}": float(d["value"]) for d in body["Results"]["series"][0]["data"]
            if d["period"].startswith("M")}
    missing = [p for p in periods if p not in vals]
    if missing:
        raise ValueError(f"BLS index missing for {missing}")
    return pd.DataFrame({"period": periods, "index": [vals[p] for p in periods],
                         "series_id": BLS_SERIES, "series_title": BLS_TITLE})


def build_history() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Every project that appears in any Capital Projects Dashboard snapshot, using its most recent record,
    with its budget converted to the latest snapshot's dollars with the BLS construction index."""
    print("Capital Projects Dashboard, all snapshots ...")
    rows = soql(DATASETS["capital_dashboard"], {
        "$select": "fms_id,reporting_period,fms_project_name,managing_agency,ten_year_plan_category,borough,"
                   "community_board,total_budget,current_phase",
        "$order": "fms_id,reporting_period",
    })
    d = pd.DataFrame(rows)
    d["total_budget"] = pd.to_numeric(d["total_budget"], errors="coerce").fillna(0.0)
    # One row per project per snapshot (some projects list several budget lines).
    per = (d.groupby(["fms_id", "reporting_period"], as_index=False)
             .agg({"total_budget": "sum", **{c: "first" for c in ("fms_project_name", "managing_agency",
                                                                    "ten_year_plan_category", "borough",
                                                                    "community_board", "current_phase")}}))
    latest_period = per["reporting_period"].max()
    last = per.sort_values("reporting_period").groupby("fms_id").tail(1).copy()
    last["in_latest_snapshot"] = last["reporting_period"] == latest_period
    excluded = last["current_phase"].isin(EXCLUDED_PHASES)
    print(f"  {d['reporting_period'].nunique()} snapshots, {len(last):,} projects ever; "
          f"{int(last['in_latest_snapshot'].sum()):,} in the latest; {int(excluded.sum()):,} excluded by status")
    last = last[~excluded]

    cpdb_ids = set()
    for key in ("cpdb_polygons", "cpdb_points"):
        cpdb_ids |= {r["maprojid"][3:] for r in soql(DATASETS[key], {"$select": "maprojid"})}
    last["has_district_tag"] = last["community_board"].fillna("").map(lambda v: bool(cds_from_dashboard(v)))
    last["has_map_location"] = last["fms_id"].isin(cpdb_ids)

    index = bls_index(sorted(per["reporting_period"].unique()))
    factor = dict(zip(index["period"], index.loc[index["period"] == latest_period, "index"].iloc[0] / index["index"]))
    last["bls_factor"] = last["reporting_period"].map(factor)
    last["budget_latest_dollars"] = last["total_budget"] * last["bls_factor"]
    print(f"  BLS {BLS_SERIES}: factors {min(factor.values()):.3f} to {max(factor.values()):.3f} (to {latest_period} dollars)")
    return last.rename(columns={"reporting_period": "last_reporting_period"}), index


def build_income() -> tuple[pd.DataFrame | None, dict]:
    """Median household income per community district, or None if there is no Census key."""
    key = os.getenv("CENSUS_API_KEY")
    if not key:
        print("Median household income: no CENSUS_API_KEY, keeping the bundled income file.")
        return None, {}
    print(f"Median household income (ACS {ACS_YEAR - 4}-{ACS_YEAR}) ...")
    r = requests.get(ACS_URL, params={"get": "NAME,B19013_001E,B19013_001M", "for": "public use microdata area:*",
                                      "in": "state:36", "key": key}, timeout=120)
    r.raise_for_status()
    rows = r.json()
    out = []
    for name, income, moe, _state, puma in rows[1:]:
        m = re.match(r"NYC-(Manhattan|Bronx|Brooklyn|Queens|Staten Island) Community Districts? (\d+)(?: & (\d+))?", name)
        if not m:
            continue
        cds = [int(x) for x in m.groups()[1:] if x]
        for cd in cds:
            out.append({"boro_cd": BORO_CODES[m.group(1).upper()] * 100 + cd,
                        "median_household_income": int(income), "income_margin_of_error": int(moe),
                        "puma": puma, "shares_puma_with_another_district": len(cds) > 1})
    income = pd.DataFrame(out).sort_values("boro_cd")
    print(f"  {len(income)} districts from {income['puma'].nunique()} PUMAs "
          f"({int(income['shares_puma_with_another_district'].sum())} districts share a PUMA)")
    return income, {"source": f"US Census Bureau, ACS {ACS_YEAR - 4}-{ACS_YEAR} 5-year estimates, table B19013 by PUMA",
                    "url": ACS_URL}


HVI_DATA = "https://raw.githubusercontent.com/nychealth/EHDP-data/production/indicators/data/2191.json"
HVI_LOOKUP = "https://raw.githubusercontent.com/nychealth/EHDP-data/production/geography/GeoLookup.csv"


def build_heat() -> tuple[pd.DataFrame, dict]:
    """NYC Health Department Heat Vulnerability Index (1 = lowest risk, 5 = highest) per community district.

    Published by Community District Tabulation Area (CDTA 2020); the Health Department's own geography lookup
    gives each area's borough and community district number ("... (CD 1)")."""
    print("Heat Vulnerability Index (NYC Health) ...")
    hvi = requests.get(HVI_DATA, timeout=60).json()
    look = pd.read_csv(io.StringIO(requests.get(HVI_LOOKUP, timeout=60).text))
    look = look[look["GeoType"] == "CDTA2020"]
    names = dict(zip(look["GeoID"].astype(int), zip(look["Borough"], look["Name"])))
    out = []
    for geo, value in zip(hvi["GeoID"], hvi["Value"]):
        borough, name = names[int(geo)]
        m = re.search(r"\(CD (\d+)\)", name)
        out.append({"boro_cd": BORO_CODES[borough.upper()] * 100 + int(m.group(1)), "heat_vulnerability_index": int(value),
                    "cdta_name": name})
    heat = pd.DataFrame(out).sort_values("boro_cd")
    if len(heat) != 59 or heat["boro_cd"].duplicated().any():
        raise RuntimeError(f"Heat Vulnerability Index: expected 59 districts, got {len(heat)}")
    print(f"  59 districts; {int((heat['heat_vulnerability_index'] >= 4).sum())} score 4 or 5")
    return heat, {"source": "NYC Department of Health and Mental Hygiene, Heat Vulnerability Index (Environment & "
                            "Health Data Portal, indicator 2191, by Community District Tabulation Area 2020)",
                  "url": HVI_DATA}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quarters", type=int, default=4, help="complete quarters of 311 history (default 4)")
    parser.add_argument("--only", choices=["income", "catalog", "history", "heat"],
                        help="refresh just one part and leave the rest of the snapshot as is")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # show progress live, even when piped

    if args.only == "history":
        hist, index = build_history()
        hist.to_csv(DATA_DIR / "capital_projects_history.csv", index=False)
        index.to_csv(DATA_DIR / "bls_construction_index.csv", index=False)
        print(f"Wrote {DATA_DIR / 'capital_projects_history.csv'} and bls_construction_index.csv")
        return
    if args.only == "catalog":
        start, end = quarter_window(args.quarters)
        build_catalog(start, end).to_csv(DATA_DIR / "311_catalog_all.csv", index=False)
        print(f"Wrote {DATA_DIR / '311_catalog_all.csv'}")
        return
    if args.only == "heat":
        heat, source = build_heat()
        heat.to_csv(DATA_DIR / "district_heat.csv", index=False)
        meta = json.loads((DATA_DIR / "metadata.json").read_text())
        meta["heat"] = {**source, "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        (DATA_DIR / "metadata.json").write_text(json.dumps(meta, indent=2))
        print(f"Wrote {DATA_DIR / 'district_heat.csv'}")
        return
    if args.only == "income":
        income, source = build_income()
        if income is not None:
            income.to_csv(DATA_DIR / "district_income.csv", index=False)
            meta = json.loads((DATA_DIR / "metadata.json").read_text())
            meta["income"] = {**source, "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            (DATA_DIR / "metadata.json").write_text(json.dumps(meta, indent=2))
            print(f"Wrote {DATA_DIR / 'district_income.csv'}")
        return

    start, end = quarter_window(args.quarters)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        districts = build_districts()
        counts, hotspots, sidewalks = build_311(start, end)
        program_units, program_start = build_programs(districts, end)
        recon_projects, recon_locations = build_reconstruction(districts)
        projects, alloc, period, coverage = build_capital(districts, recon_locations)
        build_catalog(start, end).to_csv(tmp / "311_catalog_all.csv", index=False)
        hist, bls = build_history()
        hist.to_csv(tmp / "capital_projects_history.csv", index=False)
        bls.to_csv(tmp / "bls_construction_index.csv", index=False)
        heat, heat_source = build_heat()
        heat.to_csv(tmp / "district_heat.csv", index=False)
        income, income_source = build_income()
        if income is not None:
            income.to_csv(tmp / "district_income.csv", index=False)

        districts.geometry = districts.geometry.simplify(0.0002)  # smaller file, same look on a city map
        districts.to_file(tmp / "community_districts.geojson", driver="GeoJSON")
        counts.to_csv(tmp / "311_counts.csv", index=False)
        hotspots["street"].to_csv(tmp / "311_hotspots_street.csv", index=False)
        hotspots["street_descriptor"].to_csv(tmp / "311_hotspots_street_descriptor.csv", index=False)
        hotspots["park"].to_csv(tmp / "311_hotspots_park.csv", index=False)
        sidewalks.to_csv(tmp / "311_sidewalks_city_owned.csv", index=False)
        program_units.to_csv(tmp / "program_units.csv", index=False)
        recon_projects.to_csv(tmp / "reconstruction_projects.csv", index=False)
        recon_locations.to_csv(tmp / "reconstruction_locations.csv", index=False)
        projects.to_csv(tmp / "capital_projects.csv", index=False)
        alloc.to_csv(tmp / "capital_allocations.csv", index=False)
        (tmp / "metadata.json").write_text(json.dumps({
            "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "window_start": str(start),
            "window_end_exclusive": str(end),
            "infra_agencies": INFRA_AGENCIES,
            "street_hotspot_floor": STREET_HOTSPOT_FLOOR,
            "descriptor_hotspot_floor": DESCRIPTOR_HOTSPOT_FLOOR,
            "program_window_start": program_start,
            "program_window_end_exclusive": str(end),
            "capital_reporting_period": period,
            "capital_coverage": coverage,
            "sources": {k: f"https://data.cityofnewyork.us/d/{v}" for k, v in DATASETS.items()},
            "heat": {**heat_source, "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
            **({"income": {**income_source, "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}}
               if income is not None else {"income": json.loads((DATA_DIR / "metadata.json").read_text()).get("income")
                                           if (DATA_DIR / "metadata.json").exists() else None}),
        }, indent=2))

        DATA_DIR.mkdir(exist_ok=True)
        (DATA_DIR / "311_hotspots.csv").unlink(missing_ok=True)  # replaced by the three hotspot files
        for f in tmp.iterdir():
            shutil.copy2(f, DATA_DIR / f.name)

    print(f"\nSnapshot written to {DATA_DIR}")
    for f in sorted(DATA_DIR.iterdir()):
        print(f"  {f.name:36s} {f.stat().st_size / 1024:8,.0f} KB")


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as e:
        sys.exit(f"Could not reach NYC Open Data ({e}). Existing snapshot in data/ was left unchanged.")
