# Does more history improve the cost model?

274 projects appear only in older capital-plan snapshots (May 2023 - Jan 2026); their budgets were converted to May 2026 dollars with the BLS PPI for net inputs to other nonresidential construction (WPUIP231230). Test: predict the 1138 current projects out of sample, with and without them.

| Training data | Out-of-sample R² | 50% coverage | 80% coverage | 50% range width |
|---|---|---|---|---|
| latest snapshot only | 0.293 | 49.6% | 79.1% | 5.46x |
| with older snapshots (BLS-adjusted) | 0.282 | 48.4% | 77.9% | 5.87x |

Adding the older projects raised the squared error on current projects by +0.0280 (paired t p = 6.6e-08).

Why: projects that left the plan are not like current ones (median budget, $ millions):

| Scope | Current | Left the plan |
|---|---|---|
| Green and natural drainage | 24.64 | 4.81 |
| Street light replacement | 0.3 | 29.88 |
| Street tree planting | 7.44 | 3.74 |
| Park reconstruction | 3.39 | 2.69 |
| Full street reconstruction | 19.58 | 14.63 |
| Sewer construction or upgrade | 20.44 | 5.3 |
| Water main replacement | 20.66 | 28.84 |

**Decision:** the app keeps training on the latest snapshot; the history and BLS conversion stay available (`CIVICEQUITY_COST_HISTORY=1`).
