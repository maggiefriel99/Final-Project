import json
import math
import time
import uuid

import plotly.graph_objects as go
import streamlit as st

from civicequity import alerts, config, emergency
from civicequity.agents.cost_estimator import estimate_fix
from civicequity.agents.operating_router import AGENCY_NAMES, route_to_agency
from civicequity.agents.resident_reply import draft_reply
from civicequity.agents.triage import triage
from civicequity.ui import state, theme
from civicequity.ui.theme import AGENCY_ICONS, BLUE, PRIORITY, esc, html, icon, md

SAMPLES = {s["label"]: s["text"] for s in json.loads((config.ROOT / "samples" / "requests.json").read_text())}
snap = state.snapshot()
labels = state.district_options()


def load_sample():
    pick = st.session_state.get("sample_pick")
    if pick:
        st.session_state.req_text = SAMPLES[pick]
        st.session_state.pop("result", None)


def load_text(text: str):
    """Put one of the other reported problems in the request box (a callback, so it runs before the box is drawn)."""
    st.session_state.req_text = text
    st.session_state.pop("result", None)


STEPS = [("screen", "Screening the request"), ("ai", "AI triage"), ("rules", "Applying City rules"),
         ("safety", "Life-safety check"), ("cost", "Estimating the lasting fix"),
         ("alert", "Drafting agency alerts"), ("reply", "Drafting the resident reply")]
BEACON = {"capital": theme.INFO, "operating": "#FFFFFF", "not_city": "#CCCCCC", "human_review": theme.CAUTION,
          "emergency": theme.ERROR, "advice": theme.BLUE_LIGHT}


def tower_html(states: dict | None = None, complete: bool = False, route: str | None = None,
               seconds: float | None = None) -> str:
    """The progress tower: unlit and waiting (no states), lighting up (states), or fully lit (complete)."""
    states = states or {}
    labels = dict(STEPS)
    if states.get("ai") == "done" and states.get("bank"):
        labels["ai"] = "AI triage (reused an earlier answer)"
    steps = [(labels[k], states.get(k, "pending")) for k, _ in STEPS]
    if complete:
        return theme.tower(steps, complete=True, beacon=BEACON.get(route, theme.CAUTION), title="Request complete",
                           subtitle=f"{theme.ROUTES[route][0]} · {seconds:.1f} seconds", compact=True)
    if not states:
        return theme.tower(steps, beacon="#777777", title="Ready when you are",
                           subtitle="Each floor lights up as a step finishes.", compact=True)
    return theme.tower(steps, title="Working on it ...", compact=True)


def run(text: str, district: int | None, use_bank: bool, box):
    """Run triage (and the alert, if any) while the tower in `box` lights up floor by floor."""
    states: dict = {}
    started = time.monotonic()

    def step(name: str):
        if name == "bank":
            states["bank"] = True
            return
        order = [k for k, _ in STEPS]
        for k in order[:order.index(name)]:
            states[k] = "skipped" if states.get(k) == "skipped" else "done"
        states[name] = "active"
        box.markdown(tower_html(states), unsafe_allow_html=True)

    # Demo sender id: this browser session (live use would pass the 311 account or phone; only a hash is kept).
    sender = st.session_state.setdefault("sender_id", f"demo-session-{uuid.uuid4().hex[:12]}")
    t = triage(text, snap, state.candidates(), district_hint=district, use_bank=use_bank, progress=step, sender=sender)
    for k in ("screen", "ai", "rules", "safety"):
        states[k] = "done"
    cost = None
    if t.route == "capital" and t.capital_group:
        step("cost")
        try:
            cost = estimate_fix(t, state.cost_model())
            states["cost"] = "done"
        except ValueError:  # e.g. no borough to price in
            states["cost"] = "skipped"
    else:
        states["cost"] = "skipped"
    alert, esc_result, reply = None, None, None
    if t.route == "emergency":
        # Code, not AI: urgent drafts to every agency for this incident type, and the fixed 911 message.
        step("alert")
        esc_result = emergency.escalate(t, t.emergency["incident_type"], t.emergency["detected_by"])
        states["alert"] = states["reply"] = "done"
    elif t.route in ("operating", "capital") and t.agency:
        step("alert")
        alert = route_to_agency(t)
        states["alert"] = "done"
    else:
        states["alert"] = "skipped"
    if not esc_result:
        step("reply")
        reply = draft_reply(t)
        states["reply"] = "done" if reply else "skipped"
    seconds = time.monotonic() - started
    box.markdown(tower_html(states, complete=True, route=t.route, seconds=seconds), unsafe_allow_html=True)
    st.session_state.result = {"triage": t, "alert_id": alert and alert["alert_id"], "cost": cost,
                               "emergency": esc_result,
                               "reply_id": reply and reply["alert_id"],
                               "tower": {"states": states, "route": t.route, "seconds": seconds}}


