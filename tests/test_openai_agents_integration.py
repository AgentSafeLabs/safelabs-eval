"""
tests/test_openai_agents_integration.py

Real-framework test for OpenAIAgentsAdapter: the real ``agents.Runner`` and a
real ``agents.Agent`` with a function tool, driven by a stub model. The SDK
ships no fake model, so this uses the narrowest stub of the interface it
defines: a subclass of ``agents.models.interface.Model`` implementing its two
abstract methods (``get_response`` returns a real ``agents.items.ModelResponse``
built from real ``openai.types.responses`` objects; ``stream_response`` is
unused). No network, no API key. Skipped when openai-agents is not installed.
"""

from __future__ import annotations

from importlib import metadata

import pytest

pytest.importorskip("agents")

import agents  # noqa: E402
from agents import Agent, function_tool  # noqa: E402
from agents.items import ModelResponse  # noqa: E402
from agents.models.interface import Model  # noqa: E402
from agents.usage import Usage  # noqa: E402
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText  # noqa: E402

from safelabs.agents import OpenAIAgentsAdapter  # noqa: E402

_VERSION = metadata.version("openai-agents")


@pytest.fixture(autouse=True)
def _no_tracing():
    agents.set_tracing_disabled(True)       # no trace export attempt
    yield
    agents.set_tracing_disabled(False)


def _message(text: str, response_id: str, usage: Usage) -> ModelResponse:
    msg = ResponseOutputMessage(
        id="m_" + response_id, role="assistant", status="completed", type="message",
        content=[ResponseOutputText(text=text, annotations=[], type="output_text")],
    )
    return ModelResponse(output=[msg], usage=usage, response_id=response_id)


def _tool_call(name: str, arguments: str, call_id: str, usage: Usage) -> ModelResponse:
    call = ResponseFunctionToolCall(arguments=arguments, call_id=call_id, name=name, type="function_call", id="fc_" + call_id)
    return ModelResponse(output=[call], usage=usage, response_id="r_" + call_id)


def _stub_model(*responses: ModelResponse) -> Model:
    queue = list(responses)

    class _StubModel(Model):
        async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs,
                               tracing, *, previous_response_id=None, conversation_id=None, prompt=None):
            return queue.pop(0)

        def stream_response(self, *args, **kwargs):
            raise NotImplementedError("the adapter does not stream")

    return _StubModel()


@function_tool
def lookup(q: str) -> str:
    """Look something up."""
    return "found"


@pytest.mark.asyncio
async def test_tool_call_result_usage_and_version_from_a_real_run():
    model = _stub_model(
        _tool_call("lookup", '{"q": "x"}', "c1", Usage(requests=1, input_tokens=3, output_tokens=4, total_tokens=7)),
        _message("done: found", "r2", Usage(requests=1, input_tokens=9, output_tokens=2, total_tokens=11)),
    )
    agent = Agent(name="a", instructions="be brief", model=model, tools=[lookup])
    r = await OpenAIAgentsAdapter(agent=agent).execute("hi")

    assert r.output == "done: found" and r.error is None
    assert len(r.tool_calls) == 1
    call = r.tool_calls[0]
    assert (call.name, call.arguments, call.call_id) == ("lookup", {"q": "x"}, "c1")
    assert call.result == "found"                               # tool output, in memory only
    assert "result" not in r.model_dump()["tool_calls"][0] and "found" not in repr(r.tool_calls[0])
    assert r.usage == {"prompt_tokens": 12, "completion_tokens": 6, "reasoning_tokens": 0}
    assert r.stop_reason is None                                # the SDK exposes none
    assert r.framework == "openai-agents" and r.framework_version == _VERSION
    assert r.provenance == {"tool_calls": "verified", "usage": "verified", "framework_version": "verified"}


@pytest.mark.asyncio
async def test_run_without_tool_use_gives_empty_list():
    model = _stub_model(_message("hello", "r1", Usage(requests=1, input_tokens=2, output_tokens=1, total_tokens=3)))
    r = await OpenAIAgentsAdapter(agent=Agent(name="a", instructions="x", model=model)).execute("hi")
    assert r.output == "hello" and r.tool_calls == []
    assert r.usage == {"prompt_tokens": 2, "completion_tokens": 1, "reasoning_tokens": 0}


@pytest.mark.asyncio
async def test_model_reporting_no_usage_leaves_usage_none():
    model = _stub_model(_message("hello", "r1", Usage()))           # SDK default: all zeros
    r = await OpenAIAgentsAdapter(agent=Agent(name="a", instructions="x", model=model)).execute("hi")
    assert r.output == "hello" and r.usage is None and r.tool_calls == []


@pytest.mark.asyncio
async def test_runner_exception_is_normalized_with_no_new_fields():
    class _Boom(Model):
        async def get_response(self, *a, **k):
            raise RuntimeError("provider down")

        def stream_response(self, *a, **k):
            raise NotImplementedError

    r = await OpenAIAgentsAdapter(agent=Agent(name="a", instructions="x", model=_Boom())).execute("hi")
    assert r.output == "" and "provider down" in r.error
    assert r.framework == "openai-agents" and r.tool_calls is None and r.usage is None and r.framework_version is None
