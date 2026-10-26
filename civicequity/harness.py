"""The agent harness: every CivicEquity agent is built and run through here.

What the harness guarantees for every agent, in one place:
  - Registry: only agents listed in AGENTS can be built, and each may use only the tools listed for it.
  - Shared safety rules (prompts/_harness_rules.md) come first in every agent's system prompt.
  - Data stays data: the message sent to an agent must wrap its material in a data tag
    (<constituent_request>, <facts>, <memo>, or <check_results>); a bare message is refused before any
    model call.
  - Limits: the daily spending cap is checked before the call; every call runs under the per-run token
    and call budget (config.RUN_TOKEN_BUDGET, config.RUN_REQUEST_LIMIT).
  - Failures stop safely: a provider content-filter block, a budget stop, or a model error ends the run
    with a recorded reason instead of an exception, and the caller holds the work for a person.
  - Every call is logged (tokens, cost, cache status) to logs/usage.jsonl.

What stays with each agent's own module: the agent-specific prompt, the output schema, and the checks
on its answer (which differ by agent and are listed in AGENTS[...].checked_by).
"""

import asyncio
import contextvars
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import cache

from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded

from civicequity import config, llm

DATA_TAGS = ("<constituent_request>", "<facts>", "<memo>", "<check_results>")


@dataclass(frozen=True)
class AgentSpec:
    purpose: str
    sees: str  # what material reaches the agent
    tools: tuple[str, ...] = ()
    checked_by: str = ""  # what code checks before its answer is used


AGENTS = {
    "triage": AgentSpec(
        "Map one request to an official 311 complaint type, descriptor, and street; flag danger and advice.",
        "The request text after hidden-text cleaning and personal-information removal; the 311 catalog.",
        checked_by="official type and descriptor lists, output checks (links, money, instruction-like text), "
                   "no numbers beyond the request, confidence; the route is decided by code rules."),
    "life_safety": AgentSpec(
        "On every request, rate the danger to people: emergency (911-level), urgent hazard (fix soon), or none.",
        "The same cleaned, de-identified request text.",
        checked_by="output checks and number check on its reason; emergencies escalate and hazards mark the alert "
                   "urgent (code); it can never downgrade anything."),
    "cost_estimator": AgentSpec(
        "Choose the scope of the lasting fix for a capital need.",
        "Checked Triage fields and 311 facts; never the resident's text.",
        tools=("comparable_projects",),
        checked_by="scope must be a listed option; cited projects must come from its tool; all dollars come "
                   "from the cost model, never the agent."),
    "operating_router": AgentSpec(
        "Draft the alert to the agency that handles the problem.",
        "Checked Triage fields and 311 facts; never the resident's text.",
        checked_by="every number must be in the facts; output checks; a person approves."),
    "resident_reply": AgentSpec(
        "Draft the reply to the resident, with advice only from the verified advice library.",
        "Checked Triage fields, the next step (decided by code), and the advice library.",
        checked_by="advice ids must exist; numbers only from the advice or the request; output checks; "
                   "a person approves."),
    "needs_analyst": AgentSpec("Pick best-value capital investments and already-funded quick wins.",
                               "Facts computed by code.", checked_by="picked ids must exist."),
    "equity_analyst": AgentSpec("Read the equity measures and the City's priorities.",
                                "Facts computed by code.", checked_by="named districts must be in the facts."),
    "memo_writer": AgentSpec("Draft the one-page memo.", "The analysts' findings and the facts they cite.",
                             checked_by="code number and quote check, then the Fact Verifier."),
    "fact_verifier": AgentSpec("Check every memo sentence against the facts.", "The facts and the numbered memo.",
                               checked_by="its verdicts decide which sentences are removed; code also checks numbers."),
}


@cache
def rules() -> str:
    return (config.ROOT / "prompts" / "_harness_rules.md").read_text()


@cache
def agent_prompt(name: str) -> str:
    return (config.ROOT / "prompts" / f"{name}.md").read_text()


def system_prompt(name: str, prompt: str | None = None) -> str:
    """Shared rules first, then the agent's own prompt (or a prompt the agent built from its template)."""
    return rules() + "\n" + (prompt if prompt is not None else agent_prompt(name))


