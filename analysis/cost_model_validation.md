# Cost model validation

Model: log(budget) ~ scope + borough, fit to 1138 site-specific projects in NYC's current capital plan across 7 scopes. Reproduce with `python analysis/validate_cost_model.py`.

## Fit

- R² = 0.308 (adjusted 0.302); overall F = 50.15, p < 0.001
- Scope explains 28.5% of the variation in log budgets; borough explains 1.0% on top of scope.
- Robust (HC3) Wald test, scope: F = 71.26, p < 0.001
- Robust (HC3) Wald test, borough: F = 3.85, p 0.004
- Permutation test, borough (2,000 shuffles within scope): p 0.003

## Borough multipliers (1.0 = five-borough average)

| Borough | Multiplier | Bootstrap 95% CI | Without influential projects |
|---|---|---|---|
| Bronx | 1.23 | 1.03 – 1.45 | 1.23 |
| Brooklyn | 1.11 | 0.97 – 1.28 | 1.03 |
| Manhattan | 0.81 | 0.70 – 0.94 | 0.76 |
| Queens | 1.10 | 0.95 – 1.27 | 1.16 |
| Staten Island | 0.82 | 0.67 – 1.02 | 0.90 |

## Out-of-sample accuracy (10 x stratified 10-fold, 1138 projects, each predicted 10 times)

| Model | Out-of-sample R² | RMSE (log) | Median abs. % error | Within 2× of actual |
|---|---|---|---|---|
| citywide median | -0.001 | 1.593 | 78% | 39% |
| scope only | 0.288 | 1.343 | 65% | 41% |
| scope + borough (the model) | 0.293 | 1.339 | 64% | 39% |

- Model vs. a single citywide median: paired t-test p < 0.001, Wilcoxon p < 0.001
- Adding borough to scope, out of sample: change in mean squared error -0.0120 (paired t p 0.004, Wilcoxon p 0.001)

## Are the ranges honest?

- The 50% range contained 49.4% of held-out projects (target 50%).
- The 80% range contained 79.2% of held-out projects (target 80%).

| Scope | Projects | 50% range covered | Range width (high ÷ low) |
|---|---|---|---|
| Park reconstruction | 776 | 50% | 5.5× |
| Full street reconstruction | 130 | 49% | 5.3× |
| Sewer construction or upgrade | 94 | 49% | 4.9× |
| Water main replacement | 77 | 49% | 5.2× |
| Green and natural drainage | 27 | 46% | 4.6× |
| Street tree planting | 20 | 40% | 7.8× |
| Street light replacement | 14 | 51% | 5.8× |

## Could the ranges be narrower?

| Predictors | Out-of-sample R² | 50% range width (high ÷ low) |
|---|---|---|
| scope + borough | 0.293 | 5.47× |
| + managing agency | 0.335 | 5.41× |
| + size words in name | 0.309 | 5.18× |
| + number of districts | 0.299 | 5.4× |
| + all three | 0.354 | 5.14× |

Agency and project names are unknown for a problem that has no project yet, so these predictors could not be used for new 311 needs even where they help.

## Diagnostics

- Residual normality (Jarque-Bera): stat 89.7, p < 0.001, skew -0.63, kurtosis 3.56
- Constant variance (Breusch-Pagan): LM 13.8, p 0.181
- Influential projects (Cook's D > 4/n): 60

## Data cleaning

- 10 projects under the $50,000 capital minimum dropped.
- 120 projects reclassified because their names contradict their capital plan category. Largest examples:
  - Storm & San Sewers in 20th Ave b/t 126 St & US Bulkhead Line ($147.5M): Water main replacement -> Sewer construction or upgrade
  - SEQ - New Storm Sewer N. Conduit Ave ($122.0M): Water main replacement -> Sewer construction or upgrade
  - Storm & Comb Sanitary SE Construction in Cropsey Ave BKLYN ($113.5M): Water main replacement -> Sewer construction or upgrade
  - STORM & SAN SWR, WILLET PT BLVD WHITESTONE AREA PHASE 2 ($107.5M): Water main replacement -> Sewer construction or upgrade
  - New Storm Sewers in Saxon Ave & Draper Pl, SI, HWR00511 ($94.3M): Water main replacement -> Sewer construction or upgrade
- Scopes left with too few real projects to price (now handled as existing citywide programs or not offered): Pedestrian ramp upgrades (3), Street resurfacing (1), Sidewalk reconstruction (1), Traffic signal upgrade (1)