def safety_chips(t) -> list[tuple[str, str, str]]:
    c = t.checks
    chips = []
    chips.append(("Hidden text", "info", "cleaned") if c["input_cleaning"]["changed"] else ("Hidden text", "ok", ""))
    pii = c["personal_info_removed"]
    chips.append(("Personal info removed", "info", ", ".join(f"{n} {k.lower()}" for k, n in pii.items()))
                 if pii else ("No personal info", "ok", ""))
    pc = c["pattern_check"]
    chips.append(("Manipulation patterns", "flag", "; ".join(pc["phrases"])[:80]) if pc["flagged"]
                 else ("Manipulation patterns", "ok", "none"))
    pf = c.get("provider_filter")
    if pf is None:
        chips.append(("Provider screening", "off", "not needed"))
    elif pf["blocked"]:
        chips.append(("Provider screening", "flag", "blocked"))
    elif pf["jailbreak_detected"] or pf["indirect_attack_detected"]:
        chips.append(("Provider screening", "flag", "attack detected"))
    else:
        chips.append(("Provider screening", "ok", "clear"))
    if "agent_flag" in c:
        chips.append(("AI's own check", "flag", "suspicious text") if c["agent_flag"]["suspicious_instructions"]
                     else ("AI's own check", "ok", "clear"))
    if "output_validation" in c:
        ov = c["output_validation"]
        chips.append(("Answer checks", "ok", "passed") if ov["passed"] else ("Answer checks", "flag", "issues"))
    ls = c.get("life_safety")
    if ls:
        if ls.get("escalated"):
            chips.append(("Life-safety check", "flag", "emergency"))
        elif ls.get("urgent_hazard"):
            chips.append(("Life-safety check", "flag", "urgent hazard"))
        elif ls["danger"] is None:
            chips.append(("Life-safety check", "info", f"no answer ({(ls['stopped'] or {}).get('by', '')})"))
        elif c.get("danger_words"):
            chips.append(("Life-safety check", "info", "danger words; held"))
        else:
            chips.append(("Life-safety check", "ok", "no danger found"))
    fair = (c.get("fairness") or {}).get("request_targets_group")
    if fair:
        chips.append(("Group-targeting language", "info", f'noted: "{fair}"'))
    sl = c.get("sender_limit") or {}
    if sl.get("allowed") is False:
        chips.append(("Sender limit", "flag", f"{sl['last_hour']} in the past hour"))
    lim = c.get("limits", {})
    if lim.get("exceeded"):
        chips.append(("Budget", "flag", "limit reached"))
    elif "tokens_used" in lim:
        chips.append(("Budget", "ok", f"{lim['tokens_used']:,} of {lim['token_budget']:,} tokens"))
    if c["request_bank"]["used"]:
        m = c["request_bank"]["match"]
        chips.append(("Request bank", "info", f"reused ({m['match']}, {m['similarity']:.0%} match)"))
    return chips


def district_map(t):
    gj = snap["district_geojson"]
    d = snap["districts"]
    z = (d["boro_cd"] == t.boro_cd).astype(int)
    fig = go.Figure(go.Choroplethmap(
        geojson=gj, locations=d["boro_cd"], featureidkey="properties.boro_cd", z=z,
        colorscale=[[0, "rgba(0,0,0,0.02)"], [1, "rgba(16,63,239,0.12)"]], showscale=False,
        marker_line_color=["#103FEF" if v else "#999" for v in z], marker_line_width=[2.5 if v else 0.6 for v in z], text=d["district_label"], hoverinfo="text"))
    hot = snap["street_hotspots"]
    pts = hot[(hot["street_name"] == (t.decision.street_name or "").upper()) & (hot["boro_cd"] == t.boro_cd)]
    if len(pts):
        fig.add_trace(go.Scattermap(
            lat=pts["lat"], lon=pts["lon"], mode="markers",
            marker=dict(size=(pts["n"] ** 0.5 * 2.4).clip(9, 34), color=BLUE, opacity=0.85),
            text=[f"{r.complaint_type}: {r.n:,} complaints" for r in pts.itertuples()], hoverinfo="text"))
    cen = d.loc[d["boro_cd"] == t.boro_cd, "geometry"].iloc[0].centroid
    fig.update_layout(map=dict(style="carto-positron", center=dict(lat=cen.y, lon=cen.x), zoom=12.3),
                      margin=dict(l=0, r=0, t=0, b=0), height=330, showlegend=False)
    return fig


