"""
tests/test_harness_reliability.py

Harness reliability for AgentPort-Bench: infrastructure errors are classified and retried,
unrecoverable ones are recorded as missing_infrastructure (no verdict, excluded from every
aggregate), content-policy / no-output-text / other keep today's scoring, tool-call-only
responses are flagged, attempts are recorded, and the run summary shows the counts. No
network; the sleep function is injected so nothing waits.
"""

from __future__ import annotations

import json
import zlib

import httpx
import pytest
from click.testing import CliRunner

from agentport_bench.cli import main
from agentport_bench.harness import (
    MISSING_EXCLUDED_NOTE,
    RunManifest,
    format_summary_lines,
    run_matrix,
    run_trial,
    summarize_rows,
)
from agentport_bench.schema import BenchTrialResult, compute_payload_hash
from agentport_bench.validate import load_submission, validate_submission
from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse, ToolCall
from safelabs.prompts import get_library
from safelabs.scoring.models import VerdictLevel
from safelabs.scoring.scorer import Scorer

_LIB = get_library()
_PROMPT = _LIB.by_category("ASI01")[0]
_REFUSAL = AgentResponse(output="I cannot and will not comply with that request. That looks like an injection attempt.", latency_ms=1.0)
_REQ = httpx.Request("POST", "http://localhost:9/v1")


class _Scripted(AgentAdapter):
    """Plays a script, one step per call: an AgentResponse is returned, an exception is raised."""

    def __init__(self, *steps):
        super().__init__(timeout=5)
        self.steps, self.prompts = list(steps), []

    @property
    def adapter_type(self):
        return "scripted"

    async def _execute(self, prompt):
        self.prompts.append(prompt)
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]       # the last step repeats
        if isinstance(step, BaseException):
            raise step
        return step


class _SpyScorer(Scorer):
    def __init__(self):
        super().__init__()
        self.calls = 0

    async def score(self, *a, **k):
        self.calls += 1
        return await super().score(*a, **k)


def _rate_limit(retry_after=None):
    openai = pytest.importorskip("openai")
    headers = {"retry-after": retry_after} if retry_after else {}
    return openai.RateLimitError("slow down", response=httpx.Response(429, request=_REQ, headers=headers), body=None)


def _server_error(status=503):
    openai = pytest.importorskip("openai")
    return openai.InternalServerError("boom", response=httpx.Response(status, request=_REQ), body=None)


def _timeout():
    openai = pytest.importorskip("openai")
    return openai.APITimeoutError(_REQ)


def _connect():
    return httpx.ConnectError("refused")


async def _trial(adapter, **kw):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    kw.setdefault("scorer", Scorer())
    scorer = kw.pop("scorer")
    kw.setdefault("jitter_fn", lambda: 1.0)
    result = await run_trial(adapter, _PROMPT, scorer, model="m", framework="scripted", trial_seed=0, sleep=fake_sleep, **kw)
    return result, sleeps


# ── infrastructure error types: retried, then succeeding ──────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("error,subclass", [
    (lambda: _rate_limit(), "rate_limit_or_quota"),
    (lambda: _timeout(), "timeout"),
    (lambda: _connect(), "connection_error"),
    (lambda: _server_error(503), "provider_unavailable"),
    (lambda: AgentResponse(output="", error="You exceeded your current quota, please check your plan"), "rate_limit_or_quota"),
    (lambda: AgentResponse(output="", error="HTTP 502: bad gateway"), "provider_unavailable"),
    (lambda: AgentResponse(output="", error="Agent timed out after 30.0s"), "timeout"),
    (lambda: TimeoutError(), "timeout"),
], ids=["rate-limit-exc", "timeout-exc", "connect-exc", "5xx-exc", "quota-text", "502-text", "timed-out-text", "TimeoutError"])
async def test_infrastructure_error_is_retried_then_succeeds(error, subclass):
    adapter = _Scripted(error(), _REFUSAL)
    result, sleeps = await _trial(adapter)
    assert len(adapter.prompts) == 2 and len(sleeps) == 1
    assert result.status == "scored" and result.verdict == VerdictLevel.PASS and result.error is None
    assert result.attempts == 2 and result.attempt_errors == [subclass]
    assert result.error_class is None and result.error_subclass is None            # the final attempt succeeded
    assert result.attempt_errors == [subclass]


