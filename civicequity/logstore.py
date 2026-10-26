"""Where every log is written and read: owner-only files, optional encryption, and retention.

- Permissions: the logs folder is readable only by the account running the app (0700), and each log file
  only by its owner (0600).
- Encryption (optional): if CIVICEQUITY_LOG_KEY is set in .env (a Fernet key; make one with
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`), every new
  record is encrypted. Without a key the app works exactly as before, with plain JSON lines. Old plain lines
  stay readable after a key is added; encrypted lines can't be read without the key and are skipped.
- Retention: records older than CIVICEQUITY_LOG_RETENTION_DAYS (default 180) are deleted when the app starts
  (prune_all). Alerts still waiting for a person are never deleted, however old.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from functools import cache
from pathlib import Path

from civicequity import config

ENC_PREFIX = "enc:"


@cache
def _fernet():
    key = os.getenv("CIVICEQUITY_LOG_KEY", "").strip()
    if not key:
        return None
    from cryptography.fernet import Fernet  # only needed when encryption is on
    return Fernet(key.encode())


def encryption_on() -> bool:
    return _fernet() is not None


def _private_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(config.LOG_DIR, 0o700)
        if path.parent != config.LOG_DIR:
            os.chmod(path.parent, 0o700)
    except OSError:
        pass


def _encode(text: str) -> str:
    f = _fernet()
    return ENC_PREFIX + f.encrypt(text.encode()).decode() if f else text


def _decode(line: str) -> str | None:
    if not line.startswith(ENC_PREFIX):
        return line
    f = _fernet()
    if f is None:
        return None  # encrypted, and no key here: skip
    from cryptography.fernet import InvalidToken
    try:
        return f.decrypt(line[len(ENC_PREFIX):].encode()).decode()
    except InvalidToken:
        return None


def append(path: Path, record: dict) -> dict:
    _private_dir(path)
    new = not path.exists()
    with path.open("a") as fh:
        fh.write(_encode(json.dumps(record, default=str)) + "\n")
    if new:
        os.chmod(path, 0o600)
    return record


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            text = _decode(line)
            if text is not None:
                out.append(json.loads(text))
    return out


def write_json(path: Path, obj) -> None:
    _private_dir(path)
    path.write_text(_encode(json.dumps(obj, indent=1, default=str)))
    os.chmod(path, 0o600)


def read_json(path: Path):
    text = _decode(path.read_text())
    return None if text is None else json.loads(text)


def _rewrite(path: Path, keep_lines: list[str]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(line + "\n" for line in keep_lines))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def prune(path: Path, days: int, keep_anyway=lambda r: False, time_key: str = "time") -> int:
    """Delete records older than `days`. Lines that can't be read here (encrypted, no key) are kept as is."""
    if not path.exists():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    keep, dropped = [], 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        text = _decode(line)
        if text is None:
            keep.append(line)
            continue
        r = json.loads(text)
        stamp = r.get(time_key)
        try:
            old = stamp and datetime.fromisoformat(stamp).astimezone(timezone.utc) < cutoff
        except ValueError:
            old = False
        if old and not keep_anyway(r):
            dropped += 1
        else:
            keep.append(line)
    if dropped:
        _rewrite(path, keep)
    return dropped


def prune_all(days: int | None = None) -> dict:
    """Apply the retention period to every log. Returns how many records were deleted from each."""
    days = days or config.LOG_RETENTION_DAYS
    d = config.LOG_DIR
    out = {
        "audit_log": prune(d / "audit_log.jsonl", days),
        "usage": prune(d / "usage.jsonl", days),
        "request_bank": prune(d / "request_bank.jsonl", days),
        "rate": prune(d / "rate.jsonl", 2),  # sender counts only matter for a day
        # Alerts: only decided ones age out; anything still waiting for a person stays.
        "alerts": _prune_alerts(d / "alerts.jsonl", days),
    }
    memos = d / "memos"
    cutoff = datetime.now().timestamp() - days * 86400
    out["memos"] = 0
    if memos.exists():
        for f in memos.glob("*.json"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
                out["memos"] += 1
    return out


def _prune_alerts(path: Path, days: int) -> int:
    if not path.exists():
        return 0
    latest = {}
    for r in read(path):
        latest[r["alert_id"]] = r
    still_open = {k for k, r in latest.items() if r["status"] in ("pending approval", "needs editing")}
    return prune(path, days, keep_anyway=lambda r: r.get("alert_id") in still_open, time_key="created")
