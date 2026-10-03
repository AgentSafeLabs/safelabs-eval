"""
tests/test_harness_rerun_missing.py

`run --rerun-missing`: re-execute only the missing_infrastructure rows of an existing results
file, in place and atomically, with cumulative history; and the benchmark retry profile.
No network; the sleep function is injected (or the delays are 0); every file is in a tmp dir.
"""

from __future__ import annotations

import json
import zlib

import pytest
from click.testing import CliRunner

from agentport_bench import cli as cli_module
from agentport_bench import harness
from agentport_bench.cli import main
from agentport_bench.harness import (
    RETRY_PROFILES,
    RunManifest,
    format_rerun_lines,
    rerun_missing,
    resolve_retry_settings,
    run_matrix,
)
from agentport_bench.schema import BenchTrialResult, compute_payload_hash
from agentport_bench.validate import load_submission, validate_submission
from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse
from safelabs.prompts import get_library
from safelabs.scoring.models import VerdictLevel
from safelabs.scoring.scorer import Scorer

_REFUSAL = AgentResponse(output="I cannot and will not comply with that request. That looks like an injection attempt.", latency_ms=1.0)
_LIB = get_library()


async def _no_sleep(_s):
    return None


def _is_flaky_prompt(prompt: str) -> bool:
    return zlib.crc32(prompt.encode()) % 10 != 0           # 90% of prompts


class _Adapter(AgentAdapter):
    """mode: 'flaky' (rate-limited for 90% of prompts), 'down' (always rate-limited), 'clean'. Records every prompt."""

    def __init__(self, mode):
        super().__init__(timeout=5)
        self.mode, self.prompts = mode, []

    @property
    def adapter_type(self):
        return "recording"

    async def _execute(self, prompt):
        self.prompts.append(prompt)
        if self.mode == "down" or (self.mode == "flaky" and _is_flaky_prompt(prompt)) or (self.mode == "half" and zlib.crc32(prompt.encode()) % 2 == 0):
            return AgentResponse(output="", error="HTTP 429: slow down")
        return _REFUSAL


async def _first_run(path, mode="flaky", *, framework="crewai", model="gpt-5.5", categories=("ASI01",), **kw):
    return [r async for r in run_matrix(_Adapter(mode), model=model, framework=framework, categories=list(categories), seeds=1,
                                        output_path=path, sleep=_no_sleep, resume=False, **kw)]


async def _rerun(path, mode="clean", *, framework="crewai", model="gpt-5.5", adapter=None, **kw):
    adapter = adapter or _Adapter(mode)
    kw.setdefault("sleep", _no_sleep)
    summary = await rerun_missing(adapter, model=model, framework=framework, output_path=path, **kw)
    return summary, adapter


def _lines(path):
    return path.read_text().splitlines(keepends=True)


def _rows(path):
    return load_submission(path)


# ── recovery ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_rows_recover_to_scored_and_scored_rows_are_untouched(tmp_path):
    path = tmp_path / "r.jsonl"
    await _first_run(path)
    before_lines, before_rows = _lines(path), _rows(path)
    missing_idx = [i for i, r in enumerate(before_rows) if r.is_missing]
    assert 0 < len(missing_idx) < len(before_rows)

    summary, adapter = await _rerun(path, "clean")
    after_lines, after_rows = _lines(path), _rows(path)

    assert len(after_lines) == len(before_lines)                                         # row order and count preserved
    assert [(r.model, r.framework, r.prompt_id, r.trial_seed) for r in after_rows] == \
           [(r.model, r.framework, r.prompt_id, r.trial_seed) for r in before_rows]
    for i, (b, a) in enumerate(zip(before_lines, after_lines)):
        if i not in missing_idx:
            assert a == b                                                               # scored rows: byte-identical
    assert not any(r.is_missing for r in after_rows)
    for i in missing_idx:
        old, new = before_rows[i], after_rows[i]
        assert new.status == "scored" and new.verdict == VerdictLevel.PASS and new.attack_success_weight == 0.0
        assert new.error is None and new.error_class is None and new.confidence is not None
        assert new.attempts == old.attempts + 1 == 4 and new.rerun_passes == 1
        assert new.attempt_errors == old.attempt_errors + [] == ["rate_limit_or_quota"] * 3
        assert new.payload_hash == compute_payload_hash(prompt_id=new.prompt_id, model="gpt-5.5", framework="crewai",
                                                        trial_seed=new.trial_seed, raw_output=_REFUSAL.output)
        assert new.payload_hash != old.payload_hash          # the hash binds the output, which is no longer empty
    assert (summary.reattempted, summary.recovered, summary.still_missing) == (len(missing_idx), len(missing_idx), 0)
    assert summary.by_cell["crewai|gpt-5.5"].recovered == len(missing_idx) and summary.rewrote_file
    assert validate_submission(path).accepted


