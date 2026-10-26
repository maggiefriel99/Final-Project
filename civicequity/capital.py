"""Where capital money that addresses 311 complaints goes, by community district.

Only capital plan categories in the definitions.yaml crosswalk count. Each project's money is placed:
  1. site-specific: the project's own map location or district tag (prepare_data.py), or
  2. lump-sum program: spread across districts by where that program actually worked in the
     last 3 years (square yards paved, ramps built, capital tree work orders), weighted by
     borough cost differences when use_borough_costs is on, or
  3. not assigned: no way to tell which district benefits (e.g. LED street light replacement).
"""

import re

import pandas as pd

from civicequity import rules
from civicequity.cost_model import BOROUGHS, CostModel

BORO_CODE = {"Manhattan": 1, "Bronx": 2, "Brooklyn": 3, "Queens": 4, "Staten Island": 5}
BORO_PATTERNS = {  # borough names and abbreviations used in capital project titles
    "Manhattan": r"\b(manhattan|manh|mn)\b", "Bronx": r"\b(bronx|brnx|bx)\b", "Brooklyn": r"\b(brooklyn|bk|bklyn)\b",
    "Queens": r"\b(queens|qn|qns)\b", "Staten Island": r"\b(staten island|si)\b",
}


def lump_sum_program(category: str, name: str) -> str | None:
    """Which located program a lump-sum budget line pays for, if we can tell."""
    n = name.lower()
    if category == "PRIMARY STREET RESURFACING":
        return "street_resurfacing"
    if category == "PEDESTRIAN RAMP CONSTRUCTION":
        return "pedestrian_ramps"
    if "tree" in n and "sidewalk" in n:
        return "tree_sidewalk_repair"
    if "tree" in n and re.search(r"plant|procure|street tree|grow bag|field grown", n):
        return "street_tree_planting"
    return None


def target_districts(borough: str, name: str, all_cds: list[int]) -> list[int]:
    """Districts a lump sum can reach: its borough, any boroughs named in its title, and any listed CBs."""
    boros = [borough] if borough in BORO_CODE else [b for b, p in BORO_PATTERNS.items() if re.search(p, name.lower())]
    cds = [cd for cd in all_cds if not boros or cd // 100 in {BORO_CODE[b] for b in boros}]
    # "CBs 3,4,6,8", "CB's 1,2,4,5,,7-12", "Community Boards 11,12,17,18"
    m = re.search(r"(?:CB'?s|Community Boards?)\s*([\d,\-\s]+)", name, flags=re.I)
    if m and len(boros) == 1:
        nums = set()
        for part in re.split(r"[,\s]+", m.group(1).strip()):
            if "-" in part and all(p.isdigit() for p in part.split("-")):
                lo, hi = map(int, part.split("-"))
                nums |= set(range(lo, hi + 1))
            elif part.isdigit():
                nums.add(int(part))
        listed = [cd for cd in cds if cd % 100 in nums]
        cds = listed or cds
    return cds


def district_capital(snap: dict, model: CostModel, use_borough_costs: bool = True):
    """Returns (dollars by district, group, and placement method; budget lines not assigned to any district)."""
    groups = rules.category_groups()
    projects = snap["projects"].assign(group=snap["projects"]["ten_year_plan_category"].map(groups))
    projects = projects[projects["group"].notna()]
    all_cds = sorted(snap["districts"]["boro_cd"].astype(int))

    # 1. Site-specific projects.
    alloc = snap["allocations"].merge(projects[["fms_id", "group", "total_budget"]], on="fms_id")
    site = alloc.assign(dollars=alloc["share"] * alloc["total_budget"], method="site-specific project")
    rows = [site[["boro_cd", "group", "method", "dollars"]]]

    # 2. Lump sums spread by located program work.
    units = snap["program_units"]
    multipliers = model.borough_multipliers() if use_borough_costs else dict.fromkeys(BOROUGHS, 1.0)
    cd_multiplier = {cd: multipliers[b] for b, code in BORO_CODE.items() for cd in all_cds if cd // 100 == code}
    unplaced = projects[~projects["fms_id"].isin(alloc["fms_id"])]
    unassigned = []
    for p in unplaced.itertuples():
        program = lump_sum_program(p.ten_year_plan_category, p.fms_project_name)
        cds = target_districts(p.borough, p.fms_project_name, all_cds)
        work = units[(units["program"] == program) & units["boro_cd"].isin(cds)] if program else units.iloc[0:0]
        if work.empty or p.total_budget <= 0:
            unassigned.append({"fms_id": p.fms_id, "group": p.group, "borough": p.borough,
                               "project": p.fms_project_name, "dollars": p.total_budget})
            continue
        weight = work["units"] * work["boro_cd"].map(cd_multiplier)
        rows.append(pd.DataFrame({
            "boro_cd": work["boro_cd"], "group": p.group, "method": f"lump sum spread by {work['unit'].iloc[0]}",
            "dollars": p.total_budget * weight / weight.sum(),
        }))

    dollars = pd.concat(rows).groupby(["boro_cd", "group", "method"], as_index=False)["dollars"].sum()
    return dollars, pd.DataFrame(unassigned)
