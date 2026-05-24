"""
safelabs/prompts/schemas.py

Pydantic models for the OWASP ASI prompt library.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class PromptCategory(str, Enum):
    """OWASP Agentic Security Initiative (ASI) Top 10 categories."""

    ASI01_PROMPT_INJECTION    = "ASI01"
    ASI02_INSECURE_OUTPUT     = "ASI02"
    ASI03_EXCESSIVE_AGENCY    = "ASI03"
    ASI04_RESOURCE_MANAGEMENT = "ASI04"
    ASI05_TOOL_USE_SAFETY     = "ASI05"
    ASI06_DATA_PRIVACY        = "ASI06"
    ASI07_TRUST_BOUNDARIES    = "ASI07"
    ASI08_BEHAVIORAL_DRIFT    = "ASI08"
    ASI09_SCOPE_VIOLATION     = "ASI09"
    ASI10_HALLUCINATION       = "ASI10"


Severity = Literal["low", "medium", "high", "critical"]


class PromptEntry(BaseModel):
    """A single adversarial test prompt with metadata."""

    id: str = Field(description="Unique identifier, e.g. 'ASI01-001'.")
    category: PromptCategory
    severity: Severity
    prompt: str
    expected_behavior: str
    tags: list[str] = Field(default_factory=list)


class PromptLibrary(BaseModel):
    """The full OWASP ASI adversarial prompt collection."""

    entries: list[PromptEntry]
    version: str

    def by_category(self, category: "PromptCategory | str") -> list[PromptEntry]:
        if isinstance(category, str):
            category = PromptCategory(category)
        return [e for e in self.entries if e.category == category]

    def by_severity(self, severity: str) -> list[PromptEntry]:
        return [e for e in self.entries if e.severity == severity]

    def by_tag(self, tag: str) -> list[PromptEntry]:
        return [e for e in self.entries if tag in e.tags]

    def categories(self) -> list[PromptCategory]:
        seen: set = set()
        result = []
        for e in self.entries:
            if e.category not in seen:
                seen.add(e.category)
                result.append(e.category)
        return result

    def __len__(self) -> int:
        return len(self.entries)
