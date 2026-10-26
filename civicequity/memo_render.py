"""Charts and the downloadable HTML version of a memo (memo + appendix), in the app's NYC style.

The HTML file is self-contained: charts embed plotly.js, so it opens offline by double-clicking.
All text that came from an agent or from data is escaped.
"""

from datetime import datetime
from html import escape

import numpy as np
import plotly.graph_objects as go
import plotly.io as pio

from civicequity import memo_data, rules

BLUE, BLUE_LIGHT, CAUTION, INFO = "#103FEF", "#B5DBFF", "#FFB320", "#70BAFF"
CATEGORY_COLORS = {"Streets": "#000000", "Sewers and flooding": BLUE, "Water mains": INFO,
                   "Sidewalks and ramps": "#555555", "Street lights and signals": CAUTION,
                   "Street trees": "#00B703", "Parks": "#2E7D32"}
VIEW_COLORS = {"Underfunded on both views": BLUE, "Only after income adjustment": CAUTION,
               "Only on raw complaints": INFO, "Not flagged": "#E5E5E5"}
LAYOUT = dict(font=dict(family="Noto Sans, Helvetica, Arial, sans-serif", size=13, color="#000"),
              plot_bgcolor="white", paper_bgcolor="white", margin=dict(l=10, r=20, t=50, b=10))


def _title(text: str, sub: str = "") -> dict:
    return dict(text=f"<b>{escape(text)}</b>" + (f"<br><span style='font-size:12px;color:#555'>{escape(sub)}</span>"
                                                 if sub else ""), x=0, xanchor="left")


def _more_measures(m: dict | None) -> str:
    """Equity dashboard measures (memos made before they existed have none)."""
    if not m:
        return ""
    lean, heat, ft = m["lean_of_capital_by_income"], m["high_heat_risk_districts"], m["follow_through"]
    rows = "".join(f"<tr><td>{escape(a['asset'])}</td><td>{escape(a['lowest_income_quarter'])}</td>"
                   f"<td>{escape(a['highest_income_quarter'])}</td></tr>" for a in m["capital_by_asset_type"])
    return (f'<p class="small"><b>More measures (Equity dashboard).</b> {escape(lean["meaning"])} Concentration index '
            f'{escape(lean["concentration_index"])} (95% range {escape(lean["range_95"])}); 311 complaints: '
            f'{escape(lean["complaints_concentration_index"])}. Gini of capital per resident: '
            f'{escape(m["gini_of_capital_per_resident"])}. High heat-risk districts ({escape(heat["districts"])}) hold '
            f'{escape(heat["share_of_residents"])} of residents and {escape(heat["share_of_capital"])} of capital. '
            f'Follow-through, lowest vs. highest income quarter: {escape(ft["lowest_income_quarter"]["budget_spent"])} vs. '
            f'{escape(ft["highest_income_quarter"]["budget_spent"])} of budget spent. {escape(ft["note"])} '
            f'{escape(m["caveat"])}</p><table><tr><th>Asset type</th><th>Lowest-income quarter</th>'
            f'<th>Highest-income quarter</th></tr>{rows}</table>')


def how_chosen() -> str:
    """How the recommendations were chosen, written by code from the actual rules (not by an agent), so it always
    matches the method."""
    esc = rules.escalation()
    return ("CivicEquity first finds capital candidates: places where, under the City-rules file, an operating repair "
            f"is no longer enough, because the same problem was reported at least {esc['repeat_complaints_min']} times "
            f"on one street in a year, or a failed asset (for example a cave-in) was reported at least "
            f"{esc['capital_signal_complaints_min']} times. Each candidate's fix is priced at the typical budget of "
            "current City capital projects of the same kind in that borough. Candidates are ranked by the 311 "
            "complaints the fix would address per $1 million (for trees and parks, only failed-asset complaints "
            f"count), keeping at most {memo_data.MAX_PER_CATEGORY} per category, and the AI analyst picks from the top "
            "of that list. A district counts as underfunded when it is among the 15 largest gaps between 311 "
            "complaints and capital dollars per resident, on the raw view, the income-adjusted view, or both.")


