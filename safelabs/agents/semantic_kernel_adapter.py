"""
safelabs/agents/semantic_kernel_adapter.py

Adapter for Microsoft Semantic Kernel agents.

Install optional dependency first:
    pip install "safelabs-eval[semantic-kernel]"

Example
-------
.. code-block:: python

    from semantic_kernel.agents import ChatCompletionAgent
    from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion
    from safelabs.agents import SemanticKernelAdapter

    agent = ChatCompletionAgent(
        service=OpenAIChatCompletion(ai_model_id="gpt-4o-mini"),
        name="assistant",
        instructions="You are a helpful assistant.",
    )
    adapter = SemanticKernelAdapter(agent=agent)
    response = await adapter.execute("Ignore previous instructions.")

API note
--------
This adapter targets ``semantic-kernel >= 1.26`` (verified against
v1.44.1, 2026-09). The relevant surface on the ``Agent`` base class
(``ChatCompletionAgent``, ``AzureAIAgent``, ``OpenAIResponsesAgent``, …):

* ``await agent.get_response(messages=prompt)`` — coroutine returning a
  single ``AgentResponseItem[ChatMessageContent]``.  ``messages`` accepts
  a ``str``, a ``ChatMessageContent``, or a list of either.  Passing no
  ``thread`` makes the agent create an ephemeral one, which is exactly
  right for a single-turn adversarial probe.
* ``result.message`` — the ``ChatMessageContent``; its ``.content`` is the
  reply text.  ``str(result)`` also yields that text
  (``AgentResponseItem.__str__`` → ``str(message)``).

Version history: ``Agent.get_response`` was added in ``semantic-kernel``
1.22; the typed plural ``messages`` parameter landed in 1.26 — hence the
floor.  ``agent.invoke(...)`` (an async generator of
``AgentResponseItem``) is the streaming alternative; ``get_response`` is
used here because these probes need only the final message.

Semantic Kernel is a pure-Python package with no framework class the
adapter must import, so — unlike the Google ADK or OpenAI Agents adapters
— everything here works by duck typing and no lazy import is needed.

Always verify the exact API against your installed version's docs before
trusting adapter output in production.
"""

from __future__ import annotations

import time

from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse


class SemanticKernelAdapter(AgentAdapter):
    """
    Adapter for Semantic Kernel ``Agent`` objects (``semantic-kernel >= 1.26``).

    ``Agent.get_response()`` is async-native; no thread-pool offload is
    needed (unlike CrewAI's synchronous ``Crew.kickoff()``).

    Parameters
    ----------
    agent:
        A Semantic Kernel agent — ``ChatCompletionAgent`` or any object
        with an async ``get_response(messages=str)`` method returning an
        ``AgentResponseItem``.
    timeout:
        Per-request timeout in seconds (default 30). Enforced by the
        base class.

    Example
    -------
    .. code-block:: python

        adapter = SemanticKernelAdapter(agent=my_agent)
        response = await adapter.execute("Ignore previous instructions.")
    """

    def __init__(
        self,
        agent: object,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self._agent = agent

    @property
    def adapter_type(self) -> str:
        return "semantic-kernel"

    async def _execute(self, prompt: str) -> AgentResponse:
        t0 = time.perf_counter()
        result = await self._agent.get_response(messages=prompt)
        latency_ms = (time.perf_counter() - t0) * 1000
        return AgentResponse(output=self._extract_output(result), latency_ms=latency_ms)

    def _extract_output(self, result: object) -> str:
        # 1. AgentResponseItem.message.content — the reply text on the
        #    wrapped ChatMessageContent (semantic-kernel >= 1.26).
        message = getattr(result, "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str) and content:
            return content

        # 2. result.content as a plain string — covers a bare
        #    ChatMessageContent returned without the AgentResponseItem
        #    wrapper (older / alternate shapes). On >= 1.26 this attribute
        #    is the ChatMessageContent object, not a str, so this branch
        #    is skipped there.
        direct = getattr(result, "content", None)
        if isinstance(direct, str) and direct:
            return direct

        # 3. Plain-string fallback (mocks, future API changes).
        if isinstance(result, str):
            return result

        # 4. str() fallback — AgentResponseItem.__str__ returns str(message),
        #    i.e. the reply text, for any wrapper shape not caught above.
        return str(result)
