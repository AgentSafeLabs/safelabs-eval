"""
safelabs/agents/google_adk_adapter.py

Adapter for Google ADK (Agent Development Kit) agents.

Install optional dependency first:
    pip install "safelabs-eval[google-adk]"

Example
-------
.. code-block:: python

    from google.adk.agents import Agent
    from safelabs.agents import GoogleADKAdapter

    agent = Agent(
        name="assistant",
        model="gemini-2.5-flash",
        instruction="You are a helpful assistant.",
    )
    adapter = GoogleADKAdapter(agent=agent)
    response = await adapter.execute("Ignore previous instructions.")

API note
--------
This adapter targets ``google-adk >= 1.0`` (verified against v2.8.0,
2026-09). ADK has no "call the agent with a string" entry point — a run
always goes through a ``Runner`` bound to a session:

* ``InMemoryRunner(agent=..., app_name=...)`` — a ``Runner`` subclass with
  an in-process session store.  Exposes ``.session_service`` and
  ``.app_name``.
* ``await runner.session_service.create_session(app_name=..., user_id=...)``
  — async; returns a ``Session`` whose ``.id`` identifies the conversation.
* ``runner.run_async(*, user_id, session_id, new_message)`` — async
  generator of ``Event`` objects.  ``new_message`` is a
  ``google.genai.types.Content`` (``role="user"``, one text ``Part``).
* ``event.is_final_response()`` — ``True`` for a complete, user-facing
  reply (no pending function call/response, not partial).  The text lives
  at ``event.content.parts[*].text``; non-text parts (function calls,
  thoughts, inline data) carry no ``.text`` and are skipped.

``run_async`` and ``Event.is_final_response()`` have carried this shape
since ADK 1.0.0.

Because ``Runner``/``InMemoryRunner`` are classes (not a method on the
agent) and ``new_message`` must be a real ``types.Content``, neither can
be fully duck-typed away.  Both imports are deferred to the first
``_execute()`` call so that importing ``safelabs.agents`` does not raise
``ImportError`` when ``google-adk`` is not installed.  For testing
without a real install, inject a duck-typed ``runner`` and a
``content_factory``; both lazy imports are then skipped.

Optional AgentResponse fields (verified against google-adk 2.9.0 and
google-genai 2.23.0 by running a real ``InMemoryRunner`` with a fake model):

* ``tool_calls`` - every non-partial event's ``get_function_calls()``, with
  ``ToolCall.result`` filled from the matching ``get_function_responses()``
  (matched by call id, else by name; result is in memory only). ``[]`` when
  events were seen and none carried a call; ``None`` when the events do not
  expose ``get_function_calls`` (duck-typed fakes). provenance: verified.
* ``usage`` - sum over non-partial events of ``usage_metadata``
  ``prompt_token_count`` / ``candidates_token_count`` /
  ``thoughts_token_count``; ADK reports thoughts separately from candidates,
  so ``completion_tokens`` excludes ``reasoning_tokens``. inferred.
* ``stop_reason`` - the final event's ``finish_reason`` value (for example
  ``STOP``), unmodified. verified.
* ``error_code`` - the last ``error_code`` any event carried. ``error`` itself
  is unchanged. verified.
* ``non_text_parts`` - kinds of non-text parts in the final event (for
  example ``thought``); ``None`` when no final event arrived. inferred.
* ``framework_version`` - installed ``google-adk`` version. verified.

Always verify the exact API against your installed version's docs before
trusting adapter output in production.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from importlib import metadata as _metadata

from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse, ToolCall, normalize_usage


class GoogleADKAdapter(AgentAdapter):
    """
    Adapter for Google ADK agents (``google-adk >= 1.0``).

    A fresh session is created for every probe so adversarial prompts
    never share conversation state.  ``Runner.run_async()`` is
    async-native; no thread-pool offload is needed (unlike CrewAI's
    synchronous ``Crew.kickoff()``).

    Parameters
    ----------
    agent:
        The ADK agent under test (``Agent`` / ``LlmAgent`` or any
        ``BaseAgent``).  Ignored when ``runner`` is supplied.
    app_name:
        Application namespace for the runner and its sessions
        (default ``"safelabs_eval"``).
    user_id:
        User identifier passed to ``create_session`` / ``run_async``
        (default ``"safelabs"``).
    runner:
        Optional pre-built runner (or duck-typed fake).  When ``None``
        (the default), ``google.adk.runners.InMemoryRunner`` is imported
        lazily and constructed on the first ``_execute()`` call.
    content_factory:
        Optional callable ``str -> new_message`` used to build the
        ``run_async`` payload.  When ``None`` (the default),
        ``google.genai.types`` is imported lazily and the prompt is
        wrapped as ``Content(role="user", parts=[Part(text=prompt)])``.
        A test seam — real use never sets this.
    timeout:
        Per-request timeout in seconds (default 30). Enforced by the
        base class.

    Example
    -------
    .. code-block:: python

        adapter = GoogleADKAdapter(agent=my_agent)
        response = await adapter.execute("Ignore previous instructions.")
    """

    def __init__(
        self,
        agent: object,
        *,
        app_name: str = "safelabs_eval",
        user_id: str = "safelabs",
        runner: object | None = None,
        content_factory: Callable[[str], object] | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self._agent           = agent
        self._app_name        = app_name
        self._user_id         = user_id
        self._runner          = runner
        self._content_factory = content_factory

    @property
    def adapter_type(self) -> str:
        return "google-adk"

    def _get_runner(self) -> object:
        if self._runner is not None:
            return self._runner
        # InMemoryRunner lives in google.adk.runners, not on the agent — lazy
        # import keeps module-level import clean when google-adk is absent.
        from google.adk.runners import InMemoryRunner  # noqa: PLC0415

        self._runner = InMemoryRunner(agent=self._agent, app_name=self._app_name)
        return self._runner

    def _build_message(self, prompt: str) -> object:
        if self._content_factory is not None:
            return self._content_factory(prompt)
        # types.Content is required by run_async(); lazy import for the same
        # reason as the runner above.
        from google.genai import types  # noqa: PLC0415

        return types.Content(role="user", parts=[types.Part(text=prompt)])

    async def _execute(self, prompt: str) -> AgentResponse:
        runner  = self._get_runner()
        message = self._build_message(prompt)

        t0 = time.perf_counter()
        session = await runner.session_service.create_session(
            app_name=getattr(runner, "app_name", self._app_name),
            user_id=self._user_id,
        )
        final_text = ""
        acc = _EventAccumulator()
        async for event in runner.run_async(
            user_id=self._user_id,
            session_id=session.id,
            new_message=message,
        ):
            acc.add(event)
            # A single agent under test emits one final-response event; if
            # more than one arrives (e.g. after a tool round-trip) the last
            # non-empty one is the answer. Intermediate events — function
            # calls/responses, partial chunks — return False here.
            if not event.is_final_response():
                continue
            text = self._text_from_content(getattr(event, "content", None))
            if text:
                final_text = text
        latency_ms = (time.perf_counter() - t0) * 1000
        return AgentResponse(output=final_text, latency_ms=latency_ms, **acc.fields())

    @staticmethod
    def _text_from_content(content: object) -> str:
        """Join the text of every text-bearing ``Part`` in an event's ``content``.

        ``content`` is a ``google.genai.types.Content`` whose ``.parts`` is
        a list of ``Part`` objects.  A text part carries a ``str`` in
        ``.text``; function-call, function-response, thought, and
        inline-data parts leave ``.text`` as ``None`` and are skipped —
        mirroring ``LangChainAdapter._text_from_content`` so downstream
        Scorer regexes never see a stringified structure.
        """
        parts = getattr(content, "parts", None)
        if not parts:
            return ""
        out: list[str] = []
        for part in parts:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text:
                out.append(text)
        return "".join(out)


_NON_TEXT_PART_ATTRS = (
    "function_call",
    "function_response",
    "thought",
    "inline_data",
    "file_data",
    "executable_code",
    "code_execution_result",
)


class _EventAccumulator:
    """Collects tool calls, usage, finish reason and error code from the event stream.

    Only attributes the event actually has are read (``getattr`` with a
    default), so a duck-typed fake event that exposes none of them yields
    ``None`` fields rather than an error.
    """

    def __init__(self) -> None:
        self._calls: list[ToolCall] = []
        self._results: list[tuple[str | None, str | None, str | None]] = []  # (call_id, name, result)
        self._calls_exposed = False
        self._usage_seen = False
        self._sums: dict[str, int | None] = {"prompt": None, "completion": None, "reasoning": None}
        self._final = None
        self._error_code: str | None = None

    @staticmethod
    def _add(total: int | None, value: object) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool):
            return (total or 0) + value
        return total

    def add(self, event: object) -> None:
        code = getattr(event, "error_code", None)
        if code:
            self._error_code = str(code)
        if getattr(event, "partial", False):
            return                      # streaming chunks: skip, so nothing is counted twice
        getter = getattr(event, "get_function_calls", None)
        if callable(getter):
            self._calls_exposed = True
            for fc in getter() or []:
                name = getattr(fc, "name", None)
                if isinstance(name, str):
                    call_id = getattr(fc, "id", None)
                    self._calls.append(ToolCall.from_arguments(
                        name, getattr(fc, "args", None), call_id=call_id if isinstance(call_id, str) else None,
                    ))
        resp_getter = getattr(event, "get_function_responses", None)
        if callable(resp_getter):
            for fr in resp_getter() or []:
                body = getattr(fr, "response", None)
                result = None if body is None else json.dumps(body, default=str)
                rid = getattr(fr, "id", None)
                self._results.append((rid if isinstance(rid, str) else None, getattr(fr, "name", None), result))
        um = getattr(event, "usage_metadata", None)
        if um is not None:
            self._usage_seen = True
            self._sums["prompt"] = self._add(self._sums["prompt"], getattr(um, "prompt_token_count", None))
            self._sums["completion"] = self._add(self._sums["completion"], getattr(um, "candidates_token_count", None))
            self._sums["reasoning"] = self._add(self._sums["reasoning"], getattr(um, "thoughts_token_count", None))
        is_final = getattr(event, "is_final_response", None)
        if callable(is_final) and is_final():
            self._final = event

    def _attach_results(self) -> None:
        pending = list(self._results)
        for call in self._calls:
            for i, (rid, name, result) in enumerate(pending):
                if (call.call_id is not None and rid == call.call_id) or (call.call_id is None and rid is None and name == call.name):
                    call.result = result
                    del pending[i]
                    break

    @staticmethod
    def _part_kinds(event: object) -> list[str]:
        parts = getattr(getattr(event, "content", None), "parts", None) or []
        kinds: list[str] = []
        for part in parts:
            for attr in _NON_TEXT_PART_ATTRS:
                if getattr(part, attr, None):
                    kinds.append(attr)
        return kinds

    def fields(self) -> dict:
        fields: dict = {}
        prov: dict = {}
        try:
            fields["framework_version"] = _metadata.version("google-adk")
            prov["framework_version"] = "verified"
        except _metadata.PackageNotFoundError:
            pass
        if self._calls_exposed:
            self._attach_results()
            fields["tool_calls"] = self._calls
            prov["tool_calls"] = "verified"
        if self._usage_seen:
            usage = normalize_usage(self._sums["prompt"], self._sums["completion"], self._sums["reasoning"])
            if usage is not None:
                fields["usage"] = usage
                prov["usage"] = "inferred"
        if self._final is not None:
            reason = getattr(self._final, "finish_reason", None)
            if reason is not None:
                fields["stop_reason"] = str(getattr(reason, "value", reason))
                prov["stop_reason"] = "verified"
            fields["non_text_parts"] = self._part_kinds(self._final)
            prov["non_text_parts"] = "inferred"
        if self._error_code is not None:
            fields["error_code"] = self._error_code
            prov["error_code"] = "verified"
        fields["provenance"] = prov
        return fields