def charts(result: dict, snap: dict) -> dict[str, go.Figure]:
    t = result["_tables"]
    facts = result["facts"]
    figs = {}

    # 1. Complaints by category and quarter
    q = t["quarterly"].rename(columns=memo_data.GROUP_LABELS)
    fig = go.Figure()
    for cat in q.columns:
        fig.add_bar(x=q.index, y=q[cat], name=cat, marker_color=CATEGORY_COLORS.get(cat, "#999"))
    fig.update_layout(barmode="stack", height=380, title=_title(
        "311 infrastructure complaints by quarter", "Categories peak in different seasons (streets in winter, trees in summer)"),
        legend=dict(orientation="h", y=-0.15), **LAYOUT)
    fig.update_yaxes(gridcolor="#EEE")
    figs["quarterly"] = fig

    # 2. Best value: complaints addressed per $1M
    cards = facts["best_value_capital"][::-1]
    fig = go.Figure(go.Bar(
        x=[float(c["complaints_per_million"]) for c in cards], y=[c["where"].split(" (")[0] for c in cards],
        orientation="h", marker_color=[CATEGORY_COLORS.get(c["category"], "#999") for c in cards],
        text=[f"{c['complaints_per_million']} per $1M · {c['typical_cost']}" for c in cards], textposition="outside",
        cliponaxis=False, hovertext=[f"{c['fix']}: {c['complaints_counted']} complaints" for c in cards],
        hoverinfo="text"))
    fig.update_layout(height=420, title=_title("Best value: complaints addressed per $1 million",
                                               "Top 10 capital candidates, at most 3 per category; typical cost shown"),
                      xaxis=dict(visible=False), **{**LAYOUT, "margin": dict(l=10, r=160, t=60, b=10)})
    figs["best_value"] = fig

    # 3. Income vs. complaint rate: the reporting pattern behind the two equity views
    d = t["districts"]
    view = np.select([d["underfunded_both"], d["underfunded_adjusted"], d["underfunded_raw"]],
                     list(VIEW_COLORS)[:3], default="Not flagged")
    fig = go.Figure()
    for v, color in VIEW_COLORS.items():
        sub = d[view == v]
        fig.add_scatter(x=sub["median_household_income"], y=sub["requests_per_1k"], mode="markers", name=v,
                        marker=dict(size=11, color=color, line=dict(color="#000", width=0.6)),
                        text=sub["district_label"] + " (" + sub["district_name"] + ")", hoverinfo="text+x+y")
    xs = np.linspace(d["median_household_income"].min(), d["median_household_income"].max(), 50)
    slope = t["income_fit"]["slope"]
    k = np.exp(np.mean(np.log(d["requests_per_1k"]) - slope * np.log(d["median_household_income"])))
    fig.add_scatter(x=xs, y=k * xs ** slope, mode="lines", name="Expected for that income",
                    line=dict(color="#000", dash="dot"))
    fig.update_layout(height=430, title=_title("Higher-income districts file more 311 complaints",
                                               "Each dot is a community district; the dotted line is what districts of "
                                               "similar income file"),
                      xaxis=dict(title="Median household income", tickprefix="$", gridcolor="#EEE"),
                      yaxis=dict(title="Complaints per 1,000 residents", gridcolor="#EEE"),
                      legend=dict(orientation="h", y=-0.22), **LAYOUT)
    figs["income"] = fig

    # 4. Map of the two equity views (no map tiles, so it works offline)
    gj = snap["district_geojson"]
    cats = list(VIEW_COLORS)
    z = [cats.index(v) for v in view]
    scale = [[i / (len(cats) - 1), c] for i, c in enumerate(VIEW_COLORS.values())]
    fig = go.Figure(go.Choropleth(
        geojson=gj, locations=d.index, featureidkey="properties.boro_cd", z=z, zmin=0, zmax=len(cats) - 1,
        colorscale=scale, showscale=False, marker_line_color="#FFF", marker_line_width=0.8,
        text=[f"{d.loc[cd, 'district_label']} ({d.loc[cd, 'district_name']}): {v}" for cd, v in zip(d.index, view)],
        hoverinfo="text"))
    for v, c in VIEW_COLORS.items():  # legend entries
        fig.add_scatter(x=[None], y=[None], mode="markers", marker=dict(size=12, color=c, symbol="square"), name=v)
    fig.update_geos(fitbounds="locations", visible=False)
    fig.update_layout(height=560, title=_title("Where capital dollars lag behind need",
                                               "Community districts by equity view"),
                      legend=dict(orientation="h", y=-0.02), xaxis=dict(visible=False), yaxis=dict(visible=False),
                      **LAYOUT)
    figs["map"] = fig
    return figs


# ---------- HTML ----------

