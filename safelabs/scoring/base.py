"""safelabs/scoring/base.py — abstract detector base class."""

from __future__ import annotations

from abc import ABC, abstractmethod

from safelabs.scoring.models import ScoringResult, VerdictLevel


class BaseDetector(ABC):
    @property
    @abstractmethod
    def eval_type(self) -> str: ...

    @abstractmethod
    async def detect(
        self, prompt: str, response: str, metadata: dict | None = None,
    ) -> ScoringResult: ...

    def _build_result(
        self,
        verdict: VerdictLevel,
        confidence: float,
        reasoning: str,
        indicators: list[str],
        severity: str = "medium",
        remediation_hint: str | None = None,
    ) -> ScoringResult:
        return ScoringResult(
            verdict=verdict, confidence=confidence, reasoning=reasoning,
            indicators=indicators, eval_type=self.eval_type,
            severity=severity, remediation_hint=remediation_hint,
        )