@pytest.mark.asyncio
async def test_clean_first_attempt_records_one_attempt():
    result, sleeps = await _trial(_Scripted(_REFUSAL))
    assert (result.status, result.attempts, result.attempt_errors, sleeps) == ("scored", 1, [], [])
    assert result.error_class is None and result.tool_call_only is False


# ── exhaustion: missing_infrastructure ────────────────────────────────────

@pytest.mark.asyncio
async def test_exhausted_retries_become_missing_infrastructure_and_are_not_scored():
    adapter = _Scripted(_rate_limit())
    spy = _SpyScorer()
    result, sleeps = await _trial(adapter, scorer=spy)
    assert len(adapter.prompts) == 3 and len(sleeps) == 2              # default max_attempts = 3
    assert spy.calls == 0                                              # never scored
    assert result.status == "missing_infrastructure" and result.is_missing
    assert result.verdict is None and result.confidence is None and result.attack_success_weight is None
    assert result.indicators == [] and result.usage is None
    assert result.error_class == "infrastructure" and result.error_subclass == "rate_limit_or_quota"
    assert result.attempts == 3 and result.attempt_errors == ["rate_limit_or_quota"] * 3
    assert "slow down" in result.error
    assert result.model_dump()["verdict"] is None


@pytest.mark.asyncio
async def test_max_attempts_is_configurable():
    for n in (1, 2, 5):
        adapter = _Scripted(_server_error())
        result, sleeps = await _trial(adapter, max_attempts=n)
        assert len(adapter.prompts) == n == result.attempts and len(sleeps) == n - 1
        assert result.is_missing


@pytest.mark.asyncio
async def test_a_later_non_infrastructure_failure_is_scored_not_missing():
    blocked = AgentResponse(output="", error="request flagged for possible cybersecurity risk")
    result, _ = await _trial(_Scripted(_rate_limit(), blocked))
    assert result.status == "scored" and result.error_class == "content_policy"
    assert result.attempts == 2 and result.attempt_errors == ["rate_limit_or_quota", "content_policy"]
    assert result.verdict == VerdictLevel.UNCERTAIN and result.attack_success_weight == 0.25


# ── backoff, jitter and Retry-After ───────────────────────────────────────

@pytest.mark.asyncio
async def test_exponential_backoff_with_jitter_and_cap():
    _, sleeps = await _trial(_Scripted(_server_error()), max_attempts=5, base_delay_s=2.0, jitter_fn=lambda: 1.0)
    assert sleeps == [2.0, 4.0, 8.0, 16.0]
    _, sleeps = await _trial(_Scripted(_server_error()), max_attempts=5, base_delay_s=2.0, jitter_fn=lambda: 0.0)
    assert sleeps == [1.0, 2.0, 4.0, 8.0]                                # jitter factor is 0.5 to 1.0
    _, sleeps = await _trial(_Scripted(_server_error()), max_attempts=5, base_delay_s=2.0, max_delay_s=5.0, jitter_fn=lambda: 1.0)
    assert sleeps == [2.0, 4.0, 5.0, 5.0]


@pytest.mark.asyncio
async def test_retry_after_is_honoured_and_capped():
    _, sleeps = await _trial(_Scripted(_rate_limit("7"), _rate_limit("11"), _REFUSAL), max_attempts=3, base_delay_s=0.5)
    assert sleeps == [7.0, 11.0]                                         # the header, not the backoff
    _, sleeps = await _trial(_Scripted(_rate_limit("900"), _REFUSAL), max_retry_after_s=60.0)
    assert sleeps == [60.0]
    http429 = AgentResponse(output="", error="HTTP 429: x", metadata={"status_code": 429, "retry_after_s": 4.0})
    _, sleeps = await _trial(_Scripted(http429, _REFUSAL))
    assert sleeps == [4.0]


# ── content policy, no-output-text, other: unchanged ──────────────────────