CSS = """
body{font-family:'Noto Sans',Helvetica,Arial,sans-serif;color:#000;max-width:860px;margin:32px auto;padding:0 24px;line-height:1.55}
h1,h2,h3{font-family:'Clash Grotesk','Noto Sans',Helvetica,Arial,sans-serif;font-weight:600;line-height:1.15}
.mast{background:linear-gradient(180deg,#000,#050560);color:#fff;border-radius:14px;padding:22px 26px;margin-bottom:22px}
.mast .wm{font-weight:700;letter-spacing:-.5px;font-size:20px;text-transform:uppercase}.mast .wm span{color:#B5DBFF}
.meta{display:grid;grid-template-columns:90px 1fr;gap:4px 12px;font-size:14px;border-bottom:2px solid #000;padding-bottom:12px;margin-bottom:14px}
.meta b{color:#555;font-weight:600}
.bl{background:#F4F6FF;border-left:4px solid #103FEF;padding:12px 16px;border-radius:0 10px 10px 0;font-weight:500}
h3{font-size:18px;margin:18px 0 6px}ul{margin:4px 0 0 0;padding-left:20px}li{margin:3px 0}
.badge{display:inline-block;border-radius:99px;padding:3px 10px;font-size:12.5px;font-weight:700}
.ok{background:#E7F8E7;color:#0A6B0B}.warn{background:#FFF4DC;color:#7A5200}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0 16px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #DDD;vertical-align:top}
th{background:#F4F4F4}.small{color:#555;font-size:13px}.appendix{border-top:3px solid #000;margin-top:36px;padding-top:8px}
.foot{color:#777;font-size:12px;border-top:1px solid #DDD;margin-top:30px;padding-top:10px}
@media print{.pagebreak{page-break-before:always}}
"""


def _li(items) -> str:
    return "<ul>" + "".join(f"<li>{escape(x)}</li>" for x in items) + "</ul>"


def _table(rows: list[dict], cols: list[tuple[str, str]]) -> str:
    head = "".join(f"<th>{escape(h)}</th>" for h, _ in cols)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(r.get(k, '') or ''))}</td>" for _, k in cols) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def verification_summary(result: dict) -> tuple[bool, str]:
    fc, cr = result.get("fact_check") or {}, result.get("claim_review") or {}
    removed = result.get("removed_sentences") or []
    text = (f"{fc.get('numbers_checked', 0)} numbers and {fc.get('quotes_checked', 0)} quotes checked by code; "
            f"{cr.get('claims_checked', 0)} claims checked by the Fact Verifier"
            + (f"; {len(removed)} sentence(s) removed because they could not be verified" if removed else "")
            + ("; one rewrite was needed" if result.get("fact_check_retried") else ""))
    return bool(result.get("verified")), text


