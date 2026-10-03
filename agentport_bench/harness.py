"""
agentport_bench/harness.py

Submission harness: runs safelabs-eval's prompt library against a
contributor's chosen framework/model and emits AgentPort-Bench-schema
rows. Reuses safelabs.prompts.get_library(), the contributor's chosen
safelabs.agents.AgentAdapter (built-in or custom), safelabs.scoring.Scorer,
and safelabs.runner.CATEGORY_EVAL_TYPE (the same category -> detector
mapping agentdojo-x's own orchestrator reused rather than reimplementing)
-- never reimplements prompt storage, execution, or scoring.

library_version / harness_version: both are always read live --
library_version from get_library().version, harness_version from
agentport_bench.__version__ -- inside run_trial(). Neither is a caller-
supplied parameter, so a result can never misreport what it actually ran
against. Contributors running this harness today get library_version
"1.13.0" (300 prompts); this is NOT directly comparable to agentdojo-x's
original 7,020-trial run, which scored against "1.0.0"/"1.1.0" (30
prompts) -- see schema.py's KNOWN_LIBRARY_VERSIONS and
is_library_version_comparable().

Raw output handling: payload_hash is always computed from the real
in-memory response text via schema.compute_payload_hash(). The raw text
itself is only attached to the result (as a BenchTrialResultWithRawOutput,
not a BenchTrialResult) when include_raw_output=True (default False) is
threaded all the way from the caller. Any file produced with it set
should be treated as sensitive, not submitted to the public results repo
as-is -- see schema.BenchTrialResultWithRawOutput's docstring.

Failure handling (harness reliability): a trial whose adapter call fails with an
infrastructure error (rate limit, timeout, connection error, provider outage;
see safelabs.agents.errors) is retried with exponential backoff and jitter
(max_attempts, default 3; Retry-After is honoured) and, if it still fails, is
recorded with status="missing_infrastructure" and no verdict or weight, so it
is excluded from every aggregate instead of being scored UNCERTAIN.
rerun_missing() re-executes only those missing rows later (after a cool-down),
rewriting the results file in place and atomically.
Content-policy, no-output-text and other failures are model behaviour: they
are not retried and keep their scoring. A response with empty text and tool
calls is flagged tool_call_only (still scored as before).

Scope note (v0.1.0): token usage capture is out of scope. agentdojo-x
needed a bespoke usage-capturing hook per framework (agent_factories.py,
per its orchestrator.py docstring) to get real numbers; replicating that
generically for a public multi-framework harness is future work, not
attempted here. BenchTrialResult.usage is always None from this module.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import random
import shutil
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentport_bench import __version__ as HARNESS_VERSION
from agentport_bench.schema import (
    CATEGORY_ATTACK_FAMILY,
    VERDICT_WEIGHT,
    BenchTrialResult,
    BenchTrialResultWithRawOutput,
    compute_payload_hash,
)
from safelabs.agents import (
    AgentAdapter,
    AutoGenAdapter,
    CrewAIAdapter,
    GoogleADKAdapter,
    HttpAdapter,
    LangChainAdapter,
    LlamaIndexAdapter,
    OpenAIAgentsAdapter,
    SemanticKernelAdapter,
)
from safelabs.agents.errors import classify_error
from safelabs.agents.retry import RETRY_PROFILES, resolve_retry_settings, retry_delay  # noqa: F401  (re-exported)
from safelabs.prompts import get_library
from safelabs.prompts.schemas import PromptCategory, PromptEntry
from safelabs.runner import CATEGORY_EVAL_TYPE
from safelabs.scoring.scorer import Scorer

# ── adapter construction ─────────────────────────────────────────────────

# Every built-in adapter's constructor takes a different first argument
# (runnable / crew / agent(+recipient) / workflow / agent / agent / agent)
# -- there is no honest way to give this factory one uniform kwarg name
# across all of them, so it doesn't try to. Each maps straight to its real
# constructor's own kwargs; see each class's own docstring/__init__ for
# what it expects. "http" and "custom" are handled specially below,
# not through this table.
_BUILTIN_ADAPTERS: dict[str, type[AgentAdapter]] = {
    "langchain":       LangChainAdapter,       # runnable=...
    "crewai":          CrewAIAdapter,          # crew=...
    "autogen":         AutoGenAdapter,         # agent=..., recipient=...
    "llamaindex":      LlamaIndexAdapter,      # workflow=...
    "openai-agents":   OpenAIAgentsAdapter,    # agent=...
    "google-adk":      GoogleADKAdapter,       # agent=...
    "semantic-kernel": SemanticKernelAdapter,  # agent=...
}


def build_adapter(adapter_name: str, **kwargs: Any) -> AgentAdapter:
    """
    Construct a safelabs.agents.AgentAdapter for the given --adapter name.

    - "http": kwargs go straight to HttpAdapter(**kwargs) -- typically
      just base_url=..., optionally headers=/timeout=.
    - "custom": kwargs must include module="import.path:ClassName" (a
      contributor-supplied AgentAdapter subclass); the remaining kwargs
      go to that class's constructor. Raises TypeError if the imported
      object is not an AgentAdapter subclass.
    - any of _BUILTIN_ADAPTERS's keys: kwargs go straight to that
      adapter's real constructor -- this factory does not invent a
      uniform interface across frameworks that don't have one (see the
      table above for which kwarg name each expects). The contributor
      is responsible for having already built the native chain/crew/
      agent/workflow object themselves; this harness has no way to
      synthesize one generically.

    Raises ValueError for an unrecognized adapter_name.
    """
    if adapter_name == "http":
        return HttpAdapter(**kwargs)

    if adapter_name == "custom":
        module_path = kwargs.pop("module", None)
        if not module_path or ":" not in module_path:
            raise ValueError(
                "adapter_name='custom' requires module=\"import.path:ClassName\""
            )
        mod_name, _, class_name = module_path.partition(":")
        mod = importlib.import_module(mod_name)
        cls = getattr(mod, class_name)
        if not (isinstance(cls, type) and issubclass(cls, AgentAdapter)):
            raise TypeError(
                f"{module_path!r} is not an AgentAdapter subclass"
            )
        return cls(**kwargs)

    try:
        adapter_cls = _BUILTIN_ADAPTERS[adapter_name]
    except KeyError:
        raise ValueError(
            f"unrecognized adapter_name {adapter_name!r}; expected one of "
            f"'http', 'custom', or {sorted(_BUILTIN_ADAPTERS)}"
        ) from None
    return adapter_cls(**kwargs)


# ── run manifest ──────────────────────────────────────────────────────────

class HistoryCell(BaseModel):
    rows_attempted: int = 0
    recovered: int = 0
    still_missing: int = 0


class RerunHistoryEntry(BaseModel):
    """One line of a manifest's ``rerun_history``: a run or a --rerun-missing pass. Entries are only ever appended."""

    kind: Literal["initial", "run", "rerun"] = Field(
        description="initial = the first full run (no manifest existed); run = a later non-rerun invocation; rerun = a --rerun-missing pass.",
    )
    timestamp: str = Field(description="UTC ISO-8601 time the entry was recorded.")
    retry_profile: str
    retry_settings: dict[str, float | int] = Field(description="Effective values: max_attempts, base_delay_s, max_delay_s, max_retry_after_s.")
    rows_attempted: int = Field(description="Rows executed in this run, or re-attempted in this rerun pass.")
    recovered: int = Field(default=0, description="Rerun pass only: re-attempted rows that are now scored.")
    still_missing: int = Field(description="Of the rows attempted in this entry, how many are missing_infrastructure afterwards.")
    by_cell: dict[str, HistoryCell] = Field(default_factory=dict, description="'framework|model' -> counts")


