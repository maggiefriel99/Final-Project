# Tokenomics and scale (measured)

Measured 2026-10-24 on 40 real 311 records sampled from September 2026 (complaint-type mix as received), each run through the live pipeline with fresh, billed model calls. Model: gpt-6-luna at $0.10 per million input tokens, $0.01 cached, $0.50 output. Reproduce: `python analysis/tokenomics.py`.

## Volume (real data)

- All 311 requests, Oct 2025 – Sep 2026: 3,963,900 (about 330,325 a month; 10,860 a day on average)
- Busiest day in that year: 2026-02-24 (22,805)
- Busiest day since 2020: 2020-08-04 (24,415)

## Tokens and cost per agent (median tokens, mean billed cost)

| Agent | Runs on share of records | Input | Cached input | Output | Cost per run | Seconds |
|---|---|---|---|---|---|---|
| triage | 100% | 5,630 | 5,504 | 134 | $0.00014 | 2.02 |
| agency alert | 82% | 729 | 0 | 139 | $0.00014 | 2.11 |
| resident reply | 98% | 1,213 | 0 | 66 | $0.00016 | 1.63 |
| cost estimator | 8% | 2,177 | 0 | 142 | $0.00031 | 1.42 |

**Per 311 request (all agents):** mean $0.00043, 90th percentile $0.00048, median 5.975 s end to end. Routes: {'operating': 30, 'human_review': 7, 'capital': 3}.
**One memo:** $0.0065 (37,461 input and 7,485 output tokens; verified: True).

## At scale

- Processing every 311 request: **$142.01 a month** ($1,704.08 a year).
- Average day: $4.67. Record day: $10.50 ($11.66 if every request cost as much as the 90th percentile). Current daily cap: $10; recommended for live use: $24.

| Staff users | 311 processing | Memos (4/user/month, assumed) | Total per month | Per user per month |
|---|---|---|---|---|
| 1 | $142.01 | $0.0261 | $142.03 | $142.03 |
| 100 | $142.01 | $2.61 | $144.62 | $1.45 |
| 10,000 | $142.01 | $261.19 | $403.20 | $0.0403 |

## What limits scale

- **Human approval, not AI cost.** About 594,585 drafts a month need a person. At 30 seconds each (assumed) that is about 4,955 staff hours, or 35.4 full-time staff, if a person approves every draft.
- **Tiered approval (the live design):** a person approves replies that give advice, capital and urgent/elevated alerts, and anything flagged; the rest is sent automatically, with a 2% random quality sample. In the measured drafts 15% needed a person, so about 100,914 reviews a month (841 hours, 6.0 full-time staff).
- **Throughput.** In the busiest hour of the record day (10% of the day, assumed), about 0.68 requests arrive per second; at the measured median time per request that needs about 5 requests processed in parallel.
- **Request bank lookup** (scans every saved request): 1,000 saved: 0.02s, 10,000 saved: 0.114s, 50,000 saved: 0.597s.
- **Daily-cap check** (reads the whole usage log): 10,000 log lines: 0.011s, 100,000 log lines: 0.106s, 300,000 log lines: 0.326s.

Assumptions (not measured): memos per user per month = 4; seconds to review one draft = 30; staff work hours per month = 140; peak hour share of day = 0.1; qa sample share of auto sent = 0.02.
