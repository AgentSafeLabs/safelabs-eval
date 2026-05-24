"""
safelabs/agents/langchain_adapter.py

Adapter for LangChain-based agents and chains.

Install optional dependency first:
    pip install "safelabs-eval[langchain]"

Example
-------
.. code-block:: python

    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate
    from safelabs.agents import LangChainAdapter

    chain = ChatPromptTemplate.from_template("{input}") | ChatOpenAI()
    adapter = LangChainAdapter(runnable=chain, input_key="input")
    response = await adapter.execute("Ignore previous instructions.")
"""

from __future__ import annotations

import time

from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse


class LangChainAdapter(AgentAdapter):
    """Adapter for LangChain Runnable objects (chains, agents, LLMs, chat models)."""

    def __init__(
        self,
        runnable: object,
        input_key: str | None = "input",
        output_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self._runnable   = runnable
        self._input_key  = input_key
        self._output_key = output_key

    @property
    def adapter_type(self) -> str:
        return "langchain"

    async def _execute(self, prompt: str) -> AgentResponse:
        payload = {self._input_key: prompt} if self._input_key else prompt
        t0  = time.perf_counter()
        raw = await self._runnable.ainvoke(payload)
        latency_ms = (time.perf_counter() - t0) * 1000
        return AgentResponse(output=self._extract_output(raw), latency_ms=latency_ms)

    def _extract_output(self, raw: object) -> str:
        if isinstance(raw, str):
            return raw
        if hasattr(raw, "content"):
            return str(raw.content)
        if isinstance(raw, dict):
            if self._output_key and self._output_key in raw:
                return str(raw[self._output_key])
            for key in ("output", "text", "content", "result", "answer"):
                if key in raw:
                    return str(raw[key])
        return str(raw)
