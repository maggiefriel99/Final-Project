import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from civicequity import equity
from civicequity.memo_render import LAYOUT, _title
from civicequity.ui import state, theme
from civicequity.ui.theme import esc, html, icon

# Colors (validated with the dataviz palette checker): an ordinal blue ramp for ordered groups, two
# categorical slots for two-series charts, and a red <-> gray <-> blue diverging scale.
RAMP = {4: ["#86b6ef", "#3987e5", "#1c5cab", "#0d366b"], 5: ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"]}
SLOT1, SLOT2, EQUAL = "#2a78d6", "#eb6834", "#9A9A9A"
DIVERGING = [[0, "#e34948"], [0.5, "#f0efec"], [1, "#1c5cab"]]
CFG = {"displayModeBar": False}
TOP = {**LAYOUT, "margin": dict(l=10, r=20, t=88, b=10)}  # room for a two-line title above the plot


@st.cache_data(show_spinner="Computing equity measures ...")
def tables():
    return equity.district_table(state.snapshot(), state.candidates(), state.cost_model())


@st.cache_data(show_spinner=False)
def headline(compare: str):
    t, _ = tables()
    return equity.headline(t, compare)


def money(x: float) -> str:
    return f"${x / 1e9:,.1f}B" if x >= 1e9 else f"${x / 1e6:,.0f}M" if x >= 1e6 else f"${x:,.0f}"


def tile(ic: str, value: str, label: str, note: str) -> str:
    return (f'<div class="ce-stat"><div class="ic">{icon(ic, 22)}</div><div class="v">{esc(value)}</div>'
            f'<div class="l">{esc(label)}</div><div style="font-size:13px;color:#555;margin-top:6px">{esc(note)}</div></div>')


def lean_words(ci: float, clear: bool, low: str, high: str) -> str:
    if not clear:
        return f"No clear lean toward {low} or {high} districts"
    return f"Leans toward {low if ci < 0 else high} districts"


def fairness_curve(t: pd.DataFrame, compare: str, var: str) -> go.Figure:
    c = equity.curves(t, compare)
    labels = t["district_label"].to_dict()
    fig = go.Figure()
    fig.add_scatter(x=[0, 1], y=[0, 1], mode="lines", name="Equal share for every resident",
                    line=dict(color=EQUAL, width=2, dash="dot"), hoverinfo="skip")
    for key, name, color in (("lorenz", "Districts sorted by capital per resident (Lorenz curve)", SLOT1),
                             ("concentration", f"Districts sorted by {var}, low to high", SLOT2)):
        x, y, cds = c[key]
        fig.add_scatter(x=x, y=y, mode="lines+markers", name=name, line=dict(color=color, width=2),
                        marker=dict(size=5, color=color),
                        text=["start"] + [labels[cd] for cd in cds[1:]],
                        hovertemplate="%{text}<br>%{x:.0%} of residents · %{y:.0%} of capital<extra></extra>")
    fig.update_layout(height=420, title=_title("Who gets what share of capital",
                                               "Cumulative share of residents vs. cumulative share of capital dollars"),
                      legend=dict(orientation="h", y=-0.2, x=0), hovermode="closest", **TOP)
    fig.update_xaxes(range=[0, 1], tickformat=".0%", title="Share of residents", gridcolor="#EEE", zeroline=False)
    fig.update_yaxes(range=[0, 1], tickformat=".0%", title="Share of capital dollars", gridcolor="#EEE", zeroline=False)
    return fig


def need_vs_money(t: pd.DataFrame, compare: str, groups: pd.Series) -> go.Figure:
    labels = equity.COMPARE[compare][1]
    colors = dict(zip(labels, RAMP[len(labels)]))
    per = t["dollars"] / t["population"]
    fig = go.Figure()
    for g in labels:
        for flagged in (False, True):
            m = (groups == g) & (t["underfunded_both"] == flagged)
            if not m.any():
                continue
            fig.add_scatter(
                x=t.loc[m, "candidates_per_100k"], y=per[m], mode="markers",
                name=g if not flagged else f"{g} · underfunded on both memo views",
                legendgroup=g, showlegend=not flagged or not ((groups == g) & ~t["underfunded_both"]).any(),
                marker=dict(size=12 if flagged else 10, color=colors[g], symbol="diamond" if flagged else "circle",
                            line=dict(color="#000" if flagged else "#fff", width=2)),
                text=t.loc[m, "district_label"] + " (" + t.loc[m, "district_name"] + ")",
                hovertemplate="%{text}<br>%{x:.0f} repeat-problem streets per 100K residents"
                              "<br>$%{y:,.0f} capital per resident<extra></extra>")
    xm, ym = t["candidates_per_100k"].median(), per.median()
    fig.add_vline(x=xm, line=dict(color=EQUAL, dash="dot", width=1))
    fig.add_hline(y=ym, line=dict(color=EQUAL, dash="dot", width=1))
    fig.add_annotation(x=t["candidates_per_100k"].max(), y=per.min(), text="More repeat problems,<br>less capital",
                       showarrow=False, xanchor="right", yanchor="bottom", font=dict(size=12, color="#555"))
    fig.update_layout(height=420, title=_title("Repeat problems vs. capital",
                                               "Dots: districts (dotted lines: medians). Diamonds: underfunded on "
                                               "both memo views."),
                      legend=dict(orientation="h", y=-0.2, x=0, font=dict(size=11)), **TOP)
    fig.update_xaxes(title="Repeat-problem streets per 100K residents", gridcolor="#EEE", zeroline=False)
    fig.update_yaxes(title="Capital dollars per resident", type="log", gridcolor="#EEE",
                     tickvals=[500, 1000, 2000, 5000, 10000], ticktext=["$500", "$1K", "$2K", "$5K", "$10K"])
    return fig


def asset_heatmap(g: pd.DataFrame, t: pd.DataFrame) -> go.Figure:
    # Street lights are left out: almost all lighting money is citywide (e.g. LED replacement) and can't be placed
    # in a district, so the few placed dollars would make a misleading comparison.
    rows = [k for k in equity.ASSET_LABELS if f"per_resident_{k}" in g.columns and k != "street_lights_and_signals"]
    city = {k: t[f"dollars_{k}"].sum() / t["population"].sum() for k in rows}
    ratio = np.array([[g.loc[c, f"per_resident_{k}"] / city[k] for c in g.index] for k in rows])
    z = np.clip(np.log2(np.where(ratio > 0, ratio, 1 / 8)), -2, 2)
    text = [[f"${g.loc[c, f'per_resident_{k}']:,.0f}<br>{ratio[i, j]:.1f}×" for j, c in enumerate(g.index)]
            for i, k in enumerate(rows)]
    fig = go.Figure(go.Heatmap(
        z=z, x=list(g.index), y=[equity.ASSET_LABELS[k] for k in rows], text=text, texttemplate="%{text}",
        textfont=dict(size=12), colorscale=DIVERGING, zmin=-2, zmax=2, xgap=2, ygap=2,
        colorbar=dict(title="vs. citywide", tickvals=[-2, -1, 0, 1, 2], ticktext=["¼×", "½×", "1×", "2×", "4×"],
                      thickness=12),
        hovertemplate="%{y} · %{x}<br>%{text}<extra></extra>"))
    fig.update_layout(height=60 + 52 * len(rows), title=_title(
        "Capital per resident by asset type",
        "Dollars per resident in each group, and how that compares with the citywide figure for the same asset type"),
        **TOP)
    fig.update_yaxes(autorange="reversed")
    return fig


def follow_through(g: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for col, name, color in (("share_spent", "Share of budget already spent", SLOT1),
                             ("share_building", "Share of budget in construction or finished", SLOT2)):
        fig.add_bar(x=list(g.index), y=g[col], name=name, marker=dict(color=color, line=dict(color="#fff", width=2)),
                    text=[f"{v:.0%}" for v in g[col]], textposition="outside", cliponaxis=False,
                    hovertemplate="%{x}<br>" + name + ": %{y:.0%}<extra></extra>")
    fig.update_layout(barmode="group", bargap=0.3, height=380, title=_title(
        "Follow-through on site-specific projects",
        "Budget-weighted; projects placed in a district by location or district tag"),
        legend=dict(orientation="h", y=-0.2, x=0), **TOP)
    fig.update_yaxes(tickformat=".0%", gridcolor="#EEE", range=[0, max(0.7, float(g[["share_spent", "share_building"]]
                                                                                    .max().max()) + 0.1)])
    return fig


def trend_sentence(t, compare, metrics: list[tuple[str, str]], var) -> str:
    """One sentence on whether district-level measures rise or fall with the compare-by variable."""
    clear, unclear = [], []
    for metric, what in metrics:
        r = equity.trend(t, compare, metric)
        stat = f"rank correlation {r['rho']:+.2f}, p = {r['p']:.2f}"
        if r["p"] < 0.05:
            clear.append(f"{what} is {'higher' if r['rho'] > 0 else 'lower'} where {var} is higher ({stat})")
        else:
            unclear.append(f"{what} ({stat})")
    out = []
    if clear:
        out.append("Across the 59 districts, " + "; ".join(clear) + ".")
    if unclear:
        out.append(("Across the 59 districts, " if not clear else "") + " and ".join(unclear)
                   + f" show{'s' if len(unclear) == 1 else ''} no clear relationship with {var}, so group "
                   "differences may be chance.")
    return " ".join(out)


theme.page_header("Capital memo · Equity", "balance", "Equity dashboard",
                  "Is capital money for streets, sewers, parks, and trees reaching the districts that need it? Every "
                  "number here is computed by code from City data; no AI is involved.")

t, notes = tables()
with st.expander("How to read this page", icon=":material/help:"):
    st.markdown("""
**The question:** does capital money for streets, sewers, parks, trees, sidewalks, and lights reach every kind of
neighborhood fairly? Each of the 59 community districts gets the capital dollars in the City's current capital plan
that can be placed there, divided by its residents. **Compare districts by** sorts districts into groups (income
quarters, limited-English quarters, or heat-vulnerability scores 1 to 5) and every chart follows that choice.

- **Lean (concentration index, -1 to +1):** line districts up from lowest to highest on the chosen measure. A
  negative number means capital per resident is higher at the low end (for income: poorer districts get more); a
  positive number means the high end gets more; 0 means no lean. The 95% range shows how sure we are: if it includes
  0, the lean could be chance.
- **Gini (0 to 1):** how unevenly capital is spread across New Yorkers, regardless of who they are. 0 means every
  district gets the same per resident.
- **High heat-risk share:** the share of capital going to districts the City's budget office treats as high heat
  risk, next to their share of residents. Higher than their share of residents means they get more than an even split.
- **Who gets what share (chart):** the blue curve shows how uneven the spread is; the orange curve shows whether
  districts at the low end of the chosen measure get more or less than their share.
- **Repeat problems vs. capital (chart):** districts to the right have more streets where problems keep coming
  back; districts lower down get less capital per resident. Bottom right is the most worrying.
- **Capital by asset type (table):** the same comparison for each kind of asset. Blue: more per resident than the
  citywide figure for that asset; red: less.
- **Follow-through (chart):** for projects already placed in a district, how much of the budget has been spent and
  how much is in construction or finished, by group. Slow follow-through can leave a neighborhood waiting even when
  money is promised.
- **Is a difference real?** Under each chart, a test across the 59 districts says whether the difference is
  statistically clear or could be chance.

The data sources and limits are under **Methods, sources, and caveats** at the bottom of the page.
""")
compare = st.segmented_control("Compare districts by", list(equity.COMPARE), default="Income",
                               help="Income: Census median household income. Limited English: share of residents "
                                    "with limited English. Heat vulnerability: NYC Health Heat Vulnerability Index.")
compare = compare or "Income"
var = {"Income": "income", "Limited English": "share of limited-English residents",
       "Heat vulnerability": "heat vulnerability"}[compare]
low, high = {"Income": ("lower-income", "higher-income"),
             "Limited English": ("fewer limited-English", "more limited-English"),
             "Heat vulnerability": ("lower heat-risk", "higher heat-risk")}[compare]
groups = equity.add_groups(t, compare)
g = equity.group_table(t, compare)
h = headline(compare)

html('<div class="ce-stats">'
     + tile("balance", f"{h['concentration_index']:+.2f}", lean_words(h["concentration_index"], h["lean_is_clear"], low, high),
            f"Concentration index of capital per resident, ranked by {var} (−1 to +1; 0 = no lean). 95% range "
            f"{h['ci_low']:+.2f} to {h['ci_high']:+.2f}. For comparison, 311 complaints per resident: "
            f"{h['complaints_concentration_index']:+.2f}.")
     + tile("equalizer", f"{h['gini']:.2f}", "Gini of capital per resident",
            "How unevenly capital per resident is spread across New Yorkers (0 = everyone's district gets the same "
            "per resident).")
     + tile("device_thermostat", f"{h['heat_capital_share']:.0%} of capital",
            f"High heat-risk districts hold {h['heat_resident_share']:.0%} of residents",
            f"{h['heat_districts']} districts score 4 or 5 on the Heat Vulnerability Index, the threshold NYC OMB's "
            "climate budgeting uses to flag high heat risk.")
     + "</div>")

c1, c2 = st.columns(2, gap="medium")
c1.plotly_chart(fairness_curve(t, compare, var), width="stretch", config=CFG)
c1.caption("The further the blue curve bows below the dotted line, the more unevenly capital is spread. When the "
           f"orange curve runs above the dotted line, {low} districts get more than their share of capital; below "
           f"it, less.")
c2.plotly_chart(need_vs_money(t, compare, groups), width="stretch", config=CFG)
c2.caption("Repeat-problem streets are the app's capital candidates (problems that keep coming back). 311 reporting "
           "rises with income, so the memo's income-adjusted view is the fairer test of need.")

st.plotly_chart(asset_heatmap(g, t), width="stretch", config=CFG)
st.caption(trend_sentence(t, compare, [("capital_per_resident", "total capital per resident")], var)
           + " Blue cells: more per resident than the citywide figure for that asset type; red: less. Street lights "
             "and signals are left out: almost all of that money is citywide and can't be placed in a district.")

st.plotly_chart(follow_through(g), width="stretch", config=CFG)
st.caption(trend_sentence(t, compare, [("share_spent", "the share of budget already spent"),
                                       ("share_building", "the share in construction or finished")], var)
           + f" Covers {notes['site_projects']:,} site-specific projects; {notes['phase_left_out_share']:.0%} of their "
             "budget is in phases that are neither planning nor building (cancelled, on hold, pass-through...) and is "
             "left out.")

st.markdown("##### :material/table_view: Groups")
show = g.rename(columns={**{f"per_resident_{k}": f"{v} $/resident" for k, v in equity.ASSET_LABELS.items()}})
show[["share_spent", "share_building"]] *= 100
st.dataframe(show, width="stretch", column_config={
    "residents": st.column_config.NumberColumn(format="localized"),
    "capital_per_resident": st.column_config.NumberColumn("capital $/resident", format="$%.0f"),
    "complaints_per_1k": st.column_config.NumberColumn("311 complaints per 1K", format="%.1f"),
    "candidates_per_100k": st.column_config.NumberColumn("repeat-problem streets per 100K", format="%.1f"),
    "share_spent": st.column_config.NumberColumn("budget spent", format="%.0f%%"),
    "share_building": st.column_config.NumberColumn("budget in construction+", format="%.0f%%"),
    **{c: st.column_config.NumberColumn(format="$%.0f") for c in show.columns if c.endswith("$/resident")}})

with st.expander("All 59 districts", icon=":material/table_view:"):
    d = t.assign(group=groups.astype(str), capital_per_resident=t["dollars"] / t["population"])[[
        "district_label", "district_name", "group", "population", "median_household_income", "pct_limited_english",
        "heat_vulnerability_index", "capital_per_resident", "requests_per_1k", "candidates_per_100k", "share_spent",
        "share_building", "underfunded_raw", "underfunded_adjusted"]].reset_index()
    d[["share_spent", "share_building"]] = (d[["share_spent", "share_building"]] * 100).round(1)
    st.dataframe(d, hide_index=True, width="stretch", column_config={
        "median_household_income": st.column_config.NumberColumn("median income", format="$%d"),
        "pct_limited_english": st.column_config.NumberColumn("limited English %", format="%.1f"),
        "capital_per_resident": st.column_config.NumberColumn("capital $/resident", format="$%.0f"),
        "requests_per_1k": st.column_config.NumberColumn("311 per 1K", format="%.1f"),
        "candidates_per_100k": st.column_config.NumberColumn("repeat streets per 100K", format="%.1f"),
        "population": st.column_config.NumberColumn(format="localized"),
        "share_spent": st.column_config.NumberColumn("spent %", format="%.0f%%"),
        "share_building": st.column_config.NumberColumn("construction+ %", format="%.0f%%")})
    st.download_button("Download as CSV", d.to_csv(index=False), "civicequity-equity-by-district.csv", "text/csv",
                       icon=":material/download:")

with st.expander("Methods, sources, and caveats", icon=":material/fact_check:"):
    st.markdown(f"""
- **Capital dollars** are current capital-plan budgets for infrastructure categories (streets, sewers, water mains,
  parks, trees, sidewalks, lights), {money(notes['capital_total'])} placed in districts. **{notes['lump_sum_share']:.0%}**
  of that is citywide program money (resurfacing, ramps, trees) spread by where each program worked in the last 3
  years, not money assigned to a district. Another {money(notes['unassigned_total'])} can't be placed in any district
  and is left out.
- **Concentration index** (standard in health-equity economics): population-weighted, districts ranked by the
  compare-by variable (ties broken by income). The 95% range comes from 2,000 bootstrap resamples of districts.
- **Gini** uses the same formula with districts ranked by capital per resident itself.
- **Heat Vulnerability Index**: NYC Department of Health and Mental Hygiene, by community district tabulation area
  (Environment & Health Data Portal); NYC OMB's climate budgeting treats a community district with HVI 4-5 as high
  heat risk.
- **Income** is Census ACS 2020-2024 median household income by PUMA; some districts share a PUMA and so share a value.
- **Groups** are quarters of districts (or HVI scores), and group figures are totals divided by residents.
- 311 complaints measure reporting, not need: higher-income districts file more per resident (elasticity
  {notes['income_elasticity']:.2f}). The memo's income-adjusted view corrects for this; repeat-problem streets come from
  311 too, so they carry the same bias.
- Precedents: Oakland scores equity as one of nine factors for every capital project; NYC Parks' Community Parks
  Initiative targeted parks with little capital investment over about 20 years in dense, high-poverty areas.
""")

theme.footer()