class RunManifest(BaseModel):
    """Sidecar metadata for one submission run, written alongside the
    .jsonl output by write_manifest(). New relative to agentdojo-x, which
    didn't need per-run provenance since it was a single controlled study,
    not results arriving from many contributors' machines."""

    harness_version: str
    library_version: str
    model: str
    framework: str
    started_at: str
    finished_at: str
    trial_count: int
    include_raw_output: bool
    # Optional run-summary counts (absent from manifests written before harness reliability).
    scored_trials: int | None = None
    missing_infrastructure: int | None = None
    missing_by_cell: dict[str, int] | None = None
    retries: int | None = None
    tool_call_only: int | None = None
    max_attempts: int | None = None
    missing_trials_excluded: str | None = None
    rerun_passes: int | None = None
    retry_profile: str | None = None
    rerun_history: list[RerunHistoryEntry] | None = Field(
        default=None, description="Append-only list: the initial run, then one entry per run or --rerun-missing pass. Absent in older manifests.",
    )


def write_manifest(output_path: Path, manifest: RunManifest) -> Path:
    """Write <output_path stem>.manifest.json alongside the results file."""
    manifest_path = output_path.with_suffix(".manifest.json")
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest_path


# ── resume support ───────────────────────────────────────────────────────

def existing_trial_keys(output_path: Path) -> set[tuple[str, str, str, int]]:
    """
    (model, framework, prompt_id, trial_seed) keys already present in an
    existing output file, for --resume. Mirrors agentdojo_x/orchestrator.py's
    resume-by-skip behaviour over the same four-field key.

    Tolerant, not strict: a line that fails to parse (e.g. a truncated
    final line from a crash mid-write) is skipped rather than raising --
    this function's job is resume bookkeeping, not full validation of an
    existing file (that's validate.py's job). A skipped line's trial
    simply gets re-run, which is safe since run_matrix() only ever
    appends, never overwrites.
    """
    if not output_path.exists():
        return set()
    keys: set[tuple[str, str, str, int]] = set()
    for line in output_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            keys.add((obj["model"], obj["framework"], obj["prompt_id"], obj["trial_seed"]))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return keys


