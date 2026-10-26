import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from civicequity import audit, config, harness, llm, monitor
from civicequity.ui import theme
from civicequity.ui.theme import BLUE, CAUTION, ERROR, esc, html, icon

theme.page_header("Accountability", "verified_user", "Audit & safety",
                  "Every run is logged: what came in (personal info removed), every safety check, "
                  "what the AI answered, tokens, cost, and anything stopped or blocked.")

log = audit.read()
runs = [r for r in log if r.get("flow") in ("triage", "cost_estimator", "operating_router", "resident_reply", "memo")]
decisions = [r for r in log if r.get("flow") == "human_review"]
triage_runs = [r for r in runs if r["flow"] == "triage"]
held = [r for r in triage_runs if r.get("route") == "human_review"]
stopped = [r for r in runs if r.get("stopped")]
cost = sum(r.get("cost_usd", 0) for r in runs)
tokens = sum(r.get("tokens", {}).get("input_tokens", 0) + r.get("tokens", {}).get("output_tokens", 0) for r in runs)

theme.stats([
    ("receipt_long", f"{len(runs):,}", "AI runs logged"),
    ("front_hand", f"{len(held):,}", "requests held for human review"),
    ("block", f"{len(stopped):,}", "runs stopped or blocked"),
    ("token", f"{tokens:,}", "tokens used"),
    ("payments", f"${cost:.4f}", "total AI cost"),
])

mon = monitor.check()
if mon["warnings"]:
    st.error("**Unusual activity.** A jump like this usually means an attack, a broken prompt or model change, or a "
             "real citywide event. Check the runs below.\n\n" + "\n".join(f"- {w}" for w in mon["warnings"]),
             icon=":material/monitor_heart:")
elif not mon["enough_history"]:
    st.info(f"**Monitoring:** not enough history yet to spot unusual activity ({mon['requests_baseline']} requests in "
            "the previous 7 days; spikes are judged once there are 50). Spending is still checked against the cap.",
            icon=":material/monitor_heart:")
else:
    st.success(f"**Monitoring:** no unusual activity ({mon['requests_last_hour']} requests in the last hour; shares of "
               "held, escalated, flagged, and blocked requests and today's spending are within normal ranges).",
               icon=":material/monitor_heart:")

spent = llm.spent_today()
left, right = st.columns([2, 3], gap="large")
with left:
    with st.container(border=True):
        st.markdown("##### :material/savings: Spending limits")
        st.progress(min(1.0, spent / config.DAILY_BUDGET_USD),
                    text=f"Today: \\${spent:.4f} of the \\${config.DAILY_BUDGET_USD:.0f} daily cap")
        st.caption(f"Each run is also capped at {config.RUN_TOKEN_BUDGET:,} tokens and {config.RUN_REQUEST_LIMIT} "
                   "model calls. A typical request uses about 3,000 tokens and 1 call.")
        if triage_runs:
            paid = [r for r in triage_runs if r.get("cost_usd")]
            reused = sum(r["safety_checks"]["request_bank"]["used"] for r in triage_runs)
            avg = sum(r["cost_usd"] for r in paid) / len(paid) if paid else 0
            html(theme.kv([("Average triage cost", f"${avg:.5f}"),
                           ("Answered from the bank", f"{reused} of {len(triage_runs)} (free)"),
                           ("Human decisions logged", f"{len(decisions)}")]))

    with st.container(border=True):
        st.markdown("##### :material/shield: What each safety layer caught")
        layers = {"Manipulation patterns": 0, "Provider filter": 0, "AI's own check": 0, "Answer checks": 0,
                  "Personal info removed": 0, "Budget limits": 0}
        for r in triage_runs:
            c = r["safety_checks"]
            layers["Manipulation patterns"] += c["pattern_check"]["flagged"]
            pf = c.get("provider_filter") or {}
            layers["Provider filter"] += bool(pf.get("blocked") or pf.get("jailbreak_detected")
                                              or pf.get("indirect_attack_detected"))
            layers["AI's own check"] += bool((c.get("agent_flag") or {}).get("suspicious_instructions"))
            layers["Answer checks"] += not (c.get("output_validation") or {"passed": True})["passed"]
            layers["Personal info removed"] += bool(c["personal_info_removed"])
            layers["Budget limits"] += bool((c.get("limits") or {}).get("exceeded"))
        fig = go.Figure(go.Bar(x=list(layers.values()), y=list(layers), orientation="h", marker_color="#000",
                               text=list(layers.values()), textposition="outside", cliponaxis=False))
        fig.update_layout(height=250, margin=dict(l=0, r=30, t=4, b=0), plot_bgcolor="white",
                          xaxis=dict(visible=False), yaxis=dict(autorange="reversed"), font=dict(family="Noto Sans"))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        st.caption("Number of logged requests where each layer fired. One request can trip several layers.")

