"""safelabs/scoring/base.py — abstract detector base class."""

from __future__ import annotations

from abc import ABC, abstractmethod

from safelabs.scoring.echo import EchoContext, first_counted_match
from safelabs.scoring.models import ScoringResult, VerdictLevel

# Key the Scorer adds to the metadata it hands a detector when echo-aware scoring is on (issue #69).
# Absent -> the detector scores exactly as before.
ECHO_AWARE_KEY = "_echo_aware"

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

    @staticmethod
    def _echo_context(prompt: str, response: str, metadata: dict | None) -> EchoContext | None:
        """An EchoContext when the Scorer turned echo-aware scoring on, else None.
        ``response`` must be the string the patterns are matched against."""
        return EchoContext(prompt, response) if (metadata or {}).get(ECHO_AWARE_KEY) else None

    @staticmethod
    def _first_counted_match(pattern, response: str, ctx: EchoContext | None, echoed: list[str], label: str):
        """First match of ``pattern`` not fully inside an echo span (plain ``pattern.search`` when ctx is None)."""
        return first_counted_match(pattern, response, ctx, echoed, label)

    @classmethod
    def _counts(cls, pattern, response: str, ctx: EchoContext | None, echoed: list[str], label: str) -> bool:
        """True if ``pattern`` matches in a way that should count toward the score."""
        return cls._first_counted_match(pattern, response, ctx, echoed, label) is not None

    def _build_result(
        self,
        verdict: VerdictLevel,
        confidence: float,
        reasoning: str,
        indicators: list[str],
        severity: str = "medium",
        remediation_hint: str | None = None,
        echoed: list[str] | None = None,
    ) -> ScoringResult:
        if echoed:
            reasoning = f"{reasoning}, {len(echoed)} discounted as echo"
        return ScoringResult(
            verdict=verdict, confidence=confidence, reasoning=reasoning,
            indicators=indicators, eval_type=self.eval_type,
            severity=severity, remediation_hint=remediation_hint,
            echoed=list(echoed or []),
        )
