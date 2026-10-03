"""
tests/test_google_adk_integration.py

Real-framework test for GoogleADKAdapter: a real google-adk ``Agent`` and
``InMemoryRunner`` with a ``BaseLlm`` subclass (the single abstract method is
``generate_content_async``) that yields scripted ``LlmResponse`` objects. No
network, no API key. Skipped when google-adk or google-genai is missing.
"""

from __future__ import annotations

from importlib import metadata

import pytest

pytest.importorskip("google.adk")
pytest.importorskip("google.genai")

from google.adk.agents import Agent  # noqa: E402
from google.adk.models.base_llm import BaseLlm  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.genai import types  # noqa: E402

from safelabs.agents import GoogleADKAdapter  # noqa: E402

_VERSION = metadata.version("google-adk")


def _usage(prompt, candidates, thoughts=None):
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt, candidates_token_count=candidates,
        thoughts_token_count=thoughts, total_token_count=prompt + candidates + (thoughts or 0),
    )


def _fake_llm(script):
    """A BaseLlm that yields script[i] on the i-th model call (a list of lists of LlmResponse)."""
    state = {"calls": 0}

    class _FakeLlm(BaseLlm):
        model: str = "fake-model"

        async def generate_content_async(self, llm_request, stream=False):
            responses = script[min(state["calls"], len(script) - 1)]
            state["calls"] += 1
            for response in responses:
                yield response

    return _FakeLlm(), state


def lookup(q: str) -> str:
    """Look something up."""
    return "found"


def _text(text, **kw):
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]), **kw)


def _call(name, args, call_id, **kw):
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args, id=call_id))]), **kw)


@pytest.mark.asyncio
async def test_function_call_response_and_final_text_map_to_the_response():
    llm, state = _fake_llm([
        [_call("lookup", {"q": "x"}, "fc1", usage_metadata=_usage(3, 4), finish_reason=types.FinishReason.STOP)],
        [_text("done: found", usage_metadata=_usage(9, 2, 5), finish_reason=types.FinishReason.STOP)],
    ])
    agent = Agent(name="a", model=llm, instruction="be brief", tools=[lookup])
    r = await GoogleADKAdapter(agent=agent).execute("hi")

    assert state["calls"] == 2                                  # call, then final answer
    assert r.output == "done: found" and r.error is None
    assert len(r.tool_calls) == 1
    call = r.tool_calls[0]
    assert (call.name, call.arguments, call.call_id) == ("lookup", {"q": "x"}, "fc1")
    assert call.result is not None and "found" in call.result  # function response, in memory
    assert "result" not in r.model_dump()["tool_calls"][0]
    assert r.usage == {"prompt_tokens": 12, "completion_tokens": 6, "reasoning_tokens": 5}
    assert r.stop_reason == "STOP"
    assert r.non_text_parts == []
    assert r.error_code is None
    assert r.framework == "google-adk" and r.framework_version == _VERSION
    assert r.provenance == {
        "tool_calls": "verified", "usage": "inferred", "stop_reason": "verified",
        "non_text_parts": "inferred", "framework_version": "verified",
    }


@pytest.mark.asyncio
async def test_agent_without_tool_use_gives_empty_tool_calls_not_none():
    llm, _ = _fake_llm([[_text("hello", usage_metadata=_usage(2, 1), finish_reason=types.FinishReason.MAX_TOKENS)]])
    r = await GoogleADKAdapter(agent=Agent(name="a", model=llm, instruction="x")).execute("hi")
    assert r.output == "hello"
    assert r.tool_calls == []
    assert r.stop_reason == "MAX_TOKENS"
    assert r.usage == {"prompt_tokens": 2, "completion_tokens": 1, "reasoning_tokens": None}


@pytest.mark.asyncio
async def test_event_stream_matches_what_the_runner_emits():
    """Pins the mapping to the real event shapes: call event, response event, final event."""
    from google.adk.runners import InMemoryRunner

    llm, _ = _fake_llm([[_call("lookup", {"q": "x"}, "fc1")], [_text("ok")]])
    runner = InMemoryRunner(agent=Agent(name="a", model=llm, instruction="x", tools=[lookup]), app_name="t")
    session = await runner.session_service.create_session(app_name="t", user_id="u")
    events = [e async for e in runner.run_async(
        user_id="u", session_id=session.id, new_message=types.Content(role="user", parts=[types.Part(text="hi")]))]

    shape = [(bool(e.get_function_calls()), bool(e.get_function_responses()), e.is_final_response()) for e in events]
    assert shape == [(True, False, False), (False, True, False), (False, False, True)]
