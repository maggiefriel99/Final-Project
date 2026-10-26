"""Look and feel, following the NYC design system (designsystem.nyc.gov): black and white for most
of the design, Blue 40 (#103FEF) only where people act, status colors only for status, Clash Grotesk
for headings and Noto Sans for text. The NYC logo itself is a City trademark and is not used.

Everything that puts text into HTML goes through esc(): request text and agent output are untrusted.
"""

import re
from html import escape

import streamlit as st

from civicequity.ui.skyline import skyline_svg

BLUE, BLUE_DARK, BLUE_LIGHT = "#103FEF", "#050560", "#B5DBFF"
INFO, SUCCESS, CAUTION, ERROR = "#70BAFF", "#00B703", "#FFB320", "#EC131E"

ROUTES = {  # route -> (label, icon, color, text color)
    "emergency": ("Possible emergency: call 911", "e911_emergency", ERROR, "#FFFFFF"),
    "advice": ("Advice to the resident", "tips_and_updates", BLUE_LIGHT, "#000000"),
    "capital": ("Capital need", "construction", BLUE, "#FFFFFF"),
    "operating": ("Operating repair", "build", "#000000", "#FFFFFF"),
    "not_city": ("Not a City matter", "block", "#777777", "#FFFFFF"),
    "human_review": ("Held for human review", "front_hand", CAUTION, "#000000"),
}
AGENCY_ICONS = {"DOT": "traffic", "DEP": "water_drop", "DPR": "park", "DSNY": "delete", "FDNY": "local_fire_department",
                "NYPD": "local_police", "NYCEM": "emergency", "DOB": "apartment", "HPD": "home",
                "DOHMH": "health_and_safety"}
PRIORITY = {"urgent": ERROR, "elevated": CAUTION, "routine": "#777777"}

