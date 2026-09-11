"""
tests/test_agentport_bench_harness.py

Tests for agentport_bench.harness -- build_adapter(), the resume-key
reader, the run manifest, run_trial(), and run_matrix().

Uses hand-written duck-typed fake adapters (mirroring the safelabs-eval
adapter test convention -- see tests/test_agentport_bench_validate.py's
own docstring and tests/test_crewai_adapter.py) so this file needs no
real framework installed, except where a built-in adapter class's own
constructor is exercised directly (langchain-core, autogen/ag2, crewai
happen to be installed in this dev environment already, but the tests
below construct those adapters with fakes as their wrapped object, not
real chains/crews, so they'd work either way).
"""

from __future__ import annotations

import json

import pytest

from agentport_bench import __version__ as HARNESS_VERSION
from agentport_bench.harness import (
    RunManifest,
    build_adapter,
    existing_trial_keys,
    run_matrix,
    run_trial,
    write_manifest,
)
from agentport_bench.schema import (
    CATEGORY_ATTACK_FAMILY,
    VERDICT_WEIGHT,
    BenchTrialResult,
    BenchTrialResultWithRawOutput,
    compute_payload_hash,
)
from safelabs.agents import AutoGenAdapter, CrewAIAdapter, HttpAdapter, LangChainAdapter
from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse
from safelabs.prompts import get_library
from safelabs.scoring.models import VerdictLevel
from safelabs.scoring.scorer import Scorer

_CURRENT_LIBRARY_VERSION = get_library().version


# ── fake adapters ────────────────────────────────────────────────────────

class _FakeRefusalAdapter(AgentAdapter):
    """Always refuses -- should score PASS on the prompt-injection detector."""

    @property
    def adapter_type(self) -> str:
        return "fake-refusal"

    async def _execute(self, prompt: str) -> AgentResponse:
        return AgentResponse(
            output="I cannot and will not comply with that request. That looks like an injection attempt.",
            latency_ms=1.0,
        )


class _FakeErrorAdapter(AgentAdapter):
    """Always raises -- exercises AgentAdapter.execute()'s own error handling,
    which run_trial() must score (not bypass) per its docstring."""

    @property
    def adapter_type(self) -> str:
        return "fake-error"

    async def _execute(self, prompt: str) -> AgentResponse:
        raise RuntimeError("simulated provider failure")


# ── build_adapter ─────────────────────────────────────────────────────────

def test_build_adapter_http():
    a = build_adapter("http", base_url="http://localhost:9999")
    assert isinstance(a, HttpAdapter)
    assert a.base_url == "http://localhost:9999"


def test_build_adapter_builtin_langchain():
    fake_runnable = object()
    a = build_adapter("langchain", runnable=fake_runnable, input_key="input")
    assert isinstance(a, LangChainAdapter)


def test_build_adapter_builtin_autogen_needs_two_positional_style_kwargs():
    """AutoGenAdapter's constructor takes agent AND recipient -- confirms
    build_adapter doesn't try to force a uniform single-object interface
    across adapters that don't have one."""
    a = build_adapter("autogen", agent=object(), recipient=object())
    assert isinstance(a, AutoGenAdapter)


def test_build_adapter_builtin_crewai():
    a = build_adapter("crewai", crew=object())
    assert isinstance(a, CrewAIAdapter)


def test_build_adapter_custom_valid_module():
    module_path = f"{__name__}:_FakeRefusalAdapter"
    a = build_adapter("custom", module=module_path)
    assert isinstance(a, _FakeRefusalAdapter)


def test_build_adapter_custom_missing_module_raises():
    with pytest.raises(ValueError, match="module"):
        build_adapter("custom")


def test_build_adapter_custom_non_adapter_class_raises():
    with pytest.raises(TypeError):
        build_adapter("custom", module=f"{__name__}:_NotAnAdapter")


class _NotAnAdapter:
    pass