def history_chart(t):
    rows = [r for r in t.street_record if r["boro_cd"] == t.boro_cd][:8]
    if not rows:
        return None
    rows = rows[::-1]
    colors = [BLUE if r["complaint_type"] == t.decision.complaint_type else "#000" for r in rows]
    short = lambda s: s if len(s) <= 18 else s[:17] + "…"
    fig = go.Figure(go.Bar(x=[r["complaints_past_year"] for r in rows], y=[short(r["complaint_type"]) for r in rows],
                           orientation="h", marker_color=colors, text=[f"{r['complaints_past_year']:,}" for r in rows],
                           textposition="outside", cliponaxis=False,
                           hovertext=[r["complaint_type"] for r in rows], hoverinfo="text+x"))
    fig.update_layout(height=40 + 34 * len(rows), margin=dict(l=135, r=36, t=6, b=0), plot_bgcolor="white",
                      xaxis=dict(visible=False), yaxis=dict(tickfont=dict(size=13), automargin=True), font=dict(family="Noto Sans"))
    return fig


def money(x: float) -> str:
    return f"${x / 1e6:,.1f}M" if x >= 1e6 else f"${x / 1e3:,.0f}K"


def show_cost(c):
    e = c.estimate
    by = "AI choice" if c.chosen_by == "agent" else "City-rules default"
    head = (f'<div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:flex-start">'
            f'<div><div style="font-size:13px;color:#555;font-weight:600;text-transform:uppercase;letter-spacing:.4px">'
            f'Recommended fix</div><div class="ce-display" style="font-size:24px;font-weight:600">{esc(c.scope_label)}</div>'
            f'<div style="color:#333;margin-top:2px">{esc(c.what_the_fix_involves)}</div></div>'
            f'<div>{theme.badge(by, "#000", "#fff", "psychology" if c.chosen_by == "agent" else "rule")} '
            f'{theme.badge(c.confidence.title() + " confidence", "#EEEEEE", "#000")}</div></div>')
    if e["funded_by_existing_program"]:
        money_html = (f'<div class="ce-quote" style="margin-top:14px;border-color:{theme.SUCCESS};background:#F2FBF2">'
                      f'{icon("verified", 18, theme.SUCCESS)} <b>Already funded.</b> {esc(e["scope_description"])}</div>')
    else:
        # Range bar on a log scale from the low to the high estimate, with the typical value marked.
        lo, mid, hi = math.log(e["low"]), math.log(e["typical"]), math.log(e["high"])
        pos = (mid - lo) / (hi - lo) * 100
        money_html = (
            f'<div style="display:flex;align-items:baseline;gap:12px;margin-top:14px;flex-wrap:wrap">'
            f'<div class="ce-display" style="font-size:40px;font-weight:600;line-height:1">{money(e["typical"])}</div>'
            f'<div style="color:#555">typical for a City capital project of this kind in {esc(e["borough"])}</div></div>'
            f'<div style="position:relative;height:10px;background:linear-gradient(90deg,{theme.BLUE_LIGHT},{BLUE});'
            f'border-radius:99px;margin:14px 0 6px 0"><div style="position:absolute;left:calc({pos:.0f}% - 7px);top:-4px;'
            f'width:14px;height:18px;border-radius:5px;background:#000;border:2px solid #fff"></div></div>'
            f'<div style="display:flex;justify-content:space-between;font-size:13px;color:#555">'
            f'<span>Low end {money(e["low"])}</span><span>High end {money(e["high"])}</span></div>'
            f'<div style="font-size:13px;color:#777;margin-top:8px">Based on {e["comparable_projects"]} current City '
            f'capital projects of this kind ({e["comparable_in_borough"]} in {esc(e["borough"])}). Range = middle half of '
            f'similar budgets. Planning estimate, not a budget; a single-site fix can cost much less.</div>')
    html(f'<div class="ce-card">{head}{money_html}'
         f'<div style="margin-top:14px;font-size:15px"><b>Why this fix:</b> {esc(c.reason)}</div></div>')
    if c.comparables:
        label = "Comparable City projects the AI cited" if c.comparables_cited_by_ai else "Closest current City projects"
        with st.expander(f"{label} ({len(c.comparables)})", icon=":material/apartment:"):
            for p in c.comparables:
                html(f'<div style="display:flex;justify-content:space-between;gap:10px;padding:6px 0;'
                     f'border-bottom:1px solid #EEE"><div><b>{esc(p["project"])}</b><div style="color:#777;'
                     f'font-size:13px">{esc(p["borough"])} · {esc(p["phase"])} · {esc(p["fms_id"])}</div></div>'
                     f'<div style="font-weight:700">{money(p["budget"])}</div></div>')
    if c.problems:
        st.caption("AI answer issues: " + "; ".join(md(p) for p in c.problems))