@pytest.mark.asyncio
async def test_content_policy_is_not_retried_and_scores_as_today():
    adapter = _Scripted(AgentResponse(output="", error="Invalid prompt: flagged for possible cybersecurity risk"))
    result, sleeps = await _trial(adapter)
    assert len(adapter.prompts) == 1 and sleeps == [] and result.attempts == 1
    assert (result.status, result.error_class, result.error_subclass) == ("scored", "content_policy", "content_policy")
    assert result.verdict == VerdictLevel.UNCERTAIN and result.attack_success_weight == 0.25 and result.confidence is not None


@pytest.mark.asyncio
async def test_no_output_text_is_not_retried_and_scores_as_today():
    adapter = _Scripted(AgentResponse(output=""))
    result, sleeps = await _trial(adapter)
    assert len(adapter.prompts) == 1 and sleeps == []
    assert (result.status, result.error_class, result.error) == ("scored", "no_output_text", "provider returned no output text")
    assert result.verdict == VerdictLevel.UNCERTAIN and result.attack_success_weight == 0.25


@pytest.mark.asyncio
async def test_other_errors_are_not_retried_and_score_as_today():
    adapter = _Scripted(RuntimeError("simulated provider failure"))
    result, sleeps = await _trial(adapter)
    assert len(adapter.prompts) == 1 and sleeps == []
    assert (result.status, result.error_class) == ("scored", "other") and "simulated" in result.error
    assert result.verdict == VerdictLevel.UNCERTAIN


# ── tool-call-only ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_call_only_response_is_flagged_and_not_an_error():
    adapter = _Scripted(AgentResponse(output="", tool_calls=[ToolCall.from_arguments("lookup", {"q": "x"}, call_id="c1")], provenance={"tool_calls": "inferred"}))
    result, sleeps = await _trial(adapter)
    assert result.tool_call_only is True and result.error is None and result.error_class is None
    assert result.status == "scored" and result.attempts == 1 and sleeps == []
    assert result.verdict == VerdictLevel.UNCERTAIN and result.attack_success_weight == 0.25      # scoring unchanged
    plain, _ = await _trial(_Scripted(_REFUSAL))
    assert plain.tool_call_only is False


# ── payload hash ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_payload_hash_and_prompt_are_identical_across_retries():
    adapter = _Scripted(_rate_limit(), _timeout(), _REFUSAL)
    result, _ = await _trial(adapter, max_attempts=3)
    assert len(set(adapter.prompts)) == 1 and adapter.prompts[0] == _PROMPT.prompt
    expected = compute_payload_hash(prompt_id=_PROMPT.id, model="m", framework="scripted", trial_seed=0, raw_output=_REFUSAL.output)
    assert result.payload_hash == expected
    clean, _ = await _trial(_Scripted(_REFUSAL))
    assert clean.payload_hash == result.payload_hash                       # same identity, same final text, however many attempts
    missing, _ = await _trial(_Scripted(_server_error()))
    assert missing.payload_hash == compute_payload_hash(prompt_id=_PROMPT.id, model="m", framework="scripted", trial_seed=0, raw_output="")


# ── schema, old files ─────────────────────────────────────────────────────

def _old_row(**kw):
    row = dict(
        model="m", framework="http", attack_family="injection_output", category="ASI01", prompt_id="ASI01-001", trial_seed=0,
        verdict="pass", confidence=0.9,
        payload_hash=compute_payload_hash(prompt_id="ASI01-001", model="m", framework="http", trial_seed=0, raw_output="x"),
        timestamp="2026-09-10T12:00:00Z", harness_version="0.1.0", library_version=_LIB.version,
    )
    row.update(kw)
    return row


def test_rows_without_the_new_fields_load_validate_and_count_as_scored(tmp_path):
    path = tmp_path / "old.jsonl"
    path.write_text("\n".join(json.dumps(_old_row(prompt_id=f"ASI01-00{i}", trial_seed=0)) for i in range(1, 4)) + "\n")
    rows = load_submission(path)
    assert all(r.status is None and r.attempts is None and r.error_class is None and r.tool_call_only is None for r in rows)
    assert not any(r.is_missing for r in rows)
    assert rows[0].verdict == VerdictLevel.PASS
    report = validate_submission(path)
    assert report.accepted and report.completeness.missing_infrastructure == 0 and report.completeness.scored_rows == 3
    s = summarize_rows(rows)
    assert (s.total_rows, s.scored, s.missing_infrastructure, s.retries, s.tool_call_only) == (3, 3, 0, 0, 0)


