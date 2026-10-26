import time

import pandas as pd
import streamlit as st

from civicequity import logstore, memo_data, memo_render
from civicequity.agents.memo import MEMO_DIR, run_memo
from civicequity.ui import state, theme
from civicequity.ui.theme import esc, html, icon

snap = state.snapshot()

STEPS = [("data", "Gathering City data"), ("needs", "Needs Analyst"), ("equity", "Equity Analyst"),
         ("writer", "Memo Writer"), ("check", "Fact check and verification")]


@st.cache_resource(show_spinner=False)
def tables(use_borough_costs: bool) -> dict:
    """Chart tables for a saved memo (recomputed from the same snapshot; not stored in the memo file)."""
    return memo_data.build_facts(snap, state.cost_model(), use_borough_costs)["_tables"]


def saved_memos() -> list[dict]:
    if not MEMO_DIR.exists():
        return []
    out = []
    for f in sorted(MEMO_DIR.glob("memo-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        r = logstore.read_json(f)
        if r is None:  # encrypted with a key this app doesn't have
            continue
        if r.get("memo"):
            out.append({"file": f, "label": f"{r['created'][:16].replace('T', ' ')} · "
                                             f"{'Verified' if r.get('verified') else 'Not fully verified'} · "
                                             f"{r['memo']['subject'][:60]}"})
    return out


def tower_html(states: dict, complete=False, seconds=None, verified=None) -> str:
    steps = [(label, states.get(k, "pending")) for k, label in STEPS]
    if complete:
        return theme.tower(steps, complete=True, beacon=theme.SUCCESS if verified else theme.CAUTION,
                           title="Memo ready", subtitle=f"{'Verified' if verified else 'Needs review'} · "
                                                        f"{seconds:.0f} seconds", compact=True)
    if not states:
        return theme.tower(steps, beacon="#777777", title="Ready when you are",
                           subtitle="Five AI and code steps; each floor lights up as one finishes.", compact=True)
    return theme.tower(steps, title="Drafting the memo ...", compact=True)


def generate(use_borough_costs: bool, box):
    states, started = {}, time.monotonic()

    def step(name):
        order = [k for k, _ in STEPS]
        for k in order[:order.index(name)]:
            states[k] = "done"
        states[name] = "active"
        box.markdown(tower_html(states), unsafe_allow_html=True)

    result = run_memo(snap, state.cost_model(), use_borough_costs, progress=step)
    for k, _ in STEPS:
        states[k] = "done"
    seconds = time.monotonic() - started
    box.markdown(tower_html(states, True, seconds, result["verified"]), unsafe_allow_html=True)
    st.session_state.memo = result
    st.session_state.memo_tower = {"states": states, "seconds": seconds, "verified": result["verified"]}


def memo_card(r: dict):
    m, f = r["memo"], r["facts"]
    verified, vtext = memo_render.verification_summary(r)
    li = lambda xs: "<ul style='margin:4px 0 0 0;padding-left:20px'>" + "".join(
        f"<li style='margin:4px 0'>{esc(x)}</li>" for x in xs) + "</ul>"
    sec = lambda title, ic: (f'<div style="display:flex;align-items:center;gap:8px;margin:16px 0 4px 0">'
                             f'{icon(ic, 20, theme.BLUE)}<h4 style="margin:0;font-size:18px">{esc(title)}</h4></div>')
    badge = (theme.badge("Verified", theme.SUCCESS, "#fff", "verified") if verified
             else theme.badge("Needs review", theme.CAUTION, "#000", "warning"))
    html(f"""<div class="ce-card" style="padding:26px 30px">
<div style="display:grid;grid-template-columns:70px 1fr;gap:4px 12px;font-size:14px;border-bottom:2px solid #000;
padding-bottom:12px;margin-bottom:12px">
<b style="color:#555">To</b><span>Office of the Mayor; Office of Management and Budget</span>
<b style="color:#555">From</b><span>CivicEquity analysis (AI-drafted, code- and AI-verified; for staff review)</span>
<b style="color:#555">Period</b><span>{esc(f['period']['label'])}</span>
<b style="color:#555">Re</b><span style="font-weight:700">{esc(m['subject'])}</span></div>
<div style="margin-bottom:12px">{badge} <span style="color:#555;font-size:13px">{esc(vtext)}</span></div>
<div class="ce-quote" style="font-weight:500;font-size:16px">{esc(m['bottom_line'])}</div>
{sec("What New Yorkers reported", "forum")}<p style="margin:0">{esc(m['what_new_yorkers_reported'])}</p>
{sec("Invest first", "construction")}{li(m['invest_first'])}
<p style="color:#555;font-size:13px;margin:6px 0 0 0"><b>How these were chosen.</b> {esc(memo_render.how_chosen())}</p>
{sec("Already funded: schedule these", "event_available")}{li(m['already_funded_quick_wins'])}
{sec("Where the money isn't reaching", "balance")}<p style="margin:0">{esc(m['equity'])}</p>
{sec("Best fit with City priorities", "flag")}{li(m['city_priorities'])}
<p style="color:#555;font-size:13px;margin-top:14px"><i>{esc(m['caveat'])}</i></p></div>""")
    if r.get("removed_sentences"):
        with st.expander(f"{len(r['removed_sentences'])} sentence(s) removed because they could not be verified",
                         icon=":material/content_cut:"):
            for s in r["removed_sentences"]:
                st.markdown(f"- ~~{esc(s)}~~")


theme.page_header("Flow 2 · Citywide capital memo", "description", "Capital memo",
                  "A one-page memo for the Mayor's Office and OMB on where capital money should go, drafted by AI "
                  "agents from City data and checked sentence by sentence.", compact=True)

left, right = st.columns([4, 8], gap="large")
with left:
    tower_slot = st.empty()
    with st.container(border=True):
        st.markdown("##### :material/tune: Settings")
        use_borough = st.toggle("Adjust capital dollars for borough cost differences", value=True,
                                help="Changes how citywide lump sums are spread across districts. The equity "
                                     "results are nearly identical either way (rank correlation 0.987).")
        go_clicked = st.button("Generate memo", type="primary", icon=":material/auto_awesome:", width="stretch")
        st.caption(":material/schedule: About 15–30 seconds and about half a cent of AI use.")
        memos = saved_memos()
        if memos:
            pick = st.selectbox("Or open an earlier memo", [None] + list(range(len(memos))),
                                format_func=lambda i: "Choose a saved memo ..." if i is None else memos[i]["label"])
            if pick is not None and st.button("Open", icon=":material/folder_open:", width="stretch"):
                r = logstore.read_json(memos[pick]["file"])
                r["_tables"] = tables(r["use_borough_costs"])
                st.session_state.memo = r
                st.session_state.pop("memo_tower", None)
    if go_clicked:
        generate(use_borough, tower_slot)
    elif st.session_state.get("memo_tower"):
        tw = st.session_state.memo_tower
        tower_slot.markdown(tower_html(tw["states"], True, tw["seconds"], tw["verified"]), unsafe_allow_html=True)
    else:
        tower_slot.markdown(tower_html({}), unsafe_allow_html=True)

r = st.session_state.get("memo")
with right:
    if not r:
        html(f'<div class="ce-card" style="text-align:center;padding:38px 24px">'
             f'<div style="display:inline-flex;width:64px;height:64px;border-radius:18px;background:#000;color:#fff;'
             f'align-items:center;justify-content:center;margin-bottom:12px">{icon("description", 34)}</div>'
             f'<h4>Your memo appears here</h4><p style="color:#555">Press <b>Generate memo</b>. Agents pick the '
             f'investments, read the equity data, and draft one page; code and a Fact Verifier check every number, '
             f'quote, and claim before you see it.</p>'
             f'<div style="height:90px;margin-top:18px">{theme.skyline_svg("strip", seed=5)}</div></div>')
    elif not r.get("memo"):
        st.error(f"The memo could not be drafted ({(r.get('stopped') or {}).get('by', 'unknown reason')}).",
                 icon=":material/error:")
    else:
        memo_card(r)
        c1, c2 = st.columns([1, 1])
        c1.download_button("Download memo + appendix (HTML)", memo_render.memo_html(r, snap),
                           file_name=f"civicequity-memo-{r['created'][:10]}.html", mime="text/html",
                           icon=":material/download:", width="stretch", type="primary")
        t = r["total"]
        c2.markdown(theme.badge(f"{t['input_tokens'] + t['output_tokens']:,} tokens · ${t['cost_usd']:.4f}",
                                "#EEEEEE", "#000", "payments"), unsafe_allow_html=True)

if r and r.get("memo"):
    st.markdown("### :material/library_books: Appendix")
    figs = memo_render.charts(r, snap)
    f = r["facts"]
    tabs = st.tabs([":material/bar_chart: Trend", ":material/savings: Best value", ":material/balance: Equity",
                    ":material/map: Map", ":material/flag: City priorities", ":material/fact_check: Methods & checks"])
    cfg = {"displayModeBar": False}
    with tabs[0]:
        st.plotly_chart(figs["quarterly"], width="stretch", config=cfg)
        st.dataframe(pd.DataFrame(f["citywide"]["by_category"]), hide_index=True, width="stretch")
    with tabs[1]:
        st.plotly_chart(figs["best_value"], width="stretch", config=cfg)
        cols = ["where", "fix", "typical_cost", "cost_range", "complaints_counted", "complaints_per_million",
                "district_underfunded"]
        st.dataframe(pd.DataFrame(f["best_value_capital"])[cols], hide_index=True, width="stretch")
        st.markdown("**Already funded: schedule these**")
        st.dataframe(pd.DataFrame(f["quick_wins_already_funded"])[["where", "fix", "complaints",
                                                                   "failed_asset_complaints"]],
                     hide_index=True, width="stretch")
    with tabs[2]:
        st.page_link("views/equity.py", label="More equity measures: open the Equity dashboard",
                     icon=":material/balance:")
        st.plotly_chart(figs["income"], width="stretch", config=cfg)
        eq = f["equity"]
        st.caption(eq["income_reporting_pattern"]["meaning"] + " " + eq["income_reporting_pattern"]["related_research"])
        st.markdown("**Underfunded on both views**")
        st.dataframe(pd.DataFrame(eq["underfunded_both_views"]), hide_index=True, width="stretch")
        st.markdown("**Underfunded only after the income adjustment**")
        st.dataframe(pd.DataFrame(eq["underfunded_adjusted_only"]), hide_index=True, width="stretch")
    with tabs[3]:
        st.plotly_chart(figs["map"], width="stretch", config=cfg)
    with tabs[4]:
        for p in f["priorities"]:
            with st.container(border=True):
                st.markdown(f"**{p['priority']}** · {p['candidates']} candidates")
                for e in p["evidence"]:
                    st.markdown(("> " if e["is_quote"] else "- ") + (f'"{e["text"]}"' if e["is_quote"] else e["text"])
                                + f" *({e['source']})*")
                st.caption("Top places: " + "; ".join(p["top_places"]))
    with tabs[5]:
        for k, v in f["method_notes"].items():
            st.markdown(f"- {v}")
        st.markdown(f"- Capital not assigned to any district: {f['equity']['capital_not_assigned_to_districts']}")
        verified, vtext = memo_render.verification_summary(r)
        st.markdown(f"**Verification:** {vtext}.")
        usage = pd.DataFrame([{"step": k.replace("_", " "), "input tokens": v.get("input_tokens", 0),
                               "cached": v.get("cached_input_tokens", 0), "output tokens": v.get("output_tokens", 0),
                               "cost $": v.get("cost_usd", 0)} for k, v in r["usage"].items()])
        st.dataframe(usage, hide_index=True, width="stretch",
                     column_config={"cost $": st.column_config.NumberColumn(format="$%.5f")})

theme.footer()
