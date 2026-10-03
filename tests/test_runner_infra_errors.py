"""
tests/test_runner_infra_errors.py

safelabs.runner.run_eval(): infrastructure errors are classified with safelabs.agents.errors,
retried with the shared retry profiles, and recorded as missing_infrastructure (no scoring result,
excluded from every aggregate and from the report) when they persist; content-policy,
no-output-text and other failures keep today's scoring; tool-call-only responses are flagged.
No network; the sleep function is injected.
"""

from __future__ import annotations

import json
import zlib
from datetime import datetime

import httpx
import pytest

import agentport_bench.harness as harness
import safelabs.agents.errors as errors_module
import safelabs.agents.retry as retry_module
import safelabs.runner as runner_module
from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse, ToolCall
from safelabs.runner import EvalRecord, EvalResult, run_eval
from safelabs.scoring.models import VerdictLevel
from safelabs.scoring.scorer import Scorer

_REFUSAL = "I cannot and will not comply with that request. That looks like an injection attempt."
_REQ = httpx.Request("POST", "http://localhost:9/v1")


def _openai(name, status, retry_after=None):
    openai = pytest.importorskip("openai")
    headers = {"retry-after": retry_after} if retry_after else {}
    return getattr(openai, name)("provider said no", response=httpx.Response(status, request=_REQ, headers=headers), body=None)


async def _run(agent, **kw):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    kw.setdefault("categories", ["ASI01"])
    kw.setdefault("jitter_fn", lambda: 1.0)
    result = await run_eval(agent, sleep=fake_sleep, **kw)
    return result, sleeps


def _fails_once(make_error):
    """An async agent that raises (or returns) the error on the first call for each prompt, then a clean refusal.
    (A plain async function: run_eval treats a callable object as a sync function.)"""
    seen: set[str] = set()

    async def agent(prompt):
        agent.calls += 1
        if prompt not in seen:
            seen.add(prompt)
            err = make_error()
            if isinstance(err, BaseException):
                raise err
            return err
        return _REFUSAL

    agent.calls = 0
    return agent


def _always(make_error):
    async def agent(prompt):
        agent.calls += 1
        err = make_error()
        if isinstance(err, BaseException):
            raise err
        return err

    agent.calls = 0
    return agent


# ── retried, then succeeding ──────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("make,subclass", [
    (lambda: _openai("RateLimitError", 429), "rate_limit_or_quota"),
    (lambda: httpx.ReadTimeout("slow"), "timeout"),
    (lambda: httpx.ConnectError("refused"), "connection_error"),
    (lambda: _openai("InternalServerError", 503), "provider_unavailable"),
    (lambda: TimeoutError(), "timeout"),
    (lambda: AgentResponse(output="", error="HTTP 429: slow down"), "rate_limit_or_quota"),
    (lambda: AgentResponse(output="", error="503 UNAVAILABLE. The model is overloaded."), "provider_unavailable"),
], ids=["rate-limit-exc", "read-timeout", "connect-error", "5xx-exc", "TimeoutError", "http-429-response", "unavailable-response"])
async def test_infrastructure_error_is_retried_then_scored(make, subclass):
    agent = _fails_once(make)
    result, sleeps = await _run(agent)
    assert result.total == 30 and not result.missing and len(sleeps) == 30 and agent.calls == 60
    assert all(r.status == "scored" and r.attempts == 2 and r.attempt_errors == [subclass] for r in result.records)
    assert all(r.error is None and r.error_class is None and r.verdict == VerdictLevel.PASS for r in result.records)
    assert result.retries == 30 and result.counts == {"pass": 30}


# ── exhaustion: missing, excluded everywhere ──────────────────────────────

class _SpyScorer(Scorer):
    def __init__(self):
        super().__init__()
        self.calls = 0

    async def score(self, *a, **k):
        self.calls += 1
        return await super().score(*a, **k)


@pytest.mark.asyncio
async def test_exhausted_retries_become_missing_and_are_excluded_from_every_aggregate(capsys):
    agent, spy = _always(lambda: _openai("RateLimitError", 429)), _SpyScorer()
    result, sleeps = await _run(agent, scorer=spy)
    assert agent.calls == 90 and len(sleeps) == 60 and spy.calls == 0               # 3 attempts each; nothing scored
    assert len(result.records) == 30 and len(result.missing) == 30
    assert result.total == 0 and sum(result.counts.values()) == 0
    assert result.passed == [] and result.failed == [] and result.vulnerable == [] and result.scored_records == []
    assert result.missing_by_category == {"ASI01": 30} and result.retries == 60 and len(result.errors) == 30
    rec = result.records[0]
    assert rec.status == "missing_infrastructure" and rec.is_missing and rec.scoring_result is None and rec.verdict is None
    assert (rec.error_class, rec.error_subclass, rec.attempts, rec.response) == ("infrastructure", "rate_limit_or_quota", 3, "")
    assert rec.attempt_errors == ["rate_limit_or_quota"] * 3 and "provider said no" in rec.error
    result.summary()
    out = capsys.readouterr().out
    assert "MISSING" in out and "missing_infrastructure" in out and "excluded from every aggregate" in out
    assert "by category: ASI01=30" in out and "RETRIES    : 60" in out
    assert "No prompts were scored" in out and "No vulnerabilities detected" not in out


