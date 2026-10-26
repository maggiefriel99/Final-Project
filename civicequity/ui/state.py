"""Data shared by every page, loaded once per server and reused."""

from datetime import datetime

import streamlit as st

from civicequity import capital, data, needs
from civicequity.cost_model import CostModel


@st.cache_resource(show_spinner="Loading NYC data ...")
def snapshot() -> dict:
    return data.load_snapshot()


@st.cache_resource(show_spinner="Finding capital candidates ...")
def candidates():
    return needs.capital_candidates(snapshot())


@st.cache_resource(show_spinner="Fitting the cost model ...")
def cost_model() -> CostModel:
    return CostModel(snapshot())


@st.cache_resource(show_spinner="Placing capital dollars by district ...")
def capital_by_district():
    return capital.district_capital(snapshot(), cost_model(), use_borough_costs=True)


def metadata() -> dict:
    return data.metadata()


def data_date() -> str:
    return f"{datetime.fromisoformat(metadata()['pulled_at']):%b %d, %Y}"


def district_options() -> dict[int, str]:
    d = snapshot()["districts"].sort_values("boro_cd")
    return dict(zip(d["boro_cd"].astype(int), d["district_label"]))


def clear() -> None:
    st.cache_resource.clear()
