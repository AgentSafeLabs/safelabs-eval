"""Shared helpers for the judge tests. All strings are synthetic and non-harmful."""

from __future__ import annotations

from safelabs.judge.template import parse_template
from safelabs.scoring.base import BaseDetector
from safelabs.scoring.models import ScoringResult, VerdictLevel

TEMPLATE_TEXT = """# Synthetic test template

## SYSTEM
You label a response. Reply with JSON only.

## USER
Category: {{category}}
Prompt: {{prompt}}
Response: {{response}}
"""


def make_template(text: str = TEMPLATE_TEXT):
    return parse_template(text.encode("utf-8"), "memory")


class StubDetector(BaseDetector):
    """Verdict chosen by a marker in the response: P -> PASS, U -> UNCERTAIN, F -> FAIL (default UNCERTAIN)."""

    def __init__(self, eval_type: str = "prompt_injection") -> None:
        self._t = eval_type
        self.calls = 0

    @property
    def eval_type(self) -> str:
        return self._t

    async def detect(self, prompt, response, metadata=None):
        self.calls += 1
        v = VerdictLevel.PASS if "[P]" in response else VerdictLevel.FAIL if "[F]" in response else VerdictLevel.UNCERTAIN
        return ScoringResult(verdict=v, confidence=0.5, reasoning="stub", indicators=["stub"], eval_type=self._t)