# ── retry profiles ───────────────────────────────────────────────────────

# RETRY_PROFILES and resolve_retry_settings() live in safelabs/agents/retry.py (shared with safelabs/runner.py);
# they are re-exported here, so `from agentport_bench.harness import RETRY_PROFILES` keeps working.


# ── run summary ──────────────────────────────────────────────────────────

MISSING_EXCLUDED_NOTE = (
    "missing_infrastructure trials (infrastructure errors that persisted after every retry) have no verdict "
    "and are excluded from every aggregate: pass rates, verdict counts and attack-success weights use scored trials only."
)


class RunSummary(BaseModel):
    """Counts for one run or file. Missing trials are reported here and excluded everywhere else."""

    total_rows: int
    scored: int
    missing_infrastructure: int
    missing_by_cell: dict[str, int] = Field(default_factory=dict, description="'framework|model' -> missing trials")
    trials_retried: int = Field(description="Trials that needed more than one attempt.")
    retries: int = Field(description="Extra attempts made across all trials (sum of attempts - 1).")
    tool_call_only: int
    error_classes: dict[str, int] = Field(default_factory=dict, description="error_class -> rows (final attempt).")
    note: str = MISSING_EXCLUDED_NOTE


def summarize_rows(rows: list[BenchTrialResult]) -> RunSummary:
    """Summarize rows; rows without the new fields (older files) count as scored with one attempt."""
    missing = [r for r in rows if r.is_missing]
    cells = Counter(f"{r.framework}|{r.model}" for r in missing)
    attempts = [(r.attempts or 1) for r in rows]
    return RunSummary(
        total_rows=len(rows),
        scored=len(rows) - len(missing),
        missing_infrastructure=len(missing),
        missing_by_cell=dict(sorted(cells.items())),
        trials_retried=sum(1 for a in attempts if a > 1),
        retries=sum(a - 1 for a in attempts),
        tool_call_only=sum(1 for r in rows if r.tool_call_only),
        error_classes=dict(sorted(Counter(r.error_class for r in rows if r.error_class).items())),
    )


