"""Paths, API keys, model choice, and safety limits shared by the whole app."""

import os
from pathlib import Path

from dotenv import load_dotenv

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
# Measurement runs (analysis/tokenomics.py) point this elsewhere so test traffic stays out of the real logs.
LOG_DIR = Path(os.getenv("CIVICEQUITY_LOG_DIR", ROOT / "logs"))
DEFINITIONS_FILE = ROOT / "definitions.yaml"

# Look for .env in the repo first, then one folder up (where graders keep theirs).
# The first file found wins for each key, and real environment variables beat both.
for env_file in (ROOT / ".env", ROOT.parent / ".env"):
    if env_file.exists():
        load_dotenv(env_file, override=False)

PORTKEY_API_KEY = os.getenv("PORTKEY_API_KEY")
PORTKEY_BASE_URL = os.getenv("PORTKEY_BASE_URL", "https://api.portkey.ai/v1")

MODEL_NAME = "gpt-6-luna"  # the only model allowed on the course Portkey key
# gpt-6-luna behind Portkey rejects tool calls when reasoning effort is set, so it is off.
# This also keeps token use down.
MODEL_SETTINGS = {"openai_reasoning_effort": "none"}

# gpt-6-luna list prices, USD per 1M tokens (OpenAI API pricing page, standard tier, prompts under 272K tokens).
# Checked Oct 23, 2026: https://developers.openai.com/api/docs/pricing
PRICE_PER_M = {"input": 0.10, "cached_input": 0.01, "cache_write": 0.125, "output": 0.50}

# ---------- Safety limits (per run, shared by every agent in it) ----------
RUN_TOKEN_BUDGET = 80_000  # input + output tokens
RUN_REQUEST_LIMIT = 25  # model calls
# Across all runs in one day (New York time). Covers even NYC's record 311 day (24,415 requests on
# Aug 4, 2020) at the uncached price, so real demand never hits it; a flood or a stuck bug does.
DAILY_BUDGET_USD = 10.00
# Logs older than this are deleted when the app starts (civicequity/logstore.py). Open alerts are kept.
LOG_RETENTION_DAYS = int(os.getenv("CIVICEQUITY_LOG_RETENTION_DAYS", "180"))
# Per-sender limits (civicequity/limits.py): requests past these are held for a person without using AI.
SENDER_LIMIT_PER_HOUR = int(os.getenv("CIVICEQUITY_SENDER_LIMIT_PER_HOUR", "20"))
SENDER_LIMIT_PER_DAY = int(os.getenv("CIVICEQUITY_SENDER_LIMIT_PER_DAY", "60"))
# After the daily cap is reached, only the Life-Safety Screener keeps running, up to this much more.
SAFETY_RESERVE_USD = 2.00
