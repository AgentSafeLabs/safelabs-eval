"""
safelabs/prompts/schemas.py

Pydantic models for the OWASP ASI prompt library.

Schema version 1.1.0 (2026-09) added three required metadata fields to
``PromptEntry``: ``difficulty_tier``, ``provenance``, and
``atlas_technique_ids``. The ATLAS mapping was built against MITRE ATLAS
v5.6.0 (atlas-data release ``v2026.08``). The difficulty-tier rubric and
the full per-prompt mapping rationale live in ``library.py``.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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


class DifficultyTier(str, Enum):
    """How hard a correctly-aligned agent has to work to refuse the prompt.

    Derived from this corpus's own CRITICAL/HIGH severity split (the
    backbone) plus one signal already present in each entry's ``tags``
    (an "evasion vehicle"). This is deliberately *not* an imported
    external difficulty scale. Full derivation rubric: ``library.py``.
    """

    TIER_1_OVERT      = "tier_1"   # single-turn, self-evident adversarial ask
    TIER_2_CONTEXTUAL = "tier_2"   # surface-legitimate; needs scope/impact reasoning to refuse
    TIER_3_ADAPTIVE   = "tier_3"   # attack actively works to defeat refusal


# ``provenance`` grammar: exactly "original", OR "adapted-from:<slug>", OR
# "derived-from-cve:CVE-YYYY-NNNN". All 30 v1.0.0 prompts are "original";
# the prefixed forms exist for future additions. Technique *lineage* for a
# handful of otherwise-original prompts is recorded as a code comment above
# the entry in library.py, NOT encoded here.
_PROVENANCE_RE = re.compile(
    r"^(original"
    r"|adapted-from:[a-z0-9][a-z0-9-]*"
    r"|derived-from-cve:CVE-\d{4}-\d{4,})$"
)

# ATLAS technique-id grammar: AML.T#### with an optional .### sub-technique.
_ATLAS_ID_RE = re.compile(r"^AML\.T\d{4}(\.\d{3})?$")

# Sentinel for a prompt/category with no clean ATLAS technique. Must be the
# sole element of ``atlas_technique_ids`` when used (never mixed with real
# ids) so that "no mapping" is always an explicit, visible decision.
UNMAPPED = "UNMAPPED"

# The ATLAS techniques this library actually maps prompts to, built against
# MITRE ATLAS v5.6.0 (atlas-data release v2026.08). This is a *curated
# subset* of the ATLAS matrix, not the whole thing.
#
# When you map a new prompt to an ATLAS technique that is not already in
# this set, add its id here in the SAME change -- otherwise
# ``tests/test_prompts.py::test_atlas_ids_are_known`` fails. The schema
# validator below only checks id *grammar*, not membership, so that a
# future ATLAS release never breaks ``PromptEntry`` construction; this set
# is the deliberate-vocabulary-expansion gate instead.
KNOWN_ATLAS_TECHNIQUE_IDS: frozenset[str] = frozenset(
    {
        "AML.T0010",      # AI Supply Chain Compromise
        "AML.T0011.001",  # User Execution: Malicious Package
        "AML.T0029",      # Denial of AI Service
        "AML.T0034.000",  # Cost Harvesting: Excessive Queries
        "AML.T0034.002",  # Cost Harvesting: Agentic Resource Consumption
        "AML.T0050",      # Command and Scripting Interpreter
        "AML.T0051.000",  # LLM Prompt Injection: Direct
        "AML.T0051.001",  # LLM Prompt Injection: Indirect
        "AML.T0053",      # AI Agent Tool Invocation
        "AML.T0054",      # LLM Jailbreak
        "AML.T0056",      # Extract LLM System Prompt
        "AML.T0057",      # LLM Data Leakage
    }
)


class PromptEntry(BaseModel):
    """A single adversarial test prompt with metadata."""

    id: str = Field(description="Unique identifier, e.g. 'ASI01-001'.")
    category: PromptCategory
    severity: Severity
    prompt: str
    expected_behavior: str
    tags: list[str] = Field(default_factory=list)

    # -- v1.1.0 metadata: all required, no defaults, so neither a migration
    #    nor a new entry can silently omit them ------------------------------
    difficulty_tier: DifficultyTier = Field(
        description="Refusal difficulty; derived from severity. See library.py rubric.",
    )
    provenance: str = Field(
        description=(
            "'original' | 'adapted-from:<slug>' | 'derived-from-cve:CVE-YYYY-NNNN'."
        ),
    )
    atlas_technique_ids: list[str] = Field(
        description=(
            "MITRE ATLAS v5.6.0 technique ids (AML.T####[.###]), or the single "
            "literal ['UNMAPPED'] when no ATLAS technique cleanly applies."
        ),
    )

    @field_validator("provenance")
    @classmethod
    def _validate_provenance(cls, v: str) -> str:
        if not _PROVENANCE_RE.match(v):
            raise ValueError(
                f"provenance {v!r} does not match the allowed grammar: "
                "'original' | 'adapted-from:<slug>' | 'derived-from-cve:CVE-YYYY-NNNN'"
            )
        return v

    @field_validator("atlas_technique_ids")
    @classmethod
    def _validate_atlas_technique_ids(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError(
                "atlas_technique_ids must not be empty -- set ['UNMAPPED'] explicitly "
                "when no ATLAS technique applies"
            )
        if UNMAPPED in v and v != [UNMAPPED]:
            raise ValueError("'UNMAPPED' cannot be combined with real technique ids")
        for item in v:
            if item != UNMAPPED and not _ATLAS_ID_RE.match(item):
                raise ValueError(f"malformed ATLAS technique id: {item!r}")
        return v


class PromptLibrary(BaseModel):
    """The full OWASP ASI adversarial prompt collection."""

    entries: list[PromptEntry]
    version: str = Field(
        description="Content version -- bumps when prompts are added, changed, or removed.",
    )
    schema_version: str = Field(
        description="PromptEntry shape version -- bumps when fields are added or changed.",
    )

    def by_category(self, category: "PromptCategory | str") -> list[PromptEntry]:
        if isinstance(category, str):
            category = PromptCategory(category)
        return [e for e in self.entries if e.category == category]

    def by_severity(self, severity: str) -> list[PromptEntry]:
        return [e for e in self.entries if e.severity == severity]

    def by_tag(self, tag: str) -> list[PromptEntry]:
        return [e for e in self.entries if tag in e.tags]

    def by_difficulty_tier(self, tier: "DifficultyTier | str") -> list[PromptEntry]:
        if isinstance(tier, str):
            tier = DifficultyTier(tier)
        return [e for e in self.entries if e.difficulty_tier == tier]

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
