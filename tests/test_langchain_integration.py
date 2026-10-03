"""
tests/test_langchain_integration.py

Real-framework test for LangChainAdapter: real langchain-core objects driven
by the installed fake chat model ``GenericFakeChatModel``
(langchain_core.language_models.fake_chat_models). No network, no API key.
Skipped when langchain-core is not installed.
"""

from __future__ import annotations

from importlib import metadata

import pytest

pytest.importorskip("langchain_core")

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402

from safelabs.agents import LangChainAdapter  # noqa: E402

_VERSION = metadata.version("langchain-core")


def _adapter(*messages: AIMessage, **kw) -> LangChainAdapter:
    return LangChainAdapter(runnable=GenericFakeChatModel(messages=iter(messages)), input_key=None, **kw)


@pytest.mark.asyncio
async def test_tool_call_usage_and_stop_reason_from_a_real_aimessage():
    msg = AIMessage(
        content="calling",
        tool_calls=[{"name": "search", "args": {"q": "x"}, "id": "call_1", "type": "tool_call"}],
        usage_metadata={"input_tokens": 3, "output_tokens": 5, "total_tokens": 8, "output_token_details": {"reasoning": 2}},
        response_metadata={"finish_reason": "tool_calls"},
    )
    r = await _adapter(msg).execute("hi")
    assert r.output == "calling" and r.error is None
    assert [(c.name, c.arguments, c.call_id) for c in r.tool_calls] == [("search", {"q": "x"}, "call_1")]
    assert r.usage == {"prompt_tokens": 3, "completion_tokens": 5, "reasoning_tokens": 2}
    assert r.stop_reason == "tool_calls"
    assert r.non_text_parts == []
    assert r.framework == "langchain" and r.framework_version == _VERSION
    assert r.error_code is None
    assert r.provenance == {
        "tool_calls": "verified", "usage": "verified", "stop_reason": "inferred",
        "non_text_parts": "inferred", "framework_version": "verified",
    }


@pytest.mark.asyncio
async def test_message_without_tool_calls_gives_empty_list_not_none():
    r = await _adapter(AIMessage(content="plain answer")).execute("hi")
    assert r.output == "plain answer"
    assert r.tool_calls == []                      # exposed, zero calls
    assert r.usage is None and r.stop_reason is None   # fake model supplied neither
    assert r.provenance["tool_calls"] == "verified"
    assert "usage" not in r.provenance and "stop_reason" not in r.provenance


@pytest.mark.asyncio
async def test_anthropic_style_stop_reason_key():
    r = await _adapter(AIMessage(content="x", response_metadata={"stop_reason": "end_turn"})).execute("hi")
    assert r.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_content_blocks_report_non_text_part_kinds():
    msg = AIMessage(content=[{"type": "reasoning", "summary": []}, {"type": "text", "text": "answer"}])
    r = await _adapter(msg).execute("hi")
    assert r.output == "answer"
    assert r.non_text_parts == ["reasoning"]
    assert r.provenance["non_text_parts"] == "inferred"


@pytest.mark.asyncio
async def test_default_input_key_is_rejected_by_a_bare_chat_model():
    """Documents the input_key behaviour: the default sends a dict, which a bare chat model rejects."""
    adapter = LangChainAdapter(runnable=GenericFakeChatModel(messages=iter([AIMessage(content="x")])))
    r = await adapter.execute("hi")
    assert r.output == "" and "Invalid input type" in r.error
    assert r.framework == "langchain"
    assert r.tool_calls is None                    # error path: nothing exposed


@pytest.mark.asyncio
async def test_string_result_leaves_message_fields_none():
    from langchain_core.runnables import RunnableLambda
    r = await LangChainAdapter(runnable=RunnableLambda(lambda x: "just text"), input_key=None).execute("hi")
    assert r.output == "just text"
    assert r.tool_calls is None and r.usage is None and r.stop_reason is None and r.non_text_parts is None
    assert r.framework_version == _VERSION         # version is known even when the result is not a message
    assert r.provenance == {"framework_version": "verified"}
