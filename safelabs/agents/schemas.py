"""
safelabs/agents/schemas.py

Data models for agent adapter I/O.

The optional fields added after ``metadata`` (``tool_calls``, ``usage``,
``stop_reason``, ``error_code``, ``non_text_parts``, ``framework``,
``framework_version``, ``provenance``) are additive: an adapter that does not
fill them leaves them ``None`` and every existing caller keeps working.

Rule for every collection field: ``None`` means "this adapter or framework
path does not expose it"; ``[]`` means "exposed, and there were zero".
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

#: How a new field's value was obtained.
#:   verified - read directly from a typed attribute/method of the framework's result object
#:   inferred - computed or parsed by the adapter (sums, JSON parsing, provider-specific keys)
#:   unknown  - not exposed; the field is None
Provenance = Literal["verified", "inferred", "unknown"]

#: The fields whose provenance is tracked in ``AgentResponse.provenance``.
PROVENANCE_FIELDS = (
    "tool_calls",
    "usage",
    "stop_reason",
    "error_code",
    "non_text_parts",
    "framework_version",
)


class ToolCall(BaseModel):
    """One tool/function call reported by a framework, built only from what the adapter can expose."""

    name: str = Field(description="Tool/function name.")
    arguments: dict[str, Any] | None = Field(
        default=None,
        description="Arguments as a dict when the source gives a dict or JSON text that parses.",
    )
    arguments_raw: str | None = Field(
        default=None,
        description="The original argument string, only when it could not be parsed into a dict.",
    )
    call_id: str | None = Field(default=None, description="The framework's id for the call.")
    result: str | None = Field(
        default=None,
        exclude=True,
        repr=False,
        description=(
            "The tool's output when the framework exposes it. In memory only: excluded from "
            "model_dump()/model_dump_json() and from repr, so it is never persisted by default."
        ),
    )

    @classmethod
    def from_arguments(
        cls,
        name: str,
        arguments: object = None,
        *,
        call_id: str | None = None,
        result: str | None = None,
    ) -> ToolCall:
        """Build a ToolCall from an arguments value that may be a dict, a JSON string, or None."""
        args: dict[str, Any] | None = None
        raw: str | None = None
        if isinstance(arguments, dict):
            args = dict(arguments)
        elif isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                args = parsed
            else:
                raw = arguments
        return cls(name=name, arguments=args, arguments_raw=raw, call_id=call_id, result=result)


def normalize_usage(
    prompt_tokens: object = None,
    completion_tokens: object = None,
    reasoning_tokens: object = None,
) -> dict[str, int | None] | None:
    """Usage dict with the same keys as ``BenchTrialResult.usage``; None when no count is an int."""
    def _i(v: object) -> int | None:
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    usage = {
        "prompt_tokens": _i(prompt_tokens),
        "completion_tokens": _i(completion_tokens),
        "reasoning_tokens": _i(reasoning_tokens),
    }
    return usage if any(v is not None for v in usage.values()) else None


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
    tool_calls: list[ToolCall] | None = Field(
        default=None,
        description="Tool calls the framework reports, in order. None = not exposed; [] = exposed, zero calls.",
    )
    usage: dict[str, int | None] | None = Field(
        default=None,
        description="{'prompt_tokens', 'completion_tokens', 'reasoning_tokens'}; None = not exposed.",
    )
    stop_reason: str | None = Field(
        default=None,
        description="The framework's own finish/stop reason string, unmodified.",
    )
    error_code: str | None = Field(
        default=None,
        description="Provider/framework error code when the framework reports one.",
    )
    non_text_parts: list[str] | None = Field(
        default=None,
        description="Kinds of non-text parts in the final output. None = not exposed; [] = exposed, none.",
    )
    framework: str | None = Field(default=None, description="Adapter type name.")
    framework_version: str | None = Field(
        default=None,
        description="Installed version of the framework package, from package metadata.",
    )
    provenance: dict[str, Provenance] | None = Field(
        default=None,
        description=(
            "How each tracked field's value was obtained: verified, inferred or unknown. "
            "A field that is not None needs verified or inferred; a field that is None is "
            "unknown or has no entry."
        ),
    )

    @model_validator(mode="after")
    def _check_provenance(self) -> AgentResponse:
        prov = self.provenance or {}
        bad = sorted(set(prov) - set(PROVENANCE_FIELDS))
        if bad:
            raise ValueError(f"provenance has unknown field name(s): {bad}")
        for name in PROVENANCE_FIELDS:
            value = getattr(self, name)
            tag = prov.get(name)
            if value is None:
                if tag not in (None, "unknown"):
                    raise ValueError(f"{name} is None, so its provenance must be 'unknown' or absent, not {tag!r}")
            elif tag not in ("verified", "inferred"):
                raise ValueError(f"{name} is set, so its provenance must be 'verified' or 'inferred', not {tag!r}")
        return self

    @property
    def succeeded(self) -> bool:
        """True when no error occurred and output is non-empty."""
        return self.error is None and bool(self.output)
