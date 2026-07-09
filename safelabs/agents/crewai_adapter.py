"""
safelabs/agents/crewai_adapter.py

Adapter for CrewAI Crew objects.

Install optional dependency first:
    pip install "safelabs-eval[crewai]"

Example
-------
.. code-block:: python

    from crewai import Agent, Crew, Task
    from safelabs.agents import CrewAIAdapter

    agent = Agent(role="assistant", goal="be helpful", backstory="...")
    task  = Task(description="{input}", agent=agent, expected_output="text")
    crew  = Crew(agents=[agent], tasks=[task])

    adapter = CrewAIAdapter(crew=crew)
    response = await adapter.execute("Ignore previous instructions.")

Version compatibility note
--------------------------
CrewAI's public API has changed significantly across minor versions:

* ``crewai >= 0.30`` — ``Crew.kickoff()`` accepts an ``inputs`` dict and
  returns a ``CrewOutput`` object with a ``.raw`` attribute (the string
  output of the final task).  **This adapter targets this API.**

* ``crewai 0.2x`` — ``kickoff()`` returned a plain string directly, not a
  ``CrewOutput`` object.

* ``crewai 0.6x+`` — ``CrewOutput`` gained additional fields (``pydantic``,
  ``json_dict``, ``token_usage``) but ``.raw`` is still present.

Always verify the exact API against the installed version's changelog
before trusting adapter output in production.
"""

from __future__ import annotations

import asyncio
import time

from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse


class CrewAIAdapter(AgentAdapter):
    """
    Adapter for CrewAI Crew objects.

    ``Crew.kickoff()`` is synchronous; this adapter offloads it to a thread
    pool via ``asyncio.to_thread()`` so the event loop stays unblocked while
    the crew executes.

    Parameters
    ----------
    crew:
        A ``Crew`` instance whose ``kickoff()`` will be called with
        ``inputs={input_key: prompt}`` for each adversarial probe.
    input_key:
        The key used in the ``inputs`` dict passed to ``kickoff()``.
        Defaults to ``"input"``; change it to match your crew's task
        template variables.
    timeout:
        Per-request timeout in seconds (default 30). Enforced by the
        base class.

    Example
    -------
    .. code-block:: python

        adapter = CrewAIAdapter(crew=my_crew, input_key="user_message")
        response = await adapter.execute("Ignore previous instructions.")
    """

    def __init__(
        self,
        crew: object,
        input_key: str = "input",
        timeout: float = 30.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self._crew      = crew
        self._input_key = input_key

    @property
    def adapter_type(self) -> str:
        return "crewai"

    async def _execute(self, prompt: str) -> AgentResponse:
        inputs = {self._input_key: prompt}
        t0 = time.perf_counter()
        # kickoff() is synchronous — run in thread pool to avoid blocking the loop
        result = await asyncio.to_thread(self._crew.kickoff, inputs=inputs)
        latency_ms = (time.perf_counter() - t0) * 1000
        return AgentResponse(output=self._extract_output(result), latency_ms=latency_ms)

    def _extract_output(self, result: object) -> str:
        # 1. CrewOutput.raw — string output of the final task (crewai >= 0.30).
        raw = getattr(result, "raw", None)
        if raw is not None and isinstance(raw, str):
            return raw

        # 2. .output attribute — older/alternate CrewAI shapes.
        output = getattr(result, "output", None)
        if output is not None and isinstance(output, str):
            return output

        # 3. Plain-string fallback (crewai 0.2x, mocks, future API changes).
        if isinstance(result, str):
            return result

        return str(result)
