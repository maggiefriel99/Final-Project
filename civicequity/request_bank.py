"""Bank of past triage results, so a repeat of an earlier request costs no tokens.

Only the agent's reading of the text is reused (complaint type, descriptor, street). The route is
still decided fresh by the rules and current data. Requests that were flagged or held for review
are never stored, and every new request still goes through the injection check first.
"""

import hashlib
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

from civicequity import config, logstore

BANK_FILE = config.LOG_DIR / "request_bank.jsonl"
SIMILARITY_MIN = 0.90  # share of matching words for a near-duplicate


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _key(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode()).hexdigest()[:16]


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text)


def _name_like(text: str) -> list[bool]:
    """For each word: does it look like a name or number? Capitalized words count unless they start a sentence."""
    out = []
    for m in re.finditer(r"\w+", text):
        before = text[:m.start()].rstrip()
        sentence_start = not before or before[-1] in ".!?"
        w = m.group(0)
        out.append(any(ch.isdigit() for ch in w) or (w[:1].isupper() and not sentence_start))
    return out


def _changes_are_minor(old: str, new: str) -> bool:
    """True if every word that differs is ordinary wording, not a number or a name.

    "Hi, there is a pothole" vs "Hello, there is a pothole" is minor. A different house number,
    street, or cross street is not: those words contain digits or start with a capital letter.
    """
    a, b = _words(old), _words(new)
    name_a, name_b = _name_like(old), _name_like(new)
    sm = SequenceMatcher(None, [w.lower() for w in a], [w.lower() for w in b], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal" and (any(name_a[i1:i2]) or any(name_b[j1:j2])):
            return False
    return True


def _entries() -> list[dict]:
    return logstore.read(BANK_FILE)


def find(text: str) -> dict | None:
    """The banked entry for this request (exact or near-duplicate), with match details, or None."""
    key = _key(text)
    norm = _normalize(text)
    best = None
    for e in _entries():
        if e["key"] == key:
            return {**e, "match": "exact", "similarity": 1.0}
        sm = SequenceMatcher(None, _words(_normalize(e["text"])), _words(norm), autojunk=False)
        if sm.quick_ratio() < SIMILARITY_MIN:
            continue
        score = sm.ratio()
        if score >= SIMILARITY_MIN and _changes_are_minor(e["text"], text) and (not best or score > best["similarity"]):
            best = {**e, "match": "near-duplicate", "similarity": round(score, 3)}
    return best


def add(text: str, decision: dict, run_id: str) -> None:
    entry = {"key": _key(text), "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "run_id": run_id, "text": text, "decision": decision}
    logstore.append(BANK_FILE, entry)
