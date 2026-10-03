"""
tests/test_agent_response_fields.py

Unit tests for the optional AgentResponse fields (tool_calls, usage,
stop_reason, error_code, non_text_parts, framework, framework_version,
provenance), the ToolCall type, and AgentAdapter.execute()'s handling of them.

Rule under test: None = not exposed; [] = exposed, zero. Old constructors,
old JSON and the old five-field dump must keep working unchanged.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import PROVENANCE_FIELDS, AgentResponse, ToolCall, normalize_usage

_OLD_KEYS = {"output", "latency_ms", "error", "raw", "metadata"}
_NEW_KEYS = {
    "tool_calls", "usage", "stop_reason", "error_code", "non_text_parts",
    "framework", "framework_version", "provenance",
}


# ── defaults and backward compatibility ──────────────────────────────────

def test_new_fields_default_to_none():
    r = AgentResponse(output="x")
    assert all(getattr(r, k) is None for k in _NEW_KEYS)


def test_old_construction_and_dump_unchanged():
    r = AgentResponse(output="hello", latency_ms=10.0)
    assert r.succeeded
    assert set(r.model_dump(exclude_none=True)) == {"output", "latency_ms"}
    assert set(AgentResponse.model_fields) == _OLD_KEYS | _NEW_KEYS


def test_old_json_still_loads():
    old = json.dumps({"output": "x", "latency_ms": 1.5, "error": None, "raw": {"a": 1}, "metadata": {"status_code": 200}})
    r = AgentResponse.model_validate_json(old)
    assert r.output == "x" and r.raw == {"a": 1}
    assert r.tool_calls is None and r.provenance is None


def test_new_response_round_trips_without_tool_result():
    call = ToolCall.from_arguments("search", {"q": "x"}, call_id="c1", result="SECRET RESULT")
    r = AgentResponse(
        output="ok", tool_calls=[call], usage={"prompt_tokens": 1, "completion_tokens": 2, "reasoning_tokens": None},
        stop_reason="stop", framework="fake", framework_version="1.0",
        provenance={"tool_calls": "verified", "usage": "inferred", "stop_reason": "verified", "framework_version": "verified"},
    )
    dumped = r.model_dump_json()
    assert "SECRET RESULT" not in dumped            # ToolCall.result is never serialized
    again = AgentResponse.model_validate_json(dumped)
    assert again.tool_calls[0].name == "search" and again.tool_calls[0].arguments == {"q": "x"}
    assert again.tool_calls[0].result is None
    assert again.usage == r.usage and again.provenance == r.provenance


# ── ToolCall ─────────────────────────────────────────────────────────────

def test_toolcall_from_dict_arguments():
    c = ToolCall.from_arguments("f", {"a": 1}, call_id="id1")
    assert (c.name, c.arguments, c.arguments_raw, c.call_id, c.result) == ("f", {"a": 1}, None, "id1", None)


def test_toolcall_from_json_string_arguments():
    c = ToolCall.from_arguments("f", '{"a": [1, 2]}')
    assert c.arguments == {"a": [1, 2]} and c.arguments_raw is None


@pytest.mark.parametrize("text", ["not json", "[1, 2]", "42", "\"str\""])
def test_toolcall_unparseable_or_non_dict_arguments_go_to_raw(text):
    c = ToolCall.from_arguments("f", text)
    assert c.arguments is None and c.arguments_raw == text


def test_toolcall_no_arguments():
    c = ToolCall.from_arguments("f")
    assert c.arguments is None and c.arguments_raw is None


def test_toolcall_result_is_in_memory_only():
    c = ToolCall.from_arguments("f", {}, result="top secret")
    assert c.result == "top secret"
    assert "result" not in c.model_dump()
    assert "top secret" not in c.model_dump_json()
    assert "top secret" not in repr(c)


# ── usage ────────────────────────────────────────────────────────────────

def test_normalize_usage_keeps_ints_and_nones():
    assert normalize_usage(3, 5, None) == {"prompt_tokens": 3, "completion_tokens": 5, "reasoning_tokens": None}


def test_normalize_usage_all_missing_is_none():
    assert normalize_usage() is None
    assert normalize_usage("3", None, 4.5) is None
    assert normalize_usage(True, False, None) is None      # bools are not token counts


# ── None vs [] and provenance ────────────────────────────────────────────

def test_empty_list_counts_as_exposed_and_needs_provenance():
    ok = AgentResponse(output="x", tool_calls=[], provenance={"tool_calls": "verified"})
    assert ok.tool_calls == []
    with pytest.raises(ValidationError):
        AgentResponse(output="x", tool_calls=[])            # [] is "exposed", so it needs a tag
    with pytest.raises(ValidationError):
        AgentResponse(output="x", non_text_parts=[])


def test_none_field_may_be_unknown_or_absent_never_verified():
    AgentResponse(output="x", provenance={"usage": "unknown"})
    AgentResponse(output="x", provenance={})
    with pytest.raises(ValidationError):
        AgentResponse(output="x", provenance={"usage": "verified"})
    with pytest.raises(ValidationError):
        AgentResponse(output="x", provenance={"usage": "inferred"})


def test_set_field_needs_verified_or_inferred():
    for tag in ("verified", "inferred"):
        AgentResponse(output="x", stop_reason="stop", provenance={"stop_reason": tag})
    with pytest.raises(ValidationError):
        AgentResponse(output="x", stop_reason="stop", provenance={"stop_reason": "unknown"})
    with pytest.raises(ValidationError):
        AgentResponse(output="x", stop_reason="stop")


def test_unknown_provenance_key_rejected():
    with pytest.raises(ValidationError):
        AgentResponse(output="x", provenance={"framework": "verified"})   # framework is not a tracked field
    with pytest.raises(ValidationError):
        AgentResponse(output="x", provenance={"usage": "maybe"})


def test_tracked_field_list_matches_design():
    assert set(PROVENANCE_FIELDS) == {"tool_calls", "usage", "stop_reason", "error_code", "non_text_parts", "framework_version"}


# ── AgentAdapter.execute() ───────────────────────────────────────────────

class _Plain(AgentAdapter):
    @property
    def adapter_type(self) -> str:
        return "plain"

    async def _execute(self, prompt: str) -> AgentResponse:
        return AgentResponse(output="hi", latency_ms=1.0)


class _Declares(AgentAdapter):
    @property
    def adapter_type(self) -> str:
        return "declares"

    async def _execute(self, prompt: str) -> AgentResponse:
        return AgentResponse(output="hi", framework="custom-name")


class _Boom(AgentAdapter):
    @property
    def adapter_type(self) -> str:
        return "boom"

    async def _execute(self, prompt: str) -> AgentResponse:
        raise RuntimeError("provider down")


class _Slow(AgentAdapter):
    @property
    def adapter_type(self) -> str:
        return "slow"

    async def _execute(self, prompt: str) -> AgentResponse:
        import asyncio
        await asyncio.sleep(5)
        return AgentResponse(output="late")


@pytest.mark.asyncio
async def test_execute_fills_framework_only():
    r = await _Plain().execute("p")
    assert r.framework == "plain"
    assert all(getattr(r, k) is None for k in _NEW_KEYS - {"framework"})


@pytest.mark.asyncio
async def test_execute_keeps_adapter_supplied_framework():
    assert (await _Declares().execute("p")).framework == "custom-name"


@pytest.mark.asyncio
async def test_exception_path_has_none_fields_and_framework():
    r = await _Boom().execute("p")
    assert r.error == "provider down" and r.framework == "boom"
    assert all(getattr(r, k) is None for k in _NEW_KEYS - {"framework"})


@pytest.mark.asyncio
async def test_timeout_path_has_none_fields_and_framework():
    r = await _Slow(timeout=0.01).execute("p")
    assert "timed out" in r.error and r.framework == "slow"
    assert all(getattr(r, k) is None for k in _NEW_KEYS - {"framework"})


@pytest.mark.asyncio
async def test_empty_output_rule_unchanged():
    class _Empty(_Plain):
        async def _execute(self, prompt: str) -> AgentResponse:
            return AgentResponse(output="  ")
    r = await _Empty().execute("p")
    assert r.error == "provider returned no output text" and r.framework == "plain"