def format_summary_lines(summary: RunSummary) -> list[str]:
    """Plain-text lines for the CLI and logs."""
    lines = [
        f"Trials: {summary.total_rows} total, {summary.scored} scored, "
        f"{summary.missing_infrastructure} missing_infrastructure",
        f"Retries made: {summary.retries} extra attempt(s) across {summary.trials_retried} trial(s)",
        f"Tool-call-only trials: {summary.tool_call_only}",
    ]
    if summary.missing_by_cell:
        lines.append("Missing by framework|model: " + ", ".join(f"{k}={v}" for k, v in summary.missing_by_cell.items()))
    if summary.error_classes:
        lines.append("Failures by class (final attempt): " + ", ".join(f"{k}={v}" for k, v in summary.error_classes.items()))
    lines.append("Note: " + summary.note)
    return lines


# ── trial execution ──────────────────────────────────────────────────────

_retry_delay = retry_delay        # the shared implementation, under its old private name


async def run_trial(
    adapter: AgentAdapter,
    prompt: PromptEntry,
    scorer: Scorer,
    *,
    model: str,
    framework: str,
    trial_seed: int,
    provider: str | None = None,
    include_raw_output: bool = False,
    max_attempts: int = 3,
    base_delay_s: float = 1.0,
    max_delay_s: float = 60.0,
    max_retry_after_s: float = 300.0,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    jitter_fn: Callable[[], float] | None = None,
) -> BenchTrialResult:
    """
    Execute one (adapter, prompt) trial and return a fully populated
    BenchTrialResult (or BenchTrialResultWithRawOutput if
    include_raw_output=True).

    AgentAdapter.execute() never raises (see safelabs/agents/base.py); a
    failure comes back as an AgentResponse with ``error`` set, and
    safelabs.agents.errors.classify_error() sorts it:

    * infrastructure (rate limit, timeout, connection error, provider
      outage): retried up to ``max_attempts`` times in all, with the same
      prompt, waiting ``Retry-After`` when the provider sent one and
      otherwise exponential backoff with jitter (``base_delay_s`` doubling,
      at most ``max_delay_s``). ``sleep`` and ``jitter_fn`` are injectable
      so tests never wait. If it still fails, the trial is recorded with
      status="missing_infrastructure", no verdict, confidence or weight.
    * content_policy, no_output_text, other: not retried, and the text that
      came back (including "" on an error) is scored as before -- empty text
      scores UNCERTAIN with no special-casing needed, matching the behaviour
      agentdojo-x's verification_report.md documents seeing in its own
      7,020-trial run ("the detector pipeline scores empty text as
      UNCERTAIN; expected, not a bug").

    ``attempts`` and ``attempt_errors`` record what happened. A response with
    empty text and tool calls (and no error) is flagged ``tool_call_only``.
    """
    sleeper = sleep or asyncio.sleep
    jitter = jitter_fn or random.random
    limit = max(1, int(max_attempts))
    attempts = 0
    attempt_errors: list[str] = []
    while True:
        attempts += 1
        response = await adapter.execute(prompt.prompt)
        info = classify_error(response.error, response.metadata)
        if info is not None:
            attempt_errors.append(info.error_subclass)
        if info is None or not info.is_infrastructure or attempts >= limit:
            break
        await sleeper(_retry_delay(
            attempts, info, base_delay_s=base_delay_s, max_delay_s=max_delay_s,
            max_retry_after_s=max_retry_after_s, jitter_fn=jitter,
        ))

    missing = info is not None and info.is_infrastructure
    raw_output = "" if missing else response.output
    payload_hash = compute_payload_hash(
        prompt_id=prompt.id,
        model=model,
        framework=framework,
        trial_seed=trial_seed,
        raw_output=raw_output,
    )

    fields: dict[str, Any] = dict(
        model=model,
        provider=provider,
        framework=framework,
        attack_family=CATEGORY_ATTACK_FAMILY[prompt.category],
        category=prompt.category,
        prompt_id=prompt.id,
        trial_seed=trial_seed,
        latency_ms=response.latency_ms,
        error=response.error,
        error_class=info.error_class if info is not None else None,
        error_subclass=info.error_subclass if info is not None else None,
        attempts=attempts,
        attempt_errors=attempt_errors,
        tool_call_only=bool(info is None and not response.output.strip() and response.tool_calls),
        usage=response.usage,
        payload_hash=payload_hash,
        timestamp=datetime.now(timezone.utc).isoformat(),
        harness_version=HARNESS_VERSION,
        library_version=get_library().version,
    )
    if missing:
        fields.update(status="missing_infrastructure", verdict=None, confidence=None,
                      attack_success_weight=None, indicators=[], usage=None)
    else:
        eval_type = CATEGORY_EVAL_TYPE.get(prompt.category.value, "prompt_injection")
        scoring_result = await scorer.score(eval_type, prompt.prompt, response.output)
        fields.update(
            status="scored",
            verdict=scoring_result.verdict,
            confidence=scoring_result.confidence,
            attack_success_weight=VERDICT_WEIGHT[scoring_result.verdict],
            indicators=scoring_result.indicators,
        )

    if include_raw_output:
        return BenchTrialResultWithRawOutput(**fields, raw_output=raw_output)
    return BenchTrialResult(**fields)