def test_build_adapter_unknown_name_raises():
    with pytest.raises(ValueError, match="unrecognized"):
        build_adapter("not-a-real-framework")


# ── existing_trial_keys ───────────────────────────────────────────────────

def test_existing_trial_keys_missing_file(tmp_path):
    assert existing_trial_keys(tmp_path / "does-not-exist.jsonl") == set()


def test_existing_trial_keys_reads_real_rows(tmp_path):
    p = tmp_path / "out.jsonl"
    p.write_text(
        json.dumps({"model": "m", "framework": "f", "prompt_id": "ASI01-001", "trial_seed": 0, "other": "x"}) + "\n"
        + json.dumps({"model": "m", "framework": "f", "prompt_id": "ASI01-002", "trial_seed": 3, "other": "y"}) + "\n"
    )
    keys = existing_trial_keys(p)
    assert keys == {("m", "f", "ASI01-001", 0), ("m", "f", "ASI01-002", 3)}


def test_existing_trial_keys_skips_bad_lines_without_raising(tmp_path):
    p = tmp_path / "out.jsonl"
    p.write_text(
        json.dumps({"model": "m", "framework": "f", "prompt_id": "ASI01-001", "trial_seed": 0}) + "\n"
        + "truncated garbage not json\n"
        + json.dumps({"model": "m", "framework": "f"}) + "\n"  # valid JSON, missing keys
    )
    keys = existing_trial_keys(p)
    assert keys == {("m", "f", "ASI01-001", 0)}


# ── write_manifest / RunManifest ─────────────────────────────────────────

def test_write_manifest_creates_sibling_file(tmp_path):
    out = tmp_path / "submission.jsonl"
    out.write_text("")
    manifest = RunManifest(
        harness_version="0.1.0", library_version=_CURRENT_LIBRARY_VERSION,
        model="m", framework="f", started_at="2026-09-10T00:00:00Z",
        finished_at="2026-09-10T00:01:00Z", trial_count=5, include_raw_output=False,
    )
    manifest_path = write_manifest(out, manifest)
    assert manifest_path == tmp_path / "submission.manifest.json"
    assert manifest_path.exists()
    loaded = json.loads(manifest_path.read_text())
    assert loaded["trial_count"] == 5
    assert loaded["library_version"] == _CURRENT_LIBRARY_VERSION


# ── run_trial ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_trial_happy_path_fields():
    library = get_library()
    prompt = library.by_category("ASI01")[0]
    scorer = Scorer()

    result = await run_trial(
        _FakeRefusalAdapter(), prompt, scorer,
        model="claude-opus-4-8", framework="fake-refusal", trial_seed=2,
    )

    assert isinstance(result, BenchTrialResult)
    assert not isinstance(result, BenchTrialResultWithRawOutput)
    assert result.model == "claude-opus-4-8"
    assert result.framework == "fake-refusal"
    assert result.prompt_id == prompt.id
    assert result.category == prompt.category
    assert result.attack_family == CATEGORY_ATTACK_FAMILY[prompt.category]
    assert result.trial_seed == 2
    assert result.error is None
    assert result.usage is None
    assert result.harness_version == HARNESS_VERSION
    assert result.library_version == _CURRENT_LIBRARY_VERSION
    assert result.attack_success_weight == VERDICT_WEIGHT[result.verdict]


@pytest.mark.asyncio
async def test_run_trial_threads_provider_through_to_result():
    """Regression test: provider was accepted by BenchTrialResult but
    silently dropped on the floor by run_trial()/run_matrix() until this
    was caught in review -- both must now actually set it."""
    library = get_library()
    prompt = library.by_category("ASI01")[0]
    scorer = Scorer()

    result = await run_trial(
        _FakeRefusalAdapter(), prompt, scorer,
        model="claude-opus-4-8", framework="fake-refusal", trial_seed=0,
        provider="anthropic",
    )
    assert result.provider == "anthropic"

    default_result = await run_trial(
        _FakeRefusalAdapter(), prompt, scorer,
        model="claude-opus-4-8", framework="fake-refusal", trial_seed=0,
    )
    assert default_result.provider is None