def memo_html(result: dict, snap: dict) -> str:
    m, f = result["memo"], result["facts"]
    figs = charts(result, snap)
    chart_html = {}
    for i, (k, fig) in enumerate(figs.items()):
        chart_html[k] = pio.to_html(fig, include_plotlyjs="inline" if i == 0 else False, full_html=False,
                                    config={"displayModeBar": False})
    verified, vtext = verification_summary(result)
    created = datetime.fromisoformat(result["created"]).strftime("%B %d, %Y")
    eq = f["equity"]
    pr_rows = [{"priority": p["priority"], "evidence": " · ".join(
        (f'"{e["text"]}"' if e["is_quote"] else e["text"]) + f' ({e["source"]})' for e in p["evidence"]),
                "candidates": p["candidates"], "places": "; ".join(p["top_places"])} for p in f["priorities"]]
    sources = memo_data.priorities()["sources"]
    removed = result.get("removed_sentences") or []
    body = f"""
<div class="mast"><div class="wm">Civic<span>Equity</span></div>
<div style="font-size:13px;color:#CCC">Capital planning memo generated from NYC 311 and capital plan data</div></div>
<div class="meta"><b>To</b><span>Office of the Mayor; Office of Management and Budget</span>
<b>From</b><span>CivicEquity analysis (AI-drafted, code- and AI-verified; for staff review)</span>
<b>Date</b><span>{escape(created)}</span><b>Period</b><span>{escape(f['period']['label'])}</span>
<b>Re</b><span><b style="color:#000">{escape(m['subject'])}</b></span></div>
<p><span class="badge {'ok' if verified else 'warn'}">{'Verified' if verified else 'Not fully verified'}</span>
<span class="small"> {escape(vtext)}</span></p>
<div class="bl">{escape(m['bottom_line'])}</div>
<h3>What New Yorkers reported</h3><p>{escape(m['what_new_yorkers_reported'])}</p>
<h3>Invest first</h3>{_li(m['invest_first'])}
<p class="small"><b>How these were chosen.</b> {escape(how_chosen())}</p>
<h3>Already funded: schedule these</h3>{_li(m['already_funded_quick_wins'])}
<h3>Where the money isn't reaching</h3><p>{escape(m['equity'])}</p>
<h3>Best fit with City priorities</h3>{_li(m['city_priorities'])}
<p class="small"><i>{escape(m['caveat'])}</i></p>

<div class="appendix pagebreak"><h2>Appendix</h2>
<h3>A. Complaints across the year</h3>{chart_html['quarterly']}
<p class="small">{escape(f['citywide']['infrastructure_requests'])} infrastructure requests, of which
{escape(f['citywide']['failed_asset_requests'])} describe a failed asset (cave-ins, sewer backups, collapsed sidewalks,
main breaks). {escape(f['citywide']['capital_candidates'])} streets, parks, and sidewalks meet the capital-candidate rules.</p>
{_table(f['citywide']['by_category'], [('Category', 'category'), ('Requests', 'requests'), ('Failed-asset requests', 'failed_asset_requests')])}

<h3>B. Best-value capital candidates</h3>{chart_html['best_value']}
{_table(f['best_value_capital'], [('Place', 'where'), ('Fix', 'fix'), ('Typical cost', 'typical_cost'), ('Range', 'cost_range'), ('Complaints counted', 'complaints_counted'), ('Per $1M', 'complaints_per_million'), ('District underfunded', 'district_underfunded')])}
<h3>C. Already-funded quick wins</h3>
{_table(f['quick_wins_already_funded'], [('Place', 'where'), ('Program', 'fix'), ('Complaints', 'complaints'), ('Failed-asset', 'failed_asset_complaints'), ('District underfunded', 'district_underfunded')])}

<h3 class="pagebreak">D. Equity: two views</h3>{chart_html['income']}
<p class="small">{escape(eq['income_reporting_pattern']['meaning'])} Lowest-income quarter of districts:
{escape(eq['income_reporting_pattern']['lowest_income_quarter']['complaints_per_1k'])}; highest:
{escape(eq['income_reporting_pattern']['highest_income_quarter']['complaints_per_1k'])}
({escape(eq['income_reporting_pattern']['ratio_high_to_low_complaints'])}). {escape(eq['income_reporting_pattern']['related_research'])}</p>
{chart_html['map']}
{_more_measures(eq.get('more_measures'))}
<p class="small"><b>Raw view:</b> {escape(eq['views']['raw_view'])} <b>Income-adjusted view:</b> {escape(eq['views']['income_adjusted_view'])}
<b>Both:</b> {escape(eq['views']['underfunded_on_both_views'])}</p>
<b>Underfunded on both views</b>
{_table(eq['underfunded_both_views'], [('District', 'district'), ('Median income', 'median_income'), ('Complaints', 'complaints_per_1k_residents'), ('vs. similar-income districts', 'complaints_vs_similar_income_districts'), ('Capital per resident', 'capital_per_resident')])}
<b>Underfunded only after the income adjustment (need that complaints alone would miss)</b>
{_table(eq['underfunded_adjusted_only'], [('District', 'district'), ('Median income', 'median_income'), ('Complaints', 'complaints_per_1k_residents'), ('vs. similar-income districts', 'complaints_vs_similar_income_districts'), ('Capital per resident', 'capital_per_resident')])}

<h3>E. City priorities and the evidence behind them</h3>
{_table(pr_rows, [('Priority', 'priority'), ('Stated by the City', 'evidence'), ('Candidates', 'candidates'), ('Top places', 'places')])}
<p class="small">Sources: {' · '.join(f'<a href="{escape(s["url"])}">{escape(s["title"])}</a>' for s in sources.values())}</p>

<h3>F. Methods and limits</h3>
{_li([f['method_notes']['cost_estimates'], f['method_notes']['complaints_per_million'], f['method_notes']['income_adjustment'],
      f"Capital not assigned to any district (citywide lump sums such as LED street lights): {eq['capital_not_assigned_to_districts']}.",
      "Budgets are not final costs; NYC capital projects can overrun.",
      "311 data covers reports, not inspected conditions; districts that report less may have more need than shown."])}
<h3>G. How this memo was produced and checked</h3>
<p class="small">Code computed every fact from NYC Open Data and Census data. A Needs Analyst and an Equity Analyst (AI)
selected from those facts; a Memo Writer (AI) drafted the memo; code checked every number and quote against the facts and
a Fact Verifier (AI) checked every claim. {escape(vtext)}.
Model: gpt-6-luna. AI cost of this memo: ${result['total']['cost_usd']:.4f}.</p>
{('<p class="small"><b>Removed during verification:</b></p>' + _li(removed)) if removed else ''}
</div>
<div class="foot">CivicEquity is an independent student project built on NYC Open Data and U.S. Census data. It is not
affiliated with or endorsed by the City of New York. Run {escape(result['run_id'])}.</div>"""
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{escape(m['subject'])}</title>"
            f"<link href='https://fonts.googleapis.com/css2?family=Noto+Sans:wght@400;500;600;700&display=swap' rel='stylesheet'>"
            f"<style>{CSS}</style></head><body>{body}</body></html>")
