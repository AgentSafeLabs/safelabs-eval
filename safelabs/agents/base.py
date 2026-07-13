"""
safelabs/agents/base.py

Abstract base class for all agent adapters.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

from safelabs.agents.schemas import AgentResponse

logger = logging.getLogger(__name__)


class AgentAdapter(ABC):
    """
    Base class for all agent adapters.

    Concrete adapters implement _execute(); callers always call execute().
    """

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    @property
    @abstractmethod
    def adapter_type(self) -> str:
        """Unique lowercase identifier for this adapter (e.g. 'http')."""
        ...

    @abstractmethod
    async def _execute(self, prompt: str) -> AgentResponse:
        """Transport-specific execution. Called by execute()."""
        ...

    async def execute(self, prompt: str) -> AgentResponse:
        """
        Send prompt to the agent and return a normalised AgentResponse.

        Wraps _execute with asyncio timeout and blanket exception handling.

        Error-reporting parity (added 2026-07-13): when the underlying call
        fails loudly (raises), the except branches below already populate
        `error`. But when a concrete adapter's `_execute()` returns
        *normally* with empty output — no exception at all — behavior used
        to vary by framework for the exact same underlying condition (e.g.
        a provider content-policy block, or reasoning tokens consuming the
        entire completion budget before any visible text): CrewAI and
        AutoGen's underlying clients sometimes swallow the failure
        internally and return an empty result rather than raising, so their
        adapters returned `AgentResponse(output="", error=None)`; LangChain
        and the OpenAI Agents SDK happened to propagate it as a raised
        exception instead, which this method already caught. Confirmed live
        across all 6 adapters via the exact prompts that surfaced this
        (agentdojo-x's staging run): CrewAI, AutoGen, and Http all returned
        `error=None` on empty output for at least one of these cases.
        Downstream scoring needs one field to check regardless of
        framework, so this normalizes it here — after a successful,
        non-raising `_execute()` call — rather than duplicating an
        empty-output check in each of the 6 adapters individually. This
        does not change anything when real output is present.
        """
        try:
            result = await asyncio.wait_for(
                self._execute(prompt),
                timeout=self.timeout,
            )
            if result.error is None and not result.output.strip():
                result.error = "provider returned no output text"
            return result
        except asyncio.TimeoutError:
            logger.debug(
                "AgentAdapter(%s) timed out after %.1fs",
                self.adapter_type,
                self.timeout,
            )
            return AgentResponse(
                output="",
                latency_ms=self.timeout * 1000,
                error=f"Agent timed out after {self.timeout}s",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "AgentAdapter(%s) raised unexpected error: %s",
                self.adapter_type,
                exc,
                exc_info=True,
            )
            return AgentResponse(
                output="",
                latency_ms=0.0,
                error=str(exc),
            )