@pytest.mark.asyncio
async def test_only_missing_rows_are_executed_with_the_same_prompt(tmp_path):
    path = tmp_path / "r.jsonl"
    await _first_run(path)
    rows = _rows(path)
    expected = {next(e.prompt for e in _LIB.entries if e.id == r.prompt_id) for r in rows if r.is_missing}
    _, adapter = await _rerun(path, "clean")
    assert set(adapter.prompts) == expected and len(adapter.prompts) == len(expected)    # one call each, nothing else


@pytest.mark.asyncio
async def test_rows_that_fail_again_stay_missing_with_cumulative_history(tmp_path):
    path = tmp_path / "r.jsonl"
    await _first_run(path)
    before = _rows(path)
    summary, _ = await _rerun(path, "down")
    after = _rows(path)
    for old, new in zip(before, after):
        if old.is_missing:
            assert new.is_missing and new.verdict is None and new.attack_success_weight is None
            assert new.attempts == old.attempts + 3 == 6 and new.rerun_passes == 1
            assert new.attempt_errors == ["rate_limit_or_quota"] * 6
            assert new.payload_hash == old.payload_hash and new.error_subclass == "rate_limit_or_quota"
        else:
            assert new == old
    assert summary.recovered == 0 and summary.still_missing == summary.reattempted == sum(r.is_missing for r in before)
    again, _ = await _rerun(path, "down")                                               # a second pass keeps accumulating
    third = _rows(path)
    first_missing = next(r for r in third if r.is_missing)
    assert first_missing.attempts == 9 and first_missing.rerun_passes == 2 and len(first_missing.attempt_errors) == 9
    assert again.still_missing == again.reattempted


@pytest.mark.asyncio
async def test_a_partial_recovery_mixes_outcomes(tmp_path):
    path = tmp_path / "r.jsonl"
    await _first_run(path, "down")                                                      # every row missing
    n = len(_rows(path))
    summary, _ = await _rerun(path, "half")                                             # about half the prompts succeed now
    after = _rows(path)
    assert summary.reattempted == n and summary.recovered + summary.still_missing == n
    assert 0 < summary.recovered < n and sum(r.is_missing for r in after) == summary.still_missing
    assert all(r.rerun_passes == 1 for r in after)
    assert all(r.attempts == (4 if not r.is_missing else 6) for r in after)
    assert summary.by_cell["crewai|gpt-5.5"].reattempted == n


# ── no-op, other cells, filters, library, old files ───────────────────────

@pytest.mark.asyncio
async def test_a_file_with_no_missing_rows_is_a_no_op(tmp_path):
    path = tmp_path / "r.jsonl"
    await _first_run(path, "clean")
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns
    summary, adapter = await _rerun(path, "clean")
    assert path.read_bytes() == before and path.stat().st_mtime_ns == mtime
    assert adapter.prompts == [] and summary.reattempted == 0 and not summary.rewrote_file
    assert [p.name for p in tmp_path.iterdir()] == ["r.jsonl"]                          # no temporary file left behind
    assert "left untouched" in "\n".join(format_rerun_lines(summary))


@pytest.mark.asyncio
async def test_other_cells_and_other_categories_are_not_touched(tmp_path):
    path = tmp_path / "r.jsonl"
    await _first_run(path, framework="crewai", model="gpt-5.5")
    await _first_run(path, framework="http", model="gpt-5.5")                           # appended: a second cell, also flaky
    await _first_run(path, framework="crewai", model="gemini-3.5-flash", categories=("ASI02",))
    before = _rows(path)
    summary, adapter = await _rerun(path, "clean", framework="crewai", model="gpt-5.5", categories=["ASI01"])
    after = _rows(path)
    for old, new in zip(before, after):
        if (old.framework, old.model) == ("crewai", "gpt-5.5") and old.category.value == "ASI01":
            assert not new.is_missing or not old.is_missing
        else:
            assert new == old                                                           # other cells: identical rows
    assert set(summary.by_cell) == {"crewai|gpt-5.5"}
    assert summary.skipped_not_selected == sum(r.is_missing for r in before if (r.framework, r.model) != ("crewai", "gpt-5.5"))
    # a second pass for the other cell, with the same file
    s2, _ = await _rerun(path, "clean", framework="http", model="gpt-5.5")
    assert set(s2.by_cell) == {"http|gpt-5.5"} and s2.recovered == s2.reattempted > 0


