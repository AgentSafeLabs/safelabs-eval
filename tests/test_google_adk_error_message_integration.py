"""
tests/test_google_adk_error_message_integration.py

How GoogleADKAdapter captures ``Event.error_message`` (1A.1 wave 2b): a real
``Agent`` and ``InMemoryRunner`` with a ``BaseLlm`` subclass that yields scripted
``LlmResponse`` objects. No network, no API key. Skipped when google-adk or
google-genai is missing.

Observed with google-adk 2.9.0: the runner emits an event for an ``LlmResponse``
that has an ``error_code`` (it is a final response) and keeps going if the model
yields more responses; an ``LlmResponse`` with only an ``error_message`` and no
``error_code`` produces no event at all, so that case is covered with a
duck-typed event on the accumulator instead.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("google.adk")
pytest.importorskip("google.genai")

from google.adk.agents import Agent  # noqa: E402
from google.adk.models.base_llm import BaseLlm  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.genai import types  # noqa: E402

from safelabs.agents import GoogleADKAdapter  # noqa: E402
from safelabs.agents.google_adk_adapter import _EventAccumulator  # noqa: E402


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
async def test_run_ending_in_error_event_sets_error_from_error_message():
    r = await GoogleADKAdapter(agent=_agent(LlmResponse(error_code="SAFETY", error_message="blocked by policy"))).execute("hi")
    assert r.output == "" and "blocked" not in r.output
    assert r.error == "blocked by policy" and not r.succeeded
    assert r.error_code == "SAFETY" and r.provenance["error_code"] == "verified"
    assert r.metadata == {
        "adk_error_events": [{"error_code": "SAFETY", "error_message": "blocked by policy"}],
        "adk_error_source": "event.error_message",
        "adk_error_provenance": "verified",
    }
    assert r.framework == "google-adk"


@pytest.mark.asyncio
async def test_error_code_without_message_keeps_the_generic_error():
    r = await GoogleADKAdapter(agent=_agent(LlmResponse(error_code="OTHER"))).execute("hi")
    assert r.output == "" and r.error == "provider returned no output text"        # base-class rule, unchanged
    assert r.error_code == "OTHER"
    assert r.metadata == {"adk_error_events": [{"error_code": "OTHER", "error_message": None}]}   # no source/provenance keys


@pytest.mark.asyncio
async def test_two_error_events_are_listed_in_order_and_the_last_message_is_the_error():
    r = await GoogleADKAdapter(agent=_agent(
        LlmResponse(error_code="E1", error_message="first"), LlmResponse(error_code="E2", error_message="second"),
    )).execute("hi")
    assert r.error == "second" and r.error_code == "E2"
    assert [e["error_message"] for e in r.metadata["adk_error_events"]] == ["first", "second"]


@pytest.mark.asyncio
async def test_successful_run_with_an_earlier_error_event_keeps_output_and_records_metadata():
    r = await GoogleADKAdapter(agent=_agent(
        LlmResponse(error_code="TRANSIENT", error_message="retrying"), _text("the answer"),
    )).execute("hi")
    assert r.output == "the answer" and r.error is None and r.succeeded
    assert "retrying" not in r.output
    assert r.metadata == {"adk_error_events": [{"error_code": "TRANSIENT", "error_message": "retrying"}]}   # no error source keys
    assert r.error_code == "TRANSIENT"                       # existing mapping, unchanged


@pytest.mark.asyncio
async def test_successful_run_with_a_later_error_event_keeps_output():
    r = await GoogleADKAdapter(agent=_agent(
        _text("the answer"), LlmResponse(error_code="LATE", error_message="after the text"),
    )).execute("hi")
    assert r.output == "the answer" and r.error is None
    assert r.metadata == {"adk_error_events": [{"error_code": "LATE", "error_message": "after the text"}]}


@pytest.mark.asyncio
async def test_run_without_error_events_has_no_metadata():
    r = await GoogleADKAdapter(agent=_agent(_text("plain"))).execute("hi")
    assert r.output == "plain" and r.error is None and r.metadata is None


def test_accumulator_reads_a_message_without_a_code_and_a_partial_event():
    acc = _EventAccumulator()
    acc.add(SimpleNamespace(error_message="only a message", error_code=None, partial=False, is_final_response=lambda: True, content=None))
    acc.add(SimpleNamespace(error_message="partial chunk", error_code="P", partial=True, is_final_response=lambda: False, content=None))
    assert acc.error_events == [
        {"error_code": None, "error_message": "only a message"},
        {"error_code": "P", "error_message": "partial chunk"},
    ]
    assert acc.last_error_message == "partial chunk"


def test_accumulator_ignores_events_with_neither_field():
    acc = _EventAccumulator()
    acc.add(SimpleNamespace(is_final_response=lambda: True, content=None))
    assert acc.error_events == [] and acc.last_error_message is None
