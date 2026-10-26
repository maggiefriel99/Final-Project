"""Remove personal information from constituent requests before any model, bank, or log sees it.

Street addresses are kept on purpose: they say where the problem is, and the City needs them to
fix it. What goes is information about the person: email, phone, names, and ID or account numbers.
Regex-based, so it catches common formats, not every possible one (see the report's Limits section).
"""

import re

PATTERNS = [
    ("EMAIL", r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    ("PHONE", r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"),
    ("SSN", r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
    ("ID NUMBER", r"(?<![\w-])\d{9,}(?![\w-])"),  # account, case, or card numbers
]

NAME = r"[A-Z][a-z'-]+(?:\s+[A-Z][a-z'-]+){0,2}"
NAME_PATTERNS = [
    # "My name is Jane Doe", "name: Jane Doe"
    rf"(?i:\bmy name is|\bname\s*:)\s+({NAME})",
    # Sign-offs: "Thanks, Jane" / "Sincerely,\nJane Doe"
    rf"(?i:\b(?:thanks|thank you|sincerely|regards|best regards|best|cheers|respectfully))\s*,?\s*\n?\s*-?\s*({NAME})\s*$",
]


def redact(text: str) -> tuple[str, dict[str, int]]:
    """Returns the redacted text and how many of each kind were removed."""
    counts: dict[str, int] = {}
    for label, pattern in PATTERNS:
        text, n = re.subn(pattern, f"[{label}]", text)
        if n:
            counts[label] = counts.get(label, 0) + n
    for pattern in NAME_PATTERNS:
        def _replace(m):
            counts["NAME"] = counts.get("NAME", 0) + 1
            return m.group(0)[: m.start(1) - m.start(0)] + "[NAME]"
        text = re.sub(pattern, _replace, text, flags=re.M)
    return text, counts
