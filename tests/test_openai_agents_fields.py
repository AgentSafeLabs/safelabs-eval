"""
tests/test_openai_agents_fields.py

Duck-typed (no framework) tests of OpenAIAgentsAdapter's optional fields: tool
calls from ``new_items``, results matched by call id, usage from the context
wrapper, and the None-versus-[] rule.
"""

from __future__ import annotations

from importlib import metadata

import pytest

from safelabs.agents import OpenAIAgentsAdapter


class _Item:
    def __init__(self, type, raw_item, output=None):
        self.type, self.raw_item, self.output = type, raw_item, output


class _Raw:
    def __init__(self, name=None, arguments=None, call_id=None, type=None, id=None):
        self.name, self.arguments, self.call_id, self.type, self.id = name, arguments, call_id, type, id


class _Usage:
    def __init__(self, i, o, reasoning=0):
        self.input_tokens, self.output_tokens = i, o
        self.output_tokens_details = type("D", (), {"reasoning_tokens": reasoning})()


class _Result:
    def __init__(self, final="ok", items=..., usage=...):
        self.final_output = final
        if items is not ...:
            self.new_items = items
        if usage is not ...:
            self.context_wrapper = type("W", (), {"usage": usage})()


class _Runner:
    def __init__(self, result): self._result = result
    async def run(self, agent, prompt): return self._result


def _run(result):
    return OpenAIAgentsAdapter(agent=None, runner=_Runner(result)).execute("p")


def _version():
    try:
        return metadata.version("openai-agents")
    except metadata.PackageNotFoundError:
        return None


@pytest.mark.asyncio
async def test_result_without_new_items_or_usage_exposes_nothing():
    r = await _run(_Result("ok"))
    assert r.output == "ok" and r.tool_calls is None and r.usage is None and r.stop_reason is None
    assert r.error_code is None and r.non_text_parts is None
    assert r.framework == "openai-agents" and r.framework_version == _version()


@pytest.mark.asyncio
async def test_empty_new_items_is_an_empty_list():
    r = await _run(_Result(items=[]))
    assert r.tool_calls == [] and r.provenance["tool_calls"] == "verified"


@pytest.mark.asyncio
async def test_call_and_output_matched_by_call_id_result_in_memory_only():
    items = [
        _Item("message_output_item", object()),
        _Item("tool_call_item", _Raw("lookup", '{"q": "x"}', "c1")),
        _Item("tool_call_item", _Raw("other", '{"k": 2}', "c2")),
        _Item("tool_call_output_item", {"call_id": "c2", "output": "two"}, output="two"),
        _Item("tool_call_output_item", {"call_id": "c1", "output": "one"}, output="one"),
    ]
    r = await _run(_Result(items=items))
    assert [(c.name, c.arguments, c.call_id, c.result) for c in r.tool_calls] == [
        ("lookup", {"q": "x"}, "c1", "one"), ("other", {"k": 2}, "c2", "two")]
    assert "result" not in r.model_dump()["tool_calls"][0]


@pytest.mark.asyncio
async def test_unparseable_arguments_and_unmatched_call():
    r = await _run(_Result(items=[_Item("tool_call_item", _Raw("f", "not json", "c9"))]))
    c = r.tool_calls[0]
    assert c.arguments is None and c.arguments_raw == "not json" and c.result is None


@pytest.mark.asyncio
async def test_hosted_tool_call_without_a_name_uses_its_type():
    r = await _run(_Result(items=[_Item("tool_call_item", _Raw(name=None, type="web_search_call", id="ws_1"))]))
    assert [(c.name, c.call_id) for c in r.tool_calls] == [("web_search_call", "ws_1")]


@pytest.mark.asyncio
async def test_handoff_items_are_not_tool_calls():
    r = await _run(_Result(items=[_Item("handoff_call_item", _Raw("transfer_to_x", "{}", "h1"))]))
    assert r.tool_calls == []


@pytest.mark.asyncio
async def test_usage_from_context_wrapper_including_reasoning():
    r = await _run(_Result(items=[], usage=_Usage(12, 6, reasoning=5)))
    assert r.usage == {"prompt_tokens": 12, "completion_tokens": 6, "reasoning_tokens": 5}
    assert r.provenance["usage"] == "verified"


@pytest.mark.asyncio
async def test_all_zero_usage_is_read_as_not_reported():
    r = await _run(_Result(items=[], usage=_Usage(0, 0)))
    assert r.usage is None and "usage" not in r.provenance
    assert r.stop_reason is None                  # the SDK exposes no finish reason
