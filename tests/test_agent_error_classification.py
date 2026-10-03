"""
tests/test_agent_error_classification.py

safelabs.agents.errors: classify a failed adapter call (infrastructure, content_policy,
no_output_text, other), parse Retry-After, and the metadata AgentAdapter.execute() and
HttpAdapter record for it. Exceptions are the real classes from the installed provider
SDKs where available (openai, anthropic, google-genai, httpx); no network is used.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from safelabs.agents import http_adapter
from safelabs.agents.base import AgentAdapter
from safelabs.agents.errors import (
    classify_error,
    describe_exception,
    retry_after_seconds,
)
from safelabs.agents.http_adapter import HttpAdapter
from safelabs.agents.schemas import AgentResponse, ToolCall

_REQ = httpx.Request("POST", "http://localhost:9/v1")


def _resp(status, retry_after=None):
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    return httpx.Response(status, request=_REQ, headers=headers)


def _openai(name, status=None, retry_after=None):
    openai = pytest.importorskip("openai")
    cls = getattr(openai, name)
    if status is None:
        return cls(request=_REQ) if name != "APITimeoutError" else cls(_REQ)
    return cls("provider said no", response=_resp(status, retry_after), body=None)


def _anthropic(name, status):
    pytest.importorskip("anthropic")
    from anthropic import _exceptions      # OverloadedError is defined here but not exported at the top level
    return getattr(_exceptions, name)("provider said no", response=_resp(status), body=None)


def _genai(name, code):
    errors = pytest.importorskip("google.genai.errors")
    return getattr(errors, name)(code, {"error": {"code": code, "message": "provider said no", "status": "X"}})


# (id, factory, expected subclass)
INFRA_EXCEPTIONS = [
    ("openai-RateLimit", lambda: _openai("RateLimitError", 429, "7"), "rate_limit_or_quota"),
    ("openai-Timeout", lambda: _openai("APITimeoutError"), "timeout"),
    ("openai-Connection", lambda: _openai("APIConnectionError"), "connection_error"),
    ("openai-InternalServer", lambda: _openai("InternalServerError", 500), "provider_unavailable"),
    ("openai-502-status-only", lambda: _openai("APIStatusError", 502), "provider_unavailable"),
    ("anthropic-RateLimit", lambda: _anthropic("RateLimitError", 429), "rate_limit_or_quota"),
    ("anthropic-Overloaded", lambda: _anthropic("OverloadedError", 529), "provider_unavailable"),
    ("genai-ClientError-429", lambda: _genai("ClientError", 429), "rate_limit_or_quota"),
    ("genai-ServerError-503", lambda: _genai("ServerError", 503), "provider_unavailable"),
    ("httpx-ReadTimeout", lambda: httpx.ReadTimeout("slow"), "timeout"),
    ("httpx-ConnectError", lambda: httpx.ConnectError("refused"), "connection_error"),
    ("builtin-ConnectionResetError", lambda: ConnectionResetError("reset"), "connection_error"),
]


class _Raises(AgentAdapter):
    def __init__(self, exc):
        super().__init__(timeout=5)
        self._exc = exc

    @property
    def adapter_type(self):
        return "raises"

    async def _execute(self, prompt):
        raise self._exc


@pytest.mark.asyncio
@pytest.mark.parametrize("make,expected", [(m, e) for _, m, e in INFRA_EXCEPTIONS], ids=[i for i, _, _ in INFRA_EXCEPTIONS])
async def test_provider_exceptions_are_infrastructure(make, expected):
    response = await _Raises(make()).execute("hi")
    assert response.output == "" and response.error
    assert response.metadata and response.metadata["exception_mro"]
    info = classify_error(response.error, response.metadata)
    assert (info.error_class, info.error_subclass) == ("infrastructure", expected)


@pytest.mark.asyncio
async def test_retry_after_header_is_recorded_from_the_exception():
    response = await _Raises(_openai("RateLimitError", 429, "7")).execute("hi")
    assert response.metadata["status_code"] == 429 and response.metadata["retry_after_s"] == 7.0
    assert classify_error(response.error, response.metadata).retry_after_s == 7.0


@pytest.mark.asyncio
async def test_wrapped_exception_is_classified_by_its_cause():
    try:
        try:
            raise _openai("RateLimitError", 429, "3")
        except Exception as inner:
            raise RuntimeError("agent step failed") from inner
    except RuntimeError as wrapper:
        exc = wrapper
    response = await _Raises(exc).execute("hi")
    assert response.error == "agent step failed"
    info = classify_error(response.error, response.metadata)
    assert info.error_subclass == "rate_limit_or_quota" and info.retry_after_s == 3.0


@pytest.mark.asyncio
async def test_adapter_timeout_is_a_timeout():
    class _Slow(AgentAdapter):
        @property
        def adapter_type(self):
            return "slow"

        async def _execute(self, prompt):
            await asyncio.sleep(5)

    response = await _Slow(timeout=0.01).execute("hi")
    assert "timed out" in response.error
    assert classify_error(response.error, response.metadata).error_subclass == "timeout"


@pytest.mark.asyncio
async def test_unrecognised_exception_is_other_and_not_infrastructure():
    response = await _Raises(RuntimeError("simulated provider failure")).execute("hi")
    info = classify_error(response.error, response.metadata)
    assert (info.error_class, info.error_subclass) == ("other", "other") and not info.is_infrastructure


def test_content_policy_by_exception_name_and_by_marker():
    class ContentPolicyViolationError(Exception):
        pass

    meta = describe_exception(ContentPolicyViolationError("blocked"))
    assert classify_error("blocked", meta).error_class == "content_policy"
    assert classify_error("This request was flagged for possible cybersecurity risk", None).error_subclass == "content_policy"


@pytest.mark.parametrize("text,expected", [
    ("provider returned no output text", ("no_output_text", "no_output_text")),
    ("Agent timed out after 30.0s", ("infrastructure", "timeout")),
    ("You exceeded your current quota, please check your plan", ("infrastructure", "rate_limit_or_quota")),
    ("HTTP 429: slow down", ("infrastructure", "rate_limit_or_quota")),
    ("503 UNAVAILABLE. {'error': {'message': 'The model is overloaded.'}}", ("infrastructure", "provider_unavailable")),
    ("HTTP 502: bad gateway", ("infrastructure", "provider_unavailable")),
    ("Connection error.", ("infrastructure", "connection_error")),
    ("something else entirely", ("other", "other")),
])
def test_message_markers_match_the_data_release_classes(text, expected):
    info = classify_error(text, None)
    assert (info.error_class, info.error_subclass) == expected


def test_no_error_is_not_classified():
    assert classify_error(None, {"exception_mro": ["RateLimitError"]}) is None


def test_status_code_in_metadata_drives_classification():
    assert classify_error("HTTP 429: x", {"status_code": 429}).error_subclass == "rate_limit_or_quota"
    assert classify_error("HTTP 504: x", {"status_code": 504}).error_subclass == "timeout"
    assert classify_error("HTTP 500: x", {"status_code": 500}).error_subclass == "provider_unavailable"
    assert classify_error("HTTP 400: bad request", {"status_code": 400}).error_class == "other"


def test_retry_after_seconds_parsing():
    now = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)
    assert retry_after_seconds("7") == 7.0 and retry_after_seconds(2.5) == 2.5 and retry_after_seconds(" 0 ") == 0.0
    assert retry_after_seconds("Sat, 03 Oct 2026 12:00:30 GMT", now=now) == 30.0
    assert retry_after_seconds("Sat, 03 Oct 2026 11:00:00 GMT", now=now) == 0.0          # in the past: do not wait
    for bad in (None, "", "soon", "nan", "-3", True, -1):
        assert retry_after_seconds(bad) is None


# ── HttpAdapter ───────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self.text = status, "slow down"
        self._body = body
        if headers is not None:
            self.headers = headers

    def json(self):
        return self._body


async def _http(monkeypatch, response):
    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return False
        async def post(self, url, **k): return response

    monkeypatch.setattr(http_adapter.httpx, "AsyncClient", _Client)
    return await HttpAdapter(base_url="http://localhost:9/chat").execute("hi")


@pytest.mark.asyncio
async def test_http_429_with_retry_after_header(monkeypatch):
    r = await _http(monkeypatch, _Resp(429, headers=httpx.Headers({"Retry-After": "12"})))
    assert r.metadata == {"status_code": 429, "retry_after_s": 12.0}
    info = classify_error(r.error, r.metadata)
    assert (info.error_subclass, info.retry_after_s) == ("rate_limit_or_quota", 12.0)


@pytest.mark.asyncio
async def test_http_503_without_header_keeps_the_old_metadata(monkeypatch):
    r = await _http(monkeypatch, _Resp(503))
    assert r.metadata == {"status_code": 503}
    assert classify_error(r.error, r.metadata).error_subclass == "provider_unavailable"
    r = await _http(monkeypatch, _Resp(503, headers=httpx.Headers({})))
    assert r.metadata == {"status_code": 503}


@pytest.mark.asyncio
async def test_http_400_is_not_infrastructure(monkeypatch):
    r = await _http(monkeypatch, _Resp(400))
    assert classify_error(r.error, r.metadata).error_class == "other"


# ── tool-call-only is not an error ────────────────────────────────────────

class _Fixed(AgentAdapter):
    def __init__(self, response):
        super().__init__()
        self._r = response

    @property
    def adapter_type(self):
        return "fixed"

    async def _execute(self, prompt):
        return self._r


@pytest.mark.asyncio
async def test_empty_text_with_tool_calls_is_not_an_error():
    r = await _Fixed(AgentResponse(output="", tool_calls=[ToolCall(name="lookup")], provenance={"tool_calls": "inferred"})).execute("hi")
    assert r.error is None and r.output == "" and [c.name for c in r.tool_calls] == ["lookup"]


@pytest.mark.asyncio
async def test_empty_text_with_no_or_empty_tool_calls_is_still_the_old_error():
    for calls in (None, []):
        r = await _Fixed(AgentResponse(output="  ", tool_calls=calls, provenance={"tool_calls": "inferred"} if calls is not None else None)).execute("hi")
        assert r.error == "provider returned no output text"
