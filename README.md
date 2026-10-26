# CivicEquity

A multi-agent app that turns a year of NYC 311 complaints into capital planning and equity insights for City
government. It separates complaints that need an **operating** fix (alert the right agency) from patterns that
signal a **capital** need, estimates what the lasting fix would cost from the City's own capital plan, compares
where capital dollars go with where needs are (two equity views, raw and income-adjusted), and drafts a
one-page memo for the Mayor's Office and OMB that is fact-checked sentence by sentence.

**Report:** [`report.html`](report.html) · **Slides:** [`slides.html`](slides.html) (download and open in a browser) ·
**How it all runs:** [`HARNESS.md`](HARNESS.md)

## Run it locally

**Requirements:** Python 3.12 or newer (built and tested on 3.14). No database or other services needed.

1. **Clone the repo and enter it**
   ```bash
   git clone <REPO_URL> civicequity
   cd civicequity
   ```
2. **Create a virtual environment and install dependencies**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
   This installs Streamlit, Plotly, pandas, geopandas, shapely, requests, PyYAML, pydantic-ai, python-dotenv,
   statsmodels, and scipy. Everything installs from pip with no extra system software.
3. **Add your API key.** The app looks for a `.env` file in this folder first, then in the folder above it.
   If you already have a `.env` one folder up with `PORTKEY_API_KEY`, skip this step. Otherwise:
   ```bash
   cp .env.example .env               # then put your Portkey key in .env
   ```
   The app needs only `PORTKEY_API_KEY`. Every AI call uses `gpt-6-luna`.
4. **Start the app**
   ```bash
   streamlit run app.py
   ```
   Then open http://localhost:8501 in your browser. Keep the terminal open while you use the app (Ctrl+C stops it).

## What's in the app

| Page | What it does |
|---|---|
| **Overview** | The problem, headline numbers, how a request is handled, and the streets where problems keep coming back |
| **New request** | Paste a resident's email or complaint (or click a sample, including two prompt-injection attempts and three emergency/ambiguous cases). Triage, a life-safety check, cost estimate for capital needs, and a drafted agency alert you approve or reject. Possible emergencies show a "call 911" message and urgent alerts to the emergency agencies; staff can also flag any request as an emergency |
| **Agency alerts** | Every drafted alert waits here for a person; open emergency alerts are listed first. **Nothing is ever sent**: this is a class project, and approving only records the decision. CivicEquity never contacts 911 or any agency |
| **Capital memo › Memo** | Generates the one-page memo plus an appendix with charts; download as a self-contained HTML file |
| **Capital memo › Equity dashboard** | Equity measures computed by code (no AI, no cost): concentration index and Gini of capital per resident, share of capital in high heat-vulnerability districts, a fairness (Lorenz/concentration) curve, repeat problems vs. capital, capital by asset type, and project follow-through. "Compare by" income, limited English, or heat vulnerability; each difference comes with a district-level significance test |
| **Audit & safety** | Every run logged with safety checks, tokens, and cost; spending against the daily cap; red-team results |

## Data

The repo ships with a **snapshot** in `data/` (about 21 MB), so the app runs offline and starts in seconds.

