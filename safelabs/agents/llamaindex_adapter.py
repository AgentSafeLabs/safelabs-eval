"""
safelabs/agents/llamaindex_adapter.py

Adapter for LlamaIndex AgentWorkflow objects.

Install optional dependency first:
    pip install "safelabs-eval[llamaindex]"

Example
-------
.. code-block:: python

    from llama_index.core.agent.workflow import AgentWorkflow
    from llama_index.llms.openai import OpenAI
    from safelabs.agents import LlamaIndexAdapter

    workflow = AgentWorkflow.from_tools(
        tools=[...],
        llm=OpenAI(model="gpt-4o-mini"),
    )
    adapter = LlamaIndexAdapter(workflow=workflow)
    response = await adapter.execute("Ignore previous instructions.")

Version compatibility note
--------------------------
LlamaIndex's agent API has changed significantly across major versions:

* ``llama-index-core >= 0.11`` — introduces ``AgentWorkflow``, invoked via
  ``await workflow.run(user_msg=prompt)`` which returns a result whose
  ``.response`` attribute or ``str()`` representation holds the final text.
  **This adapter targets this API.**

* ``llama-index-core 0.10.x`` — agent classes such as ``ReActAgent`` and
  ``OpenAIAgent`` expose an async ``achat(prompt)`` method returning an
  ``AgentChatResponse`` with a ``.response`` string attribute.  The
  ``_extract_output`` cascade (``.response`` → plain ``str`` → ``str()``)
  handles that result shape if you call ``achat`` yourself, but
  ``_execute`` calls ``run(user_msg=prompt)``, which is absent on those
  classes.

``AgentWorkflow.run()`` is async-native — no ``asyncio.to_thread`` is
needed (unlike CrewAI's synchronous ``Crew.kickoff()``).

Always verify the exact API against your installed version's docs before
trusting adapter output in production.
"""

from __future__ import annotations

import time

from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse


class LlamaIndexAdapter(AgentAdapter):
    """
    Adapter for LlamaIndex AgentWorkflow objects (llama-index-core >= 0.11).

    ``AgentWorkflow.run()`` is async-native; no thread-pool offload is
    needed (unlike CrewAI's synchronous ``kickoff()``).

    Parameters
    ----------
    workflow:
        An ``AgentWorkflow`` instance — or any object with an async
        ``run(user_msg=str)`` method — to probe with adversarial prompts.
    timeout:
        Per-request timeout in seconds (default 30). Enforced by the
        base class.

    Example
    -------
    .. code-block:: python

        adapter = LlamaIndexAdapter(workflow=my_workflow)
        response = await adapter.execute("Ignore previous instructions.")
    """

    def __init__(
        self,
        workflow: object,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self._workflow = workflow

    @property
    def adapter_type(self) -> str:
        return "llamaindex"

    async def _execute(self, prompt: str) -> AgentResponse:
        t0 = time.perf_counter()
        # AgentWorkflow.run() is async-native — await directly, no to_thread needed
        result = await self._workflow.run(user_msg=prompt)
        latency_ms = (time.perf_counter() - t0) * 1000
        return AgentResponse(output=self._extract_output(result), latency_ms=latency_ms)

    def _extract_output(self, result: object) -> str:
        # 1. .response attribute — AgentChatResponse (0.10.x) and some 0.11+ shapes.
        response = getattr(result, "response", None)
        if response is not None and isinstance(response, str):
            return response

        # 2. Plain string — AgentWorkflow.run() may return str directly (>= 0.11).
        if isinstance(result, str):
            return result

        # 3. Fallback for unknown or future result shapes.
        return str(result)
