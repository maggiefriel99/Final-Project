"""Flow 2: the citywide capital memo.

Code computes the facts (memo_data.py) -> Needs Analyst picks the best-value investments and quick wins
-> Equity Analyst reads the two equity views and the City's priorities -> Memo Writer drafts one page
-> code fact-checks every number and quote, and asks the writer for one fix if anything is unsupported.
Code decides the order; no agent can call another. Every step is logged with tokens and cost.
"""

import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel
from civicequity import audit, config, fact_check, harness, logstore, memo_data, safety

MEMO_DIR = config.LOG_DIR / "memos"
MAX_WORDS = 600


class Pick(BaseModel):
    id: str
    why: str


class NeedsFindings(BaseModel):
    headline: str
    recommended: list[Pick]
    quick_wins: list[Pick]
    trend_note: str


class DistrictWhy(BaseModel):
    district: str
    why: str


class PriorityFit(BaseModel):
    priority: str
    place: str
    why: str


class EquityFindings(BaseModel):
    headline: str
    districts_both_views: list[DistrictWhy]
    hidden_need: list[DistrictWhy]
    reporting_pattern: str
    other_measures: str
    priority_fit: list[PriorityFit]


class ClaimCheck(BaseModel):
    sentence_number: int
    claim: str
    verdict: Literal["supported", "unsupported"]
    evidence_or_problem: str  # the supporting fact, quoted, or what is wrong


class ClaimReview(BaseModel):
    checks: list[ClaimCheck]


