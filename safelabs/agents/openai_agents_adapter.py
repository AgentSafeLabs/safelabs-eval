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

Known version-skew bug (as of 2026-07-10)
------------------------------------------
``openai-agents==0.18.0`` combined with ``openai>=2.45`` crashes on
*every* ``Runner.run()`` call, regardless of model backend, before any
model dispatch happens: ``agents/usage.py`` constructs
``InputTokensDetails(cached_tokens=0)``, but ``openai>=2.45`` made
``InputTokensDetails.cache_write_tokens`` a required field with no
default, so pydantic validation fails inside ``RunContextWrapper``
construction at the very start of the run. Confirmed via full traceback
during real-adapter verification (see ``examples/openai_agents_adapter_verify.py``) —
zero API cost was incurred since the failure precedes any network call.

Optional AgentResponse fields (audited against openai-agents 0.18.0 with
openai 2.44.0, driving the real ``Runner`` with a stub ``Model``):

* ``tool_calls`` - every ``RunResult.new_items`` entry whose ``type`` is
  ``tool_call_item`` (``raw_item.name``, ``raw_item.arguments`` JSON text,
  ``raw_item.call_id``); ``ToolCall.result`` comes from the
  ``tool_call_output_item`` with the same ``call_id`` (``.output``; in memory
  only). A hosted-tool call that has no ``name`` is recorded under its
  ``raw_item.type``. ``[]`` when ``new_items`` is a list with no tool calls;
  ``None`` when the result has no ``new_items`` list. Agent *handoffs*
  (``handoff_call_item``) are not counted as tool calls. provenance: verified.
* ``usage`` - ``RunResult.context_wrapper.usage`` (``input_tokens``,
  ``output_tokens``, ``output_tokens_details.reasoning_tokens``), totalled by
  the SDK across every model request of the run. An all-zero usage is read as
  "the provider reported none" and left ``None`` (a completed call always uses
  tokens; the SDK defaults missing usage to zero). verified.
* ``stop_reason`` - not exposed by the SDK (``ModelResponse`` carries only
  ``output``, ``usage``, ``response_id``, ``request_id``): stays ``None``.
* ``framework_version`` - installed ``openai-agents`` version. verified.

Pinned around via ``openai<2.45`` in the ``openai-agents`` extra in
``pyproject.toml``. ``openai-agents==0.18.1`` (released after 0.18.0,
the version this adapter was originally verified against) already
rewrote ``agents/usage.py`` to build ``InputTokensDetails`` via
``model_validate``/``TypeAdapter`` instead of direct keyword
construction — confirmed locally (no API call) that this no longer
crashes even with ``openai==2.45.0`` installed. So the upstream fix has
likely already landed; the ``openai<2.45`` pin is now mostly
defense-in-depth for anyone still resolving to ``openai-agents==0.18.0``
specifically. Safe to consider loosening the pin once ``openai-agents``
in this project's lock/extras floor moves past ``0.18.0``.
"""

from __future__ import annotations

import time
from importlib import metadata as _metadata

from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse, ToolCall, normalize_usage


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
        return AgentResponse(
            output=self._extract_output(result),
            latency_ms=latency_ms,
            **self._optional_fields(result),
        )

    @staticmethod
    def _optional_fields(result: object) -> dict:
        """Fill the optional AgentResponse fields from a ``RunResult`` (see module docstring)."""
        fields: dict = {}
        prov: dict = {}

        try:
            fields["framework_version"] = _metadata.version("openai-agents")
            prov["framework_version"] = "verified"
        except _metadata.PackageNotFoundError:
            pass

        items = getattr(result, "new_items", None)
        if isinstance(items, list):
            calls: list[ToolCall] = []
            outputs: dict[str, str] = {}
            for item in items:
                kind = getattr(item, "type", None)
                raw = getattr(item, "raw_item", None)
                if kind == "tool_call_item":
                    name = _get(raw, "name") or _get(raw, "type")
                    if isinstance(name, str):
                        args = _get(raw, "arguments")
                        cid = _get(raw, "call_id") or _get(raw, "id")
                        calls.append(ToolCall.from_arguments(
                            name, args if isinstance(args, (dict, str)) else None,
                            call_id=cid if isinstance(cid, str) else None,
                        ))
                elif kind == "tool_call_output_item":
                    cid = _get(raw, "call_id")
                    out = getattr(item, "output", None)
                    if isinstance(cid, str) and out is not None:
                        outputs[cid] = str(out)
            for call in calls:
                if call.call_id is not None and call.call_id in outputs:
                    call.result = outputs[call.call_id]
            fields["tool_calls"] = calls
            prov["tool_calls"] = "verified"

        usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
        if usage is not None:
            inp = getattr(usage, "input_tokens", None)
            out = getattr(usage, "output_tokens", None)
            if isinstance(inp, int) and isinstance(out, int) and (inp or out):
                reasoning = getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", None)
                fields["usage"] = normalize_usage(inp, out, reasoning)
                prov["usage"] = "verified"

        fields["provenance"] = prov
        return fields

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


def _get(obj: object, key: str) -> object:
    """Read ``key`` from a dict or an attribute-style object (raw items are either)."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