@pytest.mark.asyncio
async def test_run_matrix_threads_provider_through_to_every_row(tmp_path):
    out = tmp_path / "sub.jsonl"
    results = await _collect(run_matrix(
        _FakeRefusalAdapter(), model="m", framework="f", provider="openai",
        categories=["ASI09"], seeds=1, output_path=out,
    ))
    assert results
    assert all(r.provider == "openai" for r in results)


@pytest.mark.asyncio
async def test_run_trial_payload_hash_matches_independent_computation():
    library = get_library()
    prompt = library.by_category("ASI01")[0]
    scorer = Scorer()
    adapter = _FakeRefusalAdapter()

    result = await run_trial(
        adapter, prompt, scorer,
        model="claude-opus-4-8", framework="fake-refusal", trial_seed=0,
    )

    # Independently reconstruct the raw output the same fake adapter would
    # have produced, and confirm the stored hash matches recomputing it --
    # this is the harness-side half of the same guarantee
    # test_agentport_bench_schema.py locks from the schema side.
    raw_output = (await adapter.execute(prompt.prompt)).output
    expected_hash = compute_payload_hash(
        prompt_id=prompt.id, model="claude-opus-4-8", framework="fake-refusal",
        trial_seed=0, raw_output=raw_output,
    )
    assert result.payload_hash == expected_hash


@pytest.mark.asyncio
async def test_run_trial_scores_empty_output_on_adapter_error_not_bypass():
    """AgentAdapter.execute() never raises -- an adapter error becomes an
    AgentResponse with output="" and error set. run_trial() must still
    score that empty text (yielding UNCERTAIN, per the pattern-matching
    detectors having no signal on empty input) rather than special-casing
    around the scorer -- matching agentdojo-x's own observed behaviour
    (verification_report.md: error rows score UNCERTAIN, "expected, not a bug")."""
    library = get_library()
    prompt = library.by_category("ASI01")[0]
    scorer = Scorer()

    result = await run_trial(
        _FakeErrorAdapter(), prompt, scorer,
        model="m", framework="fake-error", trial_seed=0,
    )

    assert result.error is not None
    assert "simulated provider failure" in result.error
    assert result.verdict == VerdictLevel.UNCERTAIN


@pytest.mark.asyncio
async def test_run_trial_include_raw_output_true_returns_subclass_with_text():
    library = get_library()
    prompt = library.by_category("ASI01")[0]
    scorer = Scorer()

    result = await run_trial(
        _FakeRefusalAdapter(), prompt, scorer,
        model="m", framework="fake-refusal", trial_seed=0, include_raw_output=True,
    )

    assert isinstance(result, BenchTrialResultWithRawOutput)
    assert result.raw_output.startswith("I cannot and will not comply")


@pytest.mark.asyncio
async def test_run_trial_include_raw_output_false_has_no_raw_text_field():
    library = get_library()
    prompt = library.by_category("ASI01")[0]
    scorer = Scorer()

    result = await run_trial(
        _FakeRefusalAdapter(), prompt, scorer,
        model="m", framework="fake-refusal", trial_seed=0, include_raw_output=False,
    )

    assert "raw_output" not in result.model_dump()


# ── run_matrix ────────────────────────────────────────────────────────────

async def _collect(agen):
    return [x async for x in agen]


@pytest.mark.asyncio
async def test_run_matrix_covers_full_category_and_writes_all_lines(tmp_path):
    library = get_library()
    out = tmp_path / "sub.jsonl"

    results = await _collect(run_matrix(
        _FakeRefusalAdapter(), model="m", framework="f",
        categories=["ASI06"], seeds=1, output_path=out,
    ))

    expected_n = len(library.by_category("ASI06"))
    assert len(results) == expected_n
    assert len(out.read_text().splitlines()) == expected_n

    written_ids = {json.loads(line)["prompt_id"] for line in out.read_text().splitlines()}
    assert written_ids == {e.id for e in library.by_category("ASI06")}


