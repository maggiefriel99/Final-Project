"""CivicEquity: Streamlit app. Run with: streamlit run app.py"""

import streamlit as st

from civicequity import config, data, llm, logstore, monitor
from civicequity.ui import state, theme

st.set_page_config(page_title="CivicEquity · NYC", page_icon=":material/location_city:", layout="wide")
theme.inject()


@st.cache_resource(show_spinner=False)
def apply_log_retention() -> dict:
    """Once per app start: delete log records older than the retention period (config.LOG_RETENTION_DAYS)."""
    return logstore.prune_all()


apply_log_retention()

pages = st.navigation({
    "Requests": [
        st.Page("views/home.py", title="Overview", icon=":material/location_city:", default=True),
        st.Page("views/new_request.py", title="New request", icon=":material/edit_note:"),
        st.Page("views/alerts.py", title="Agency alerts", icon=":material/notifications_active:"),
    ],
    "Capital memo": [
        st.Page("views/memo.py", title="Memo", icon=":material/description:"),
        st.Page("views/equity.py", title="Equity dashboard", icon=":material/balance:"),
    ],
    "Oversight": [
        st.Page("views/audit.py", title="Audit & safety", icon=":material/verified_user:"),
    ],
})

theme.sidebar_brand(state.data_date(), llm.spent_today(), config.DAILY_BUDGET_USD)
if monitor.check()["warnings"]:
    with st.sidebar:
        st.page_link("views/audit.py", label="Unusual activity: see Audit & safety", icon=":material/monitor_heart:")
with st.sidebar:
    st.space("small")
    if st.button("Refresh NYC data", icon=":material/sync:", help="About 2 minutes. If it fails, the current data stays.",
                 width="stretch"):
        with st.spinner("Pulling from NYC Open Data ..."):
            ok, log = data.refresh_snapshot()
        if ok:
            state.clear()
            st.toast("Data refreshed.", icon=":material/check_circle:")
            st.rerun()
        else:
            st.warning("Refresh failed. Still using the existing data.")
            st.code(log)

pages.run()