CSS = f"""
<style>
@import url('https://api.fontshare.com/v2/css?f[]=clash-grotesk@500,600&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans:wght@400;500;600;700&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,400..600,0..1,0&display=block');

html, body, [class*="st-"], .stMarkdown, p, li, label, input, textarea, button {{
  font-family: 'Noto Sans', system-ui, -apple-system, 'Segoe UI', sans-serif;
}}
h1, h2, h3, h4, .ce-display {{ font-family: 'Clash Grotesk', 'Noto Sans', sans-serif !important; letter-spacing: 0; }}
h1 {{ font-weight: 600 !important; }}
h2, h3 {{ font-weight: 600 !important; }}
.block-container {{ padding-top: 2.2rem; max-width: 1240px; }}
[data-testid="stSidebar"] {{ background: #000; }}
[data-testid="stSidebar"] * {{ color: #fff; }}
[data-testid="stSidebarNav"] a span {{ font-weight: 500; }}
[data-testid="stSidebarNav"] a[aria-current="page"] {{ background: {BLUE} !important; border-radius: 8px; }}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {{ color: #CCCCCC !important; }}

/* Streamlit's own icons share class names with the font rule above; give them their icon font back */
[data-testid="stIconMaterial"], [data-testid*="Icon"] {{ font-family: 'Material Symbols Rounded' !important; }}
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] {{ background: transparent !important;
  border: 1px solid #555 !important; color: #fff !important; }}
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]:hover {{ border-color: {BLUE_LIGHT} !important;
  color: {BLUE_LIGHT} !important; }}

.material-symbols-rounded {{ font-family: 'Material Symbols Rounded'; font-weight: 500; font-style: normal;
  line-height: 1; display: inline-block; vertical-align: middle; font-variation-settings: 'FILL' 1; }}

/* Wordmark: block capitals, no spacing, after the NYC logo's style (not the logo itself) */
.ce-wordmark {{ font-family: 'Clash Grotesk', sans-serif; font-weight: 600; font-size: 30px; line-height: .86;
  letter-spacing: -0.5px; color: #fff; text-transform: uppercase; }}
.ce-wordmark span {{ color: {BLUE_LIGHT}; }}
.ce-tag {{ font-size: 12px; color: #CCCCCC; margin-top: 6px; letter-spacing: .4px; text-transform: uppercase; }}

/* Hero */
.ce-hero {{ position: relative; overflow: hidden; border-radius: 18px; color: #fff; padding: 44px 44px 0 44px;
  background: radial-gradient(1200px 400px at 75% 120%, rgba(255,122,26,.20), transparent 60%),
              linear-gradient(180deg, #000 0%, {BLUE_DARK} 100%); margin-bottom: 26px; }}
.ce-hero .kicker {{ display: inline-flex; gap: 8px; align-items: center; background: rgba(255,255,255,.08);
  border: 1px solid rgba(255,255,255,.18); padding: 6px 12px; border-radius: 99px; font-size: 13px; }}
.ce-hero h1 {{ font-size: 60px; line-height: 1.04; margin: 18px 0 10px 0; color: #fff; max-width: 820px; }}
.ce-hero h1 em {{ font-style: normal; color: {BLUE_LIGHT}; }}
.ce-hero p.lead {{ font-size: 20px; line-height: 1.5; color: #DDDDDD; max-width: 720px; font-weight: 500; }}
.ce-hero .sky {{ height: 210px; margin: 10px -44px 0 -44px; }}
.ce-hero .sky svg, .ce-strip svg {{ width: 100%; height: 100%; display: block; }}

/* Page header with a light skyline strip */
.ce-head {{ position: relative; border-bottom: 2px solid #000; margin-bottom: 22px; padding-bottom: 14px; }}
.ce-head .ce-strip {{ position: absolute; right: 0; bottom: 0; width: 58%; height: 96px; z-index: 0; opacity: .9; }}
.ce-head .txt {{ position: relative; z-index: 1; }}
.ce-head h1 {{ font-size: 44px; margin: 0; padding: 0; line-height: 1.1; }}
.ce-head.compact {{ margin-bottom: 14px; padding-bottom: 10px; }}
.ce-head.compact h1 {{ font-size: 34px; }}
.ce-head.compact p {{ font-size: 15px; margin-top: 2px; }}
.ce-head.compact .ce-strip {{ height: 70px; }}
.ce-head p {{ color: #555; font-size: 17px; margin: 6px 0 0 0; max-width: 640px; font-weight: 500; }}
.ce-head .eyebrow {{ display: inline-flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 600;
  letter-spacing: .5px; text-transform: uppercase; color: {BLUE}; }}

/* Cards, stats, badges */
.ce-card {{ background: #fff; border: 1px solid #DDDDDD; border-radius: 14px; padding: 18px 20px;
  box-shadow: 0 1px 2px rgba(0,0,0,.04); height: 100%; }}
.ce-card h4 {{ margin: 0 0 6px 0; font-size: 20px; }}
.ce-card p {{ margin: 0; color: #333; }}
.ce-stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; margin: 4px 0 22px 0; }}
.ce-stat {{ background: #fff; border: 1px solid #DDDDDD; border-radius: 14px; padding: 16px 18px; position: relative; }}
.ce-stat .ic {{ width: 38px; height: 38px; border-radius: 10px; background: #000; color: #fff;
  display: flex; align-items: center; justify-content: center; margin-bottom: 12px; }}
.ce-stat .ic.blue {{ background: {BLUE}; }}
.ce-stat .v {{ font-family: 'Clash Grotesk', sans-serif; font-weight: 600; font-size: 34px; line-height: 1; color: #000; }}
.ce-stat .l {{ color: #555; font-size: 14px; margin-top: 6px; font-weight: 500; }}
.ce-badge {{ display: inline-flex; align-items: center; gap: 5px; border-radius: 99px; padding: 3px 10px;
  font-size: 13px; font-weight: 600; line-height: 1.5; white-space: nowrap; }}
.ce-route {{ display: flex; align-items: center; gap: 14px; border-radius: 14px; padding: 16px 20px; margin-bottom: 14px; }}
.ce-route .t {{ font-family: 'Clash Grotesk', sans-serif; font-weight: 600; font-size: 26px; line-height: 1.1; }}
.ce-route .r {{ font-size: 15px; opacity: .92; font-weight: 500; }}
.ce-kv {{ display: grid; grid-template-columns: max-content 1fr; gap: 6px 14px; font-size: 15px; }}
.ce-kv .k {{ color: #555; font-weight: 500; }}
.ce-kv .v {{ color: #000; font-weight: 600; }}
.ce-checks {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.ce-check {{ display: inline-flex; align-items: center; gap: 6px; border: 1px solid #DDDDDD; border-radius: 10px;
  padding: 7px 11px; font-size: 13.5px; font-weight: 500; background: #fff; }}
.ce-step {{ display: flex; gap: 14px; align-items: flex-start; }}
.ce-step .n {{ min-width: 44px; height: 44px; border-radius: 12px; background: #000; color: #fff;
  display: flex; align-items: center; justify-content: center; }}
.ce-step h4 {{ margin: 2px 0 4px 0; font-size: 19px; }}
.ce-step p {{ margin: 0; color: #555; font-size: 15px; }}
.ce-flow {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 14px; }}
.ce-quote {{ border-left: 4px solid {BLUE}; padding: 10px 14px; background: #F6F7FF; border-radius: 0 10px 10px 0;
  color: #222; font-size: 15px; white-space: pre-wrap; }}
.ce-meter {{ height: 8px; background: #333; border-radius: 99px; overflow: hidden; margin-top: 6px; }}
.ce-meter > div {{ height: 100%; background: {BLUE_LIGHT}; }}
.ce-foot {{ color: #777; font-size: 13px; margin-top: 34px; border-top: 1px solid #DDDDDD; padding-top: 12px; }}

/* Progress skyscraper */
@keyframes ceTwinkle {{ 0%, 100% {{ fill: #23285E; }} 50% {{ fill: {CAUTION}; }} }}
@keyframes ceBeacon {{ 0%, 100% {{ opacity: .35; }} 50% {{ opacity: 1; }} }}
@keyframes ceRise {{ from {{ opacity: 0; transform: translateY(6px); }} to {{ opacity: 1; transform: none; }} }}
@keyframes ceSpin {{ to {{ transform: rotate(360deg); }} }}
.ce-tower {{ display: flex; gap: 22px; align-items: flex-end; border-radius: 16px; padding: 20px 22px 18px 22px;
  background: radial-gradient(300px 160px at 20% 110%, rgba(255,122,26,.22), transparent 70%),
              linear-gradient(180deg, #000 0%, {BLUE_DARK} 100%); color: #fff; margin-top: 12px; }}
.ce-tower svg {{ flex: 0 0 auto; }}
.ce-tower .win-active {{ animation: ceTwinkle 1.1s ease-in-out infinite; }}
.ce-tower .beacon {{ animation: ceBeacon 1.4s ease-in-out infinite; }}
.ce-tower ol {{ list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 9px; flex: 1; }}
.ce-tower li {{ display: flex; gap: 9px; align-items: center; font-size: 14.5px; font-weight: 500; color: #888; }}
.ce-tower li.done {{ color: #fff; }}
.ce-tower li.active {{ color: {CAUTION}; font-weight: 600; }}
.ce-tower li.skipped {{ color: #555; text-decoration: line-through; }}
.ce-tower li .spin {{ display: inline-block; animation: ceSpin 1s linear infinite; }}
.ce-tower .title {{ font-family: 'Clash Grotesk', sans-serif; font-weight: 600; font-size: 20px; margin-bottom: 12px;
  animation: ceRise .4s ease-out; }}
.ce-tower .sub {{ color: #CCC; font-size: 13px; margin-top: 12px; }}
.ce-tower.compact {{ padding: 14px 18px 12px 16px; gap: 16px; margin: 0 0 12px 0; align-items: center; }}
.ce-tower.compact .title {{ font-size: 17px; margin-bottom: 8px; }}
.ce-tower.compact ol {{ gap: 5px; }}
.ce-tower.compact li {{ font-size: 13.5px; }}
.ce-tower.compact .sub {{ margin-top: 8px; font-size: 12.5px; }}

/* Streamlit widgets in NYC style */
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primaryFormSubmit"] {{
  background: {BLUE}; border-color: {BLUE}; border-radius: 10px; font-weight: 600; }}
.stButton > button[kind="primary"]:hover {{ background: {BLUE_DARK}; border-color: {BLUE_DARK}; }}
.stButton > button {{ border-radius: 10px; font-weight: 600; }}
[data-testid="stExpander"] details {{ border-radius: 12px; }}
textarea {{ font-size: 15.5px !important; line-height: 1.55 !important; }}
</style>
"""