# ── matrix run ────────────────────────────────────────────────────────────

async def run_matrix(
    adapter: AgentAdapter,
    *,
    model: str,
    framework: str,
    provider: str | None = None,
    categories: list[str] | None = None,
    seeds: int = 1,
    output_path: Path,
    resume: bool = True,
    include_raw_output: bool = False,
    max_concurrency: int = 1,
    scorer: Scorer | None = None,
    max_attempts: int = 3,
    base_delay_s: float = 1.0,
    max_delay_s: float = 60.0,
    max_retry_after_s: float = 300.0,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    jitter_fn: Callable[[], float] | None = None,
) -> AsyncIterator[BenchTrialResult]:
    """
    Run every (category, prompt, seed) cell for the given adapter/model,
    appending each BenchTrialResult to output_path as it completes
    (crash-safe, like agentdojo_x.orchestrator.run_cells -- results
    already written are safe if this is interrupted). Yields each result
    as it's written, in completion order (not necessarily plan order),
    for caller-side progress reporting.

    categories=None runs all 10 ASI categories. resume=True (default)
    skips any (model, framework, prompt_id, trial_seed) cell already
    present in output_path -- including a missing_infrastructure row, which
    is not re-run on resume (see design notes).

    max_attempts, base_delay_s, max_delay_s, max_retry_after_s, sleep and
    jitter_fn are passed to every run_trial(); see its docstring.
    """
    library = get_library()
    cats = [PromptCategory(c.upper()) for c in categories] if categories else list(PromptCategory)

    prompts: list[PromptEntry] = []
    for cat in cats:
        prompts.extend(library.by_category(cat))

    already_done = existing_trial_keys(output_path) if resume else set()

    plan: list[tuple[PromptEntry, int]] = [
        (prompt, seed)
        for prompt in prompts
        for seed in range(seeds)
        if (model, framework, prompt.id, seed) not in already_done
    ]

    scorer = scorer or Scorer()
    semaphore = asyncio.Semaphore(max(1, max_concurrency))
    write_lock = asyncio.Lock()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    async def _run_one(prompt: PromptEntry, seed: int) -> BenchTrialResult:
        async with semaphore:
            return await run_trial(
                adapter, prompt, scorer,
                model=model, framework=framework, trial_seed=seed,
                provider=provider, include_raw_output=include_raw_output,
                max_attempts=max_attempts, base_delay_s=base_delay_s, max_delay_s=max_delay_s,
                max_retry_after_s=max_retry_after_s, sleep=sleep, jitter_fn=jitter_fn,
            )

    tasks = [asyncio.create_task(_run_one(prompt, seed)) for prompt, seed in plan]
    for finished in asyncio.as_completed(tasks):
        result = await finished
        async with write_lock:
            with output_path.open("a", encoding="utf-8") as f:
                f.write(result.model_dump_json() + "\n")
        yield result


# ── rerun only the missing trials, in place ──────────────────────────────

class RerunCell(BaseModel):
    reattempted: int = 0
    recovered: int = 0
    still_missing: int = 0


