"""
tests/test_adapter_conformance.py

Shared AgentResponse conformance suite. One parametrized set of checks runs
against every adapter that fills the optional fields (HTTP, LangChain, Google
ADK, OpenAI Agents); the adapters still on the old behaviour (CrewAI, AutoGen,
LlamaIndex, Semantic Kernel) are checked only for "the new fields are None", from
an explicit list, so the gap stays visible. A test fails if an exported adapter
is in neither list: adding an adapter forces a decision.

Checks: response type; the None-vs-[] rule; provenance keys and values;
ToolCall.result absent from serialization and repr; timeout, exception and
empty-output normalization through execute(); serialization round trip.

All adapters are driven by duck-typed fakes (no framework install needed, no
network). Real-framework coverage lives in the per-adapter integration tests.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import safelabs.agents as agents_pkg
from safelabs.agents import (
    AutoGenAdapter, CrewAIAdapter, GoogleADKAdapter, HttpAdapter, LangChainAdapter,
    LlamaIndexAdapter, OpenAIAgentsAdapter, SemanticKernelAdapter,
)
from safelabs.agents import http_adapter
from safelabs.agents.schemas import PROVENANCE_FIELDS, AgentResponse, ToolCall

SENTINEL = "SENTINEL-TOOL-RESULT-DO-NOT-SERIALIZE"

#: adapter_type values that fill the optional fields
CONFORMING = ("http", "langchain", "google-adk", "openai-agents")
#: adapter_type values still on the old behaviour (new fields stay None)
OLD_BEHAVIOUR = ("crewai", "autogen", "llamaindex", "semantic-kernel")

_COLLECTIONS = ("tool_calls", "non_text_parts")
_NEW_EXCEPT_FRAMEWORK = (
    "tool_calls", "usage", "stop_reason", "error_code", "non_text_parts", "framework_version", "provenance",
)


# ── fakes: HTTP ──────────────────────────────────────────────────────────

class _HttpResp:
    status_code = 200

    def __init__(self, body):
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


def _http(monkeypatch, scenario: str, timeout: float = 30.0):
    rich = {"response": "ok", "choices": [{"message": {"content": "ok", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "lookup", "arguments": "{\"q\": \"x\"}"}}]},
        "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 3, "completion_tokens": 4}}
    plain = {"response": "ok", "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 2}}
    empty = {"response": ""}

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return False

        async def post(self, url, **kw):
            if scenario == "slow":
                await asyncio.sleep(5)
            if scenario == "boom":
                raise ConnectionError("refused")
            return _HttpResp({"rich": rich, "plain": plain, "empty": empty}[scenario])

    monkeypatch.setattr(http_adapter.httpx, "AsyncClient", _Client)
    return HttpAdapter(base_url="http://localhost:9/chat", timeout=timeout)


# ── fakes: LangChain ─────────────────────────────────────────────────────

class _Msg:
    def __init__(self, content, tool_calls, usage_metadata, response_metadata):
        self.content, self.tool_calls = content, tool_calls
        self.usage_metadata, self.response_metadata = usage_metadata, response_metadata


def _langchain(monkeypatch, scenario: str, timeout: float = 30.0):
    messages = {
        "rich": _Msg("ok", [{"name": "lookup", "args": {"q": "x"}, "id": "c1", "type": "tool_call"}],
                     {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}, {"finish_reason": "tool_calls"}),
        "plain": _Msg("ok", [], {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}, {"finish_reason": "stop"}),
        "empty": _Msg("", [], None, {}),
    }

    class _Runnable:
        async def ainvoke(self, payload):
            if scenario == "slow":
                await asyncio.sleep(5)
            if scenario == "boom":
                raise RuntimeError("provider down")
            return messages[scenario]

    return LangChainAdapter(runnable=_Runnable(), input_key=None, timeout=timeout)


# ── fakes: Google ADK ────────────────────────────────────────────────────

class _Fc:
    def __init__(self, name, args, id): self.name, self.args, self.id = name, args, id


class _Fr:
    def __init__(self, name, response, id): self.name, self.response, self.id = name, response, id


class _Part:
    def __init__(self, text): self.text = text


class _Content:
    def __init__(self, text): self.parts = [_Part(text)]


class _Usage:
    def __init__(self, p, c): self.prompt_token_count, self.candidates_token_count, self.thoughts_token_count = p, c, None


class _Reason:
    value = "STOP"


class _Event:
    def __init__(self, text=None, *, final=True, calls=None, responses=None, usage=None, finish=None):
        self._final = final
        self.content = _Content(text)
        self.usage_metadata, self.finish_reason, self.error_code, self.partial = usage, finish, None, False
        self.get_function_calls = lambda: calls or []
        self.get_function_responses = lambda: responses or []

    def is_final_response(self): return self._final


def _adk(monkeypatch, scenario: str, timeout: float = 30.0):
    class _Sessions:
        async def create_session(self, **kw):
            return type("S", (), {"id": "s"})()

    class _Runner:
        app_name = "t"
        session_service = _Sessions()

        async def run_async(self, **kw):
            if scenario == "slow":
                await asyncio.sleep(5)
            if scenario == "boom":
                raise RuntimeError("provider down")
            if scenario == "rich":
                yield _Event(None, final=False, calls=[_Fc("lookup", {"q": "x"}, "c1")], usage=_Usage(3, 4))
                yield _Event(None, final=False, responses=[_Fr("lookup", {"v": SENTINEL}, "c1")])
                yield _Event("ok", usage=_Usage(9, 2), finish=_Reason())
            elif scenario == "plain":
                yield _Event("ok", usage=_Usage(1, 2), finish=_Reason())
            else:
                yield _Event("")

    return GoogleADKAdapter(agent=None, runner=_Runner(), content_factory=lambda p: p, timeout=timeout)


# ── fakes: OpenAI Agents ─────────────────────────────────────────────────

class _Item:
    def __init__(self, type, raw_item, output=None): self.type, self.raw_item, self.output = type, raw_item, output


class _Raw:
    def __init__(self, name, arguments, call_id): self.name, self.arguments, self.call_id = name, arguments, call_id


class _OaUsage:
    def __init__(self, i, o): self.input_tokens, self.output_tokens = i, o
    output_tokens_details = type("D", (), {"reasoning_tokens": 0})()


class _OaResult:
    def __init__(self, final_output, items, usage):
        self.final_output, self.new_items = final_output, items
        self.context_wrapper = type("W", (), {"usage": usage})()


def _openai(monkeypatch, scenario: str, timeout: float = 30.0):
    class _Runner:
        async def run(self, agent, prompt):
            if scenario == "slow":
                await asyncio.sleep(5)
            if scenario == "boom":
                raise RuntimeError("provider down")
            if scenario == "rich":
                items = [_Item("tool_call_item", _Raw("lookup", "{\"q\": \"x\"}", "c1")),
                         _Item("tool_call_output_item", {"call_id": "c1", "output": SENTINEL}, output=SENTINEL)]
                return _OaResult("ok", items, _OaUsage(12, 6))
            if scenario == "plain":
                return _OaResult("ok", [], _OaUsage(1, 2))
            return _OaResult("", [], _OaUsage(1, 0))

    return OpenAIAgentsAdapter(agent=None, runner=_Runner(), timeout=timeout)


_BUILDERS = {"http": _http, "langchain": _langchain, "google-adk": _adk, "openai-agents": _openai}


# ── fakes: adapters still on the old behaviour ───────────────────────────

class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _old(adapter_type: str, scenario: str, timeout: float = 30.0):
    async def maybe():
        if scenario == "slow":
            await asyncio.sleep(5)
        if scenario == "boom":
            raise RuntimeError("provider down")

    if adapter_type == "crewai":
        class _Crew:
            def kickoff(self, inputs):
                if scenario == "slow":
                    import time as _t
                    _t.sleep(0.3)
                if scenario == "boom":
                    raise RuntimeError("provider down")
                return _Obj(raw="ok" if scenario != "empty" else "")
        return CrewAIAdapter(crew=_Crew(), timeout=timeout)
    if adapter_type == "autogen":
        class _Recipient:
            async def a_initiate_chat(self, agent, message, max_turns):
                await maybe()
                return _Obj(chat_history=[{"role": "assistant", "content": "ok" if scenario != "empty" else ""}])
        return AutoGenAdapter(agent=object(), recipient=_Recipient(), timeout=timeout)
    if adapter_type == "llamaindex":
        class _Workflow:
            async def run(self, user_msg):
                await maybe()
                return "ok" if scenario != "empty" else ""
        return LlamaIndexAdapter(workflow=_Workflow(), timeout=timeout)
    class _Reply(_Obj):
        def __str__(self):                      # SemanticKernelAdapter falls back to str(result)
            return self.message.content

    class _Agent:
        async def get_response(self, messages):
            await maybe()
            return _Reply(message=_Obj(content="ok" if scenario != "empty" else ""))
    return SemanticKernelAdapter(agent=_Agent(), timeout=timeout)


# ── check helpers ────────────────────────────────────────────────────────

def _check_none_vs_empty(r: AgentResponse, *, expect_calls: bool) -> None:
    for name in _COLLECTIONS:
        value = getattr(r, name)
        assert value is None or isinstance(value, list), f"{name} must be None (not exposed) or a list"
    if r.tool_calls is not None:
        assert all(isinstance(c, ToolCall) for c in r.tool_calls)
    if expect_calls:
        assert r.tool_calls, "a scenario with a tool call must expose it"
    else:
        assert r.tool_calls in (None, []), "no tool call in the scenario: None or [], never a non-empty list"


def _check_provenance(r: AgentResponse) -> None:
    prov = r.provenance or {}
    assert set(prov) <= set(PROVENANCE_FIELDS)
    for name in PROVENANCE_FIELDS:
        tag = prov.get(name)
        if getattr(r, name) is None:
            assert tag in (None, "unknown"), f"{name} is None but provenance is {tag!r}"
        else:
            assert tag in ("verified", "inferred"), f"{name} is set but provenance is {tag!r}"


def _check_result_not_serialized(r: AgentResponse) -> None:
    for call in r.tool_calls or []:
        assert "result" not in call.model_dump()
    for text in (r.model_dump_json(), json.dumps(r.model_dump(), default=str), repr(r)):
        assert SENTINEL not in text


def _all_new_none(r: AgentResponse) -> bool:
    return all(getattr(r, k) is None for k in _NEW_EXCEPT_FRAMEWORK)


# ── conforming adapters ──────────────────────────────────────────────────

@pytest.mark.parametrize("name", CONFORMING)
@pytest.mark.asyncio
async def test_rich_response_conforms(name, monkeypatch):
    r = await _BUILDERS[name](monkeypatch, "rich").execute("p")
    assert isinstance(r, AgentResponse) and r.output and r.error is None
    assert r.framework == name
    _check_none_vs_empty(r, expect_calls=True)
    assert r.tool_calls[0].name == "lookup" and r.tool_calls[0].arguments == {"q": "x"} and r.tool_calls[0].call_id == "c1"
    _check_provenance(r)
    _check_result_not_serialized(r)
    if name in ("google-adk", "openai-agents"):
        assert SENTINEL in (r.tool_calls[0].result or ""), "framework exposes tool results: kept in memory"


@pytest.mark.parametrize("name", CONFORMING)
@pytest.mark.asyncio
async def test_plain_response_exposes_empty_not_nonempty(name, monkeypatch):
    r = await _BUILDERS[name](monkeypatch, "plain").execute("p")
    assert isinstance(r, AgentResponse) and r.output == "ok"
    _check_none_vs_empty(r, expect_calls=False)
    assert r.tool_calls == [], "these adapters expose tool calls, so zero calls is [] and not None"
    _check_provenance(r)
    _check_result_not_serialized(r)


@pytest.mark.parametrize("name", CONFORMING)
@pytest.mark.asyncio
async def test_provenance_values_are_the_three_allowed_tags(name, monkeypatch):
    for scenario in ("rich", "plain"):
        r = await _BUILDERS[name](monkeypatch, scenario).execute("p")
        assert set((r.provenance or {}).values()) <= {"verified", "inferred", "unknown"}


@pytest.mark.parametrize("name", CONFORMING)
@pytest.mark.asyncio
async def test_timeout_is_normalized(name, monkeypatch):
    r = await _BUILDERS[name](monkeypatch, "slow", timeout=0.05).execute("p")
    assert r.output == "" and "timed out" in r.error and not r.succeeded
    assert r.framework == name and _all_new_none(r)


@pytest.mark.parametrize("name", CONFORMING)
@pytest.mark.asyncio
async def test_exception_is_normalized(name, monkeypatch):
    r = await _BUILDERS[name](monkeypatch, "boom").execute("p")
    assert r.output == "" and r.error and not r.succeeded
    assert r.framework == name and _all_new_none(r)


@pytest.mark.parametrize("name", CONFORMING)
@pytest.mark.asyncio
async def test_empty_output_is_normalized(name, monkeypatch):
    r = await _BUILDERS[name](monkeypatch, "empty").execute("p")
    assert r.error == "provider returned no output text" and not r.succeeded
    assert r.framework == name
    _check_provenance(r)


@pytest.mark.parametrize("name", CONFORMING)
@pytest.mark.asyncio
async def test_serialization_round_trip(name, monkeypatch):
    for scenario in ("rich", "plain"):
        r = await _BUILDERS[name](monkeypatch, scenario).execute("p")
        again = AgentResponse.model_validate_json(r.model_dump_json())
        for field in ("output", "usage", "stop_reason", "error_code", "non_text_parts", "framework", "framework_version", "provenance"):
            assert getattr(again, field) == getattr(r, field), field
        assert [(c.name, c.arguments, c.call_id) for c in again.tool_calls or []] == \
               [(c.name, c.arguments, c.call_id) for c in r.tool_calls or []]
        assert all(c.result is None for c in again.tool_calls or [])
        _check_provenance(again)


# ── adapters still on the old behaviour: only "new fields are None" ──────

@pytest.mark.parametrize("name", OLD_BEHAVIOUR)
@pytest.mark.asyncio
async def test_old_adapters_leave_new_fields_none(name):
    r = await _old(name, "plain").execute("p")
    assert isinstance(r, AgentResponse) and r.output == "ok"
    assert _all_new_none(r), "this adapter has not been migrated; extend CONFORMING when it is"
    assert r.framework == name                       # execute() fills the framework name for every adapter


@pytest.mark.parametrize("name", OLD_BEHAVIOUR)
@pytest.mark.asyncio
async def test_old_adapters_still_normalize_errors(name):
    r = await _old(name, "boom").execute("p")
    assert r.output == "" and r.error and _all_new_none(r)
    r = await _old(name, "empty").execute("p")
    assert r.error == "provider returned no output text"
    r = await _old(name, "slow", timeout=0.05).execute("p")
    assert "timed out" in r.error and _all_new_none(r)


# ── the gap stays visible ────────────────────────────────────────────────

def test_every_exported_adapter_is_classified():
    ctor = {
        "HttpAdapter": lambda: HttpAdapter(base_url="http://x"),
        "LangChainAdapter": lambda: LangChainAdapter(runnable=object()),
        "GoogleADKAdapter": lambda: GoogleADKAdapter(agent=object()),
        "OpenAIAgentsAdapter": lambda: OpenAIAgentsAdapter(agent=object()),
        "CrewAIAdapter": lambda: CrewAIAdapter(crew=object()),
        "AutoGenAdapter": lambda: AutoGenAdapter(agent=object(), recipient=object()),
        "LlamaIndexAdapter": lambda: LlamaIndexAdapter(workflow=object()),
        "SemanticKernelAdapter": lambda: SemanticKernelAdapter(agent=object()),
    }
    exported = {n for n in agents_pkg.__all__ if n.endswith("Adapter") and n != "AgentAdapter"}
    assert exported == set(ctor), "a new adapter is exported: add it to this table and to CONFORMING or OLD_BEHAVIOUR"
    types = {ctor[n]().adapter_type for n in exported}
    assert types == set(CONFORMING) | set(OLD_BEHAVIOUR)
    assert not set(CONFORMING) & set(OLD_BEHAVIOUR)
