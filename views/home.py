import streamlit as st

from civicequity.ui import state, theme
from civicequity.ui.theme import esc, html, icon

snap = state.snapshot()
cands = state.candidates()
dollars, unassigned = state.capital_by_district()
labels = state.district_options()

theme.hero(
    "NYC 311 · Capital planning · Equity",
    "Turn what New Yorkers report into <em>where the City should build next.</em>",
    "CivicEquity reads a year of 311 complaints, separates quick repairs from problems that need "
    "capital investment, and shows where the City's capital dollars go compared with where the need is.",
)

theme.stats([
    ("forum", f"{snap['requests']['n'].sum():,}", "infrastructure 311 requests in the past year"),
    ("construction", f"{len(cands):,}", "streets, parks, and sidewalks flagged as capital candidates"),
    ("account_balance", f"${dollars['dollars'].sum() / 1e9:,.1f}B", "capital dollars traced to districts"),
    ("map", f"{len(labels)}", "community districts compared"),
])

c1, c2 = st.columns([3, 2], gap="large")
with c1:
    st.subheader("How a new request is handled")
    steps = [
        ("edit_note", "Paste a request", "A staff member pastes an email or complaint from a resident."),
        ("shield", "Screen it", "Personal details are removed and the text is checked for attempts to manipulate the AI."),
        ("alt_route", "Triage", "An AI agent maps it to an official 311 complaint type. City rules, not the AI, "
                                "decide whether it is a repair or a capital need."),
        ("notifications_active", "Draft the agency alert", "A second agent drafts a note to the right agency using "
                                                           "only checked facts from 311 data."),
        ("how_to_reg", "A person approves", "Nothing is sent until a staff member approves or edits it. "
                                            "Every step is logged."),
    ]
    html('<div style="display:flex;flex-direction:column;gap:16px">' + "".join(
        f'<div class="ce-step"><div class="n">{icon(ic, 24)}</div><div><h4>{esc(t)}</h4><p>{esc(p)}</p></div></div>'
        for ic, t, p in steps) + "</div>")
    st.space("small")
    b1, b2 = st.columns(2)
    b1.page_link("views/new_request.py", label="Try a request", icon=":material/arrow_forward:")
    b2.page_link("views/audit.py", label="See the safety record", icon=":material/verified_user:")

with c2:
    st.subheader("Where problems keep coming back")
    st.caption("Top capital candidates: streets with the most complaints that signal a failed asset.")
    group_icon = {"streets": "add_road", "sewers_and_flooding": "flood", "water_mains": "water_drop",
                  "street_trees": "park", "sidewalks_and_ramps": "accessible", "parks": "nature_people",
                  "street_lights_and_signals": "traffic"}
    top = cands[~cands["reason"].str.startswith("City-owned")].head(6)
    rows = "".join(
        f'<div style="display:flex;gap:12px;align-items:center;padding:10px 0;border-bottom:1px solid #EEE">'
        f'<div class="ce-stat" style="padding:0;border:0"><div class="ic" style="margin:0">'
        f'{icon(group_icon.get(r.group, "construction"), 20)}</div></div>'
        f'<div style="flex:1"><div style="font-weight:600">{esc(r.location.title())}</div>'
        f'<div style="color:#555;font-size:14px">{esc(labels.get(int(r.boro_cd), ""))} · {esc(r.complaint_type)}</div></div>'
        f'<div style="text-align:right"><div class="ce-display" style="font-size:22px;font-weight:600">{r.complaints:,}</div>'
        f'<div style="color:#777;font-size:12px">complaints</div></div></div>'
        for r in top.itertuples())
    html(f'<div class="ce-card">{rows}</div>')

theme.footer()
