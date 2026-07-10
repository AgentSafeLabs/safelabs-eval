"""safelabs/scoring/base.py — abstract detector base class."""

from __future__ import annotations

from abc import ABC, abstractmethod

from safelabs.scoring.models import ScoringResult, VerdictLevel

_PUNCTUATION_NORMALIZATION = str.maketrans({
    "‘": "'",   # LEFT SINGLE QUOTATION MARK
    "’": "'",   # RIGHT SINGLE QUOTATION MARK (curly apostrophe)
    "“": '"',   # LEFT DOUBLE QUOTATION MARK
    "”": '"',   # RIGHT DOUBLE QUOTATION MARK
    "–": "-",   # EN DASH
    "—": "-",   # EM DASH
    "…": "...", # HORIZONTAL ELLIPSIS
})


def normalize_punctuation(text: str) -> str:
    """Normalize Unicode "smart punctuation" to its ASCII equivalent.

    Every detector's regex patterns are written with straight ASCII
    punctuation. Models that emit typographic punctuation instead (curly
    apostrophes in contractions, em-dashes, ellipses) silently fail every
    affected pattern match. Confirmed live during cross-framework
    integration testing: gpt-5.4-nano's refusal "I can't comply..." used
    U+2019 for the apostrophe, so the hardcoded ``can't`` pattern (straight
    ASCII ``'``) never matched, misclassifying a clean refusal as
    UNCERTAIN instead of PASS.

    Call once on `response` before any regex matching. Import this
    function rather than duplicating the substitution table in each
    detector file.
    """
    return text.translate(_PUNCTUATION_NORMALIZATION)


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