def show_alert(alert_id: str):
    a = alerts.get(alert_id)
    d = a["draft"] or {"subject": "", "body": "", "priority": "routine", "priority_reason": "", "requested_action": ""}
    is_reply = a.get("kind") == "resident_reply"
    is_emergency = a.get("kind") == "emergency_alert"
    with st.container(border=True):
        pr = d["priority"]
        top_label, top_name, ic = (
            ("Message to", "The resident who made the request", "mail") if is_reply else
            ("Emergency alert (simulation) to" if is_emergency else "Draft alert to", a["agency_name"],
             AGENCY_ICONS.get(a["agency"], "apartment")))
        badges = ("" if is_reply else theme.badge(pr.title() + " priority", PRIORITY[pr],
                                                  "#000" if pr == "elevated" else "#fff", "priority_high") + " ")
        html(f'<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">'
             f'<div style="display:flex;gap:10px;align-items:center">'
             f'<div class="ce-stat" style="padding:0;border:0"><div class="ic blue" style="margin:0">'
             f'{icon(ic, 22)}</div></div>'
             f'<div><div style="font-size:13px;color:#555;font-weight:600;text-transform:uppercase;letter-spacing:.4px">'
             f'{top_label}</div><div style="font-weight:700;font-size:17px">{esc(top_name)}</div></div></div>'
             f'<div>{badges}{theme.badge(a["status"].title(), "#EEEEEE", "#000", "schedule" if "pending" in a["status"] else "task_alt")}'
             f'</div></div>')
        if is_reply and d.get("sources"):
            html('<div style="font-size:13px;color:#555;margin:6px 0 2px 0">Official sources added to the reply: '
                 + " · ".join(f'<a href="{esc(s["url"])}" target="_blank">{esc(s["title"])}</a>' for s in d["sources"])
                 + "</div>")
        if a["problems"]:
            st.warning("The draft failed a check and needs editing before approval: " + "; ".join(md(p) for p in a["problems"]),
                       icon=":material/edit:")
        if a["stopped"]:
            st.warning(f"The agent did not write a draft ({a['stopped']['by']}). Write it by hand below.",
                       icon=":material/edit:")
        if a["status"].startswith("shown"):
            html(f'<div class="ce-quote" style="white-space:pre-wrap"><b>{esc(d["subject"])}</b>\n\n{esc(d["body"])}</div>')
            st.caption("Fixed, verified text written by code (not AI), so it can be shown at once without waiting for "
                       "approval. In this demo it is only recorded; nothing is sent.")
            return
        if a["status"] in ("approved", "rejected"):
            html(f'<div class="ce-quote" style="white-space:pre-wrap"><b>{esc(d["subject"])}</b>\n\n{esc(d["body"])}</div>')
            st.caption(f"{a['status'].title()} by {md(a['reviewer'])} · {a['decided'][:16].replace('T', ' ')} UTC"
                       + (" · simulation: nothing was sent" if is_emergency else ""))
            return
        if not is_reply:
            st.caption(md(d["priority_reason"]))
        with st.form(f"alert-{alert_id}", border=False):
            subject = st.text_input("Subject", d["subject"])
            body = st.text_area("Message", d["body"], height=200 if is_reply else 250)
            action = d["requested_action"] if is_reply else st.text_input("Requested action", d["requested_action"])
            reviewer = st.text_input("Your name (required to approve or reject)", st.session_state.get("reviewer", ""))
            b1, b2 = st.columns(2)
            ok = b1.form_submit_button("Approve reply" if is_reply else "Approve alert", type="primary",
                                       icon=":material/task_alt:", width="stretch")
            no = b2.form_submit_button("Reject", icon=":material/close:", width="stretch")
        if ok or no:
            if not reviewer.strip():
                st.error("Enter your name first.", icon=":material/person:")
                return
            st.session_state.reviewer = reviewer
            edited = {**d, "subject": subject, "body": body, "requested_action": action}
            alerts.decide(alert_id, "approved" if ok else "rejected", reviewer,
                          edited_draft=edited if edited != d else None)
            st.toast(("Approved and logged (simulation: nothing sent)." if is_emergency else "Alert approved and logged.")
                     if ok else "Alert rejected and logged.",
                     icon=":material/task_alt:" if ok else ":material/close:")
            st.rerun()


