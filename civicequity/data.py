"""Load the NYC data snapshot that prepare_data.py writes to data/."""

import json
import subprocess
import sys

import pandas as pd
from shapely.geometry import shape

from civicequity import config

REFRESH_TIMEOUT_SECONDS = 600


def metadata() -> dict:
    return json.loads((config.DATA_DIR / "metadata.json").read_text())


def read_districts() -> tuple[pd.DataFrame, dict]:
    """District attributes with shapely shapes, plus the raw GeoJSON for maps.

    The app deliberately does not import geopandas: importing it loads pyproj, which opens PROJ's
    SQLite database. On macOS, once that database is open, any later fork of the process (Streamlit
    runs `git` in a forked child for each browser session) crashes the child inside PROJ's fork
    handler, and macOS shows "Python quit unexpectedly". prepare_data.py, a separate process, still
    uses geopandas. The app never converts coordinates, so shapely is all it needs.
    """
    geojson = json.loads((config.DATA_DIR / "community_districts.geojson").read_text())
    rows = [{**f["properties"], "geometry": shape(f["geometry"])} for f in geojson["features"]]
    return pd.DataFrame(rows), geojson


def load_snapshot() -> dict:
    d = config.DATA_DIR
    districts, district_geojson = read_districts()
    income_file = d / "district_income.csv"
    if income_file.exists():  # median household income (Census ACS), bundled by prepare_data.py
        districts = districts.merge(pd.read_csv(income_file)[["boro_cd", "median_household_income",
                                                               "income_margin_of_error",
                                                               "shares_puma_with_another_district"]],
                                    on="boro_cd", how="left")
    heat_file = d / "district_heat.csv"
    if heat_file.exists():  # NYC Health Heat Vulnerability Index, 1-5 (prepare_data.py --only heat)
        districts = districts.merge(pd.read_csv(heat_file)[["boro_cd", "heat_vulnerability_index"]], on="boro_cd",
                                    how="left")
    return {
        "districts": districts,
        "district_geojson": district_geojson,
        "requests": pd.read_csv(d / "311_counts.csv"),
        # Every 311 complaint type and agency (all agencies), so Triage can route anything 311 receives.
        "catalog_all": pd.read_csv(d / "311_catalog_all.csv") if (d / "311_catalog_all.csv").exists() else None,
        "street_hotspots": pd.read_csv(d / "311_hotspots_street.csv"),
        "street_descriptor_hotspots": pd.read_csv(d / "311_hotspots_street_descriptor.csv"),
        "park_hotspots": pd.read_csv(d / "311_hotspots_park.csv"),
        "city_owned_sidewalks": pd.read_csv(d / "311_sidewalks_city_owned.csv", dtype={"bbl": str}),
        "projects": pd.read_csv(d / "capital_projects.csv"),
        # Every project from all capital-plan snapshots, budgets in latest-snapshot dollars (BLS index)
        "projects_history": (pd.read_csv(d / "capital_projects_history.csv")
                             if (d / "capital_projects_history.csv").exists() else None),
        "allocations": pd.read_csv(d / "capital_allocations.csv"),
        "program_units": pd.read_csv(d / "program_units.csv"),
        "reconstruction_projects": pd.read_csv(d / "reconstruction_projects.csv"),
        "reconstruction_locations": pd.read_csv(d / "reconstruction_locations.csv"),
    }


def refresh_snapshot() -> tuple[bool, str]:
    """Rebuild data/ from NYC Open Data. On any failure the old snapshot stays in place."""
    try:
        result = subprocess.run(
            [sys.executable, str(config.ROOT / "prepare_data.py")],
            capture_output=True, text=True, timeout=REFRESH_TIMEOUT_SECONDS, cwd=config.ROOT,
        )
    except subprocess.TimeoutExpired:
        return False, f"NYC Open Data took longer than {REFRESH_TIMEOUT_SECONDS // 60} minutes. Kept the existing snapshot."
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output[-2000:]
