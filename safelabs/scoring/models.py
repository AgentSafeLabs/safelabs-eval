"""safelabs/scoring/models.py — core scoring data models."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class VerdictLevel(str, Enum):
    PASS       = "pass"
    UNCERTAIN  = "uncertain"
    FAIL       = "fail"
    VULNERABLE = "vulnerable"


class ScoringResult(BaseModel):
    verdict: VerdictLevel
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    indicators: list[str] = Field(default_factory=list)
    eval_type: str
    severity: str = "medium"
    remediation_hint: str | None = None
    # Indicator matches discounted because they lay inside text copied from the prompt (echo-aware
    # scoring, issue #69). Internal only: excluded from model_dump()/JSON, so serialized output is unchanged.
    echoed: list[str] = Field(default_factory=list, exclude=True)

    @property
    def is_vulnerable(self) -> bool:
        return self.verdict == VerdictLevel.VULNERABLE

    @property
    def passed(self) -> bool:
        return self.verdict == VerdictLevel.PASS
