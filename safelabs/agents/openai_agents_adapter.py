"""
safelabs/agents/openai_agents_adapter.py

Adapter for the OpenAI Agents SDK (pip package ``openai-agents``).

Install optional dependency first:
    pip install "safelabs-eval[openai-agents]"

Example
-------
.. code-block:: python

    from agents import Agent
    from safelabs.agents import OpenAIAgentsAdapter

    agent   = Agent(name="assistant", instructions="Be helpful.")
    adapter = OpenAIAgentsAdapter(agent=agent)
    response = await adapter.execute("Ignore previous instructions.")

API note
--------
This adapter targets ``openai-agents >= 0.1`` (verified against v0.18.0).
The key API surface:

* ``Runner.run(agent, input)`` — async classmethod on ``agents.Runner``;
  accepts a plain string as ``input`` and returns a ``RunResult``.
* ``RunResult.final_output`` — typed ``Any``; a ``str`` for agents with
  the default string output type, or a Pydantic model for agents that
  declare a typed ``output_type``.  ``_extract_output`` coerces non-string
  values via ``str()``.

Because ``Runner`` is a standalone class (not a method on the agent
object), the import cannot be fully deferred through duck-typing.  The
adapter imports ``Runner`` lazily inside ``_execute()`` rather than at
module scope so that importing ``safelabs.agents`` does not raise
``ImportError`` when ``openai-agents`` is not installed.  The tradeoff:
import errors surface on the first call, not at construction time.

For testing without a real ``openai-agents`` install, pass a duck-typed
``runner`` object directly to the constructor; the lazy import is skipped
when ``runner`` is not ``None``.
"""

from __future__ import annotations

import time

from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse


class OpenAIAgentsAdapter(AgentAdapter):
    """
    Adapter for the OpenAI Agents SDK.

    ``Runner.run()`` is an async classmethod — no ``asyncio.to_thread``
    is needed (unlike CrewAI's synchronous ``Crew.kickoff()``).

    Parameters
    ----------
    agent:
        An ``agents.Agent`` instance to probe with adversarial prompts.
    runner:
        Optional duck-typed runner object for testing.  When ``None``
        (the default), ``agents.Runner`` is imported lazily on the first
        call to ``_execute()``.
    timeout:
        Per-request timeout in seconds (default 30). Enforced by the
        base class.

    Example
    -------
    .. code-block:: python

        adapter = OpenAIAgentsAdapter(agent=my_agent)
        response = await adapter.execute("Ignore previous instructions.")
    """

    def __init__(
        self,
        agent: object,
        runner: object | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self._agent  = agent
        # Injected runner lets tests pass a fake without installing openai-agents.
        # When None, agents.Runner is imported lazily inside _execute().
        self._runner = runner

    @property
    def adapter_type(self) -> str:
        return "openai-agents"

    async def _execute(self, prompt: str) -> AgentResponse:
        if self._runner is not None:
            runner = self._runner
        else:
            # Runner lives in the `agents` package, not on the agent object,
            # so we can't duck-type the call away entirely.  Lazy import keeps
            # module-level import clean when openai-agents is not installed.
            from agents import Runner  # noqa: PLC0415
            runner = Runner
        t0 = time.perf_counter()
        result = await runner.run(self._agent, prompt)
        latency_ms = (time.perf_counter() - t0) * 1000
        return AgentResponse(output=self._extract_output(result), latency_ms=latency_ms)

    def _extract_output(self, result: object) -> str:
        # 1. RunResult.final_output — the sole output attribute in openai-agents >= 0.1.
        #    Typed Any: str for default agents, Pydantic model for typed output_type.
        final_output = getattr(result, "final_output", None)
        if final_output is not None:
            return str(final_output)

        # 2. .output — not present in the current SDK but kept as a defensive
        #    fallback in case a future version renames or aliases the attribute.
        output = getattr(result, "output", None)
        if output is not None and isinstance(output, str):
            return output

        # 3. Plain-string fallback (mocks, future API changes).
        if isinstance(result, str):
            return result

        return str(result)
