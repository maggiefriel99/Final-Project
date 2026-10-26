"""Audit log: one record per run (and per human approval) in logs/audit_log.jsonl.

Each run records what came in (personal information already removed), the result of every safety
check, what the agent answered, the route and why, tokens and cost, and whether the run was stopped
or blocked and for what reason. Records are append-only.
"""

from datetime import datetime, timezone

from civicequity import config, logstore

AUDIT_LOG = config.LOG_DIR / "audit_log.jsonl"


def record(event: dict) -> dict:
    event = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event}
    return logstore.append(AUDIT_LOG, event)


def read(limit: int | None = None) -> list[dict]:
    rows = logstore.read(AUDIT_LOG)
    return rows[-limit:] if limit else rows