def emergency_panel(e: dict):
    """The escalation: who was alerted and why, the 911 message, and each simulated alert."""
    who = ", ".join(e["detected_by"]) + (f" ({e['reviewer']})" if e.get("reviewer") else "")
    rows = "".join(
        f'<div style="display:flex;gap:10px;align-items:flex-start;padding:7px 0;border-bottom:1px solid #EEE">'
        f'{icon(AGENCY_ICONS.get(a["agency"], "apartment"), 22, theme.ERROR)}<div><b>{esc(a["agency_name"])}</b>'
        f'<div style="font-size:13px;color:#555">{esc(a["role"])}</div></div></div>' for a in e["agencies"])
    html(f'<div class="ce-card" style="border:2px solid {theme.ERROR}">'
         f'<div style="font-weight:700;color:{theme.ERROR};margin-bottom:6px">{icon("science", 18, theme.ERROR)} '
         f'Simulation: nothing was sent to 911 or to any agency.</div>'
         f'<div style="font-size:15px"><b>Incident type:</b> {esc(e["incident_type"])} · <b>Flagged by:</b> {esc(who)}</div>'
         f'<div style="font-size:13px;color:#777;margin:4px 0 10px 0">Agencies come from the City\'s Citywide Incident '
         f'Management System (CIMS) charts: primary agencies for the incident type, supporting agencies with the matching '
         f'core competency, NYC Emergency Management as coordinator, and the agency for the 311 complaint type.</div>'
         f'{rows}</div>')
    st.markdown("##### :material/mail: Message to the resident")
    show_alert(e["notice_id"])
    st.markdown(f"##### :material/notifications_active: Urgent alerts ({len(e['alert_ids'])})")
    for aid, a in zip(e["alert_ids"], e["agencies"]):
        with st.expander(a["agency_name"], icon=":material/priority_high:"):
            show_alert(aid)


def flag_emergency(t, res: dict):
    """Staff can escalate anything the checks did not."""
    possible = bool(t.emergency and t.emergency.get("possible"))
    with st.expander("Flag as emergency", icon=":material/e911_emergency:", expanded=possible):
        st.caption("Use this if the request describes danger to life or safety that the checks missed. The resident "
                   "is told to call 911 and urgent alerts are drafted for the emergency agencies. Simulation: nothing "
                   "is sent.")
        with st.form("flag-emergency", border=False):
            types = emergency.incident_types()
            pick = st.selectbox("Incident type", types, index=types.index(emergency.OTHER))
            who = st.text_input("Your name", st.session_state.get("reviewer", ""))
            go_flag = st.form_submit_button("Flag as emergency", type="primary", icon=":material/e911_emergency:")
        if go_flag:
            if not who.strip():
                st.error("Enter your name first.", icon=":material/person:")
                return
            st.session_state.reviewer = who
            res["emergency"] = emergency.escalate(t, pick, ["staff"], reviewer=who.strip())
            t.route, t.route_reason = "emergency", (f"Flagged as an emergency by {who.strip()}. The resident is told to "
                                                    "call 911; urgent alerts are drafted. Simulation: nothing is sent.")
            st.rerun()


