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

Always verify the exact API against your installed version's docs before
trusting adapter output in production.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse


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
        async for event in runner.run_async(
            user_id=self._user_id,
            session_id=session.id,
            new_message=message,
        ):
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
        return AgentResponse(output=final_text, latency_ms=latency_ms)

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
