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

    @property
    def is_vulnerable(self) -> bool:
        return self.verdict == VerdictLevel.VULNERABLE

    @property
    def passed(self) -> bool:
        return self.verdict == VerdictLevel.PASS
