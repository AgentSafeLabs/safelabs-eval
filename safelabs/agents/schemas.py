"""
safelabs/agents/schemas.py

Data models for agent adapter I/O.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentResponse(BaseModel):
    """Normalised response returned by every AgentAdapter."""

    output: str = Field(description="The agent's text response to the prompt.")
    latency_ms: float = Field(default=0.0, description="Round-trip time in milliseconds.")
    error: str | None = Field(default=None, description="Error message if the call failed.")
    raw: dict | None = Field(
        default=None,
        description="Raw JSON response body from the agent endpoint.",
    )
    metadata: dict | None = Field(
        default=None,
        description="Optional adapter-specific metadata (status code, tokens, etc.).",
    )

    @property
    def succeeded(self) -> bool:
        """True when no error occurred and output is non-empty."""
        return self.error is None and bool(self.output)