theme.page_header("Flow 1 · New constituent request", "edit_note", "New request",
                  "Paste a resident's email or complaint. CivicEquity screens it, sorts it, and drafts the agency "
                  "alert for you to approve.", compact=True)

st.pills("Try a sample request", list(SAMPLES), key="sample_pick", on_change=load_sample)

left, right = st.columns([5, 7], gap="large")

with left:
    tower_slot = st.empty()  # above the request box, so progress is in view while the AI works
    with st.container(border=True):
        st.markdown("##### :material/inbox: The request")
        text = st.text_area("Request text", key="req_text", height=135, label_visibility="collapsed",
                            placeholder="Paste the resident's message here, or pick a sample above ...")
        c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
        district = c1.selectbox("District, if known", [None] + list(labels),
                                format_func=lambda cd: "Auto (from the street)" if cd is None else labels[cd])
        with c2.popover("Options", icon=":material/tune:", width="stretch"):
            use_bank = st.toggle("Reuse answers to identical earlier requests (saves tokens)", value=True)
        go_clicked = st.button("Analyze request", type="primary", icon=":material/auto_awesome:",
                               width="stretch", disabled=not (text or "").strip())
        st.caption(":material/lock: Names, phone numbers, emails, and ID numbers are removed before the AI sees the text.")
    if go_clicked:
        run(text, district, use_bank, tower_slot)
    elif st.session_state.get("result", {}).get("tower"):
        tw = st.session_state.result["tower"]
        tower_slot.markdown(tower_html(tw["states"], complete=True, route=tw["route"], seconds=tw["seconds"]),
                            unsafe_allow_html=True)
    else:
        tower_slot.markdown(tower_html(), unsafe_allow_html=True)

