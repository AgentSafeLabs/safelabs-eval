"""
tests/test_google_adk_adapter_fields.py

Duck-typed (no framework) tests of how GoogleADKAdapter collects the optional
AgentResponse fields from an event stream: partial events are skipped,
usage is summed, results are matched to calls, and events that do not expose
the ADK methods leave the fields None.
"""

from __future__ import annotations

import pytest

from safelabs.agents.google_adk_adapter import GoogleADKAdapter


class _Fc:
    def __init__(self, name, args=None, id=None):
        self.name, self.args, self.id = name, args, id


class _Fr:
    def __init__(self, name, response=None, id=None):
        self.name, self.response, self.id = name, response, id


class _Part:
    def __init__(self, text=None, **flags):
        self.text = text
        for k, v in flags.items():
            setattr(self, k, v)


class _Usage:
    def __init__(self, prompt=None, candidates=None, thoughts=None):
        self.prompt_token_count, self.candidates_token_count, self.thoughts_token_count = prompt, candidates, thoughts


class _Reason:
    value = "STOP"


class _Content:
    def __init__(self, parts):
        self.parts = parts


class _Event:
    def __init__(self, text=None, *, final=True, calls=None, responses=None, usage=None, finish=None,
                 error_code=None, partial=False, parts=None, expose=True):
        self._final = final
        self.content = _Content(parts if parts is not None else [_Part(text)])
        self.usage_metadata, self.finish_reason, self.error_code, self.partial = usage, finish, error_code, partial
        if expose:
            self.get_function_calls = lambda: calls or []
            self.get_function_responses = lambda: responses or []

    def is_final_response(self):
        return self._final


class _Session:
    id = "s1"


class _Sessions:
    async def create_session(self, **kw):
        return _Session()


class _Runner:
    app_name = "t"
    session_service = _Sessions()

    def __init__(self, events):
        self._events = events

    async def run_async(self, **kw):
        for e in self._events:
            yield e


def _run(events):
    adapter = GoogleADKAdapter(agent=None, runner=_Runner(events), content_factory=lambda p: p)
    return adapter.execute("hi")


@pytest.mark.asyncio
async def test_events_without_adk_methods_leave_fields_none():
    r = await _run([_Event("hello", expose=False)])
    assert r.output == "hello"
    assert r.tool_calls is None and r.usage is None and r.stop_reason is None and r.error_code is None
    assert r.non_text_parts == []                  # a final event was seen and its parts inspected
    assert r.provenance["non_text_parts"] == "inferred"
    assert "tool_calls" not in r.provenance


@pytest.mark.asyncio
async def test_no_events_at_all_exposes_nothing():
    r = await _run([])
    assert r.tool_calls is None and r.non_text_parts is None and r.usage is None


@pytest.mark.asyncio
async def test_events_without_calls_give_empty_list():
    r = await _run([_Event("done")])
    assert r.tool_calls == [] and r.provenance["tool_calls"] == "verified"


@pytest.mark.asyncio
async def test_call_and_response_matched_by_id_result_in_memory_only():
    events = [
        _Event(None, final=False, calls=[_Fc("lookup", {"q": "x"}, "a"), _Fc("lookup", {"q": "y"}, "b")]),
        _Event(None, final=False, responses=[_Fr("lookup", {"v": 2}, "b"), _Fr("lookup", {"v": 1}, "a")]),
        _Event("done"),
    ]
    r = await _run(events)
    assert [(c.call_id, c.arguments) for c in r.tool_calls] == [("a", {"q": "x"}), ("b", {"q": "y"})]
    assert [c.result for c in r.tool_calls] == ['{"v": 1}', '{"v": 2}']
    assert "result" not in r.model_dump()["tool_calls"][0]


@pytest.mark.asyncio
async def test_response_matched_by_name_when_ids_are_absent():
    events = [_Event(None, final=False, calls=[_Fc("f", {})]), _Event(None, final=False, responses=[_Fr("f", {"ok": True})]), _Event("x")]
    r = await _run(events)
    assert r.tool_calls[0].call_id is None and r.tool_calls[0].result == '{"ok": true}'


@pytest.mark.asyncio
async def test_usage_summed_over_non_partial_events_only():
    events = [
        _Event("c", partial=True, final=False, usage=_Usage(100, 100, 100)),      # streaming chunk: skipped
        _Event(None, final=False, usage=_Usage(3, 4)),
        _Event("done", usage=_Usage(9, 2, 5), finish=_Reason()),
    ]
    r = await _run(events)
    assert r.usage == {"prompt_tokens": 12, "completion_tokens": 6, "reasoning_tokens": 5}
    assert r.stop_reason == "STOP"
    assert r.provenance["usage"] == "inferred" and r.provenance["stop_reason"] == "verified"


@pytest.mark.asyncio
async def test_usage_none_when_no_event_has_usage():
    r = await _run([_Event("done", usage=None)])
    assert r.usage is None and "usage" not in r.provenance


@pytest.mark.asyncio
async def test_error_code_is_captured_and_error_text_unchanged():
    r = await _run([_Event(None, parts=[], error_code="SAFETY")])
    assert r.error_code == "SAFETY" and r.provenance["error_code"] == "verified"
    assert r.error == "provider returned no output text"       # execute()'s normalization is unchanged


@pytest.mark.asyncio
async def test_non_text_part_kinds_from_the_final_event():
    parts = [_Part("answer"), _Part(None, thought=True), _Part(None, inline_data=object())]
    r = await _run([_Event(None, parts=parts)])
    assert r.output == "answer"
    assert r.non_text_parts == ["thought", "inline_data"]


@pytest.mark.asyncio
async def test_framework_fields_set():
    r = await _run([_Event("x")])
    assert r.framework == "google-adk"
    from importlib import metadata
    try:
        expected = metadata.version("google-adk")
    except metadata.PackageNotFoundError:
        expected = None
    assert r.framework_version == expected
