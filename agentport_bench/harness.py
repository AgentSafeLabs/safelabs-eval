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
"1.6.0" (131 prompts); this is NOT directly comparable to agentdojo-x's
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
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

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


# ── trial execution ──────────────────────────────────────────────────────

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
) -> BenchTrialResult:
    """
    Execute one (adapter, prompt) trial and return a fully populated
    BenchTrialResult (or BenchTrialResultWithRawOutput if
    include_raw_output=True).

    Always scores whatever text comes back, including "" on an adapter
    error -- AgentAdapter.execute() never raises (see safelabs/agents/base.py),
    and empty text scores UNCERTAIN with no special-casing needed, matching
    the behaviour agentdojo-x's verification_report.md documents seeing in
    its own 7,020-trial run ("the detector pipeline scores empty text as
    UNCERTAIN; expected, not a bug").
    """
    response = await adapter.execute(prompt.prompt)

    eval_type = CATEGORY_EVAL_TYPE.get(prompt.category.value, "prompt_injection")
    scoring_result = await scorer.score(eval_type, prompt.prompt, response.output)

    payload_hash = compute_payload_hash(
        prompt_id=prompt.id,
        model=model,
        framework=framework,
        trial_seed=trial_seed,
        raw_output=response.output,
    )

    fields: dict[str, Any] = dict(
        model=model,
        provider=provider,
        framework=framework,
        attack_family=CATEGORY_ATTACK_FAMILY[prompt.category],
        category=prompt.category,
        prompt_id=prompt.id,
        trial_seed=trial_seed,
        verdict=scoring_result.verdict,
        confidence=scoring_result.confidence,
        attack_success_weight=VERDICT_WEIGHT[scoring_result.verdict],
        latency_ms=response.latency_ms,
        error=response.error,
        indicators=scoring_result.indicators,
        usage=None,
        payload_hash=payload_hash,
        timestamp=datetime.now(timezone.utc).isoformat(),
        harness_version=HARNESS_VERSION,
        library_version=get_library().version,
    )

    if include_raw_output:
        return BenchTrialResultWithRawOutput(**fields, raw_output=response.output)
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
) -> AsyncIterator[BenchTrialResult]:
    """
    Run every (category, prompt, seed) cell for the given adapter/model,
    appending each BenchTrialResult to output_path as it completes
    (crash-safe, like agentdojo_x.orchestrator.run_cells -- results
    already written are safe if this is interrupted). Yields each result
    as it's written, in completion order (not necessarily plan order),
    for caller-side progress reporting.

    categories=None runs all 10 OWASP ASI categories. resume=True (default)
    skips any (model, framework, prompt_id, trial_seed) cell already
    present in output_path.
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
            )

    tasks = [asyncio.create_task(_run_one(prompt, seed)) for prompt, seed in plan]
    for finished in asyncio.as_completed(tasks):
        result = await finished
        async with write_lock:
            with output_path.open("a", encoding="utf-8") as f:
                f.write(result.model_dump_json() + "\n")
        yield result