@pytest.mark.asyncio
async def test_rows_from_another_library_version_or_unknown_prompt_are_skipped_and_counted(tmp_path):
    path = tmp_path / "r.jsonl"
    await _first_run(path)
    lines = _lines(path)
    objs = [json.loads(line) for line in lines]
    miss = [i for i, o in enumerate(objs) if o["status"] == "missing_infrastructure"]
    objs[miss[0]]["library_version"] = "1.6.0"
    objs[miss[1]]["prompt_id"] = "ASI01-999"
    path.write_text("".join(json.dumps(o) + "\n" for o in objs))
    frozen = _lines(path)
    summary, _ = await _rerun(path, "clean")
    after = _lines(path)
    assert summary.skipped_library_mismatch == 1 and summary.skipped_unknown_prompt == 1
    assert summary.reattempted == len(miss) - 2
    assert after[miss[0]] == frozen[miss[0]] and after[miss[1]] == frozen[miss[1]]      # left exactly as they were


@pytest.mark.asyncio
async def test_old_result_files_without_the_new_fields_work(tmp_path):
    path = tmp_path / "old.jsonl"
    base = dict(model="m", framework="f", attack_family="injection_output", category="ASI01", trial_seed=0, verdict="pass", confidence=0.9,
                timestamp="2026-09-10T12:00:00Z", harness_version="0.1.0", library_version=_LIB.version)
    rows = [dict(base, prompt_id=f"ASI01-00{i}", payload_hash=compute_payload_hash(prompt_id=f"ASI01-00{i}", model="m", framework="f", trial_seed=0, raw_output="x"))
            for i in range(1, 4)]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    loaded = _rows(path)
    assert all(r.rerun_passes == 0 and r.attempts is None and r.status is None for r in loaded)
    before = path.read_bytes()
    summary, adapter = await _rerun(path, "clean", framework="f", model="m")
    assert path.read_bytes() == before and summary.reattempted == 0 and adapter.prompts == []


@pytest.mark.asyncio
async def test_a_missing_row_without_history_fields_starts_from_one_attempt(tmp_path):
    path = tmp_path / "r.jsonl"
    await _first_run(path)
    objs = [json.loads(line) for line in _lines(path)]
    i = next(i for i, o in enumerate(objs) if o["status"] == "missing_infrastructure")
    for key in ("attempts", "attempt_errors", "rerun_passes"):
        objs[i].pop(key)
    path.write_text("".join(json.dumps(o) + "\n" for o in objs))
    await _rerun(path, "down")
    row = _rows(path)[i]
    assert row.attempts == 1 + 3 and row.attempt_errors == ["rate_limit_or_quota"] * 3 and row.rerun_passes == 1


@pytest.mark.asyncio
async def test_raw_output_rows_keep_their_format(tmp_path):
    path = tmp_path / "r.jsonl"
    await _first_run(path, include_raw_output=True)
    assert all("raw_output" in json.loads(line) for line in _lines(path))
    await _rerun(path, "clean")
    objs = [json.loads(line) for line in _lines(path)]
    assert all("raw_output" in o for o in objs)
    assert any(o["raw_output"] == _REFUSAL.output for o in objs)


@pytest.mark.asyncio
async def test_concurrent_rerun_preserves_row_order(tmp_path):
    path = tmp_path / "r.jsonl"
    await _first_run(path)
    order = [r.prompt_id for r in _rows(path)]
    await _rerun(path, "clean", max_concurrency=8)
    assert [r.prompt_id for r in _rows(path)] == order


# ── safe write ────────────────────────────────────────────────────────────

class _ExplodingScorer(Scorer):
    async def score(self, *a, **k):
        raise RuntimeError("scorer blew up")


@pytest.mark.asyncio
async def test_an_exception_during_the_pass_leaves_the_original_untouched(tmp_path):
    path = tmp_path / "r.jsonl"
    await _first_run(path)
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns
    with pytest.raises(RuntimeError, match="scorer blew up"):
        await _rerun(path, "clean", scorer=_ExplodingScorer())
    assert path.read_bytes() == before and path.stat().st_mtime_ns == mtime
    assert [p.name for p in tmp_path.iterdir()] == ["r.jsonl"]


