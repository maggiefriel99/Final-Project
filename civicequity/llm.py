"""The gpt-6-luna model through Portkey, plus a token-usage log for every agent call."""

import os
import threading
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RunUsage, UsageLimits

from civicequity import config, logstore

USAGE_LOG = config.LOG_DIR / "usage.jsonl"

# Azure screens every prompt for jailbreak and indirect (embedded) attacks and reports what it found,
# even when it does not block the call. A run can collect those reports by setting this to a list.
PROMPT_SCREENING: ContextVar[list | None] = ContextVar("prompt_screening", default=None)


# Gateway details of each model call (cache status, latency), attached to the next usage log record, so
# cost reports can tell fresh answers from answers served by a cache.
_CALLS: list[dict] = []
_CALLS_LOCK = threading.Lock()
# Agents that run at the same time (Triage and the Life-Safety Screener) each set their own list here, so
# one agent's calls are never logged under the other.
CALL_LOG: ContextVar[list | None] = ContextVar("call_log", default=None)


async def _record_call(request) -> None:
    request.extensions["civicequity_started"] = time.monotonic()


async def _collect_prompt_screening(response) -> None:
    if response.request.url.path.endswith("/chat/completions"):
        started = response.request.extensions.get("civicequity_started")
        call = {
            "status": response.status_code,
            "portkey_cache": response.headers.get("x-portkey-cache-status"),
            "provider": response.headers.get("x-portkey-provider"),
            "seconds": round(time.monotonic() - started, 2) if started else None,
        }
        own = CALL_LOG.get()
        with _CALLS_LOCK:
            (own if own is not None else _CALLS).append(call)
    sink = PROMPT_SCREENING.get()
    if sink is None or not response.request.url.path.endswith("/chat/completions"):
        return
    await response.aread()
    try:
        body = response.json()
    except ValueError:
        return
    # Allowed calls report results in prompt_filter_results; blocked calls report them in the error.
    results = [r.get("content_filter_results", {}) for r in body.get("prompt_filter_results", [])]
    error = body.get("error") or {}
    inner = (error.get("innererror") or {}).get("content_filter_result")
    if inner:
        results.append(inner)
    for r in results:
        sink.append({
            "jailbreak_detected": bool((r.get("jailbreak") or {}).get("detected")),
            "indirect_attack_detected": bool((r.get("indirect_attack") or {}).get("detected")),
            "blocked": response.status_code >= 400,
        })
LIMITS = UsageLimits(total_tokens_limit=config.RUN_TOKEN_BUDGET, request_limit=config.RUN_REQUEST_LIMIT)


def build_model() -> OpenAIChatModel:
    if not config.PORTKEY_API_KEY:
        raise RuntimeError(
            "PORTKEY_API_KEY is not set. Put it in a .env file in this folder or the folder above it "
            "(see .env.example)."
        )
    headers = {"x-portkey-api-key": config.PORTKEY_API_KEY}
    if os.getenv("CIVICEQUITY_FRESH_CALLS") == "1":
        # Measurement mode (analysis/tokenomics.py): skip Portkey's response cache so every call is a real,
        # billed model call and cost figures are not understated.
        headers["x-portkey-cache-force-refresh"] = "true"
    client = AsyncOpenAI(
        api_key=config.PORTKEY_API_KEY,
        base_url=config.PORTKEY_BASE_URL,
        default_headers=headers,
        http_client=DefaultAsyncHttpxClient(event_hooks={"request": [_record_call],
                                                         "response": [_collect_prompt_screening]}),
    )
    return OpenAIChatModel(config.MODEL_NAME, provider=OpenAIProvider(openai_client=client))


def empty_usage():
    """Zero usage, for runs that were stopped before the model answered."""
    return RunUsage()


def cost_usd(input_tokens: int, cached_input_tokens: int, output_tokens: int, cache_write_tokens: int = 0) -> float:
    """Dollar cost of one call. Input tokens include the cached ones, which bill at the cached rate."""
    p = config.PRICE_PER_M
    full_price_input = input_tokens - cached_input_tokens - cache_write_tokens
    return (full_price_input * p["input"] + cached_input_tokens * p["cached_input"]
            + cache_write_tokens * p["cache_write"] + output_tokens * p["output"]) / 1_000_000


def spent_today() -> float:
    """Dollars spent on model calls so far today (New York time), from the usage log."""
    ny = ZoneInfo("America/New_York")
    today = datetime.now(ny).date()
    total = 0.0
    for r in logstore.read(USAGE_LOG):
        if datetime.fromisoformat(r["time"]).astimezone(ny).date() == today:
            total += r.get("cost_usd", 0.0)
    return total


def daily_budget_reached() -> bool:
    return spent_today() >= config.DAILY_BUDGET_USD


def log_usage(run_id: str, agent: str, usage, note: str = "") -> dict:
    """Append one agent's token counts to logs/usage.jsonl and return the record."""
    record = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": run_id,
        "agent": agent,
        "model": config.MODEL_NAME,
        "requests": usage.requests,
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "output_tokens": usage.output_tokens,
    }
    list_price = cost_usd(usage.input_tokens, usage.cache_read_tokens, usage.output_tokens, usage.cache_write_tokens)
    if note:
        record["note"] = note
    source = CALL_LOG.get()
    source = _CALLS if source is None else source
    with _CALLS_LOCK:
        calls = list(source)
        source.clear()
    # Portkey answers an identical repeat request from its response cache without calling the model (seen as
    # x-portkey-cache-status: HIT, about 0.2 s), but still reports the original token counts. Those calls cost
    # nothing, so cost_usd is what was actually billed; cost_usd_if_uncached is the price had every call gone
    # to the model. For a run that mixes hits and misses, the bill is prorated by calls (an approximation).
    hits = sum(c.get("portkey_cache") == "HIT" for c in calls)
    billed_share = (len(calls) - hits) / len(calls) if calls else 1.0
    record["cost_usd"] = round(list_price * billed_share, 8)
    record["cost_usd_if_uncached"] = round(list_price, 8)
    if calls:
        record["calls"] = calls
        record["portkey_cache_hits"] = hits
    return logstore.append(USAGE_LOG, record)