@pytest.mark.asyncio
async def test_run_matrix_multiple_seeds_multiplies_trial_count(tmp_path):
    library = get_library()
    out = tmp_path / "sub.jsonl"

    results = await _collect(run_matrix(
        _FakeRefusalAdapter(), model="m", framework="f",
        categories=["ASI09"], seeds=3, output_path=out,
    ))

    assert len(results) == len(library.by_category("ASI09")) * 3
    seeds_seen = {r.trial_seed for r in results}
    assert seeds_seen == {0, 1, 2}


@pytest.mark.asyncio
async def test_run_matrix_resume_skips_completed_cells(tmp_path):
    out = tmp_path / "sub.jsonl"

    first = await _collect(run_matrix(
        _FakeRefusalAdapter(), model="m", framework="f",
        categories=["ASI06"], seeds=1, output_path=out,
    ))
    assert len(first) > 0

    second = await _collect(run_matrix(
        _FakeRefusalAdapter(), model="m", framework="f",
        categories=["ASI06"], seeds=1, output_path=out, resume=True,
    ))
    assert second == []
    # file wasn't truncated/rewritten -- still exactly the first run's rows
    assert len(out.read_text().splitlines()) == len(first)


@pytest.mark.asyncio
async def test_run_matrix_resume_false_reruns_everything(tmp_path):
    out = tmp_path / "sub.jsonl"

    first = await _collect(run_matrix(
        _FakeRefusalAdapter(), model="m", framework="f",
        categories=["ASI06"], seeds=1, output_path=out,
    ))

    second = await _collect(run_matrix(
        _FakeRefusalAdapter(), model="m", framework="f",
        categories=["ASI06"], seeds=1, output_path=out, resume=False,
    ))
    assert len(second) == len(first)
    # appended, not overwritten -- file now has both runs' rows
    assert len(out.read_text().splitlines()) == len(first) * 2


@pytest.mark.asyncio
async def test_run_matrix_partial_prior_run_only_fills_the_gap(tmp_path):
    """Simulates a crashed prior run: output_path already has some rows
    before run_matrix() is ever called on it."""
    library = get_library()
    out = tmp_path / "sub.jsonl"
    entries = library.by_category("ASI06")
    out.write_text(json.dumps({
        "model": "m", "framework": "f", "prompt_id": entries[0].id, "trial_seed": 0,
    }) + "\n")

    results = await _collect(run_matrix(
        _FakeRefusalAdapter(), model="m", framework="f",
        categories=["ASI06"], seeds=1, output_path=out, resume=True,
    ))

    assert len(results) == len(entries) - 1
    assert entries[0].id not in {r.prompt_id for r in results}


@pytest.mark.asyncio
async def test_run_matrix_creates_output_parent_directory(tmp_path):
    out = tmp_path / "nested" / "dir" / "sub.jsonl"
    results = await _collect(run_matrix(
        _FakeRefusalAdapter(), model="m", framework="f",
        categories=["ASI09"], seeds=1, output_path=out,
    ))
    assert out.exists()
    assert len(results) > 0


@pytest.mark.asyncio
async def test_run_matrix_concurrency_does_not_change_coverage(tmp_path):
    library = get_library()
    out = tmp_path / "sub.jsonl"

    results = await _collect(run_matrix(
        _FakeRefusalAdapter(), model="m", framework="f",
        categories=["ASI06"], seeds=1, output_path=out, max_concurrency=8,
    ))

    assert len(results) == len(library.by_category("ASI06"))
    assert len({r.prompt_id for r in results}) == len(library.by_category("ASI06"))
