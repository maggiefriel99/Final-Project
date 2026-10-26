"""Queue of agency alerts waiting for a person. Nothing is ever sent automatically.

Each alert is one line in logs/alerts.jsonl; approving or rejecting appends a new version of it.
Every decision is also written to the audit log with who made it and when.
"""

import uuid
from datetime import datetime, timezone

from civicequity import audit, config, logstore

ALERTS_FILE = config.LOG_DIR / "alerts.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append(alert: dict) -> dict:
    return logstore.append(ALERTS_FILE, alert)


def queue(**fields) -> dict:
    """Add a drafted alert. Drafts that failed checks, or that the agent never wrote, need a person to write or fix them."""
    needs_fix = bool(fields.get("problems")) or fields.get("draft") is None
    status = fields.pop("status", None)  # only the fixed 911 message to the resident sets its own status
    return _append({
        "alert_id": f"alert-{uuid.uuid4().hex[:8]}", "created": _now(),
        "status": status or ("needs editing" if needs_fix else "pending approval"), **fields,
    })


def all_alerts() -> list[dict]:
    """Latest version of every alert, newest first."""
    latest = {}
    for a in logstore.read(ALERTS_FILE):
        latest[a["alert_id"]] = a
    return sorted(latest.values(), key=lambda a: a["created"], reverse=True)


def get(alert_id: str) -> dict:
    return next(a for a in all_alerts() if a["alert_id"] == alert_id)


def decide(alert_id: str, decision: str, reviewer: str, edited_draft: dict | None = None, note: str = "") -> dict:
    """A person approves or rejects an alert (optionally after editing it). Recorded in the audit log."""
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    if not reviewer.strip():
        raise ValueError("A reviewer name is required.")
    alert = get(alert_id)
    if alert["status"] in ("approved", "rejected"):
        raise ValueError(f"Alert {alert_id} was already {alert['status']}.")
    updated = {**alert, "status": decision, "reviewer": reviewer.strip(), "decided": _now(), "review_note": note,
               "edited_by_reviewer": edited_draft is not None, "draft": edited_draft or alert["draft"]}
    _append(updated)
    audit.record({"flow": "human_review", "alert_id": alert_id, "decision": decision, "reviewer": reviewer.strip(),
                  "edited_by_reviewer": edited_draft is not None, "note": note, "agency": alert["agency"],
                  "triage_run_id": alert["triage_run_id"]})
    return updated
