"""Operating vs. capital rules, read from definitions.yaml. Change rules there, not here."""

from functools import cache

import yaml

from civicequity import config

NOT_INFRASTRUCTURE = "not_infrastructure"
OPERATING_REPAIR = "operating_repair"
CAPITAL_SIGNAL = "capital_signal"


@cache
def load() -> dict:
    return yaml.safe_load(config.DEFINITIONS_FILE.read_text())


def complaint_rule(complaint_type: str) -> dict:
    return load()["complaint_classes"].get(complaint_type, {"class": load()["default_class"]})


def classify(complaint_type: str, descriptor: str | None = None) -> str:
    """not_infrastructure, operating_repair, or capital_signal for one complaint."""
    rule = complaint_rule(complaint_type)
    if rule["class"] == CAPITAL_SIGNAL or descriptor in rule.get("capital_signal_descriptors", []):
        return CAPITAL_SIGNAL
    return rule["class"]


def crosswalk_group(complaint_type: str) -> str | None:
    """Which capital program area (streets, sewers, ...) addresses this complaint type."""
    for group, spec in load()["crosswalk"].items():
        if complaint_type in spec["complaint_types"]:
            return group
    return None


def category_groups() -> dict[str, str]:
    """Capital plan category -> crosswalk group, for categories that address 311 complaints."""
    return {cat: group for group, spec in load()["crosswalk"].items() for cat in spec["capital_categories"]}


def escalation() -> dict:
    return load()["escalation"]