| Source | Link |
|---|---|
| 311 Service Requests (DOT, DEP, Parks, Sanitation; Oct 2025 – Sep 2026) | [NYC Open Data erm2-nwe9](https://data.cityofnewyork.us/d/erm2-nwe9) |
| Capital Projects Dashboard: budget and schedule | [fb86-vt7u](https://data.cityofnewyork.us/d/fb86-vt7u) |
| Capital Projects Database (CPDB): project locations | [h2ic-zdws](https://data.cityofnewyork.us/d/h2ic-zdws), [9jkp-n57r](https://data.cityofnewyork.us/d/9jkp-n57r) |
| Community District boundaries | [5crt-au7u](https://data.cityofnewyork.us/d/5crt-au7u) |
| Population by Community District (ACS 2015–2019) | [9ji4-nien](https://data.cityofnewyork.us/d/9ji4-nien) |
| PLUTO tax lots (which lots the City owns, for sidewalk responsibility) | [64uk-42ks](https://data.cityofnewyork.us/d/64uk-42ks) |
| DOT In-house Street Resurfacing (last 3 years) | [ffaf-8mrv](https://data.cityofnewyork.us/d/ffaf-8mrv) |
| Pedestrian Ramp Program Progress (last 3 years) | [e7gc-ub6z](https://data.cityofnewyork.us/d/e7gc-ub6z) |
| Parks Forestry Work Orders (capital tree planting and tree-sidewalk repair) | [bdjm-n7q4](https://data.cityofnewyork.us/d/bdjm-n7q4) |
| Street and Highway Capital Reconstruction Projects (used to place projects) | [97nd-ff3i](https://data.cityofnewyork.us/d/97nd-ff3i) |
| Median household income by district (Census ACS 2020–2024, table B19013) | [api.census.gov](https://api.census.gov/data/2024/acs/acs5) |
| Heat Vulnerability Index by district (NYC Health, Environment & Health Data Portal) | [EHDP-data indicator 2191](https://github.com/nychealth/EHDP-data) |

**To switch to fresh data**, click **Refresh NYC data** in the sidebar, or run `python prepare_data.py`
(about 2 minutes; if it fails, the existing snapshot stays). Refreshing income needs an optional free
`CENSUS_API_KEY` in `.env`; without it the bundled income file is kept. The app itself never needs that key.

## How it works

Full description of every flow, every agent, and the agent harness (shared safety rules, tool allowlist,
limits): **[HARNESS.md](HARNESS.md)**. `python analysis/harness_check.py` tests the harness rules (no cost).

**Rules files you can read and edit:**
- [`definitions.yaml`](definitions.yaml): operating vs. capital, based on NYC Comptroller Directive #10 (capital work
  costs at least $50,000, lasts at least 5 years, and builds a new asset or substantially improves one).
- [`priorities.yaml`](priorities.yaml): the administration's stated priorities, each tied to a quote or fact checked
  word for word against its official source.
- [`advice.yaml`](advice.yaml): the only advice a resident reply may give (10 entries), each tied to an official page.
- [`emergencies.yaml`](emergencies.yaml): which agencies get an alert for each kind of emergency, from the City's
  Citywide Incident Management System (CIMS) charts, and the fixed "call 911" message (311's own wording).
  `python analysis/verify_sources.py` checks every quote, CIMS role, and CIMS primary-agency list word for word.

**Agents** (all `gpt-6-luna`; code decides the order, no agent calls another):

| Flow | Agent | Job | Tools / inputs |
|---|---|---|---|
| New request | Triage | Map the request to an official 311 complaint type, descriptor, and street (or, when no type fits, the official advice that does) | Complaint catalog from the data; code decides the route |
| New request | Life-Safety Screener | On every request, in parallel with Triage: emergency (911-level), urgent hazard (fix soon), or none | Emergency (from it OR Triage): fixed 911 message to the resident and urgent alerts (simulated) to the CIMS agencies. Urgent hazard: the agency alert is marked urgent by code. Danger words alone hold the request for a person |
| New request | Cost Estimator | Choose the scope of the lasting fix for a capital need | Tool: comparable current City capital projects; code supplies all dollar figures |
| New request | Operating Router | Draft the agency alert | Only checked facts; code checks every number; a person approves |
| New request | Resident Reply | Draft the reply to the resident | Only the verified advice library; code checks it; a person approves |
| Memo | Needs Analyst | Pick best-value investments and already-funded quick wins | Facts computed by code |
| Memo | Equity Analyst | Read the two equity views and the City's priorities | Facts computed by code |
| Memo | Memo Writer | Draft the one-page memo | The analysts' picks and the facts |
| Memo | Fact Verifier | Check every claim in every sentence against the facts | Code also checks every number and quote; anything unverified is removed |

**Safety:** input cleaning, personal-information removal, prompt-injection pattern checks, the provider's content
filter, output checks before anything is passed on, a request bank that never reuses flagged requests, per-run
token caps, a $10/day spending cap, human approval for every alert, and an append-only audit log
(`logs/audit_log.jsonl`). `python redteam/run_redteam.py` runs 22 attacks and 8 normal requests against Triage.
`python analysis/ambiguity_check.py` tests loosely worded requests, emergencies, and look-alike non-emergencies.
**After any change, run `python run_checks.py`** (no-cost checks: code, unit checks, harness rules, sources) or
`python run_checks.py --full` to add the AI tests (red team, life safety, fairness; about $0.05).

## Analysis

- `python analysis/validate_cost_model.py`: statistical validation of the cost model (robust tests, permutation
  test, bootstrap, repeated cross-validation, range coverage, diagnostics, data cleaning log).
- `Rscript analysis/compare_cost_models.R` (optional; needs R with lme4, rstanarm, ggplot2, patchwork, dplyr, tidyr,
  readr, jsonlite): compares the app's model with hierarchical and Bayesian hierarchical models on identical folds.
  The app never needs R.

## Project layout

```
app.py                   Streamlit app (navigation)
views/                   Pages: home, new_request, alerts, memo, equity, audit
prepare_data.py          Builds the data/ snapshot from NYC Open Data and the Census
definitions.yaml         Operating vs. capital rules and the complaint-to-capital crosswalk
priorities.yaml          City priorities with verified sources
prompts/                 One prompt file per agent, plus _harness_rules.md (shared rules for all agents)
civicequity/
  agents/                triage, life_safety, cost_estimator, operating_router, resident_reply, memo
  harness.py             Builds and runs every agent: shared rules, registry, tool allowlist, limits
  emergency.py           Emergency escalation (code only; simulation, nothing is sent)
  needs.py, capital.py   Capital candidates; capital dollars by district
  cost_model.py          Cost model (cleaned capital plan, scope + borough, per-scope ranges)
  memo_data.py           Every fact the memo may use, computed by code
  equity.py              Equity dashboard measures (concentration index, Gini, groups, follow-through)
  fact_check.py          Number and quote check
  safety.py, privacy.py  Input and output checks; personal-information removal
  audit.py, alerts.py    Audit log; alert approval queue
  request_bank.py        Reuse of earlier triage answers
  llm.py, config.py      Model, token and cost logging, budgets, .env lookup
  ui/                    Theme, skyline illustrations, shared data loaders
data/                    The snapshot (committed)
redteam/                 Attack suite and results
analysis/                Cost model validation and R model comparison
```

CivicEquity is an independent student project built on NYC Open Data and U.S. Census data. It is not affiliated
with or endorsed by the City of New York.
