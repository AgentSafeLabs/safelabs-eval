"""
tests/test_degraded_responses.py

Degraded-response convention (#73): a target can mark a fallback answer with the
``X-SafeLabs-Degraded`` header, a top-level ``"safelabs_degraded": true`` in a JSON body, or
``AgentResponse.degraded``. run_eval retries it like an infrastructure error and, if it stays
degraded, records it as missing_infrastructure (error_subclass "degraded"). It is never scored.
The httpx client is replaced by a scripted fake; no network, and the sleep function is injected.
"""

from __future__ import annotations

import httpx
import pytest

from safelabs import cli
from safelabs.agents import http_adapter
from safelabs.agents.base import AgentAdapter
from safelabs.agents.http_adapter import HttpAdapter
from safelabs.agents.schemas import AgentResponse
from safelabs.runner import run_eval
from safelabs.scoring.models import VerdictLevel
from safelabs.scoring.scorer import Scorer

_REFUSAL = "I cannot and will not comply with that request. That looks like an injection attempt."


def _http(monkeypatch, scripted):
    """Install a fake httpx client that returns scripted responses in order (the last one repeats)."""
    calls = {"n": 0}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **kw):
            resp = scripted[min(calls["n"], len(scripted) - 1)]
            calls["n"] += 1
            return resp

    monkeypatch.setattr(http_adapter.httpx, "AsyncClient", _Client)
    return calls


def _ok(body=None, headers=None):
    return httpx.Response(200, json=body if body is not None else {"response": _REFUSAL}, headers=headers or {})


class _SpyScorer(Scorer):
    def __init__(self):
        super().__init__()
        self.calls = 0

    async def score(self, *a, **k):
        self.calls += 1
        return await super().score(*a, **k)


async def _run(agent, **kw):
    async def fake_sleep(s):
        pass

    kw.setdefault("categories", ["ASI01"])
    kw.setdefault("limit", 1)
    kw.setdefault("jitter_fn", lambda: 1.0)
    return await run_eval(agent, sleep=fake_sleep, **kw)


def _adapter():
    return HttpAdapter(base_url="http://localhost:9/chat")


class _FrameworkAdapter(AgentAdapter):
    """A fake framework adapter that sets AgentResponse.degraded itself."""

    def __init__(self, outputs):
        super().__init__(timeout=5.0)
        self.outputs, self.calls = outputs, 0

    @property
    def adapter_type(self) -> str:
        return "fake_framework"

    async def _execute(self, prompt):
        degraded = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        return AgentResponse(output=_REFUSAL, degraded=degraded)


# ── each signal: retried, then missing, never scored ──────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes"])
async def test_header_signal_is_retried_then_missing_and_never_scored(monkeypatch, value):
    calls = _http(monkeypatch, [_ok(headers={"X-SafeLabs-Degraded": value})])
    spy = _SpyScorer()
    result = await _run(_adapter().execute, scorer=spy)
    assert calls["n"] == 3 and spy.calls == 0
    rec = result.records[0]
    assert rec.status == "missing_infrastructure" and rec.is_missing and rec.scoring_result is None
    assert (rec.error_class, rec.error_subclass, rec.attempts, rec.response) == ("infrastructure", "degraded", 3, "")
    assert rec.attempt_errors == ["degraded"] * 3 and rec.error
    assert result.total == 0 and len(result.missing) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["0", "false", "no", ""])
async def test_header_with_other_values_is_not_degraded(monkeypatch, value):
    _http(monkeypatch, [_ok(headers={"X-SafeLabs-Degraded": value})])
    result = await _run(_adapter().execute)
    assert result.records[0].status == "scored" and result.records[0].attempts == 1


@pytest.mark.asyncio
async def test_json_field_signal_is_retried_then_missing_and_never_scored(monkeypatch):
    calls = _http(monkeypatch, [_ok({"response": "keyword search", "safelabs_degraded": True})])
    spy = _SpyScorer()
    result = await _run(_adapter().execute, scorer=spy)
    assert calls["n"] == 3 and spy.calls == 0
    rec = result.records[0]
    assert rec.status == "missing_infrastructure" and rec.error_subclass == "degraded" and rec.scoring_result is None


@pytest.mark.asyncio
async def test_json_field_must_be_true_to_count(monkeypatch):
    _http(monkeypatch, [_ok({"response": _REFUSAL, "safelabs_degraded": "true"})])
    result = await _run(_adapter().execute)
    assert result.records[0].status == "scored"


@pytest.mark.asyncio
async def test_agent_response_degraded_from_a_framework_adapter(monkeypatch):
    adapter, spy = _FrameworkAdapter([True]), _SpyScorer()
    result = await _run(adapter.execute, scorer=spy)
    assert adapter.calls == 3 and spy.calls == 0
    rec = result.records[0]
    assert rec.status == "missing_infrastructure" and (rec.error_class, rec.error_subclass) == ("infrastructure", "degraded")


# ── retry then success, no signal, max-attempts 1 ─────────────────────────

@pytest.mark.asyncio
async def test_degraded_then_normal_is_scored_normally(monkeypatch):
    calls = _http(monkeypatch, [_ok(headers={"X-SafeLabs-Degraded": "1"}), _ok()])
    result = await _run(_adapter().execute)
    rec = result.records[0]
    assert calls["n"] == 2 and rec.status == "scored" and rec.attempts == 2 and rec.attempt_errors == ["degraded"]
    assert rec.error is None and rec.error_class is None and rec.verdict == VerdictLevel.PASS


@pytest.mark.asyncio
async def test_no_signal_is_scored_as_before(monkeypatch):
    calls = _http(monkeypatch, [_ok()])
    result = await _run(_adapter().execute)
    rec = result.records[0]
    assert calls["n"] == 1 and rec.status == "scored" and rec.attempts == 1 and rec.attempt_errors == []
    assert rec.error is None and rec.error_class is None and rec.error_subclass is None
    assert rec.verdict == VerdictLevel.PASS and rec.response == _REFUSAL
    assert AgentResponse(output="x").degraded is False


@pytest.mark.asyncio
async def test_max_attempts_one_makes_a_degraded_response_missing_at_once(monkeypatch):
    calls = _http(monkeypatch, [_ok(headers={"X-SafeLabs-Degraded": "1"})])
    spy = _SpyScorer()
    result = await _run(_adapter().execute, scorer=spy, max_attempts=1)
    rec = result.records[0]
    assert calls["n"] == 1 and spy.calls == 0 and rec.attempts == 1 and rec.status == "missing_infrastructure"


# ── --output json row ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_json_missing_row_has_the_same_keys_as_other_infrastructure_missing_rows(monkeypatch):
    _http(monkeypatch, [_ok(headers={"X-SafeLabs-Degraded": "1"})])
    degraded = (await _run(_adapter().execute)).records[0]

    async def rate_limited(prompt):
        return AgentResponse(output="", error="HTTP 429: slow down")

    other = (await _run(rate_limited)).records[0]
    drow, orow = cli._result_row(degraded), cli._result_row(other)
    assert set(drow) == set(orow) == {"id", "status", "error", "error_class", "error_subclass", "attempts"}
    assert drow["status"] == "missing_infrastructure"
    assert (drow["error_class"], drow["error_subclass"], drow["attempts"]) == ("infrastructure", "degraded", 3)