def esc(value) -> str:
    return escape("" if value is None else str(value))


MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+!|<>~$:-])")


def md(value) -> str:
    """Text from a request, an agent, or a reviewer, made safe to show in Streamlit markdown: no links,
    images (which a browser would load), formatting, or LaTeX can come from it."""
    return MD_SPECIAL.sub(r"\\\1", "" if value is None else str(value))


def icon(name: str, size: int = 20, color: str | None = None) -> str:
    style = f"font-size:{size}px;" + (f"color:{color};" if color else "")
    return f'<span class="material-symbols-rounded" style="{style}">{esc(name)}</span>'


def inject() -> None:
    st.html(CSS)


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def sidebar_brand(data_date: str, spent_today: float, daily_cap: float) -> None:
    pct = min(100, spent_today / daily_cap * 100) if daily_cap else 0
    with st.sidebar:
        html(f'<div style="padding:4px 0 18px 0"><div class="ce-wordmark">Civic<br><span>Equity</span></div>'
             f'<div class="ce-tag">NYC capital planning, powered by 311</div></div>')
    with st.sidebar:
        st.caption(f"Data as of **{esc(data_date)}**")
        html(f'<div style="font-size:12.5px;color:#CCC">AI spend today: <b style="color:#fff">${spent_today:.4f}</b>'
             f' of ${daily_cap:.0f} cap</div><div class="ce-meter"><div style="width:{max(pct, 1):.1f}%"></div></div>')


