"""Re-check every sourced quote in advice.yaml, priorities.yaml, and emergencies.yaml against the live official pages.

    python analysis/verify_sources.py

Fetches each page, strips the HTML, normalizes quote marks, dashes, and spaces, and confirms the quote
appears word for word. Prints a table and exits with status 1 if anything is missing.
"""

import re
import sys
import time
from html import unescape
from io import BytesIO
from pathlib import Path

import requests
import yaml
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; CivicEquity source check)"}


def norm(s: str) -> str:
    s = unescape(s)
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("‑", "-"), ("–", "-"), (" ", " ")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip().lower()


def page_text(url: str, cache: dict) -> str:
    if url not in cache:
        for attempt in range(3):  # official sites are sometimes slow; a single timeout is not a missing quote
            try:
                r = requests.get(url, headers=HEADERS, timeout=60)
                r.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(5)
        if url.lower().endswith(".pdf") or "pdf" in r.headers.get("content-type", ""):
            cache[url] = norm(" ".join(p.extract_text() or "" for p in PdfReader(BytesIO(r.content)).pages))
        else:
            html = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text, flags=re.S)
            cache[url] = norm(re.sub(r"<[^>]+>", " ", html))
    return cache[url]


def main():
    checks = []
    advice = yaml.safe_load((ROOT / "advice.yaml").read_text())["entries"]
    checks += [(f"advice:{k}", v["source_quote"], v["url"]) for k, v in advice.items()]
    checks += [(f"advice:{k} (more)", m["source_quote"], m["url"]) for k, v in advice.items()
               for m in v.get("more_sources", [])]
    pr = yaml.safe_load((ROOT / "priorities.yaml").read_text())
    for k, p in pr["priorities"].items():
        for e in p["evidence"]:
            if "quote" in e:
                checks.append((f"priority:{k}", e["quote"], pr["sources"][e["source"]]["url"]))
    em = yaml.safe_load((ROOT / "emergencies.yaml").read_text())
    msg = em["resident_message"]
    checks.append(("emergency:resident_message", msg["source_quote"], em["sources"][msg["source"]]["url"]))
    # Every CIMS agency role and incident type used must appear in the CIMS charts.
    cims = em["sources"]["cims"]["url"]
    for role in [em["coordinating_agency"]["competency"], *em["competencies"].values()]:
        checks.append(("emergency:cims_role", role, cims))
    for name, inc in em["incidents"].items():
        if inc["command"]:
            agencies = inc.get("cims_primary_text") or ", ".join(inc["primary"])
            checks.append(("emergency:cims_incident", inc.get("cims_match") or f"{name} {inc['command']} {agencies}", cims))
            if inc.get("note", "").startswith("CIMS footnote: "):
                checks.append(("emergency:cims_note", inc["note"].removeprefix("CIMS footnote: "), cims))
    cache, failed = {}, 0
    for name, quote, url in checks:
        try:
            ok = norm(quote) in page_text(url, cache)
        except requests.RequestException as e:
            ok, quote = False, f"{quote} (could not fetch: {type(e).__name__})"
        failed += not ok
        print(f"{'OK     ' if ok else 'MISSING'} {name:28s} {quote[:70]}")
    print(f"\n{len(checks) - failed} of {len(checks)} quotes found word for word.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