class RerunSummary(BaseModel):
    """What one `rerun_missing()` pass did. Scored rows are never re-executed."""

    reattempted: int = 0
    recovered: int = Field(default=0, description="Re-attempted rows that are now scored.")
    still_missing: int = Field(default=0, description="Re-attempted rows that failed again with an infrastructure error.")
    extra_attempts: int = Field(default=0, description="Adapter calls made in this pass.")
    by_cell: dict[str, RerunCell] = Field(default_factory=dict, description="'framework|model' -> counts")
    skipped_not_selected: int = Field(default=0, description="Missing rows of another framework/model or outside --categories.")
    skipped_library_mismatch: int = Field(default=0, description="Missing rows scored against a different prompt-library version.")
    skipped_unknown_prompt: int = Field(default=0, description="Missing rows whose prompt_id is not in the current library.")
    rewrote_file: bool = False


def format_rerun_lines(summary: RerunSummary) -> list[str]:
    """Plain-text lines for the CLI and logs."""
    lines = [
        f"Rerun pass: {summary.reattempted} row(s) re-attempted, {summary.recovered} recovered (now scored), "
        f"{summary.still_missing} still missing_infrastructure",
    ]
    for cell, c in sorted(summary.by_cell.items()):
        lines.append(f"  {cell}: {c.reattempted} re-attempted, {c.recovered} recovered, {c.still_missing} still missing")
    skipped = summary.skipped_not_selected + summary.skipped_library_mismatch + summary.skipped_unknown_prompt
    if skipped:
        lines.append(
            f"Skipped missing row(s): {summary.skipped_not_selected} other framework/model or category, "
            f"{summary.skipped_library_mismatch} different library version, {summary.skipped_unknown_prompt} unknown prompt id"
        )
    lines.append("Scored rows were not re-executed; the file was " + ("rewritten in place." if summary.rewrote_file else "left untouched."))
    return lines


def history_entry_initial(
    rows: list[BenchTrialResult], *, retry_profile: str, retry_settings: dict[str, float | int], kind: str = "initial",
) -> RerunHistoryEntry:
    """History entry for a run: its own row counts, by framework x model."""
    cells: dict[str, HistoryCell] = {}
    for r in rows:
        c = cells.setdefault(f"{r.framework}|{r.model}", HistoryCell())
        c.rows_attempted += 1
        c.still_missing += 1 if r.is_missing else 0
    return RerunHistoryEntry(
        kind=kind, timestamp=datetime.now(timezone.utc).isoformat(), retry_profile=retry_profile,
        retry_settings=dict(retry_settings), rows_attempted=len(rows),
        still_missing=sum(c.still_missing for c in cells.values()), by_cell=dict(sorted(cells.items())),
    )


def history_entry_from_rerun(
    summary: RerunSummary, *, retry_profile: str, retry_settings: dict[str, float | int],
) -> RerunHistoryEntry:
    """History entry for one --rerun-missing pass, from its RerunSummary."""
    return RerunHistoryEntry(
        kind="rerun", timestamp=datetime.now(timezone.utc).isoformat(), retry_profile=retry_profile,
        retry_settings=dict(retry_settings), rows_attempted=summary.reattempted, recovered=summary.recovered,
        still_missing=summary.still_missing,
        by_cell={k: HistoryCell(rows_attempted=c.reattempted, recovered=c.recovered, still_missing=c.still_missing)
                 for k, c in sorted(summary.by_cell.items())},
    )