def hero(kicker: str, title_html: str, lead: str) -> None:
    """title_html is trusted markup written in code (it may contain <em> for the accent color)."""
    html(f'<div class="ce-hero"><span class="kicker">{icon("location_city", 18)} {esc(kicker)}</span>'
         f'<h1>{title_html}</h1><p class="lead">{esc(lead)}</p>'
         f'<div class="sky">{skyline_svg("hero")}</div></div>')


def page_header(eyebrow: str, eyebrow_icon: str, title: str, subtitle: str, compact: bool = False) -> None:
    html(f'<div class="ce-head{" compact" if compact else ""}"><div class="ce-strip">{skyline_svg("strip", seed=11)}</div>'
         f'<div class="txt"><span class="eyebrow">{icon(eyebrow_icon, 16)} {esc(eyebrow)}</span>'
         f'<h1>{esc(title)}</h1><p>{esc(subtitle)}</p></div></div>')


def stats(items: list[tuple[str, str, str]], blue_first: bool = True) -> None:
    """items: (icon, value, label)."""
    cards = "".join(
        f'<div class="ce-stat"><div class="ic {"blue" if blue_first and i == 0 else ""}">{icon(ic, 22)}</div>'
        f'<div class="v">{esc(v)}</div><div class="l">{esc(l)}</div></div>'
        for i, (ic, v, l) in enumerate(items))
    html(f'<div class="ce-stats">{cards}</div>')


def badge(text: str, color: str, text_color: str = "#fff", icon_name: str | None = None) -> str:
    ic = icon(icon_name, 15, text_color) if icon_name else ""
    return f'<span class="ce-badge" style="background:{color};color:{text_color}">{ic}{esc(text)}</span>'


def route_banner(route: str, reason: str) -> None:
    label, ic, bg, fg = ROUTES[route]
    html(f'<div class="ce-route" style="background:{bg};color:{fg}">{icon(ic, 34, fg)}'
         f'<div><div class="t">{esc(label)}</div><div class="r">{esc(reason)}</div></div></div>')


def checks_row(items: list[tuple[str, str, str]]) -> None:
    """items: (label, status, detail) with status ok | flag | info | off."""
    look = {"ok": ("check_circle", SUCCESS), "flag": ("flag", ERROR), "info": ("info", "#0B6FD6"), "off": ("remove", "#777")}
    chips = "".join(
        f'<span class="ce-check" title="{esc(detail)}">{icon(look[s][0], 18, look[s][1])}{esc(label)}'
        + (f'<span style="color:#555;font-weight:400">· {esc(detail)}</span>' if detail else "") + "</span>"
        for label, s, detail in items)
    html(f'<div class="ce-checks">{chips}</div>')


def kv(rows: list[tuple[str, str]], pairs_per_row: int = 1) -> str:
    cols = " ".join(["max-content 1fr"] * pairs_per_row)
    return f'<div class="ce-kv" style="grid-template-columns:{cols}">' + "".join(f'<div class="k">{esc(k)}</div><div class="v">{esc(v)}</div>'
                                           for k, v in rows) + "</div>"


