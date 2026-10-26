"""Where 311 complaints point to capital needs: per-district need and specific capital candidates."""

import pandas as pd

from civicequity import rules

CANDIDATE_COLUMNS = [
    "boro_cd", "group", "complaint_type", "location", "reason", "complaints", "signal_complaints",
    "signal_descriptors", "lat", "lon", "first_seen", "last_seen",
]


def label_requests(requests: pd.DataFrame) -> pd.DataFrame:
    """Add the operating/capital class and crosswalk group to each row of 311 counts."""
    pairs = requests[["complaint_type", "descriptor"]].drop_duplicates()
    pairs["class"] = [rules.classify(t, d) for t, d in zip(pairs["complaint_type"], pairs["descriptor"])]
    pairs["group"] = pairs["complaint_type"].map(rules.crosswalk_group)
    return requests.merge(pairs, on=["complaint_type", "descriptor"], how="left")


def capital_candidates(snap: dict) -> pd.DataFrame:
    """Streets, parks, and City-owned sidewalks where the rules say an operating fix is no longer enough."""
    esc = rules.escalation()

    # Rule 1: the same infrastructure problem keeps coming back on one street.
    # Complaint types with their own escalation rule (parks) are handled separately below.
    street = snap["street_hotspots"].copy()
    street["class"] = street["complaint_type"].map(lambda t: rules.complaint_rule(t)["class"])
    own_rule = street["complaint_type"].map(lambda t: "escalation_descriptors" in rules.complaint_rule(t))
    # Some descriptors don't count toward repeats (e.g. hydrants opened in summer, for water mains).
    desc_all = snap["street_descriptor_hotspots"]
    excluded = desc_all[[d in rules.complaint_rule(t).get("repeat_exclude_descriptors", [])
                         for t, d in zip(desc_all["complaint_type"], desc_all["descriptor"])]]
    excluded = excluded.groupby(["boro_cd", "complaint_type", "street_name"])["n"].sum().rename("excluded")
    street = street.join(excluded, on=["boro_cd", "complaint_type", "street_name"])
    street["n"] = street["n"] - street["excluded"].fillna(0).astype(int)
    repeat = street[(street["class"] != rules.NOT_INFRASTRUCTURE) & ~own_rule
                    & (street["n"] >= esc["repeat_complaints_min"])]
    repeat = repeat.rename(columns={"n": "complaints", "street_name": "location"})

    # Rule 2: enough complaints whose wording itself signals a failed asset (cave-ins, sewer backups ...).
    desc = snap["street_descriptor_hotspots"].copy()
    desc = desc[[rules.classify(t, d) == rules.CAPITAL_SIGNAL for t, d in zip(desc["complaint_type"], desc["descriptor"])]]
    signal = desc.groupby(["boro_cd", "complaint_type", "street_name"], as_index=False).agg(
        signal_complaints=("n", "sum"),
        signal_descriptors=("descriptor", lambda s: "; ".join(sorted(set(s)))),
        lat=("lat", "mean"), lon=("lon", "mean"), first_seen=("first_seen", "min"), last_seen=("last_seen", "max"),
    ).rename(columns={"street_name": "location"})
    signal = signal[signal["signal_complaints"] >= esc["capital_signal_complaints_min"]]

    keys = ["boro_cd", "complaint_type", "location"]
    streets = repeat.merge(signal, on=keys, how="outer", suffixes=("", "_sig"))
    for col in ("lat", "lon", "first_seen", "last_seen"):
        streets[col] = streets[col].fillna(streets[f"{col}_sig"])
    totals = snap["street_hotspots"].rename(columns={"street_name": "location", "n": "street_total"})
    streets = streets.merge(totals[keys + ["street_total"]], on=keys, how="left")
    streets["complaints"] = streets["complaints"].fillna(streets["street_total"]).fillna(streets["signal_complaints"])
    is_repeat = streets["complaints"] >= esc["repeat_complaints_min"]
    is_signal = streets["signal_complaints"].fillna(0) >= esc["capital_signal_complaints_min"]
    streets["reason"] = (is_repeat.map({True: "repeat complaints", False: ""}) + " + "
                         + is_signal.map({True: "failed-asset complaints", False: ""})).str.strip(" +")

    # City-owned sidewalks: the City, not a private owner, must fix these. Pooled into one
    # candidate per district, since most lots have a single complaint.
    side_rule = rules.complaint_rule("Sidewalk Condition").get("city_owned_frontage_signal_descriptors", [])
    side = snap["city_owned_sidewalks"]
    side = side[side["descriptor"].isin(side_rule)].copy()
    side["lot"] = side["owner_name"].fillna("City property").str.title() + " (lot " + side["bbl"] + ")"
    side = side.groupby("boro_cd", as_index=False).agg(
        complaints=("n", "sum"), lots=("bbl", "nunique"), lat=("lat", "mean"), lon=("lon", "mean"),
        signal_descriptors=("descriptor", lambda s: "; ".join(sorted(set(s)))),
        lot_list=("lot", lambda s: "; ".join(sorted(set(s)))))
    side["complaint_type"] = "Sidewalk Condition"
    side["location"] = "City-owned sidewalks at " + side["lots"].astype(str) + " lots: " + side["lot_list"]
    side["reason"] = "City-owned sidewalks (pooled)"
    side["signal_complaints"] = side["complaints"]

    # Parks: only structure problems that keep coming back at the same park.
    park_rule = rules.complaint_rule("Maintenance or Facility")
    park = snap["park_hotspots"]
    park = park[park["descriptor"].isin(park_rule.get("escalation_descriptors", []))]
    park = park.groupby(["boro_cd", "park_facility_name", "complaint_type"], as_index=False).agg(
        complaints=("n", "sum"), lat=("lat", "mean"), lon=("lon", "mean"),
        first_seen=("first_seen", "min"), last_seen=("last_seen", "max"),
        signal_descriptors=("descriptor", lambda s: "; ".join(sorted(set(s)))))
    park = park[park["complaints"] >= esc["capital_signal_complaints_min"]].rename(
        columns={"park_facility_name": "location"})
    park["reason"] = "repeated park structure problems"
    park["signal_complaints"] = park["complaints"]

    out = pd.concat([streets, side, park], ignore_index=True)
    out["group"] = out["complaint_type"].map(rules.crosswalk_group)
    out = out[out["group"].notna()]  # e.g. street signs: repairs with no capital program behind them
    out["complaints"] = out["complaints"].astype(int)
    out["signal_complaints"] = out["signal_complaints"].fillna(0).astype(int)
    return out[CANDIDATE_COLUMNS].sort_values(["signal_complaints", "complaints"], ascending=False, ignore_index=True)


def district_need(snap: dict, candidates: pd.DataFrame) -> pd.DataFrame:
    """Per district and crosswalk group: infrastructure complaints, failed-asset complaints, capital candidates."""
    req = label_requests(snap["requests"])
    req = req[(req["class"] != rules.NOT_INFRASTRUCTURE) & req["group"].notna()]
    req = req.assign(signal=req["n"].where(req["class"] == rules.CAPITAL_SIGNAL, 0))
    need = req.groupby(["boro_cd", "group"], as_index=False).agg(requests=("n", "sum"), signal_requests=("signal", "sum"))
    cand = candidates.groupby(["boro_cd", "group"]).size().rename("candidates").reset_index()
    need = need.merge(cand, on=["boro_cd", "group"], how="outer").fillna(0)
    return need.astype({"requests": int, "signal_requests": int, "candidates": int})
