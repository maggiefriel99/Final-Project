"""Build the self-contained report.html and slides.html (every image embedded, so they open anywhere).

    python report/build.py [--github URL]

Sources: report/report_source.html, report/slides_source.html, report/diagram.svg, report/img/*.jpg.
Placeholders: {{img:NAME}} -> the image as a data URI, {{diagram}} -> the inline SVG, {{GITHUB_LINK}} -> the repo link.
"""

import argparse
import base64
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def fill(text: str, github: str | None) -> str:
    def img(m):
        data = base64.b64encode((HERE / "img" / f"{m.group(1)}.jpg").read_bytes()).decode()
        return f"data:image/jpeg;base64,{data}"
    text = re.sub(r"\{\{img:([a-z0-9_]+)\}\}", img, text)
    text = text.replace("{{diagram}}", (HERE / "diagram.svg").read_text())
    link = f'<a href="{github}">{github}</a>' if github else "link added at submission"
    return text.replace("{{GITHUB_LINK}}", link)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--github", default=None)
    a = ap.parse_args()
    for src, out in (("report_source.html", "report.html"), ("slides_source.html", "slides.html")):
        if (HERE / src).exists():
            html = fill((HERE / src).read_text(), a.github)
            left = re.findall(r"\{\{[^}]+\}\}", html)
            if left:
                raise SystemExit(f"{src}: unfilled placeholders {left}")
            (ROOT / out).write_text(html)
            print(f"wrote {out} ({len(html) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
