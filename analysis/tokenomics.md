# Tokenomics and scale (measured)

Measured 2026-10-24 on 80 real 311 records sampled from September 2026 (complaint-type mix as received), each run through the live pipeline with fresh, billed model calls. Model: gpt-6-luna at $0.10 per million input tokens, $0.01 cached, $0.50 output. Reproduce: `python analysis/tokenomics.py`.

## Volume (real data)

- All 311 requests, Oct 2025 – Sep 2026: 3,963,900 (about 330,325 a month; 10,860 a day on average)
- Busiest day in that year: 2026-02-24 (22,805)
- Busiest day since 2020: 2020-08-04 (24,415)

## Tokens and cost per agent (median tokens, mean billed cost)

| Agent | Runs on share of records | Input | Cached input | Output | Cost per run | Seconds |
|---|---|---|---|---|---|---|
| life safety screener | 100% | 1,844 | 1,673 | 54 | $0.00006 | 1.71 |
| triage | 100% | 7,393 | 7,222 | 146 | $0.00016 | 2.3 |
| agency alert | 98% | 1,264 | 0 | 128 | $0.00022 | 2.25 |
| resident reply | 99% | 2,296 | 2,080 | 61 | $0.00008 | 1.78 |
| cost estimator | 4% | 3,233 | 2,443 | 140 | $0.00019 | 1.67 |

**Per 311 request (all agents):** mean $0.00052, 90th percentile $0.00054, median 6.6 s end to end. Routes: {'operating': 75, 'capital': 3, 'human_review': 2}. Escalated as emergencies: 0% (alerts written by code, no AI cost); urgent hazards: 0%.
**One memo:** $0.0079 (43,087 input and 6,937 output tokens; verified: True).

## At scale

- Processing every 311 request: **$171.90 a month** ($2,062.81 a year).
- Average day: $5.65. Record day: $12.71 ($13.24 if every request cost as much as the 90th percentile). Current daily cap: $10; recommended for live use: $27. Past the cap, only the Life-Safety Screener keeps running, on a $2 reserve; on the record day the screener alone would cost $1.50, so the reserve covers emergency detection for every request.

| Staff users | 311 processing | Memos (4/user/month, assumed) | Total per month | Per user per month |
|---|---|---|---|---|
| 1 | $171.90 | $0.0317 | $171.93 | $171.93 |
| 100 | $171.90 | $3.17 | $175.07 | $1.75 |
| 10,000 | $171.90 | $317.25 | $489.15 | $0.0489 |

## What limits scale

- **Human approval, not AI cost.** About 648,098 drafts a month need a person. At 30 seconds each (assumed) that is about 5,401 staff hours, or 38.6 full-time staff, if a person approves every draft.
- **Tiered approval (the live design):** a person approves replies that give advice, capital, urgent, and elevated alerts, every emergency alert, and anything flagged (the fixed 911 message is shown at once); the rest is sent automatically, with a 2% random quality sample. In the measured drafts 9% needed a person, so about 69,598 reviews a month (580 hours, 4.1 full-time staff).
- **Throughput.** In the busiest hour of the record day (10% of the day, assumed), about 0.68 requests arrive per second; at the measured median time per request that needs about 5 requests processed in parallel.
- **Request bank lookup** (scans every saved request): 1,000 saved: 0.021s, 10,000 saved: 0.116s, 50,000 saved: 0.611s.
- **Daily-cap check** (reads the whole usage log): 10,000 log lines: 0.012s, 100,000 log lines: 0.125s, 300,000 log lines: 0.44s.

Assumptions (not measured): memos per user per month = 4; seconds to review one draft = 30; staff work hours per month = 140; peak hour share of day = 0.1; qa sample share of auto sent = 0.02.

## Compared with the previous measurement

Previous run: 2026-10-24, 40 records, before the Life-Safety Screener, the shared harness rules, and the longer Triage prompt (`analysis/tokenomics_before.md`).

| Measure | Before | Now |
|---|---|---|
| Cost per 311 request (mean) | $0.00043 | $0.00052 |
| 90th percentile | $0.00048 | $0.00054 |
| All 311, per month | $142.01 | $171.90 |
| Record day | $10.50 | $12.71 |
| Recommended live daily cap | $24 | $27 |
| One memo | $0.0065 | $0.0079 |
| Median seconds per request | 5.975 | 6.6 |
| Tiered review, full-time staff | 6.0 | 4.1 |
