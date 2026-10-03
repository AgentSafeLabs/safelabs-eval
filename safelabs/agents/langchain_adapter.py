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

``input_key``: the default ``"input"`` sends ``{"input": prompt}`` (a dict),
which is what a prompt-template chain expects. A **bare chat model**
(``ChatOpenAI()``, ``GenericFakeChatModel(...)``) takes a string or messages,
not a dict, and rejects the dict ("Invalid input type <class 'dict'>"): pass
``input_key=None`` for a chat model so the prompt is sent as a plain string.
The default is unchanged so existing callers keep working.

Optional AgentResponse fields (filled only when the runnable returns a
message-like object, normally an ``AIMessage``; a ``str`` or ``dict`` result
leaves them ``None``):

* ``tool_calls``  - ``AIMessage.tool_calls`` (``name``, ``args``, ``id``);
  ``[]`` when the message has none. provenance: verified.
* ``usage``       - ``AIMessage.usage_metadata`` (``input_tokens``,
  ``output_tokens``, ``output_token_details['reasoning']``). verified.
* ``stop_reason`` - ``response_metadata['finish_reason']`` or
  ``['stop_reason']`` (keys vary by provider). inferred.
* ``non_text_parts`` - the ``type`` of every non-text content block. inferred.
* ``framework_version`` - installed ``langchain-core`` version. verified.

Verified against langchain-core 1.4.0 (``AIMessage`` fields, ``GenericFakeChatModel``).
"""

from __future__ import annotations

import time
from importlib import metadata as _metadata

from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse, ToolCall, normalize_usage


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
        return AgentResponse(
            output=self._extract_output(raw),
            latency_ms=latency_ms,
            **self._optional_fields(raw),
        )

    @staticmethod
    def _optional_fields(raw: object) -> dict:
        """Fill the optional AgentResponse fields from a message-like result (see module docstring)."""
        fields: dict = {}
        prov: dict = {}

        version = _framework_version("langchain-core")
        if version is not None:
            fields["framework_version"] = version
            prov["framework_version"] = "verified"

        calls = getattr(raw, "tool_calls", None)
        if isinstance(calls, list):
            fields["tool_calls"] = [
                ToolCall.from_arguments(
                    c["name"],
                    c.get("args"),
                    call_id=c.get("id") if isinstance(c.get("id"), str) else None,
                )
                for c in calls
                if isinstance(c, dict) and isinstance(c.get("name"), str)
            ]
            prov["tool_calls"] = "verified"

        meta = getattr(raw, "usage_metadata", None)
        if isinstance(meta, dict):
            details = meta.get("output_token_details")
            usage = normalize_usage(
                meta.get("input_tokens"),
                meta.get("output_tokens"),
                details.get("reasoning") if isinstance(details, dict) else None,
            )
            if usage is not None:
                fields["usage"] = usage
                prov["usage"] = "verified"

        rmeta = getattr(raw, "response_metadata", None)
        if isinstance(rmeta, dict):
            stop = next((rmeta[k] for k in ("finish_reason", "stop_reason") if isinstance(rmeta.get(k), str) and rmeta[k]), None)
            if stop is not None:
                fields["stop_reason"] = stop
                prov["stop_reason"] = "inferred"

        content = getattr(raw, "content", None)
        if isinstance(content, str):
            fields["non_text_parts"] = []
            prov["non_text_parts"] = "inferred"
        elif isinstance(content, list):
            kinds: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type", "text") != "text":
                    kind = block.get("type")
                    if isinstance(kind, str):
                        kinds.append(kind)
            fields["non_text_parts"] = kinds
            prov["non_text_parts"] = "inferred"

        fields["provenance"] = prov
        return fields

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


def _framework_version(distribution: str) -> str | None:
    """Installed version of a distribution from package metadata, or None if not installed."""
    try:
        return _metadata.version(distribution)
    except _metadata.PackageNotFoundError:
        return None