def footer() -> None:
    html('<div class="ce-foot">CivicEquity is an independent student project built on NYC Open Data. '
         'It is not affiliated with or endorsed by the City of New York. Styling follows the public '
         '<a href="https://designsystem.nyc.gov" target="_blank">NYC design system</a>.</div>')


TOWER_FLOORS_PER_STEP = 2


def tower(steps: list[tuple[str, str]], complete: bool = False, beacon: str = CAUTION, title: str = "",
          subtitle: str = "", compact: bool = False) -> str:
    """A skyscraper that lights up floor by floor as the run's steps finish.

    steps: (label, state) with state done | active | pending | skipped, listed bottom floor first.
    The active step's floors twinkle (CSS animation, so it moves while Python waits on the model).
    When complete, every window lights and the spire beacon glows in the route's color.
    """
    n_floors = len(steps) * TOWER_FLOORS_PER_STEP
    fh, top, x0, w, cols = 22, 70, 26, 68, 5
    height = top + n_floors * fh + 14
    parts = [f'<rect x="{x0 - 8}" y="{height - 14}" width="{w + 16}" height="14" rx="2" fill="#0A0A1F"/>',
             f'<rect x="{x0}" y="{top}" width="{w}" height="{n_floors * fh}" fill="#0A0A1F"/>',
             f'<rect x="{x0 + 10}" y="{top - 16}" width="{w - 20}" height="16" fill="#0A0A1F"/>',
             f'<rect x="{x0 + 21}" y="{top - 30}" width="{w - 42}" height="14" fill="#0A0A1F"/>',
             f'<rect x="{x0 + w / 2 - 1.5}" y="18" width="3" height="{top - 46}" fill="#0A0A1F"/>']
    crown_fill = CAUTION if complete else "#23285E"
    parts.append(f'<rect x="{x0 + 14}" y="{top - 12}" width="{w - 28}" height="6" fill="{crown_fill}"/>')
    parts.append(f'<rect x="{x0 + 25}" y="{top - 26}" width="{w - 50}" height="5" fill="{crown_fill}"/>')
    for i, (_, state) in enumerate(steps):
        for f in range(TOWER_FLOORS_PER_STEP):
            floor = i * TOWER_FLOORS_PER_STEP + f  # 0 = bottom
            y = top + (n_floors - 1 - floor) * fh + 5
            for c in range(cols):
                x = x0 + 6 + c * 12.4
                if complete or state == "done":
                    parts.append(f'<rect x="{x:.1f}" y="{y}" width="7" height="12" fill="{CAUTION}"/>')
                elif state == "active":
                    delay = ((c * 37 + f * 53) % 100) / 100
                    parts.append(f'<rect class="win-active" style="animation-delay:{delay:.2f}s" x="{x:.1f}" y="{y}" '
                                 f'width="7" height="12" fill="#23285E"/>')
                else:
                    parts.append(f'<rect x="{x:.1f}" y="{y}" width="7" height="12" fill="#23285E"/>')
    cx = x0 + w / 2
    if complete:
        parts.append(f'<circle cx="{cx}" cy="16" r="16" fill="{beacon}" opacity=".18"/>')
        parts.append(f'<circle cx="{cx}" cy="16" r="5" fill="{beacon}"/>')
    else:
        parts.append(f'<circle class="beacon" cx="{cx}" cy="16" r="4" fill="{beacon}"/>')
    scale = 0.62 if compact else 1.0
    svg = (f'<svg width="{(x0 * 2 + w) * scale:.0f}" height="{height * scale:.0f}" viewBox="0 0 {x0 * 2 + w} {height}" '
           f'role="img" aria-label="Progress tower">{"".join(parts)}</svg>')

    look = {"done": ("check_circle", "#00B703", ""), "active": ("progress_activity", CAUTION, "spin"),
            "pending": ("radio_button_unchecked", "#555", ""), "skipped": ("remove_circle_outline", "#555", "")}
    items = "".join(
        f'<li class="{state}"><span class="{look[state][2]}">{icon(look[state][0], 19, look[state][1])}</span>'
        f'{esc(label)}</li>' for label, state in reversed(steps))  # top floor listed first, like the tower
    return (f'<div class="ce-tower{" compact" if compact else ""}">{svg}<div style="flex:1"><div class="title">{esc(title)}</div><ol>{items}</ol>'
            + (f'<div class="sub">{esc(subtitle)}</div>' if subtitle else "") + "</div></div>")
