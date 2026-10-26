"""Code check of a written memo against the facts it was given.

1. Every number in the memo must equal a number in the facts. "$18.7M" and "$18.7 million" count as
   the same; "$19M" for a fact of "$18.7M" does not.
2. Any quotation longer than six words must appear word for word in the facts (no invented quotes).
"""

import json
import re

NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?(?:\s?(?:[KMB]\b|million\b|billion\b|thousand\b))?(?:x\b|%)?", re.I)
UNITS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}
QUOTE = re.compile(r"[\"“]([^\"”]{20,})[\"”]")


def canonical(token: str) -> tuple[str, float]:
    t = token.strip().lower()
    kind = "money" if t.startswith("$") else "ratio" if t.endswith("x") else "pct" if t.endswith("%") else "num"
    t = t.lstrip("$").rstrip("x%").strip()
    m = re.match(r"([\d,]*\.?\d+)\s*([a-z]*)", t)
    value = float(m.group(1).replace(",", "")) * UNITS.get(m.group(2), 1)
    return kind, round(value, 6)


def numbers_in(text: str) -> list[str]:
    return [m.group(0).strip() for m in NUMBER.finditer(text)]


def _strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)
    elif obj is not None:
        yield str(obj)


def check(memo_text: str, facts: dict) -> dict:
    fact_text = "\n".join(_strings(facts))
    allowed = {canonical(n) for n in numbers_in(fact_text)}
    allowed_values = {v for _, v in allowed}
    unsupported = []
    for tok in numbers_in(memo_text):
        kind, value = canonical(tok)
        # Plain numbers may match a fact written with a unit or sign (e.g. "42" vs "42 per 1,000").
        ok = (kind, value) in allowed or (kind == "num" and value in allowed_values)
        if not ok:
            unsupported.append(tok)
    norm = lambda s: re.sub(r"\s+", " ", s.replace("’", "'").replace("‘", "'")).strip().lower()
    quotes = [q for q in QUOTE.findall(memo_text) if len(q.split()) > 6]
    invented = [q for q in quotes if norm(q) not in norm(fact_text)]
    return {"numbers_checked": len(numbers_in(memo_text)), "unsupported_numbers": sorted(set(unsupported)),
            "quotes_checked": len(quotes), "unsupported_quotes": invented,
            "passed": not unsupported and not invented}


def facts_json(facts: dict) -> str:
    return json.dumps(facts, indent=1, default=str)