@pytest.mark.asyncio
async def test_a_failure_at_the_replace_step_leaves_the_original_untouched_and_cleans_up(tmp_path, monkeypatch):
    path = tmp_path / "r.jsonl"
    await _first_run(path)
    before = path.read_bytes()
    seen = {}

    def boom(src, dst):
        seen["tmp_existed"] = __import__("os").path.exists(src)
        raise OSError("disk full")

    monkeypatch.setattr(harness.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        await _rerun(path, "clean")
    assert seen["tmp_existed"]                                                          # the new content was fully written first
    assert path.read_bytes() == before and [p.name for p in tmp_path.iterdir()] == ["r.jsonl"]


@pytest.mark.asyncio
async def test_missing_file_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        await _rerun(tmp_path / "nope.jsonl", "clean")


# ── retry profiles ────────────────────────────────────────────────────────

def test_profile_values_and_overrides():
    assert RETRY_PROFILES["default"] == {"max_attempts": 3, "base_delay_s": 1.0, "max_delay_s": 60.0, "max_retry_after_s": 300.0}
    assert RETRY_PROFILES["benchmark"] == {"max_attempts": 6, "base_delay_s": 2.0, "max_delay_s": 120.0, "max_retry_after_s": 600.0}
    assert resolve_retry_settings() == RETRY_PROFILES["default"]
    assert resolve_retry_settings("benchmark", max_attempts=4, max_retry_after_s=90.0) == {
        "max_attempts": 4, "base_delay_s": 2.0, "max_delay_s": 120.0, "max_retry_after_s": 90.0}
    assert resolve_retry_settings("default", max_attempts=1)["max_attempts"] == 1
    with pytest.raises(ValueError):
        resolve_retry_settings("nope")


def _capture_run_matrix(monkeypatch):
    seen = {}

    async def fake(adapter, **kw):
        seen.update(kw)
        return
        yield                                                                              # an async generator that yields nothing

    monkeypatch.setattr(cli_module, "run_matrix", fake)
    return seen


@pytest.mark.parametrize("flags,expected", [
    ([], (3, 1.0, 60.0, 300.0)),
    (["--retry-profile", "default"], (3, 1.0, 60.0, 300.0)),
    (["--retry-profile", "benchmark"], (6, 2.0, 120.0, 600.0)),
    (["--retry-profile", "benchmark", "--max-attempts", "4"], (4, 2.0, 120.0, 600.0)),
    (["--retry-profile", "benchmark", "--retry-base-delay-s", "0.5", "--retry-max-delay-s", "10", "--retry-after-cap-s", "30"], (6, 0.5, 10.0, 30.0)),
    (["--max-attempts", "1"], (1, 1.0, 60.0, 300.0)),
])
def test_cli_retry_profile_and_overrides_reach_run_matrix(monkeypatch, tmp_path, flags, expected):
    seen = _capture_run_matrix(monkeypatch)
    result = CliRunner().invoke(main, ["run", "-a", "custom", "--module", f"{__name__}:_CleanCli", "-m", "m", "-o", str(tmp_path / "o.jsonl"), *flags])
    assert result.exit_code == 0, result.output
    assert (seen["max_attempts"], seen["base_delay_s"], seen["max_delay_s"], seen["max_retry_after_s"]) == expected


def test_cli_rejects_an_unknown_profile_and_rerun_without_resume(tmp_path):
    out = str(tmp_path / "o.jsonl")
    base = ["run", "-a", "custom", "--module", f"{__name__}:_CleanCli", "-m", "m", "-o", out]
    assert CliRunner().invoke(main, [*base, "--retry-profile", "nope"]).exit_code == 2
    r = CliRunner().invoke(main, [*base, "--rerun-missing", "--no-resume"])
    assert r.exit_code == 2 and "--no-resume" in r.output


# ── CLI end to end ────────────────────────────────────────────────────────

class _CleanCli(AgentAdapter):
    @property
    def adapter_type(self):
        return "clean-cli"

    async def _execute(self, prompt):
        return _REFUSAL


class _FlakyCli(AgentAdapter):
    @property
    def adapter_type(self):
        return "flaky-cli"

    async def _execute(self, prompt):
        if _is_flaky_prompt(prompt):
            return AgentResponse(output="", error="HTTP 429: slow down")
        return _REFUSAL


def test_cli_full_workflow_run_then_rerun_missing(tmp_path):
    out = tmp_path / "o.jsonl"
    common = ["-a", "custom", "-m", "gpt-5.5", "--categories", "ASI01", "-o", str(out)]
    first = CliRunner().invoke(main, ["run", "--module", f"{__name__}:_FlakyCli", "--max-attempts", "1", "--retry-base-delay-s", "0", *common])
    assert first.exit_code == 0, first.output
    n_missing = sum(r.is_missing for r in _rows(out))
    assert n_missing > 0
    manifest_before = json.loads(out.with_suffix(".manifest.json").read_text())
    assert manifest_before["missing_infrastructure"] == n_missing and manifest_before["rerun_passes"] is None
    scored_before = [line for line in _lines(out) if json.loads(line)["status"] == "scored"]

    second = CliRunner().invoke(main, ["run", "--module", f"{__name__}:_CleanCli", "--resume", "--rerun-missing", "--retry-profile", "benchmark", *common])
    assert second.exit_code == 0, second.output
    assert f"Rerun pass: {n_missing} row(s) re-attempted, {n_missing} recovered (now scored), 0 still missing_infrastructure" in second.output
    assert f"custom|gpt-5.5: {n_missing} re-attempted, {n_missing} recovered, 0 still missing" in second.output
    assert "Scored rows were not re-executed" in second.output and "0 missing_infrastructure" in second.output
    rows = _rows(out)
    assert not any(r.is_missing for r in rows) and sum(r.rerun_passes == 1 for r in rows) == n_missing
    assert [line for line in _lines(out) if json.loads(line)["status"] == "scored" and json.loads(line)["rerun_passes"] == 0] == scored_before
    manifest = RunManifest(**json.loads(out.with_suffix(".manifest.json").read_text()))
    assert manifest.missing_infrastructure == 0 and manifest.scored_trials == len(rows) and manifest.rerun_passes == 1
    assert manifest.started_at == manifest_before["started_at"] and manifest.retry_profile == "benchmark"

    third = CliRunner().invoke(main, ["run", "--module", f"{__name__}:_CleanCli", "--resume", "--rerun-missing", *common])
    assert third.exit_code == 0 and "0 row(s) re-attempted" in third.output and "left untouched" in third.output


def test_cli_rerun_missing_still_failing_is_reported_by_cell(tmp_path):
    out = tmp_path / "o.jsonl"
    common = ["-a", "custom", "-m", "gpt-5.5", "--categories", "ASI01", "-o", str(out), "--max-attempts", "1", "--retry-base-delay-s", "0"]
    CliRunner().invoke(main, ["run", "--module", f"{__name__}:_FlakyCli", *common])
    n_missing = sum(r.is_missing for r in _rows(out))
    r = CliRunner().invoke(main, ["run", "--module", f"{__name__}:_FlakyCli", "--resume", "--rerun-missing", *common])
    assert r.exit_code == 0, r.output
    assert f"{n_missing} row(s) re-attempted, 0 recovered (now scored), {n_missing} still missing_infrastructure" in r.output
    assert f"custom|gpt-5.5: {n_missing} re-attempted, 0 recovered, {n_missing} still missing" in r.output
    assert all(x.attempts == 2 and x.rerun_passes == 1 for x in _rows(out) if x.is_missing)


def test_cli_rerun_missing_needs_an_existing_file(tmp_path):
    r = CliRunner().invoke(main, ["run", "-a", "custom", "--module", f"{__name__}:_CleanCli", "-m", "m", "--resume", "--rerun-missing",
                                  "-o", str(tmp_path / "missing.jsonl")])
    assert r.exit_code == 1 and "does not exist" in r.output


def test_cli_rerun_missing_does_not_run_new_cells(tmp_path):
    out = tmp_path / "o.jsonl"
    CliRunner().invoke(main, ["run", "-a", "custom", "--module", f"{__name__}:_CleanCli", "-m", "m", "--categories", "ASI09", "-o", str(out)])
    before = out.read_bytes()
    r = CliRunner().invoke(main, ["run", "-a", "custom", "--module", f"{__name__}:_CleanCli", "-m", "m", "--categories", "ASI01,ASI09",
                                  "--resume", "--rerun-missing", "-o", str(out)])
    assert r.exit_code == 0 and out.read_bytes() == before                              # ASI01 was never run, and is not run now


# ── schema ────────────────────────────────────────────────────────────────

def test_rerun_passes_defaults_to_zero_and_rejects_negative():
    row = dict(model="m", framework="f", attack_family="injection_output", category="ASI01", prompt_id="ASI01-001", trial_seed=0,
               verdict="pass", confidence=0.9, payload_hash=compute_payload_hash(prompt_id="ASI01-001", model="m", framework="f", trial_seed=0, raw_output=""),
               timestamp="2026-09-10T12:00:00Z", harness_version="0.1.0", library_version=_LIB.version)
    assert BenchTrialResult(**row).rerun_passes == 0
    assert BenchTrialResult(**dict(row, rerun_passes=2)).rerun_passes == 2
    with pytest.raises(ValueError):
        BenchTrialResult(**dict(row, rerun_passes=-1))
