"""
tests/test_http_adapter_openai_body.py

HttpAdapter output from an OpenAI chat-completions body (1A.1 wave 2b): when no
common top-level output key is present, ``choices[0].message.content`` is the
output; the optional fields (stop_reason, usage, tool_calls, provenance) come
from the same body. The httpx client is replaced by a fake; no network is used.
"""

from __future__ import annotations

import json

import pytest

from safelabs.agents import http_adapter
from safelabs.agents.http_adapter import HttpAdapter


class _Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


async def _run(monkeypatch, body, **kw):
    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return False
        async def post(self, url, **k): return _Resp(body)

    monkeypatch.setattr(http_adapter.httpx, "AsyncClient", _Client)
    return await HttpAdapter(base_url="http://localhost:9/chat", **kw).execute("hello")


def _chat(content="hello world", **message):
    msg = {"role": "assistant", "content": content}
    msg.update(message)
    return {"id": "chatcmpl-1", "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 11, "total_tokens": 18}}


@pytest.mark.asyncio
async def test_chat_completions_body_gives_output_and_fields(monkeypatch):
    body = _chat()
    r = await _run(monkeypatch, body)
    assert r.output == "hello world" and r.error is None and r.succeeded
    assert r.stop_reason == "stop"
    assert r.usage == {"prompt_tokens": 7, "completion_tokens": 11, "reasoning_tokens": None}
    assert r.tool_calls == []
    assert r.provenance == {"tool_calls": "inferred", "stop_reason": "inferred", "usage": "inferred"}
    assert r.raw == body and r.metadata == {"status_code": 200}


@pytest.mark.asyncio
async def test_chat_completions_tool_calls_are_parsed_with_provenance(monkeypatch):
    body = _chat("calling", tool_calls=[
        {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": json.dumps({"q": "x", "n": 2})}},
        {"id": "call_2", "type": "function", "function": {"name": "ping", "arguments": "{}"}},
    ])
    body["choices"][0]["finish_reason"] = "tool_calls"
    r = await _run(monkeypatch, body)
    assert r.output == "calling" and r.stop_reason == "tool_calls"
    assert [(c.name, c.arguments, c.arguments_raw, c.call_id) for c in r.tool_calls] == [
        ("lookup", {"q": "x", "n": 2}, None, "call_1"), ("ping", {}, None, "call_2")]
    assert all(c.result is None for c in r.tool_calls)
    assert r.provenance["tool_calls"] == "inferred"


@pytest.mark.asyncio
async def test_tool_call_with_non_json_arguments_keeps_the_raw_text(monkeypatch):
    body = _chat("ok", tool_calls=[{"id": "call_9", "type": "function", "function": {"name": "raw", "arguments": "not { json"}}])
    r = await _run(monkeypatch, body)
    call = r.tool_calls[0]
    assert call.name == "raw" and call.arguments is None and call.arguments_raw == "not { json" and call.call_id == "call_9"
    assert r.provenance["tool_calls"] == "inferred"


@pytest.mark.asyncio
async def test_existing_top_level_key_wins_over_choices(monkeypatch):
    body = _chat("from choices")
    body["response"] = "from response key"
    r = await _run(monkeypatch, body)
    assert r.output == "from response key"
    assert r.stop_reason == "stop" and r.tool_calls == []       # the optional fields are still read from choices


@pytest.mark.asyncio
async def test_other_common_keys_and_response_key_also_win(monkeypatch):
    for key in ("output", "message", "text", "content", "result"):
        body = _chat("from choices")
        body[key] = f"top {key}"
        assert (await _run(monkeypatch, body)).output == f"top {key}"
    body = _chat("from choices")
    body["custom"] = "from response_key"
    assert (await _run(monkeypatch, body, response_key="custom")).output == "from response_key"


@pytest.mark.asyncio
async def test_unknown_shape_keeps_the_stringified_fallback(monkeypatch):
    body = {"foo": 1, "bar": [2]}
    r = await _run(monkeypatch, body)
    assert r.output == str(body) and r.error is None
    assert r.tool_calls is None and r.usage is None and r.stop_reason is None and r.provenance is None


@pytest.mark.asyncio
async def test_empty_choices_list_falls_back_without_error(monkeypatch):
    body = {"choices": [], "id": "x"}
    r = await _run(monkeypatch, body)
    assert r.output == str(body) and r.error is None and r.tool_calls is None


@pytest.mark.asyncio
@pytest.mark.parametrize("choices", [
    [None], ["text"], [{}], [{"message": None}], [{"message": "str"}], [{"message": {}}],
    [{"message": {"content": None}}], [{"message": {"content": [{"type": "text", "text": "parts"}]}}], "not a list",
])
async def test_other_choices_shapes_fall_back_to_the_stringified_body(monkeypatch, choices):
    body = {"choices": choices}
    r = await _run(monkeypatch, body)
    assert r.output == str(body) and r.error is None


@pytest.mark.asyncio
async def test_tool_call_only_message_keeps_the_stringified_fallback(monkeypatch):
    body = _chat(None, tool_calls=[{"id": "c", "type": "function", "function": {"name": "f", "arguments": "{}"}}])
    r = await _run(monkeypatch, body)
    assert r.output == str(body)                                 # content is None: not read (documented behaviour)
    assert [c.name for c in r.tool_calls] == ["f"]


@pytest.mark.asyncio
async def test_empty_string_content_is_the_output_and_the_base_class_flags_it(monkeypatch):
    r = await _run(monkeypatch, _chat(""))
    assert r.output == "" and r.error == "provider returned no output text" and not r.succeeded