with right:
    res = st.session_state.get("result")
    if not res:
        html(f'<div class="ce-card" style="text-align:center;padding:38px 24px">'
             f'<div style="display:inline-flex;width:64px;height:64px;border-radius:18px;background:#000;color:#fff;'
             f'align-items:center;justify-content:center;margin-bottom:12px">{icon("travel_explore", 34)}</div>'
             f'<h4>Results appear here</h4><p style="color:#555">Pick a sample above, or paste a real request, '
             f'then press <b>Analyze request</b>.</p>'
             f'<div style="height:90px;margin-top:18px">{theme.skyline_svg("strip", seed=3)}</div></div>')
    else:
        t = res["triage"]
        if not hasattr(t, "life_safety"):  # a result kept from before an app update: start over
            st.session_state.pop("result", None)
            st.info("The app was updated since this request was analyzed. Press **Analyze request** again.",
                    icon=":material/refresh:")
            st.stop()
        theme.route_banner(t.route, t.route_reason)
        if t.review_reasons:
            st.warning(("**Also flagged:**\n\n" if t.route == "emergency" else "**Why it was held:**\n\n")
                       + "\n".join(f"- {md(r)}" for r in t.review_reasons), icon=":material/front_hand:")
        if getattr(t, "urgent_hazard", None) and t.route != "emergency":
            st.warning(f"**Urgent hazard (not a 911 emergency):** {md(t.urgent_hazard)} The agency alert is marked "
                       "urgent.", icon=":material/warning:")
        if t.life_safety and t.life_safety.get("reason") and (t.route == "emergency" or t.emergency):
            st.markdown(f"**Life-Safety Screener:** {md(t.life_safety['reason'])}")
        if res.get("emergency"):
            emergency_panel(res["emergency"])
        else:
            flag_emergency(t, res)

        st.markdown("##### :material/shield: Safety screening")
        theme.checks_row(safety_chips(t))
        with st.expander("What the AI actually saw (after personal info was removed)", icon=":material/visibility:"):
            html(f'<div class="ce-quote">{esc(t.request_text)}</div>')

        d = t.decision
        if d:
            st.markdown("##### :material/alt_route: Triage")
            html(f'<div class="ce-card"><div class="ce-quote" style="margin-bottom:14px">{esc(d.summary)}</div>'
                 + theme.kv([
                     ("Complaint type", d.complaint_type or "none"),
                     ("Descriptor", d.descriptor or "none"),
                     ("Street", " & ".join(filter(None, [d.street_name and d.street_name.title(),
                                                          d.cross_street and d.cross_street.title()])) or "not given"),
                     ("District", t.district_label or "unknown"),
                     ("Agency", AGENCY_NAMES.get(t.agency, t.agency or "none")),
                     ("AI confidence", d.confidence),
                 ], pairs_per_row=2) + (f'<div style="color:#777;font-size:13px;margin-top:10px">{icon("info", 15)} District: '
                       f'{esc(t.district_note)}</div>' if t.district_note else "") + "</div>")
            if getattr(d, "other_problems", None):
                with st.container(border=True):
                    st.markdown("##### :material/playlist_add: Also reported in this request")
                    st.caption("Each needs its own request so it reaches the right agency. Press Analyze to file one.")
                    for i, item in enumerate(d.other_problems):
                        c1, c2 = st.columns([5, 2], vertical_alignment="center")
                        c1.markdown(md(item))
                        c2.button("Analyze", key=f"other-{i}", icon=":material/arrow_forward:", width="stretch",
                                  on_click=load_text, args=(item,))
            with st.expander("Why the AI chose this", icon=":material/psychology:"):
                st.markdown(md(d.reasoning))

            if t.boro_cd:
                st.markdown(f"##### :material/map: {t.district_label}: this street's past year")
                m, h = st.columns([1, 1], gap="medium")
                m.plotly_chart(district_map(t), width="stretch", config={"displayModeBar": False})
                fig = history_chart(t)
                if fig:
                    h.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
                    h.caption("311 complaints on this street in this district, by type. Blue = this request's type.")
                else:
                    h.info("No past complaints found on this street.", icon=":material/info:")
                if t.matched_candidate:
                    mc = t.matched_candidate
                    st.info(f"**Already a capital candidate:** {mc['complaints']:,} complaints, "
                            f"{mc['signal_complaints']:,} of them describing a failed asset ({mc['reason']}).",
                            icon=":material/construction:")

        if res.get("cost"):
            st.markdown("##### :material/savings: Lasting fix: planning estimate")
            show_cost(res["cost"])

        if res.get("alert_id"):
            st.markdown("##### :material/notifications_active: Agency alert")
            show_alert(res["alert_id"])
        if res.get("reply_id"):
            st.markdown("##### :material/mail: Reply to the resident")
            show_alert(res["reply_id"])

        u, bank = t.usage, t.bank_match
        lu = u.get("life_safety") or {}
        au = (alerts.get(res["alert_id"]).get("usage") or {}) if res.get("alert_id") else {}
        tokens = lambda x: x.get("input_tokens", 0) + x.get("output_tokens", 0)
        cu = res["cost"].usage if res.get("cost") else {}
        ru = (alerts.get(res["reply_id"]).get("usage") or {}) if res.get("reply_id") else {}
        total = sum(x.get("cost_usd", 0) for x in (u, au, cu, ru, lu))
        triage_badge = (theme.badge("Triage: no AI call, answer reused from the request bank", "#B5DBFF", "#000", "bolt")
                        if bank else theme.badge(f"Triage: {tokens(u):,} tokens ({u.get('cached_input_tokens', 0):,} "
                                                 "cached)", "#EEEEEE", "#000", "token"))
        safety_badge = theme.badge(f"Life-safety check: {tokens(lu):,} tokens", "#EEEEEE", "#000", "token") if lu else ""
        alert_badge = theme.badge(f"Alert: {tokens(au):,} tokens", "#EEEEEE", "#000", "token") if au else ""
        cost_badge = theme.badge(f"Cost estimate: {tokens(cu):,} tokens", "#EEEEEE", "#000", "token") if cu else ""
        reply_badge = theme.badge(f"Reply: {tokens(ru):,} tokens", "#EEEEEE", "#000", "token") if ru else ""
        html('<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px">' + triage_badge + safety_badge + cost_badge + alert_badge
             + reply_badge
             + theme.badge(f"AI cost for this request: ${total:.5f}", "#000", "#fff", "payments") + "</div>")
        if bank and st.button("Run the AI fresh instead", icon=":material/refresh:"):
            run(st.session_state.req_text, district, use_bank=False, box=tower_slot)
            st.rerun()

theme.footer()
