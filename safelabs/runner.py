"""
safelabs/runner.py

Top-level eval runner for the OWASP ASI red-teaming framework.

Quick start
-----------
    import asyncio
    from safelabs.runner import run_eval

    async def my_agent(prompt: str) -> str:
        return "I cannot help with that."

    result = asyncio.run(run_eval(my_agent, categories=["ASI01"]))
    result.summary()
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import Counter
from typing import Awaitable, Callable

from pydantic import BaseModel, Field

from safelabs.prompts.loader import get_library
from safelabs.prompts.schemas import PromptCategory, PromptEntry
from safelabs.scoring.models import ScoringResult, VerdictLevel
from safelabs.scoring.scorer import Scorer

logger = logging.getLogger(__name__)

# Maps each OWASP ASI category to the detector eval_type that best covers it.
# Mirrors the mapping used in safelabs/cli.py; categories without a dedicated
# detector fall back to "prompt_injection".
CATEGORY_EVAL_TYPE: dict[str, str] = {
    "ASI01": "prompt_injection",
    "ASI02": "prompt_injection",   # insecure output — best available
    "ASI03": "scope_violation",    # excessive agency / unauthorised actions
    "ASI04": "prompt_injection",   # resource management
    "ASI05": "prompt_injection",   # tool use safety
    "ASI06": "data_leakage",
    "ASI07": "prompt_injection",   # trust boundaries
    "ASI08": "jailbreak",
    "ASI09": "scope_violation",
    "ASI10": "hallucination",
}

# A callable that takes a prompt string and returns a response string.
# Both sync ``def`` and ``async def`` functions are accepted.
AgentFn = Callable[[str], "str | Awaitable[str]"]


async def _call_agent(agent_fn: AgentFn, prompt: str) -> tuple[str, float]:
    """Invoke agent_fn (sync or async) and return ``(output, latency_ms)``."""
    t0 = time.perf_counter()
    if inspect.iscoroutinefunction(agent_fn):
        output = await agent_fn(prompt)
    else:
        loop = asyncio.get_running_loop()
        output = await loop.run_in_executor(None, agent_fn, prompt)
    latency_ms = (time.perf_counter() - t0) * 1000
    return str(output), latency_ms


class EvalRecord(BaseModel):
    """Result for a single prompt → response → score round-trip."""

    prompt_id: str
    category: str
    severity: str
    prompt: str
    response: str
    latency_ms: float = 0.0
    scoring_result: ScoringResult
    error: str | None = None

    @property
    def verdict(self) -> VerdictLevel:
        return self.scoring_result.verdict


class EvalResult(BaseModel):
    """Aggregated result returned by ``run_eval()``."""

    records: list[EvalRecord] = Field(default_factory=list)
    categories_run: list[str] = Field(default_factory=list)

    # ── aggregate helpers ────────────────────────────────────────────────────

    @property
    def total(self) -> int:
        """Total number of prompts evaluated."""
        return len(self.records)

    @property
    def counts(self) -> Counter:
        """Verdict counts across all records."""
        return Counter(r.verdict.value for r in self.records)

    @property
    def vulnerable(self) -> list[EvalRecord]:
        return [r for r in self.records if r.verdict == VerdictLevel.VULNERABLE]

    @property
    def failed(self) -> list[EvalRecord]:
        return [r for r in self.records if r.verdict == VerdictLevel.FAIL]

    @property
    def passed(self) -> list[EvalRecord]:
        return [r for r in self.records if r.verdict == VerdictLevel.PASS]

    @property
    def errors(self) -> list[EvalRecord]:
        return [r for r in self.records if r.error is not None]

    # ── reporting ────────────────────────────────────────────────────────────

    def summary(self) -> None:
        """Print a clean, colour-coded eval report to stdout."""
        _RED    = "\033[91m"
        _YELLOW = "\033[93m"
        _GREEN  = "\033[92m"
        _CYAN   = "\033[96m"
        _BOLD   = "\033[1m"
        _RESET  = "\033[0m"
        _VC = {
            VerdictLevel.VULNERABLE: _RED,
            VerdictLevel.FAIL:       _YELLOW,
            VerdictLevel.UNCERTAIN:  _CYAN,
            VerdictLevel.PASS:       _GREEN,
        }

        cats = ", ".join(self.categories_run) if self.categories_run else "all"
        print(f"\n{_BOLD}safelabs-eval — Eval Report{_RESET}")
        print(f"Categories : {cats}")
        print(f"Prompts run: {self.total}")
        print("─" * 62)

        for r in self.records:
            c = _VC.get(r.verdict, _RESET)
            verdict_label = f"{c}{r.verdict.value.upper():<10}{_RESET}"
            print(
                f"  [{r.prompt_id}] {r.severity.upper():<8} "
                f"{verdict_label}  "
                f"{r.scoring_result.confidence:.0%} conf  "
                f"{r.latency_ms:>6.0f} ms"
            )
            if r.error:
                print(f"    {_RED}error :{_RESET} {r.error}")
            elif r.scoring_result.remediation_hint and r.verdict in (
                VerdictLevel.VULNERABLE, VerdictLevel.FAIL,
            ):
                print(f"    {_YELLOW}fix   :{_RESET} {r.scoring_result.remediation_hint}")

        c = self.counts
        print("─" * 62)
        print(f"{_BOLD}SUMMARY{_RESET}  ({self.total} prompts evaluated)")
        print(f"  {_RED}VULNERABLE{_RESET} : {c.get('vulnerable', 0)}")
        print(f"  {_YELLOW}FAIL{_RESET}       : {c.get('fail', 0)}")
        print(f"  {_CYAN}UNCERTAIN{_RESET}  : {c.get('uncertain', 0)}")
        print(f"  {_GREEN}PASS{_RESET}       : {c.get('pass', 0)}")
        if self.errors:
            print(f"  {_RED}ERRORS{_RESET}     : {len(self.errors)}")

        if c.get("vulnerable", 0):
            print(
                f"\n{_RED}⚠  {c['vulnerable']} VULNERABLE finding(s)"
                f" — immediate attention required{_RESET}"
            )
        elif c.get("fail", 0):
            print(f"\n{_YELLOW}⚠  {c['fail']} FAIL finding(s) — review recommended{_RESET}")
        else:
            print(f"\n{_GREEN}✓  No vulnerabilities detected{_RESET}")
        print()


async def run_eval(
    agent_fn: AgentFn,
    categories: list[str] | None = None,
    scorer: Scorer | None = None,
) -> EvalResult:
    """
    Run the OWASP ASI eval suite against *agent_fn*.

    Parameters
    ----------
    agent_fn:
        Any callable ``(prompt: str) -> str``. Both ``def`` and ``async def``
        are supported. Raise an exception to signal a hard failure; return an
        empty string or refusal text for a scoreable response.
    categories:
        List of OWASP ASI category codes, e.g. ``["ASI01", "ASI06"]``.
        ``None`` (default) runs all 10 categories (30 prompts total).
    scorer:
        Optional pre-configured :class:`~safelabs.scoring.scorer.Scorer`.
        Defaults to the standard five-detector suite.

    Returns
    -------
    EvalResult
        Aggregated result with one :class:`EvalRecord` per prompt.
    """
    library = get_library()
    scorer  = scorer or Scorer()

    cats: list[PromptCategory]
    if categories:
        cats = [PromptCategory(c.upper()) for c in categories]
    else:
        cats = list(PromptCategory)

    prompts: list[PromptEntry] = []
    for cat in cats:
        prompts.extend(library.by_category(cat))

    records: list[EvalRecord] = []

    for entry in prompts:
        eval_type     = CATEGORY_EVAL_TYPE.get(entry.category.value, "prompt_injection")
        response_text = ""
        latency_ms    = 0.0
        error_msg: str | None = None

        try:
            response_text, latency_ms = await _call_agent(agent_fn, entry.prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent_fn raised for %s: %s", entry.id, exc)
            error_msg = str(exc)

        scoring_result = await scorer.score(
            eval_type, entry.prompt, response_text,
        )

        records.append(EvalRecord(
            prompt_id=entry.id,
            category=entry.category.value,
            severity=entry.severity,
            prompt=entry.prompt,
            response=response_text,
            latency_ms=latency_ms,
            scoring_result=scoring_result,
            error=error_msg,
        ))

    return EvalResult(
        records=records,
        categories_run=[c.value for c in cats],
    )
