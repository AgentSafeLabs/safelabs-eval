"""safelabs/judge/result.py: JudgeResult and the JudgeBackend protocol."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from safelabs.judge.labels import JudgeLabel


class JudgeResult(BaseModel):
    label: JudgeLabel
    confidence: int = Field(ge=1, le=3)
    rationale: str = ""
    raw: str = ""
    backend_id: str
    template_hash: str
    latency_ms: float = 0.0
    usage: dict[str, int | None] = Field(default_factory=dict)  # input_tokens, output_tokens
    parsed_ok: bool = True
    error: str | None = None  # set when the backend call failed (label is then UNCLEAR)
    cached: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)  # e.g. neutralized_markers


@runtime_checkable
class JudgeBackend(Protocol):
    backend_id: str
    template_hash: str
    is_local: bool  # True only for a model that runs on this machine; external backends are False

    async def judge(
        self, prompt: str, response: str, category: str, *, functional_content: bool = False,
    ) -> JudgeResult: ...


class ContentGuardError(RuntimeError):
    """An item marked functional_content was about to go to a non-local backend."""


class CacheMiss(LookupError):
    """Replay mode found no cached result for the requested key."""


def enforce_content_guard(backend: object, functional_content: bool) -> None:
    if functional_content and not getattr(backend, "is_local", False):
        raise ContentGuardError(
            "item is marked functional_content; only a local backend may judge it"
            f" (backend {getattr(backend, 'backend_id', '?')} is not local)"
        )