def build(name: str, output_type, prompt: str | None = None, tools: list[Callable] = (), deps_type=None) -> Agent:
    """Build a registered agent with the shared rules and only its allowed tools."""
    spec = AGENTS[name]  # KeyError = not a registered agent
    extra = [t.__name__ for t in tools if t.__name__ not in spec.tools]
    if extra:
        raise ValueError(f"{name} is not allowed these tools: {', '.join(extra)}")
    kwargs = {"deps_type": deps_type} if deps_type is not None else {}
    return Agent(llm.build_model(), system_prompt=system_prompt(name, prompt), output_type=output_type,
                 tools=list(tools), model_settings=config.MODEL_SETTINGS, **kwargs)


@dataclass
class Run:
    output: object = None  # the agent's structured answer, or None if it was stopped
    usage: dict = field(default_factory=dict)  # the usage-log record (tokens, cost, cache status)
    stopped: dict | None = None  # {"by": ..., "detail": ...} when the run did not finish
    messages: list | None = None  # the conversation, for a follow-up turn (memo rewrite)


def stop_reason(e: Exception) -> str:
    if isinstance(e, UsageLimitExceeded):
        return "token budget"
    return "provider content filter" if "content_filter" in str(e) else "model error"


def _run_isolated(agent: Agent, message: str, **kwargs):
    """Run one agent call on its own fresh event loop in its own short-lived thread.

    agent.run_sync() reuses the calling thread's event loop. Streamlit reuses its script thread across reruns, so a
    rerun that starts while a call is still in progress (a second click, another button) found that loop already
    running and crashed ("This event loop is already running"), leaving the page stuck. A fresh loop per call
    can't collide. The context (per-run call log, prompt screening) is copied into the thread."""
    ctx = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(ctx.run, lambda: asyncio.run(agent.run(message, **kwargs))).result()


def run(name: str, agent: Agent, message: str, run_id: str, *, deps=None, history=None,
        screening: list | None = None) -> Run:
    """Run one agent call under the shared limits. Never raises for budget, filter, or model errors."""
    if not any(tag in message for tag in DATA_TAGS):
        raise ValueError(f"{name}: the material must be wrapped in a data tag {DATA_TAGS}.")
    # Once the daily cap is reached, everything stops except the Life-Safety Screener, which may use a small
    # reserve, so a flood of requests can't switch off emergency detection.
    cap = config.DAILY_BUDGET_USD + (config.SAFETY_RESERVE_USD if name == "life_safety" else 0)
    if llm.spent_today() >= cap:
        return Run(usage=llm.log_usage(run_id, name, llm.empty_usage(), note="stopped: daily budget"),
                   stopped={"by": "daily budget", "detail": f"${llm.spent_today():.2f} spent today; cap is "
                                                            f"${cap:.2f}."})
    spent = llm.empty_usage()  # filled in as the run goes, so tokens are counted even if it is stopped
    token = llm.PROMPT_SCREENING.set(screening) if screening is not None else None
    # Each run keeps its own record of gateway calls, so runs from different users at the same time (or the
    # Life-Safety Screener running beside Triage) never have their cache status and cost mixed up.
    calls_token = llm.CALL_LOG.set([])
    try:
        out = _run_isolated(agent, message, deps=deps, message_history=history, usage_limits=llm.LIMITS,
                            usage=spent)
        return Run(out.output, llm.log_usage(run_id, name, out.usage), None, out.all_messages())
    except (ModelHTTPError, UsageLimitExceeded) as e:
        by = stop_reason(e)
        return Run(usage=llm.log_usage(run_id, name, spent, note=f"stopped: {by}"),
                   stopped={"by": by, "detail": f"{type(e).__name__}: {str(e)[:300]}"})
    finally:
        llm.CALL_LOG.reset(calls_token)
        if token is not None:
            llm.PROMPT_SCREENING.reset(token)


def describe() -> list[dict]:
    """The registry as rows, for documentation and the audit page."""
    return [{"agent": k, "purpose": s.purpose, "sees": s.sees, "tools": ", ".join(s.tools) or "none",
             "checked_by": s.checked_by} for k, s in AGENTS.items()]
