import streamlit as st

from civicequity import alerts
from civicequity.ui import theme
from civicequity.ui.theme import AGENCY_ICONS, PRIORITY, esc, html, icon, md

theme.page_header("Human in the loop", "notifications_active", "Agency alerts",
                  "Every alert waits here for a person. In this class project approving only records the decision: nothing is ever sent to an agency or to 911.")

items = alerts.all_alerts()
count = lambda s: sum(a["status"] == s for a in items)
emergencies = sum(a.get("kind") == "emergency_alert" and a["status"] in ("pending approval", "needs editing") for a in items)
theme.stats([
    ("schedule", f"{count('pending approval')}", "waiting for approval"),
    ("edit", f"{count('needs editing')}", "need editing (failed a check)"),
    ("task_alt", f"{count('approved')}", "approved"),
    ("close", f"{count('rejected')}", "rejected"),
])
if emergencies:
    st.error(f"**{emergencies} open emergency alert{'s' if emergencies != 1 else ''}**, listed first. "
             "Simulation: nothing was sent to 911 or to any agency.", icon=":material/e911_emergency:")
# Open emergency alerts first, then newest first.
items.sort(key=lambda a: not (a.get("kind") == "emergency_alert" and a["status"] in ("pending approval", "needs editing")))

if not items:
    html(f'<div class="ce-card" style="text-align:center;padding:34px">{icon("inbox", 40)}'
         f'<h4 style="margin-top:8px">No alerts yet</h4><p style="color:#555">Analyze a request on the '
         f'<b>New request</b> page and its agency alert will appear here.</p></div>')
    st.stop()

c1, c2 = st.columns([3, 2])
show = c1.segmented_control("Show", ["Open", "Approved", "Rejected", "All"], default="Open",
                            label_visibility="collapsed")
agencies = sorted({a["agency"] for a in items if a.get("agency")})
pick = c2.multiselect("Agency", agencies, placeholder="All agencies", label_visibility="collapsed")
want = {"Open": {"pending approval", "needs editing"}, "Approved": {"approved"},
        "Rejected": {"rejected"}, "All": None}[show or "All"]
items = [a for a in items if (want is None or a["status"] in want) and (not pick or a["agency"] in pick)]
st.caption(f"{len(items)} alert{'s' if len(items) != 1 else ''}")

for a in items:
    d = a["draft"] or {"subject": "(no draft: write one)", "body": "", "priority": "routine",
                       "priority_reason": "", "requested_action": ""}
    pr = d["priority"]
    with st.container(border=True):
        html(f'<div style="display:flex;gap:14px;align-items:flex-start">'
             f'<div class="ce-stat" style="padding:0;border:0"><div class="ic {"blue" if "pending" in a["status"] else ""}" '
             f'style="margin:0">{icon(AGENCY_ICONS.get(a["agency"], "apartment"), 22)}</div></div>'
             f'<div style="flex:1"><div style="font-weight:700;font-size:17px">{esc(d["subject"])}</div>'
             f'<div style="color:#555;font-size:14px;margin-top:2px">'
             f'{"Reply to resident" if a.get("kind") == "resident_reply" else esc(a["agency_name"])}'
             f'{" · EMERGENCY (simulation, not sent)" if a.get("kind") == "emergency_alert" else ""} · '
             f'{esc(a["district"] or "district unknown")} · {esc(a["created"][:16].replace("T", " "))} UTC</div></div>'
             f'<div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">'
             f'{theme.badge(pr.title(), PRIORITY[pr], "#000" if pr == "elevated" else "#fff")}'
             f'{theme.badge(a["status"].title(), "#EEEEEE", "#000")}</div></div>')
        with st.expander("Open", icon=":material/open_in_full:"):
            if a["problems"]:
                st.warning("Failed a check: " + "; ".join(md(p) for p in a["problems"]), icon=":material/edit:")
            if a["status"] in ("approved", "rejected") or a["status"].startswith("shown"):
                html(f'<div class="ce-quote">{esc(d["body"])}</div>')
                if a["status"].startswith("shown"):
                    st.caption("Fixed 911 message written by code; shown at once. Simulation: nothing was sent.")
                    continue
                st.caption(f"{a['status'].title()} by {md(a['reviewer'])} · {a['decided'][:16].replace('T', ' ')} UTC"
                           + (" · edited by reviewer" if a.get("edited_by_reviewer") else ""))
                continue
            with st.form(f"q-{a['alert_id']}", border=False):
                subject = st.text_input("Subject", d["subject"])
                body = st.text_area("Message", d["body"], height=180)
                action = st.text_input("Requested action", d["requested_action"])
                reviewer = st.text_input("Your name", st.session_state.get("reviewer", ""))
                b1, b2 = st.columns(2)
                ok = b1.form_submit_button("Approve", type="primary", icon=":material/task_alt:", width="stretch")
                no = b2.form_submit_button("Reject", icon=":material/close:", width="stretch")
            if ok or no:
                if not reviewer.strip():
                    st.error("Enter your name first.", icon=":material/person:")
                else:
                    st.session_state.reviewer = reviewer
                    edited = {**d, "subject": subject, "body": body, "requested_action": action}
                    alerts.decide(a["alert_id"], "approved" if ok else "rejected", reviewer,
                                  edited_draft=edited if edited != d else None)
                    st.toast("Decision logged.", icon=":material/task_alt:")
                    st.rerun()

theme.footer()