def _replace_file_atomically(path: Path, text: str) -> None:
    """Write ``text`` to a temporary file in the same directory, then os.replace it over ``path``.
    If anything raises before the replace, ``path`` is untouched and the temporary file is removed."""
    tmp = path.with_name(f".{path.name}.rerun-{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


async def rerun_missing(
    adapter: AgentAdapter,
    *,
    model: str,
    framework: str,
    output_path: Path,
    categories: list[str] | None = None,
    max_concurrency: int = 1,
    scorer: Scorer | None = None,
    max_attempts: int = 3,
    base_delay_s: float = 1.0,
    max_delay_s: float = 60.0,
    max_retry_after_s: float = 300.0,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    jitter_fn: Callable[[], float] | None = None,
) -> RerunSummary:
    """
    Re-execute only the ``status == "missing_infrastructure"`` rows of ``output_path`` for this
    (model, framework), then rewrite the file in place.

    * Only those rows run again (same trial key, prompt text from the library, seed and
      identity fields); scored rows are never re-executed and their lines are kept byte for byte.
    * A row that now succeeds becomes ``status="scored"`` with its verdict, and its payload_hash
      is computed from the new output (the hash binds the output, so a recovered row's hash
      necessarily differs from the empty-output hash it had while missing); a row that fails
      again stays ``missing_infrastructure`` (same hash, same key).
    * History is cumulative: ``attempts`` adds this pass's attempts, ``attempt_errors`` appends
      this pass's entries to the earlier ones, ``rerun_passes`` goes up by one.
    * Rows scored against a different library version, or whose prompt id is gone, are skipped
      and counted (the prompt would not be the same one).
    * Safe write: the full updated file goes to a temporary file in the same directory and is
      os.replace()d over the original, so row order is preserved and an exception or kill
      before the replace leaves the original untouched. Nothing is written when no row ran.
    """
    if not output_path.exists():
        raise FileNotFoundError(f"{output_path} does not exist; nothing to rerun")
    library = get_library()
    by_id = {entry.id: entry for entry in library.entries}
    wanted = {c.upper() for c in categories} if categories else None
    scorer = scorer or Scorer()
    summary = RerunSummary()

    lines = output_path.read_text(encoding="utf-8").splitlines(keepends=True)
    targets: list[tuple[int, BenchTrialResult, PromptEntry]] = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict) or obj.get("status") != "missing_infrastructure":
                continue
            row = (BenchTrialResultWithRawOutput if "raw_output" in obj else BenchTrialResult)(**obj)
        except Exception:  # noqa: BLE001  (a line that does not parse is left exactly as it is)
            continue
        if row.model != model or row.framework != framework or (wanted is not None and row.category.value not in wanted):
            summary.skipped_not_selected += 1
        elif row.library_version != library.version:
            summary.skipped_library_mismatch += 1
        elif row.prompt_id not in by_id:
            summary.skipped_unknown_prompt += 1
        else:
            targets.append((i, row, by_id[row.prompt_id]))
    if not targets:
        return summary

    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def _one(row: BenchTrialResult, prompt: PromptEntry) -> BenchTrialResult:
        async with semaphore:
            fresh = await run_trial(
                adapter, prompt, scorer,
                model=row.model, framework=row.framework, trial_seed=row.trial_seed, provider=row.provider,
                include_raw_output=isinstance(row, BenchTrialResultWithRawOutput),
                max_attempts=max_attempts, base_delay_s=base_delay_s, max_delay_s=max_delay_s,
                max_retry_after_s=max_retry_after_s, sleep=sleep, jitter_fn=jitter_fn,
            )
        merged = fresh.model_dump()
        merged["attempts"] = (row.attempts or 1) + (fresh.attempts or 1)
        merged["attempt_errors"] = list(row.attempt_errors or []) + list(fresh.attempt_errors or [])
        merged["rerun_passes"] = row.rerun_passes + 1
        summary.extra_attempts += fresh.attempts or 1
        return type(fresh)(**merged)

    tasks = [asyncio.create_task(_one(row, prompt)) for _, row, prompt in targets]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        raise

    for (i, row, _), new in zip(targets, results):
        lines[i] = new.model_dump_json() + ("\n" if lines[i].endswith("\n") else "")
        cell = summary.by_cell.setdefault(f"{row.framework}|{row.model}", RerunCell())
        cell.reattempted += 1
        summary.reattempted += 1
        if new.is_missing:
            cell.still_missing += 1
            summary.still_missing += 1
        else:
            cell.recovered += 1
            summary.recovered += 1
    _replace_file_atomically(output_path, "".join(lines))
    summary.rewrote_file = True
    return summary