@pytest.mark.asyncio
async def test_one_mostly_rate_limited_run_reports_missing_and_does_not_inflate_uncertain(capsys):
    async def _mostly(prompt):
        if zlib.crc32(prompt.encode()) % 10 != 0:
            raise _openai("RateLimitError", 429)
        return _REFUSAL

    result, _ = await _run(_mostly)
    n_missing, n_ok = len(result.missing), result.total
    assert n_missing + n_ok == 30 and 0 < n_ok < 10
    assert result.counts == {"pass": n_ok}                                          # no UNCERTAIN from the missing trials
    result.summary()
    out = capsys.readouterr().out
    assert "Prompts run: 30" in out and f"({n_ok} prompts evaluated)" in out and f"MISSING    : {n_missing}" in out
    assert f"among the {n_ok} scored prompts" in out and "excluded" in out


@pytest.mark.asyncio
async def test_a_later_non_infrastructure_failure_is_scored_not_missing():
    calls = {"n": 0}

    async def agent(prompt):
        calls["n"] += 1
        if calls["n"] % 2 == 1:
            raise _openai("RateLimitError", 429)
        raise RuntimeError("request flagged for possible cybersecurity risk")

    result, _ = await _run(agent, categories=["ASI09"])
    assert not result.missing and all(r.status == "scored" and r.error_class == "content_policy" and r.attempts == 2 for r in result.records)
    assert all(r.attempt_errors == ["rate_limit_or_quota", "content_policy"] for r in result.records)


# ── content-policy, no-output-text, other: as today ───────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("make,cls", [
    (lambda: RuntimeError("Invalid prompt: flagged for possible cybersecurity risk"), "content_policy"),
    (lambda: AgentResponse(output="", error="This request was flagged for possible cybersecurity risk"), "content_policy"),
    (lambda: RuntimeError("provider returned no output text"), "no_output_text"),
    (lambda: RuntimeError("Simulated agent failure"), "other"),
])
async def test_non_infrastructure_failures_are_not_retried_and_score_as_today(make, cls):
    agent = _always(make)
    result, sleeps = await _run(agent)
    assert agent.calls == 30 and sleeps == [] and result.total == 30 and not result.missing
    assert all(r.status == "scored" and r.error_class == cls and r.attempts == 1 and r.error for r in result.records)
    assert all(r.verdict == VerdictLevel.UNCERTAIN for r in result.records)          # empty text scores UNCERTAIN, as before
    assert result.counts == {"uncertain": 30}


@pytest.mark.asyncio
async def test_an_empty_string_return_is_unchanged():
    async def agent(prompt):
        return ""

    result, sleeps = await _run(agent)
    assert sleeps == [] and result.total == 30
    assert all(r.error is None and r.error_class is None and r.status == "scored" and r.verdict == VerdictLevel.UNCERTAIN for r in result.records)
    assert all(r.tool_call_only is False for r in result.records)


# ── tool-call-only, AgentResponse returns ─────────────────────────────────

@pytest.mark.asyncio
async def test_tool_call_only_response_is_flagged_and_not_an_error():
    async def agent(prompt):
        return AgentResponse(output="", tool_calls=[ToolCall.from_arguments("lookup", {"q": "x"})], provenance={"tool_calls": "inferred"})

    result, sleeps = await _run(agent)
    assert sleeps == [] and len(result.tool_call_only) == 30
    assert all(r.tool_call_only is True and r.error is None and r.error_class is None and r.status == "scored" for r in result.records)
    assert all(r.verdict == VerdictLevel.UNCERTAIN for r in result.records)          # scoring unchanged
    plain, _ = await _run(lambda p: _REFUSAL)
    assert plain.tool_call_only == [] and all(r.tool_call_only is False for r in plain.records)


@pytest.mark.asyncio
async def test_an_agent_response_is_scored_on_its_output_text():
    async def agent(prompt):
        return AgentResponse(output=_REFUSAL)

    result, _ = await _run(agent)
    assert result.counts == {"pass": 30} and all(r.response == _REFUSAL for r in result.records)


@pytest.mark.asyncio
async def test_run_eval_with_an_adapter_execute_sees_infrastructure_errors():
    class _Down(AgentAdapter):
        @property
        def adapter_type(self):
            return "down"

        async def _execute(self, prompt):
            raise _openai("RateLimitError", 429, "7")

    result, sleeps = await _run(_Down(timeout=5).execute, categories=["ASI09"])
    assert len(result.missing) == len(result.records) > 0 and result.total == 0
    assert set(sleeps) == {7.0}                                                       # Retry-After from the exception, via metadata
    assert all(r.error_subclass == "rate_limit_or_quota" and r.attempts == 3 for r in result.records)


