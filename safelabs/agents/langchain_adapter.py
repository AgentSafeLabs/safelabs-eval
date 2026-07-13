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
            return self._text_from_content(raw.content)
        if isinstance(raw, dict):
            if self._output_key and self._output_key in raw:
                return str(raw[self._output_key])
            for key in ("output", "text", "content", "result", "answer"):
                if key in raw:
                    return str(raw[key])
        return str(raw)

    @staticmethod
    def _text_from_content(content: object) -> str:
        """Extract clean text from a LangChain message's ``.content``.

        ``.content`` is a plain ``str`` for most providers (Anthropic,
        OpenAI chat completions) but a **list of content-block dicts** for
        others — confirmed live: ChatGoogleGenerativeAI (Gemini 3.x)
        returns ``[{"type": "text", "text": "...", "extras": {...}}]``, and
        OpenAI models routed through the Responses API return a list that
        interleaves reasoning blocks (``{"type": "reasoning", ...}``, no
        ``text``) with the actual text block.

        The previous implementation did ``str(raw.content)`` unconditionally,
        which stringified the whole Python list/dict structure into the
        output (e.g. ``"[{'type': 'text', 'text': 'A firewall...'}]"``) —
        corrupting downstream Scorer regex matching. This joins the text of
        every text-bearing block and drops non-text blocks (reasoning,
        tool calls, images), depending only on the documented block shape,
        not on any transitional LangChain accessor API.
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    text = block.get("text")
                    # A standard text block ({"type": "text", "text": ...}),
                    # or a provider block that carries "text" without an
                    # explicit type. Blocks with no usable "text" (reasoning,
                    # tool_use, image_url, ...) are intentionally skipped.
                    if isinstance(text, str) and block.get("type", "text") == "text":
                        parts.append(text)
            return "".join(parts)
        # Unknown shape — fall back to a string, matching prior behavior for
        # anything that is neither str nor a list of blocks.
        return str(content)
