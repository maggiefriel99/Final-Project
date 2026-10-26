"""Per-sender limits, so one source can't use up the day's AI budget and stall everyone else.

Each request can carry a sender id (in live use: the 311 account, phone, or app install; in the demo: the
browser session). Only a salted hash of it is stored (logs/rate.jsonl), never the id itself. A sender over
the limit is not sent to the AI: the request is held for a person, and the code danger-word check still
runs, so a possible emergency is still flagged.

The daily spending cap has the same weakness at citywide scale (enough requests stop all AI triage), so the
Life-Safety Screener alone may keep running into a small reserve above the cap (config.SAFETY_RESERVE_USD);
see civicequity.harness.
"""

import hashlib
import os
from datetime import datetime, timedelta, timezone

from civicequity import config, logstore

RATE_FILE = config.LOG_DIR / "rate.jsonl"


def sender_hash(sender: str) -> str:
    salt = os.getenv("CIVICEQUITY_SENDER_SALT", "civicequity")
    return hashlib.sha256(f"{salt}:{sender}".encode()).hexdigest()[:16]


def check_and_record(sender: str | None) -> dict:
    """Count this request against the sender's limits. Returns {"allowed", "last_hour", "last_day", ...}."""
    if not sender:
        return {"allowed": True, "sender": None}
    h = sender_hash(sender)
    now = datetime.now(timezone.utc)
    times = [datetime.fromisoformat(r["time"]) for r in logstore.read(RATE_FILE) if r.get("sender") == h]
    last_hour = sum(t > now - timedelta(hours=1) for t in times)
    last_day = sum(t > now - timedelta(days=1) for t in times)
    allowed = last_hour < config.SENDER_LIMIT_PER_HOUR and last_day < config.SENDER_LIMIT_PER_DAY
    logstore.append(RATE_FILE, {"time": now.isoformat(timespec="seconds"), "sender": h, "allowed": allowed})
    return {"allowed": allowed, "sender": h, "last_hour": last_hour + 1, "last_day": last_day + 1,
            "limit_per_hour": config.SENDER_LIMIT_PER_HOUR, "limit_per_day": config.SENDER_LIMIT_PER_DAY}
