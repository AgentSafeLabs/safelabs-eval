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
        """
        try:
            return await asyncio.wait_for(
                self._execute(prompt),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
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
            logger.exception(
                "AgentAdapter(%s) raised unexpected error: %s",
                self.adapter_type,
                exc,
            )
            return AgentResponse(
                output="",
                latency_ms=0.0,
                error=str(exc),
            )
