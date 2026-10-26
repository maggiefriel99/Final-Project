"""Monitoring: warn staff when the share of flagged, held, or escalated requests, or spending, jumps.

A sudden jump usually means one of three things: an attack (many manipulation flags or sender-limit hits),
a broken prompt or model change (many holds or low-confidence answers), or a real citywide event (many
emergencies). The app can't tell which; it tells a person, who can.

Compares the last hour with the 7 days before it, from the audit and usage logs. Thresholds are deliberately
simple and listed in THRESHOLDS so they can be tuned.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from civicequity import audit, config, llm, logstore

WINDOW = timedelta(hours=1)
BASELINE = timedelta(days=7)
THRESHOLDS = {
    "min_requests_in_window": 10,  # fewer than this in the last hour: too few to judge a share
    "min_baseline_requests": 50,   # fewer than this in the previous 7 days: not enough history to compare
    "ratio": 2.0,                  # warn if the window's share is at least this many times the baseline ...
    "min_increase": 0.15,          # ... and at least 15 percentage points higher
    "spend_ratio": 2.0,            # warn if today's spending is twice the recent daily average ...
    "min_spend_usd": 1.00,         # ... and at least $1
    "cap_share": 0.80,             # warn when today's spending passes 80% of the daily cap
}

SIGNALS = {
    "held for a person": lambda r: r.get("route") == "human_review",
    "escalated as possible emergencies": lambda r: r.get("route") == "emergency",
    "flagged as possible manipulation": lambda r: bool((r.get("safety_checks") or {}).get("pattern_check", {})
                                                       .get("flagged")
                                                       or ((r.get("safety_checks") or {}).get("agent_flag") or {})
                                                       .get("suspicious_instructions")),
    "blocked by the provider's filter": lambda r: bool(((r.get("safety_checks") or {}).get("provider_filter") or {})
                                                       .get("blocked")),
    "stopped by the sender limit": lambda r: (r.get("stopped") or {}).get("by") == "sender limit",
}


def _time(r: dict) -> datetime:
    return datetime.fromisoformat(r["time"]).astimezone(timezone.utc)


def check(now: datetime | None = None) -> dict:
    """Returns {"warnings": [...], "window": {...}, "baseline": {...}} for the Audit page and the sidebar."""
    now = now or datetime.now(timezone.utc)
    runs = [r for r in audit.read() if r.get("flow") == "triage" and r.get("time")]
    window = [r for r in runs if _time(r) > now - WINDOW]
    base = [r for r in runs if now - WINDOW - BASELINE < _time(r) <= now - WINDOW]
    warnings, shares = [], {}
    enough = len(window) >= THRESHOLDS["min_requests_in_window"] and len(base) >= THRESHOLDS["min_baseline_requests"]
    for name, test in SIGNALS.items():
        w = sum(map(test, window)) / len(window) if window else 0.0
        b = sum(map(test, base)) / len(base) if base else 0.0
        shares[name] = {"last_hour": w, "baseline": b}
        if enough and w >= b * THRESHOLDS["ratio"] and w - b >= THRESHOLDS["min_increase"]:
            warnings.append(f"{w:.0%} of the last hour's {len(window)} requests were {name}, vs. {b:.0%} over the "
                            "previous 7 days.")
    hourly_base = len(base) / (BASELINE.total_seconds() / 3600) if base else 0.0
    if enough and len(window) >= 5 * hourly_base:
        warnings.append(f"{len(window)} requests in the last hour, vs. about {hourly_base:.1f} per hour over the "
                        "previous 7 days.")

    # Spending: today vs. the average of the previous 7 days (New York days), and vs. the cap.
    ny = ZoneInfo("America/New_York")
    today = now.astimezone(ny).date()
    by_day = {}
    for r in logstore.read(llm.USAGE_LOG):
        d = datetime.fromisoformat(r["time"]).astimezone(ny).date()
        by_day[d] = by_day.get(d, 0.0) + r.get("cost_usd", 0.0)
    spent = by_day.get(today, 0.0)
    prior = [by_day.get(today - timedelta(days=i), 0.0) for i in range(1, 8)]
    avg = sum(prior) / 7
    if spent >= THRESHOLDS["min_spend_usd"] and spent >= THRESHOLDS["spend_ratio"] * avg:
        warnings.append(f"${spent:.2f} spent on AI today, vs. a ${avg:.2f} daily average over the previous 7 days.")
    if spent >= THRESHOLDS["cap_share"] * config.DAILY_BUDGET_USD:
        warnings.append(f"Today's AI spending (${spent:.2f}) has passed {THRESHOLDS['cap_share']:.0%} of the "
                        f"${config.DAILY_BUDGET_USD:.2f} daily cap.")
    return {"warnings": warnings, "enough_history": len(base) >= THRESHOLDS["min_baseline_requests"],
            "requests_last_hour": len(window), "requests_baseline": len(base),
            "shares": shares, "spent_today": spent, "daily_average": avg}
