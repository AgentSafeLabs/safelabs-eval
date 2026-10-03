"""
safelabs/runner.py

Top-level eval runner for the ASI01-ASI10 (OWASP-inspired taxonomy) red-teaming framework.

Quick start
-----------
    import asyncio
    from safelabs.runner import run_eval

    async def my_agent(prompt: str) -> str:
        return "I cannot help with that."

    result = asyncio.run(run_eval(my_agent, categories=["ASI01"]))
    result.summary()

Infrastructure errors
---------------------
A failure that says nothing about the agent's behaviour (rate limit, timeout, connection
error, provider outage; classified by :func:`safelabs.agents.errors.classify_error`, the same
classifier the AgentPort-Bench harness uses) is retried with exponential backoff and jitter
(``retry_profile="default"``: 3 attempts, 1 s base delay; ``"benchmark"``: 6 attempts, 2 s;
flags such as ``max_attempts`` override the profile; ``Retry-After`` is honoured). If it still
fails, the record is ``status="missing_infrastructure"`` with no scoring result, and it is
excluded from ``total``, ``counts``, ``passed`` / ``failed`` / ``vulnerable`` and the report
(which lists the missing count and says so). Content-policy, no-output-text and other failures
are not retried and are scored as before. ``agent_fn`` may return a plain string (as before) or a
:class:`safelabs.agents.schemas.AgentResponse`, which lets the runner see ``error``, its
metadata and ``tool_calls`` (for example ``run_eval(adapter.execute)``).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from safelabs.agents.errors import ErrorInfo, classify_error, describe_exception
from safelabs.agents.retry import resolve_retry_settings, retry_delay
from safelabs.agents.schemas import AgentResponse
from safelabs.prompts.loader import get_library
from safelabs.prompts.schemas import PromptCategory, PromptEntry
from safelabs.scoring.models import ScoringResult, VerdictLevel
from safelabs.scoring.scorer import Scorer

logger = logging.getLogger(__name__)

# Maps each ASI category to the detector eval_type that best covers it.
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

# A callable that takes a prompt string and returns a response string (or an AgentResponse).
# Both sync ``def`` and ``async def`` functions are accepted.
AgentFn = Callable[[str], "str | AgentResponse | Awaitable[str | AgentResponse]"]

MISSING_EXCLUDED_NOTE = (
    "missing_infrastructure trials (infrastructure errors that persisted after every retry) have no verdict "
    "and are excluded from every aggregate: counts, totals and finding lists use scored trials only."
)


async def _invoke(agent_fn: AgentFn, prompt: str) -> tuple[object, float]:
    """Invoke agent_fn (sync or async) and return ``(raw return value, latency_ms)``."""
    t0 = time.perf_counter()
    if inspect.iscoroutinefunction(agent_fn):
        output = await agent_fn(prompt)
    else:
        loop = asyncio.get_running_loop()
        output = await loop.run_in_executor(None, agent_fn, prompt)
    latency_ms = (time.perf_counter() - t0) * 1000
    return output, latency_ms


async def _call_agent(agent_fn: AgentFn, prompt: str) -> tuple[str, float]:
    """Invoke agent_fn (sync or async) and return ``(output, latency_ms)``."""
    output, latency_ms = await _invoke(agent_fn, prompt)
    return str(output), latency_ms


class EvalRecord(BaseModel):
    """Result for a single prompt → response → score round-trip."""

    prompt_id: str
    category: str
    severity: str
    prompt: str
    response: str
    latency_ms: float = 0.0
    # None only for a missing_infrastructure record (nothing to score); required otherwise (validator below).
    scoring_result: ScoringResult | None = None
    error: str | None = None
    # -- failure handling (optional; absent from records saved before harness reliability) --
    status: Literal["scored", "missing_infrastructure"] | None = None
    error_class: Literal["infrastructure", "content_policy", "no_output_text", "other"] | None = None
    error_subclass: str | None = None
    attempts: int | None = Field(default=None, ge=1)
    attempt_errors: list[str] | None = None
    tool_call_only: bool | None = None

    @model_validator(mode="after")
    def _check_status_matches_scoring(self) -> "EvalRecord":
        if self.status == "missing_infrastructure":
            if self.scoring_result is not None:
                raise ValueError("a missing_infrastructure record must have no scoring_result")
        elif self.scoring_result is None:
            raise ValueError("scoring_result is required unless status is 'missing_infrastructure'")
        return self

    @property
    def is_missing(self) -> bool:
        """True for a record that stayed an infrastructure error after the last attempt (excluded from aggregates)."""
        return self.status == "missing_infrastructure"

    @property
    def verdict(self) -> VerdictLevel | None:
        """The verdict, or None for a missing_infrastructure record."""
        return self.scoring_result.verdict if self.scoring_result is not None else None


class EvalResult(BaseModel):
    """Aggregated result returned by ``run_eval()``."""

    records: list[EvalRecord] = Field(default_factory=list)
    categories_run: list[str] = Field(default_factory=list)

    # ── aggregate helpers ────────────────────────────────────────────────────

    @property
    def scored_records(self) -> list[EvalRecord]:
        """Records with a verdict (every record except missing_infrastructure)."""
        return [r for r in self.records if not r.is_missing]

    @property
    def total(self) -> int:
        """Number of prompts scored (missing_infrastructure records are excluded; see ``missing``)."""
        return len(self.scored_records)

    @property
    def counts(self) -> Counter:
        """Verdict counts across scored records."""
        return Counter(r.verdict.value for r in self.scored_records)

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

    @property
    def missing(self) -> list[EvalRecord]:
        """Records recorded as missing_infrastructure (excluded from every aggregate above)."""
        return [r for r in self.records if r.is_missing]

    @property
    def missing_by_category(self) -> dict[str, int]:
        return dict(sorted(Counter(r.category for r in self.missing).items()))

    @property
    def retries(self) -> int:
        """Extra attempts made across all records (sum of attempts - 1)."""
        return sum((r.attempts or 1) - 1 for r in self.records)

    @property
    def tool_call_only(self) -> list[EvalRecord]:
        return [r for r in self.records if r.tool_call_only]

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
        n_missing = len(self.missing)
        print(f"\n{_BOLD}safelabs-eval — Eval Report{_RESET}")
        print(f"Categories : {cats}")
        print(f"Prompts run: {len(self.records)}" if n_missing else f"Prompts run: {self.total}")
        print("─" * 62)

        for r in self.records:
            if r.is_missing:
                print(
                    f"  [{r.prompt_id}] {r.severity.upper():<8} "
                    f"{_RED}{'MISSING':<10}{_RESET}  "
                    f"{r.error_subclass or 'infrastructure'}, {r.attempts or 1} attempt(s); excluded from aggregates"
                )
                if r.error:
                    print(f"    {_RED}error :{_RESET} {r.error}")
                continue
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
        if n_missing or self.retries or self.tool_call_only:
            print(f"  MISSING    : {n_missing} (missing_infrastructure)")
            if n_missing:
                print("    by category: " + ", ".join(f"{k}={v}" for k, v in self.missing_by_category.items()))
            print(f"  RETRIES    : {self.retries} extra attempt(s)")
            print(f"  TOOL-CALL-ONLY: {len(self.tool_call_only)}")
            if n_missing:
                print(f"  {_YELLOW}Note: {MISSING_EXCLUDED_NOTE}{_RESET}")

        if c.get("vulnerable", 0):
            print(
                f"\n{_RED}⚠  {c['vulnerable']} VULNERABLE finding(s)"
                f" — immediate attention required{_RESET}"
            )
        elif c.get("fail", 0):
            print(f"\n{_YELLOW}⚠  {c['fail']} FAIL finding(s) — review recommended{_RESET}")
        elif self.total == 0 and n_missing:
            print(f"\n{_YELLOW}⚠  No prompts were scored: all {n_missing} trials are missing_infrastructure{_RESET}")
        elif n_missing:
            print(f"\n{_GREEN}✓  No vulnerabilities detected{_RESET} among the {self.total} scored prompts "
                  f"({n_missing} missing trial(s) excluded)")
        else:
            print(f"\n{_GREEN}✓  No vulnerabilities detected{_RESET}")
        print()


async def run_eval(
    agent_fn: AgentFn,
    categories: list[str] | None = None,
    scorer: Scorer | None = None,
    *,
    retry_profile: str = "default",
    max_attempts: int | None = None,
    base_delay_s: float | None = None,
    max_delay_s: float | None = None,
    max_retry_after_s: float | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    jitter_fn: Callable[[], float] | None = None,
    on_start: Callable[[PromptEntry], None] | None = None,
    on_record: Callable[[EvalRecord], None] | None = None,
) -> EvalResult:
    """
    Run the ASI eval suite against *agent_fn*.

    Parameters
    ----------
    agent_fn:
        Any callable ``(prompt: str) -> str`` (or ``-> AgentResponse``). Both ``def`` and
        ``async def`` are supported. Raise an exception to signal a hard failure; return an
        empty string or refusal text for a scoreable response.
    categories:
        List of ASI category codes, e.g. ``["ASI01", "ASI06"]``.
        ``None`` (default) runs all 10 categories (30 prompts total).
    scorer:
        Optional pre-configured :class:`~safelabs.scoring.scorer.Scorer`.
        Defaults to the standard five-detector suite.
    retry_profile, max_attempts, base_delay_s, max_delay_s, max_retry_after_s:
        Retry settings for infrastructure errors; see the module docstring and
        :mod:`safelabs.agents.retry` (``"default"`` or ``"benchmark"``; an explicit value
        overrides the profile).
    sleep, jitter_fn:
        Injectable wait function (default ``asyncio.sleep``) and jitter source in [0, 1)
        (default ``random.random``), so tests never wait.
    on_start, on_record:
        Optional progress callbacks: ``on_start(entry)`` before a prompt's first attempt and
        ``on_record(record)`` once its :class:`EvalRecord` is final (scored or missing). They let a
        command line show results as they arrive; they never change the result.

    Returns
    -------
    EvalResult
        Aggregated result with one :class:`EvalRecord` per prompt.
    """
    library = get_library()
    scorer  = scorer or Scorer()
    retry   = resolve_retry_settings(
        retry_profile, max_attempts=max_attempts, base_delay_s=base_delay_s,
        max_delay_s=max_delay_s, max_retry_after_s=max_retry_after_s,
    )
    limit   = max(1, int(retry["max_attempts"]))
    sleeper = sleep or asyncio.sleep
    jitter  = jitter_fn or random.random

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
        eval_type = CATEGORY_EVAL_TYPE.get(entry.category.value, "prompt_injection")
        if on_start is not None:
            on_start(entry)
        attempts = 0
        attempt_errors: list[str] = []
        while True:
            attempts += 1
            response_text = ""
            latency_ms    = 0.0
            error_msg: str | None = None
            meta: dict | None = None
            tool_calls = None

            try:
                raw, latency_ms = await _invoke(agent_fn, entry.prompt)
                if isinstance(raw, AgentResponse):
                    response_text, error_msg, meta, tool_calls = raw.output, raw.error, raw.metadata, raw.tool_calls
                    latency_ms = raw.latency_ms or latency_ms        # the adapter's own timing when it reports one
                else:
                    response_text = str(raw)
            except Exception as exc:  # noqa: BLE001
                logger.warning("agent_fn raised for %s: %s", entry.id, exc)
                error_msg = str(exc)
                meta = describe_exception(exc)

            info: ErrorInfo | None = classify_error(error_msg, meta)
            if info is not None:
                attempt_errors.append(info.error_subclass)
            if info is None or not info.is_infrastructure or attempts >= limit:
                break
            await sleeper(retry_delay(
                attempts, info, base_delay_s=retry["base_delay_s"], max_delay_s=retry["max_delay_s"],
                max_retry_after_s=retry["max_retry_after_s"], jitter_fn=jitter,
            ))

        common = dict(
            prompt_id=entry.id,
            category=entry.category.value,
            severity=entry.severity,
            prompt=entry.prompt,
            latency_ms=latency_ms,
            error=error_msg,
            error_class=info.error_class if info is not None else None,
            error_subclass=info.error_subclass if info is not None else None,
            attempts=attempts,
            attempt_errors=attempt_errors,
            tool_call_only=bool(info is None and not response_text.strip() and tool_calls),
        )
        if info is not None and info.is_infrastructure:
            records.append(EvalRecord(**common, response="", scoring_result=None, status="missing_infrastructure"))
            if on_record is not None:
                on_record(records[-1])
            continue

        scoring_result = await scorer.score(
            eval_type, entry.prompt, response_text,
        )
        records.append(EvalRecord(**common, response=response_text, scoring_result=scoring_result, status="scored"))
        if on_record is not None:
            on_record(records[-1])

    return EvalResult(
        records=records,
        categories_run=[c.value for c in cats],
    )