class MemoDraft(BaseModel):
    subject: str
    bottom_line: str
    what_new_yorkers_reported: str
    invest_first: list[str]
    already_funded_quick_wins: list[str]
    equity: str
    city_priorities: list[str]
    caveat: str

    def text(self) -> str:
        return "\n".join(self.sentences())

    def without(self, bad: list[str]) -> "MemoDraft":
        """A copy with these sentences removed (bullets left empty are dropped)."""
        def strip(s: str) -> str:
            for b in bad:
                s = s.replace(b, "")
            return " ".join(s.split())
        data = {k: ([strip(x) for x in v if strip(x)] if isinstance(v, list) else strip(v))
                for k, v in self.model_dump().items()}
        return MemoDraft(**data)

    def sentences(self) -> list[str]:
        """Each claim-bearing line, split into sentences so the verifier can point at one."""
        import re
        parts = [self.subject, self.bottom_line, self.what_new_yorkers_reported, *self.invest_first,
                 *self.already_funded_quick_wins, self.equity, *self.city_priorities, self.caveat]
        return [s.strip() for p in parts for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", p) if s.strip()]


def _run(agent_name: str, output_type, facts: dict, run_id: str, extra: str = "", history=None):
    """One agent call with the shared limits. Returns (output or None, usage record, stop reason or None, messages)."""
    prompt = extra or f"<facts>\n{fact_check.facts_json(facts)}\n</facts>"
    r = harness.run(agent_name, harness.build(agent_name, output_type), prompt, run_id, history=history)
    return r.output, r.usage, r.stopped, r.messages


def run_memo(snap: dict, model, use_borough_costs: bool = True,
             progress: Callable[[str], None] | None = None) -> dict:
    progress = progress or (lambda step: None)
    run_id = f"memo-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    usage, problems, stopped = {}, [], None

    progress("data")
    facts = memo_data.build_facts(snap, model, use_borough_costs)
    tables = facts.pop("_tables")
    cards = {c["id"]: c for key in ("best_value_capital", "best_in_each_category", "quick_wins_already_funded")
             for c in facts[key]}

    # ---------- Needs Analyst ----------
    progress("needs")
    needs_facts = {k: facts[k] for k in ("period", "citywide", "best_value_capital", "best_in_each_category",
                                         "quick_wins_already_funded")}
    needs_facts["method_notes"] = {k: facts["method_notes"][k] for k in ("cost_estimates", "complaints_per_million")}
    needs, usage["needs_analyst"], stop, _ = _run("needs_analyst", NeedsFindings, needs_facts, run_id)
    stopped = stopped or stop
    if needs:
        for field in ("recommended", "quick_wins"):
            bad = [p.id for p in getattr(needs, field) if p.id not in cards]
            if bad:
                problems.append(f"Needs Analyst picked ids that don't exist: {', '.join(bad)} (dropped)")
                setattr(needs, field, [p for p in getattr(needs, field) if p.id in cards])

    # ---------- Equity Analyst ----------
    progress("equity")
    equity_facts = {"period": facts["period"], "equity": facts["equity"], "priorities": facts["priorities"],
                    "method_notes": {"income_adjustment": facts["method_notes"]["income_adjustment"]}}
    equity, usage["equity_analyst"], stop, _ = _run("equity_analyst", EquityFindings, equity_facts, run_id)
    stopped = stopped or stop
    if equity:
        listed = {d["district"] for key in ("underfunded_both_views", "underfunded_adjusted_only",
                                            "underfunded_raw_only") for d in facts["equity"][key]}
        bad = [d.district for d in equity.districts_both_views + equity.hidden_need if d.district not in listed]
        if bad:
            problems.append(f"Equity Analyst named districts not in the facts: {', '.join(bad)}")

    # ---------- Memo Writer ----------
    progress("writer")
    writer_facts = {
        "period": facts["period"],
        "citywide": {k: facts["citywide"][k] for k in ("infrastructure_requests", "failed_asset_requests",
                                                      "capital_candidates", "by_category")},
        "needs_findings": needs.model_dump() if needs else None,
        "picked_places": [cards[p.id] for p in (needs.recommended + needs.quick_wins)] if needs else [],
        "equity_findings": equity.model_dump() if equity else None,
        "equity_numbers": {k: facts["equity"][k] for k in ("income_reporting_pattern", "underfunded_both_views",
                                                          "underfunded_adjusted_only", "more_measures")},
        "priorities_with_sources": facts["priorities"],
        "method_notes": facts["method_notes"],
    }
    memo, usage["memo_writer"], stop, history = _run("memo_writer", MemoDraft, writer_facts, run_id)
    stopped = stopped or stop

    # ---------- Verification: code number/quote check + Fact Verifier, with one chance to fix ----------
    progress("check")
    check, claims, retried, first_round = None, None, False, None
    code_facts = {k: v for k, v in writer_facts.items() if k not in ("needs_findings", "equity_findings")}

    def verify(draft: MemoDraft, label: str):
        sentences = draft.sentences()
        numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences))
        review, usage[label], _, _ = _run(
            "fact_verifier", ClaimReview, code_facts, run_id,
            extra=f"<facts>\n{fact_check.facts_json(code_facts)}\n</facts>\n<memo>\n{numbered}\n</memo>")
        checks = review.checks if review else []
        flagged = [{"sentence": sentences[c.sentence_number - 1] if 0 < c.sentence_number <= len(sentences) else "",
                    "claim": c.claim, "problem": c.evidence_or_problem} for c in checks if c.verdict == "unsupported"]
        return fact_check.check(draft.text(), code_facts), {"ran": review is not None, "claims_checked": len(checks),
                                                             "unsupported": flagged}

    if memo:
        check, claims = verify(memo, "fact_verifier")
        first_round = {"fact_check": check, "claim_review": claims}
        if not check["passed"] or claims["unsupported"]:
            retried = True
            issues = [f"number not in the facts: {n}" for n in check["unsupported_numbers"]]
            issues += [f"quote not in the facts: {q}" for q in check["unsupported_quotes"]]
            issues += [f'"{c["claim"]}": {c["problem"]}' for c in claims["unsupported"]]
            fix = ("Checks found problems in your memo:\n<check_results>\n- " + "\n- ".join(issues) +
                   "\n</check_results>\nRewrite the memo so every number, quote, and claim is directly supported by the facts. "
                   "Copy numbers exactly; drop any claim you cannot support.")
            fixed, usage["memo_writer_retry"], stop, _ = _run("memo_writer", MemoDraft, writer_facts, run_id,
                                                              extra=fix, history=history)
            if fixed:
                memo = fixed
                check, claims = verify(memo, "fact_verifier_recheck")

    # Anything that still fails after the rewrite is removed, so no unverified sentence reaches the reader.
    removed = []
    if memo and (claims and claims["unsupported"] or check and not check["passed"]):
        bad = {c["sentence"] for c in claims["unsupported"] if c["sentence"]} if claims else set()
        bad |= {s for s in memo.sentences()
                if any(n in s for n in check["unsupported_numbers"]) or any(q in s for q in check["unsupported_quotes"])}
        removed = sorted(bad)
        memo = memo.without(removed)
        check = fact_check.check(memo.text(), code_facts)
        words = len(memo.text().split())
        if words > MAX_WORDS:
            problems.append(f"Memo is {words} words; target is about 450.")
        for p in safety.output_problems(memo.text(), None, None, allow_funding_words=True, max_chars=10_000):
            if "$" not in p:  # dollar figures are expected in this memo; links and instructions are not
                problems.append(f"Memo text: {p}")

    total = {k: round(sum(u.get(k, 0) for u in usage.values()), 8) for k in
             ("input_tokens", "cached_input_tokens", "output_tokens", "cost_usd")}
    result = {
        "run_id": run_id, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "use_borough_costs": use_borough_costs, "facts": facts, "needs": needs and needs.model_dump(),
        "equity": equity and equity.model_dump(), "memo": memo and memo.model_dump(), "fact_check": check,
        "claim_review": claims, "first_round_checks": first_round, "removed_sentences": removed,
        # Verified = the number/quote check passes and the Fact Verifier ran; any sentence it flagged was removed.
        "verified": bool(check and check["passed"] and claims and claims["ran"]),
        "fact_check_retried": retried, "problems": problems, "stopped": stopped, "usage": usage, "total": total,
        "seconds": round(time.monotonic() - started, 1),
    }
    logstore.write_json(MEMO_DIR / f"{run_id}.json", result)
    audit.record({"run_id": run_id, "flow": "memo", "stopped": stopped, "fact_check": check,
                  "claim_review": claims, "verified": result["verified"],
                  "fact_check_retried": retried, "problems": problems,
                  "tokens": {k: total[k] for k in ("input_tokens", "cached_input_tokens", "output_tokens")},
                  "cost_usd": total["cost_usd"], "seconds": result["seconds"]})
    result["_tables"] = tables
    return result