with right:
    rt_file = config.ROOT / "redteam" / "results.json"
    with st.container(border=True):
        st.markdown("##### :material/swords: Red-team test")
        if rt_file.exists():
            rt = json.loads(rt_file.read_text())
            s = rt["summary"]
            o = s["outcomes"]
            html('<div class="ce-stats" style="margin:6px 0 12px 0">'
                 f'<div class="ce-stat"><div class="v">{o.get("caught", 0)}<span style="font-size:18px;color:#777">'
                 f' / {s["attacks"]}</span></div><div class="l">attacks caught and held for review</div></div>'
                 f'<div class="ce-stat"><div class="v">{o.get("FAILED", 0)}</div><div class="l">attacks that got '
                 f'what they wanted</div></div>'
                 f'<div class="ce-stat"><div class="v">{s["false_alarms"]}<span style="font-size:18px;color:#777">'
                 f' / {s["normal_requests"]}</span></div><div class="l">normal requests wrongly held</div></div></div>')
            df = pd.DataFrame(rt["results"])
            df = df[df["kind"] == "attack"][["id", "category", "outcome", "layers"]]
            df["layers"] = df["layers"].map(lambda x: ", ".join(x) or "-")
            st.dataframe(df, hide_index=True, width="stretch", height=280,
                         column_config={"id": "Case", "category": "Attack", "outcome": "Result",
                                        "layers": "Layers that fired"})
            st.caption(f"Run {s['run_at'][:10]} · \\${s['total_cost_usd']:.4f} · re-run with "
                       "`python redteam/run_redteam.py`")
        else:
            st.info("Run `python redteam/run_redteam.py` to test the defenses against 22 attacks.", icon=":material/info:")

st.markdown("##### :material/receipt_long: Run log")
if not runs:
    st.info("No runs yet.", icon=":material/info:")
else:
    rows = []
    for r in reversed(runs):
        t = r.get("tokens", {})
        rows.append({
            "time (UTC)": r["time"][:19].replace("T", " "), "step": r["flow"].replace("_", " "),
            "run": r["run_id"],
            "result": r.get("route") or r.get("status") or r.get("scope")
                      or ({True: "memo verified", False: "memo needs review"}.get(r.get("verified")) if r["flow"] == "memo" else None),
            "stopped": (r.get("stopped") or {}).get("by", ""),
            "why held": "; ".join(r.get("review_reasons", []))[:120],
            "tokens in": t.get("input_tokens", 0), "cached": t.get("cached_input_tokens", 0),
            "tokens out": t.get("output_tokens", 0), "cost $": r.get("cost_usd", 0.0),
        })
    df = pd.DataFrame(rows)
    f1, f2 = st.columns([2, 3])
    only = f1.segmented_control("Filter", ["All", "Held", "Stopped"], default="All", label_visibility="collapsed")
    if only == "Held":
        df = df[df["result"] == "human_review"]
    elif only == "Stopped":
        df = df[df["stopped"] != ""]
    st.dataframe(df, hide_index=True, width="stretch", height=360,
                 column_config={"cost $": st.column_config.NumberColumn(format="$%.6f")})
    pick = st.selectbox("Open a run's full record", [r["run"] for r in rows], index=None,
                        placeholder="Choose a run ...")
    if pick:
        st.json(next(r for r in runs if r["run_id"] == pick), expanded=2)

st.markdown("##### :material/rule: Agent harness")
st.caption("Every agent is built and run through one harness (civicequity/harness.py): shared safety rules first in "
           "every prompt, only registered agents and their listed tools, material always wrapped as data, spending "
           "and token limits, and safe stops. `python analysis/harness_check.py` tests these rules.")
st.dataframe(pd.DataFrame(harness.describe()), hide_index=True, width="stretch")
with st.expander("The shared safety rules every agent receives", icon=":material/gavel:"):
    st.markdown(harness.rules().split("## Your job")[0])

theme.footer()