# ── profiles and overrides ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_default_and_benchmark_profiles_and_overrides():
    def agent_for():
        return _always(lambda: httpx.ConnectError("refused"))

    a = agent_for()
    _, sleeps = await _run(a, categories=["ASI09"])
    n = a.calls // 3
    assert a.calls == 3 * n and len(sleeps) == 2 * n and sleeps[:2] == [1.0, 2.0]                 # default: 3 attempts, base 1 s
    a = agent_for()
    _, sleeps = await _run(a, categories=["ASI09"], retry_profile="benchmark")
    assert a.calls == 6 * n and sleeps[:5] == [2.0, 4.0, 8.0, 16.0, 32.0]                         # benchmark: 6 attempts, base 2 s
    a = agent_for()
    _, sleeps = await _run(a, categories=["ASI09"], retry_profile="benchmark", max_attempts=2, base_delay_s=0.5)
    assert a.calls == 2 * n and sleeps[:1] == [0.5]                                                # explicit flags override the profile
    a = agent_for()
    _, sleeps = await _run(a, categories=["ASI09"], retry_profile="benchmark", max_delay_s=5.0)
    assert sleeps[:5] == [2.0, 4.0, 5.0, 5.0, 5.0]
    a = agent_for()
    _, _ = await _run(a, categories=["ASI09"], max_attempts=1)
    assert a.calls == n                                                                           # 1 disables retries


@pytest.mark.asyncio
async def test_retry_after_is_honoured_and_capped():
    _, sleeps = await _run(_always(lambda: _openai("RateLimitError", 429, "11")), categories=["ASI09"])
    assert set(sleeps) == {11.0}
    _, sleeps = await _run(_always(lambda: _openai("RateLimitError", 429, "900")), categories=["ASI09"], max_retry_after_s=60.0)
    assert set(sleeps) == {60.0}
    _, sleeps = await _run(_always(lambda: _openai("RateLimitError", 429, "900")), categories=["ASI09"], retry_profile="benchmark")
    assert set(sleeps) == {600.0}                                                                 # the benchmark profile's cap


@pytest.mark.asyncio
async def test_unknown_profile_is_an_error():
    with pytest.raises(ValueError):
        await run_eval(lambda p: _REFUSAL, categories=["ASI09"], retry_profile="nope")


def test_the_runner_and_the_harness_share_one_classifier_and_one_set_of_profiles():
    assert runner_module.classify_error is errors_module.classify_error
    assert runner_module.resolve_retry_settings is retry_module.resolve_retry_settings
    assert harness.RETRY_PROFILES is retry_module.RETRY_PROFILES and harness.resolve_retry_settings is retry_module.resolve_retry_settings
    assert retry_module.RETRY_PROFILES["benchmark"] == {"max_attempts": 6, "base_delay_s": 2.0, "max_delay_s": 120.0, "max_retry_after_s": 600.0}


# ── old results and validation ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_results_saved_before_these_fields_load_unchanged(capsys):
    result, _ = await _run(lambda p: _REFUSAL, categories=["ASI09"])
    old = json.loads(result.model_dump_json())
    for rec in old["records"]:
        for key in ("status", "error_class", "error_subclass", "attempts", "attempt_errors", "tool_call_only"):
            rec.pop(key)
    loaded = EvalResult.model_validate_json(json.dumps(old))
    assert loaded.total == len(old["records"]) and loaded.counts == {"pass": len(old["records"])}
    assert all(r.status is None and r.attempts is None and not r.is_missing and r.verdict == VerdictLevel.PASS for r in loaded.records)
    assert loaded.missing == [] and loaded.retries == 0
    loaded.summary()
    out = capsys.readouterr().out
    assert "MISSING" not in out and "RETRIES" not in out and "No vulnerabilities detected" in out


@pytest.mark.asyncio
async def test_record_validation_ties_status_to_scoring_result():
    result, _ = await _run(lambda p: _REFUSAL, categories=["ASI09"])
    good = json.loads(result.records[0].model_dump_json())
    with pytest.raises(ValueError):
        EvalRecord(**dict(good, status="missing_infrastructure"))                    # a missing record must not have a scoring result
    with pytest.raises(ValueError):
        EvalRecord(**dict(good, status="scored", scoring_result=None))
    with pytest.raises(ValueError):
        EvalRecord(**{k: v for k, v in good.items() if k not in ("scoring_result", "status")})


@pytest.mark.asyncio
async def test_a_missing_record_round_trips():
    result, _ = await _run(_always(lambda: httpx.ConnectError("refused")), categories=["ASI09"])
    back = EvalResult.model_validate_json(result.model_dump_json())
    assert back.total == 0 and len(back.missing) == len(result.records) and back.records[0].verdict is None
