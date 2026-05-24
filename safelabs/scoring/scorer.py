"""safelabs/scoring/scorer.py — orchestrates detector dispatch."""

from __future__ import annotations
import asyncio
import logging
from safelabs.scoring.base import BaseDetector
from safelabs.scoring.detectors.data_leakage import DataLeakageDetector
from safelabs.scoring.detectors.hallucination import HallucinationDetector
from safelabs.scoring.detectors.jailbreak import JailbreakDetector
from safelabs.scoring.detectors.prompt_injection import PromptInjectionDetector
from safelabs.scoring.detectors.scope_violation import ScopeViolationDetector
from safelabs.scoring.models import ScoringResult, VerdictLevel

logger = logging.getLogger(__name__)

_DEFAULT_DETECTORS: list[BaseDetector] = [
    PromptInjectionDetector(), JailbreakDetector(),
    DataLeakageDetector(), HallucinationDetector(), ScopeViolationDetector(),
]


class Scorer:
    """Orchestrates detector dispatch. Thread-safe for read access."""

    def __init__(self, detectors: list[BaseDetector] | None = None) -> None:
        self._registry: dict[str, BaseDetector] = {}
        for d in (detectors or _DEFAULT_DETECTORS):
            self._registry[d.eval_type] = d

    @property
    def available_eval_types(self) -> list[str]:
        return sorted(self._registry)

    def register(self, detector: BaseDetector) -> None:
        self._registry[detector.eval_type] = detector

    async def score(
        self, eval_type: str, prompt: str, response: str, metadata: dict | None = None,
    ) -> ScoringResult:
        """Run detector for eval_type. Returns UNCERTAIN if not registered."""
        detector = self._registry.get(eval_type)
        if detector is None:
            logger.debug("No detector for eval_type=%s — returning UNCERTAIN", eval_type)
            return ScoringResult(
                verdict=VerdictLevel.UNCERTAIN, confidence=0.0,
                reasoning=f"No detector registered for eval_type '{eval_type}'",
                indicators=[], eval_type=eval_type, severity="low",
            )
        return await detector.detect(prompt=prompt, response=response, metadata=metadata or {})

    async def score_all(
        self, prompt: str, response: str, metadata: dict | None = None,
    ) -> dict[str, ScoringResult]:
        """Run all registered detectors concurrently."""
        tasks = {et: d.detect(prompt, response, metadata) for et, d in self._registry.items()}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        return {et: r for et, r in zip(tasks, results) if isinstance(r, ScoringResult)}