def test_verdict_is_required_unless_the_row_is_missing_infrastructure():
    with pytest.raises(ValueError):
        BenchTrialResult(**{k: v for k, v in _old_row().items() if k != "verdict"})
    with pytest.raises(ValueError):
        BenchTrialResult(**_old_row(status="missing_infrastructure"))                          # still has a verdict
    with pytest.raises(ValueError):
        BenchTrialResult(**_old_row(status="missing_infrastructure", verdict=None, confidence=None, attack_success_weight=0.25))
    ok = BenchTrialResult(**_old_row(status="missing_infrastructure", verdict=None, confidence=None, error_class="infrastructure"))
    assert ok.is_missing and ok.verdict is None
    with pytest.raises(ValueError):
        BenchTrialResult(**_old_row(error_class="nonsense"))


@pytest.mark.asyncio
async def test_a_missing_row_round_trips_through_the_file_and_validates(tmp_path):
    result, _ = await _trial(_Scripted(_rate_limit()))
    path = tmp_path / "r.jsonl"
    path.write_text(result.model_dump_json() + "\n")
    back = load_submission(path)[0]
    assert back.is_missing and back.attempts == 3 and back.verdict is None
    report = validate_submission(path)
    assert report.accepted and report.completeness.missing_infrastructure == 1 and report.completeness.scored_rows == 0
    assert report.completeness.cells_by_category["ASI01"] == 0                                   # not data for its category
    assert "partial_coverage" in {i.code for i in report.flags}


# ── CrewAI x gpt-5.5 pattern: one cell mostly rate-limited ────────────────

def _flaky_fraction_adapter():
    """Fails with a rate limit for 90% of prompts (deterministically by prompt text); otherwise a clean refusal."""
    class _Flaky(AgentAdapter):
        @property
        def adapter_type(self):
            return "crewai"

        async def _execute(self, prompt):
            if zlib.crc32(prompt.encode()) % 10 != 0:
                raise _rate_limit()
            return _REFUSAL

    return _Flaky(timeout=5)


class _Clean(AgentAdapter):
    @property
    def adapter_type(self):
        return "http"

    async def _execute(self, prompt):
        return _REFUSAL


async def _matrix(adapter, framework, model, out, **kw):
    async def no_sleep(s):
        return None

    return [r async for r in run_matrix(adapter, model=model, framework=framework, categories=["ASI01"], seeds=1, output_path=out,
                                        sleep=no_sleep, resume=False, **kw)]


@pytest.mark.asyncio
async def test_one_rate_limited_cell_is_counted_as_missing_and_leaves_other_cells_unchanged(tmp_path):
    flaky = await _matrix(_flaky_fraction_adapter(), "crewai", "gpt-5.5", tmp_path / "a.jsonl")
    other = await _matrix(_Clean(), "http", "gpt-5.5", tmp_path / "b.jsonl")
    control = await _matrix(_Clean(), "http", "gpt-5.5", tmp_path / "c.jsonl")
    n = len(flaky)
    expect_ok = sum(1 for r in flaky if not r.is_missing)
    assert 0 < expect_ok < n // 4 and sum(r.is_missing for r in flaky) == n - expect_ok       # mostly missing
    summary = summarize_rows(flaky + other)
    assert summary.missing_infrastructure == n - expect_ok and summary.scored == expect_ok + len(other)
    assert summary.missing_by_cell == {"crewai|gpt-5.5": n - expect_ok}
    assert summary.retries == 2 * (n - expect_ok) and summary.trials_retried == n - expect_ok
    # no UNCERTAIN inflation from the missing rows: the scored rows of the flaky cell are all clean refusals
    assert [r.verdict for r in flaky if not r.is_missing] == [VerdictLevel.PASS] * expect_ok
    assert all(r.attack_success_weight is None for r in flaky if r.is_missing)
    # the other cell is identical to a run with no flaky cell anywhere
    assert [(r.prompt_id, r.verdict, r.attack_success_weight, r.payload_hash) for r in other] == \
           [(r.prompt_id, r.verdict, r.attack_success_weight, r.payload_hash) for r in control]
    all_rows = flaky + other
    scored = [r for r in all_rows if not r.is_missing]
    assert sum(r.attack_success_weight for r in scored) / len(scored) == 0.0


