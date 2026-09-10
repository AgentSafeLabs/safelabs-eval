"""Tests for the OWASP ASI prompt library."""
from __future__ import annotations

import re

from safelabs.prompts import get_critical_prompts, get_library, get_prompts_for_category
from safelabs.prompts.schemas import (
    KNOWN_ATLAS_TECHNIQUE_IDS,
    UNMAPPED,
    DifficultyTier,
    PromptCategory,
)

_ATLAS_ID_RE = re.compile(r"^AML\.T\d{4}(\.\d{3})?$")
_PROVENANCE_RE = re.compile(
    r"^(original|adapted-from:[a-z0-9][a-z0-9-]*|derived-from-cve:CVE-\d{4}-\d{4,})$"
)

# Categories reviewed 2026-09 and consciously accepted as having no clean
# MITRE ATLAS v5.6.0 technique. Any UNMAPPED entry outside this set is an
# accidental default, not a decision -- test_no_silently_defaulted_unmapped
# fails on it.
_APPROVED_UNMAPPED_CATEGORIES = {
    PromptCategory.ASI02_INSECURE_OUTPUT,
    PromptCategory.ASI10_HALLUCINATION,
}


# ── existing coverage ─────────────────────────────────────────────────────

def test_library_loads():
    assert len(get_library()) == 30


def test_library_version():
    lib = get_library()
    assert lib.version == "1.1.0"
    assert lib.schema_version == "1.1.0"


def test_all_categories_present():
    cats = {e.category for e in get_library().entries}
    for cat in PromptCategory:
        assert cat in cats


def test_three_prompts_per_category():
    lib = get_library()
    for cat in PromptCategory:
        assert len(lib.by_category(cat)) == 3


def test_get_prompts_for_category_string():
    prompts = get_prompts_for_category("ASI01")
    assert len(prompts) == 3
    assert all(p.category == PromptCategory.ASI01_PROMPT_INJECTION for p in prompts)


def test_get_critical_prompts():
    critical = get_critical_prompts()
    assert len(critical) > 0
    assert all(p.severity == "critical" for p in critical)


def test_prompt_ids_unique():
    ids = [e.id for e in get_library().entries]
    assert len(ids) == len(set(ids))


def test_all_entries_have_expected_behavior():
    for entry in get_library().entries:
        assert entry.expected_behavior, f"{entry.id} missing expected_behavior"


def test_correct_enum_member_names():
    assert PromptCategory.ASI05_TOOL_USE_SAFETY.value == "ASI05"
    assert PromptCategory.ASI07_TRUST_BOUNDARIES.value == "ASI07"


def test_library_singleton_cached():
    assert get_library() is get_library()


# ── v1.1.0 metadata: difficulty_tier ─────────────────────────────────────

def test_all_entries_have_difficulty_tier():
    for entry in get_library().entries:
        assert isinstance(entry.difficulty_tier, DifficultyTier), entry.id


def test_difficulty_tier_respects_severity_floor():
    """Rubric invariant: 'critical' severity starts at base tier 2, so it can
    never resolve to tier_1. 'high' / 'medium' may land anywhere in 1..3.
    This checks the *derivation*, not mere field presence.
    """
    for entry in get_library().entries:
        if entry.severity == "critical":
            assert entry.difficulty_tier in (
                DifficultyTier.TIER_2_CONTEXTUAL,
                DifficultyTier.TIER_3_ADAPTIVE,
            ), f"{entry.id}: critical severity must not be tier_1"


def test_difficulty_tier_spread_matches_migration():
    """The migrated 1.1.0 corpus is a fixed set; lock its tier distribution
    so an accidental bulk edit is caught.
    """
    from collections import Counter

    spread = Counter(e.difficulty_tier for e in get_library().entries)
    assert spread[DifficultyTier.TIER_1_OVERT] == 10
    assert spread[DifficultyTier.TIER_2_CONTEXTUAL] == 16
    assert spread[DifficultyTier.TIER_3_ADAPTIVE] == 4


# ── v1.1.0 metadata: provenance ──────────────────────────────────────────

def test_all_entries_have_provenance():
    for entry in get_library().entries:
        assert entry.provenance, f"{entry.id} missing provenance"
        assert _PROVENANCE_RE.match(entry.provenance), (
            f"{entry.id}: provenance {entry.provenance!r} outside allowed grammar"
        )


def test_all_current_prompts_are_original():
    """Every 1.0.0 prompt was assessed as original text (2026-09). The three
    with public-technique lineage (ASI01-003, ASI07-002, ASI08-002) keep
    provenance='original' by decision; their lineage is a code comment, not
    a field value. If a future prompt is a genuine adaptation this test
    should be narrowed, not deleted.
    """
    for entry in get_library().entries:
        assert entry.provenance == "original", (
            f"{entry.id}: unexpected non-original provenance {entry.provenance!r}"
        )


# ── v1.1.0 metadata: atlas_technique_ids ─────────────────────────────────

def test_all_entries_have_atlas_mapping():
    for entry in get_library().entries:
        ids = entry.atlas_technique_ids
        assert ids, f"{entry.id}: atlas_technique_ids is empty"
        if UNMAPPED in ids:
            assert ids == [UNMAPPED], f"{entry.id}: UNMAPPED mixed with real ids ({ids})"
        else:
            for tid in ids:
                assert _ATLAS_ID_RE.match(tid), f"{entry.id}: malformed ATLAS id {tid!r}"


def test_no_silently_defaulted_unmapped():
    """UNMAPPED must only appear in categories we consciously decided have no
    clean ATLAS technique -- and there, for every prompt in the category,
    not just some. A new UNMAPPED anywhere else fails here.
    """
    unmapped_cats = {
        e.category
        for e in get_library().entries
        if e.atlas_technique_ids == [UNMAPPED]
    }
    assert unmapped_cats == _APPROVED_UNMAPPED_CATEGORIES

    for cat in _APPROVED_UNMAPPED_CATEGORIES:
        entries = get_library().by_category(cat)
        assert all(e.atlas_technique_ids == [UNMAPPED] for e in entries), (
            f"{cat.value}: partial UNMAPPED -- some prompts mapped, some not"
        )


def test_atlas_ids_are_known():
    """Non-UNMAPPED ids must be in the curated KNOWN_ATLAS_TECHNIQUE_IDS set
    in schemas.py. Mapping a prompt to a new technique requires registering
    the id there in the same change. Also flags stale ids nothing uses.
    """
    used: set[str] = set()
    for entry in get_library().entries:
        if entry.atlas_technique_ids != [UNMAPPED]:
            used.update(entry.atlas_technique_ids)

    unknown = used - KNOWN_ATLAS_TECHNIQUE_IDS
    assert not unknown, (
        f"ATLAS ids used in library but not registered in "
        f"KNOWN_ATLAS_TECHNIQUE_IDS: {sorted(unknown)}"
    )
    unused = KNOWN_ATLAS_TECHNIQUE_IDS - used
    assert not unused, (
        f"KNOWN_ATLAS_TECHNIQUE_IDS lists ids nothing references: {sorted(unused)}"
    )
