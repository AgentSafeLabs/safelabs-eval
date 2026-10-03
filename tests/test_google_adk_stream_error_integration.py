"""
tests/test_google_adk_stream_error_integration.py

Real-framework tests of how GoogleADKAdapter maps streaming (partial) and error
events: a real ``Agent`` and ``InMemoryRunner`` with a ``BaseLlm`` subclass
that yields scripted ``LlmResponse`` objects. No network, no API key.
Skipped when google-adk or google-genai is missing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("google.adk")
pytest.importorskip("google.genai")

from google.adk.agents import Agent  # noqa: E402
from google.adk.models.base_llm import BaseLlm  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from safelabs.agents import GoogleADKAdapter  # noqa: E402


def _usage(prompt, candidates, thoughts=None):
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt, candidates_token_count=candidates, thoughts_token_count=thoughts,
        total_token_count=prompt + candidates,
    )


def _text(text, **kw):
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]), **kw)


def _agent(*responses):
    class _Llm(BaseLlm):
        model: str = "fake-model"

        async def generate_content_async(self, llm_request, stream=False):
            for response in responses:
                yield response

    return Agent(name="a", model=_Llm(), instruction="x")


@pytest.mark.asyncio
async def test_partial_chunks_are_passed_through_but_not_counted():
    agent = _agent(
        _text("par", partial=True, usage_metadata=_usage(100, 100)),
        _text("final answer", usage_metadata=_usage(5, 2), finish_reason=types.FinishReason.STOP),
    )
    # the real runner does emit the partial event (partial=True, not final)
    runner = InMemoryRunner(agent=agent, app_name="t")
    session = await runner.session_service.create_session(app_name="t", user_id="u")
    events = [e async for e in runner.run_async(
        user_id="u", session_id=session.id, new_message=types.Content(role="user", parts=[types.Part(text="hi")]))]
    assert [(bool(e.partial), e.is_final_response()) for e in events] == [(True, False), (False, True)]

    r = await GoogleADKAdapter(agent=agent).execute("hi")
    assert r.output == "final answer"                               # the partial chunk is not part of the output
    assert r.usage == {"prompt_tokens": 5, "completion_tokens": 2, "reasoning_tokens": None}
    assert r.stop_reason == "STOP" and r.tool_calls == []


@pytest.mark.asyncio
async def test_error_event_without_text_sets_error_code_and_keeps_error_normalization():
    agent = _agent(LlmResponse(
        error_code="SAFETY", error_message="blocked by policy",
        finish_reason=types.FinishReason.SAFETY, usage_metadata=_usage(4, 0),
    ))
    r = await GoogleADKAdapter(agent=agent).execute("hi")
    assert r.output == ""
    assert r.error == "provider returned no output text"            # execute()'s rule, unchanged
    assert r.error_code == "SAFETY" and r.provenance["error_code"] == "verified"
    assert r.stop_reason == "SAFETY"
    assert r.usage == {"prompt_tokens": 4, "completion_tokens": 0, "reasoning_tokens": None}
    assert r.non_text_parts == []


@pytest.mark.asyncio
async def test_error_event_with_text_keeps_the_text_and_records_the_code():
    agent = _agent(_text("cut off here", error_code="MAX_TOKENS", error_message="cut",
                         finish_reason=types.FinishReason.MAX_TOKENS, usage_metadata=_usage(4, 9)))
    r = await GoogleADKAdapter(agent=agent).execute("hi")
    assert r.output == "cut off here" and r.error is None           # text present: not an adapter error
    assert r.error_code == "MAX_TOKENS" and r.stop_reason == "MAX_TOKENS"


@pytest.mark.asyncio
async def test_thought_tokens_are_summed_as_reasoning_tokens():
    agent = _agent(_text("answer", usage_metadata=_usage(6, 3, thoughts=11), finish_reason=types.FinishReason.STOP))
    r = await GoogleADKAdapter(agent=agent).execute("hi")
    assert r.usage == {"prompt_tokens": 6, "completion_tokens": 3, "reasoning_tokens": 11}
