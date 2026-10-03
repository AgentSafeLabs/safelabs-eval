"""
tests/test_http_adapter_response.py

HttpAdapter's own tests: output, latency, error paths, the timeout it hands to
httpx, and the optional AgentResponse fields inferred from an OpenAI-compatible
body. The httpx client is replaced by a fake; no network is used.
"""

from __future__ import annotations

import json

import pytest

from safelabs.agents import http_adapter
from safelabs.agents.http_adapter import HttpAdapter

_NEW = ("tool_calls", "usage", "stop_reason", "error_code", "non_text_parts", "framework_version", "provenance")


class _Resp:
    def __init__(self, status=200, body=None, text=None, bad_json=False):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else json.dumps(body)
        self._bad = bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._body


def _install(monkeypatch, response):
    seen = {}

    class _Client:
        def __init__(self, *a, **kw):
            seen["client_kwargs"] = kw

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **kw):
            seen["url"], seen["post_kwargs"] = url, kw
            return response

    monkeypatch.setattr(http_adapter.httpx, "AsyncClient", _Client)
    return seen


def _adapter(**kw):
    return HttpAdapter(base_url="http://localhost:9/chat", **kw)


async def _run(monkeypatch, response, **kw):
    seen = _install(monkeypatch, response)
    return await _adapter(**kw).execute("hello"), seen


def _chat_body(**message):
    msg = {"role": "assistant", "content": "ok"}
    msg.update(message)
    return {"choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 11, "total_tokens": 18}}


# ── basics, errors, timeout ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_plain_json_output_latency_and_request(monkeypatch):
    r, seen = await _run(monkeypatch, _Resp(body={"response": "hi there"}))
    assert r.output == "hi there" and r.error is None and r.latency_ms >= 0
    assert r.raw == {"response": "hi there"} and r.metadata == {"status_code": 200}
    assert seen["url"] == "http://localhost:9/chat" and seen["post_kwargs"]["json"] == {"prompt": "hello"}
    assert r.framework == "http"


@pytest.mark.asyncio
async def test_timeout_is_passed_to_httpx_client(monkeypatch):
    _, seen = await _run(monkeypatch, _Resp(body={"response": "x"}), timeout=7.5)
    assert seen["client_kwargs"]["timeout"] == 7.5
    _, seen = await _run(monkeypatch, _Resp(body={"response": "x"}))
    assert seen["client_kwargs"]["timeout"] == 30.0


@pytest.mark.asyncio
async def test_http_error_status_sets_error_and_no_new_fields(monkeypatch):
    r, _ = await _run(monkeypatch, _Resp(status=503, body={"error": "busy"}))
    assert r.output == "" and r.error.startswith("HTTP 503:") and r.metadata == {"status_code": 503}
    assert all(getattr(r, k) is None for k in _NEW)


@pytest.mark.asyncio
async def test_non_json_body_keeps_text_and_new_fields_none(monkeypatch):
    r, _ = await _run(monkeypatch, _Resp(text="plain words", bad_json=True))
    assert r.output == "plain words" and r.raw is None
    assert all(getattr(r, k) is None for k in _NEW)


@pytest.mark.asyncio
async def test_json_list_body_has_no_new_fields(monkeypatch):
    r, _ = await _run(monkeypatch, _Resp(body=["a", "b"]))
    assert r.raw is None and all(getattr(r, k) is None for k in _NEW)


@pytest.mark.asyncio
async def test_transport_exception_becomes_error_response(monkeypatch):
    class _Boom:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return False
        async def post(self, *a, **kw): raise ConnectionError("refused")
    monkeypatch.setattr(http_adapter.httpx, "AsyncClient", _Boom)
    r = await _adapter().execute("x")
    assert r.output == "" and "refused" in r.error and r.framework == "http"
    assert all(getattr(r, k) is None for k in _NEW)


# ── optional fields from an OpenAI-compatible body ───────────────────────

@pytest.mark.asyncio
async def test_openai_body_without_tool_calls_gives_empty_list(monkeypatch):
    r, _ = await _run(monkeypatch, _Resp(body=_chat_body()), response_key="content")  # response_key irrelevant to the new fields
    assert r.tool_calls == []
    assert r.stop_reason == "stop"
    assert r.usage == {"prompt_tokens": 7, "completion_tokens": 11, "reasoning_tokens": None}
    assert r.non_text_parts is None and r.error_code is None and r.framework_version is None
    assert r.provenance == {"tool_calls": "inferred", "stop_reason": "inferred", "usage": "inferred"}


@pytest.mark.asyncio
async def test_openai_body_with_tool_calls(monkeypatch):
    body = _chat_body(content=None, tool_calls=[
        {"id": "call_9", "type": "function", "function": {"name": "lookup", "arguments": json.dumps({"q": "x"})}},
        {"id": "call_10", "type": "function", "function": {"name": "raw", "arguments": "not json"}},
    ])
    body["choices"][0]["finish_reason"] = "tool_calls"
    r, _ = await _run(monkeypatch, _Resp(body=body))
    assert [c.name for c in r.tool_calls] == ["lookup", "raw"]
    assert r.tool_calls[0].arguments == {"q": "x"} and r.tool_calls[0].call_id == "call_9"
    assert r.tool_calls[1].arguments is None and r.tool_calls[1].arguments_raw == "not json"
    assert r.stop_reason == "tool_calls"
    assert all(c.result is None for c in r.tool_calls)       # HTTP bodies carry no tool results


@pytest.mark.asyncio
async def test_usage_alternate_names_and_reasoning_detail(monkeypatch):
    body = {"choices": [], "usage": {"input_tokens": 4, "output_tokens": 9, "output_tokens_details": {"reasoning_tokens": 6}}}
    r, _ = await _run(monkeypatch, _Resp(body=body))
    assert r.usage == {"prompt_tokens": 4, "completion_tokens": 9, "reasoning_tokens": 6}
    assert r.tool_calls is None            # no choices[0].message: not exposed


@pytest.mark.asyncio
async def test_non_openai_body_leaves_everything_none(monkeypatch):
    r, _ = await _run(monkeypatch, _Resp(body={"output": "fine", "tokens": 5}))
    assert r.output == "fine" and all(getattr(r, k) is None for k in _NEW)


@pytest.mark.asyncio
async def test_top_level_stop_reason_key(monkeypatch):
    r, _ = await _run(monkeypatch, _Resp(body={"response": "x", "finish_reason": "length"}))
    assert r.stop_reason == "length" and r.provenance == {"stop_reason": "inferred"}


@pytest.mark.asyncio
async def test_malformed_tool_calls_value_is_not_exposed(monkeypatch):
    body = _chat_body(tool_calls="oops")
    r, _ = await _run(monkeypatch, _Resp(body=body))
    assert r.tool_calls is None