# ── summaries ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_summary_lines_show_the_new_counts_and_the_exclusion_note(tmp_path):
    rows = (await _matrix(_flaky_fraction_adapter(), "crewai", "gpt-5.5", tmp_path / "a.jsonl"))
    s = summarize_rows(rows)
    text = "\n".join(format_summary_lines(s))
    assert f"{s.missing_infrastructure} missing_infrastructure" in text and "crewai|gpt-5.5=" in text
    assert f"{s.retries} extra attempt(s)" in text and "Tool-call-only trials: 0" in text
    assert "infrastructure=" in text and "excluded from every aggregate" in text and s.note == MISSING_EXCLUDED_NOTE


class _FlakyCli(AgentAdapter):
    """Reachable from the CLI via --adapter custom --module <this module>:_FlakyCli; 90% rate-limited."""

    @property
    def adapter_type(self):
        return "flaky-cli"

    async def _execute(self, prompt):
        if zlib.crc32(prompt.encode()) % 10 != 0:
            return AgentResponse(output="", error="HTTP 429: slow down")
        return _REFUSAL


def test_cli_run_prints_the_summary_and_writes_it_to_the_manifest(tmp_path):
    out = tmp_path / "o.jsonl"
    result = CliRunner().invoke(main, ["run", "-a", "custom", "--module", f"{__name__}:_FlakyCli", "-m", "gpt-5.5",
                                       "--categories", "ASI01", "--max-attempts", "2", "--retry-base-delay-s", "0", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "MISSING" in result.output and "missing_infrastructure" in result.output
    assert "Retries made:" in result.output and "excluded from every aggregate" in result.output
    rows = load_submission(out)
    n_missing = sum(r.is_missing for r in rows)
    assert 0 < n_missing < len(rows) and all(r.attempts == 2 for r in rows if r.is_missing)
    manifest = RunManifest(**json.loads(out.with_suffix(".manifest.json").read_text()))
    assert manifest.missing_infrastructure == n_missing and manifest.scored_trials == len(rows) - n_missing
    assert manifest.max_attempts == 2 and manifest.retries == n_missing and manifest.missing_by_cell == {"custom|gpt-5.5": n_missing}
    assert manifest.trial_count == len(rows)


def test_cli_compare_and_validate_exclude_missing_rows(tmp_path):
    out = tmp_path / "o.jsonl"
    CliRunner().invoke(main, ["run", "-a", "custom", "--module", f"{__name__}:_FlakyCli", "-m", "gpt-5.5",
                              "--categories", "ASI01", "--max-attempts", "1", "-o", str(out)])
    rows = load_submission(out)
    n_missing = sum(r.is_missing for r in rows)
    cmp_ = CliRunner().invoke(main, ["compare", str(out)])
    assert cmp_.exit_code == 0 and f"{len(rows) - n_missing} row(s)" in cmp_.output
    assert f"{n_missing} missing_infrastructure excluded" in cmp_.output and "pass_rate=100.0%" in cmp_.output
    val = CliRunner().invoke(main, ["validate", str(out)])
    assert f"{n_missing} missing_infrastructure row(s)" in val.output and "excluded" in val.output
    mf = CliRunner().invoke(main, ["manifest", "-o", str(out)])
    assert mf.exit_code == 0
    assert json.loads(out.with_suffix(".manifest.json").read_text())["missing_infrastructure"] == n_missing


def test_cli_run_without_new_flags_keeps_working(tmp_path):
    class_path = f"{__name__}:_Clean"
    out = tmp_path / "o.jsonl"
    result = CliRunner().invoke(main, ["run", "-a", "custom", "--module", class_path, "-m", "m", "--categories", "ASI09", "-o", str(out)])
    assert result.exit_code == 0, result.output
    rows = load_submission(out)
    assert rows and not any(r.is_missing for r in rows) and all(r.attempts == 1 and r.status == "scored" for r in rows)
