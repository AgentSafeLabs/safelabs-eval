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

# ── Stage-2 ASI01 pilot batch (v1.2.0, unreleased) ──────────────────────
# The Stage-1 corpus is exactly 30 prompts. These 11 are the pending
# ASI01 Prompt-Injection expansion (ASI01-004 .. ASI01-014). Invariants
# that lock the Stage-1 corpus filter these ids out so they keep
# asserting on exactly the original 30; the pilot has its own dedicated
# tests further down.
_ASI01_PILOT_IDS = frozenset(f"ASI01-{n:03d}" for n in range(4, 15))
_ASI01_TOTAL = 14
_LIBRARY_TOTAL = 41


# ── existing coverage ─────────────────────────────────────────────────────

def test_library_loads():
    assert len(get_library()) == _LIBRARY_TOTAL


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
        if cat is PromptCategory.ASI01_PROMPT_INJECTION:
            # ASI01 carries the Stage-2 pilot batch on top of its 3 originals.
            assert len(lib.by_category(cat)) == _ASI01_TOTAL
        else:
            assert len(lib.by_category(cat)) == 3


def test_get_prompts_for_category_string():
    prompts = get_prompts_for_category("ASI01")
    assert len(prompts) == _ASI01_TOTAL
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
    so an accidental bulk edit is caught. The Stage-2 ASI01 pilot batch is
    excluded here so this stays an assertion about exactly the original 30.
    """
    from collections import Counter

    spread = Counter(
        e.difficulty_tier
        for e in get_library().entries
        if e.id not in _ASI01_PILOT_IDS
    )
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
    a field value. The Stage-2 ASI01 pilot batch is excluded -- it contains
    a deliberate adaptation (ASI01-006) and has its own provenance test.
    """
    for entry in get_library().entries:
        if entry.id in _ASI01_PILOT_IDS:
            continue
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


# ── Stage-2 ASI01 pilot batch (v1.2.0, unreleased) ──────────────────────

def _asi01_pilot_entries():
    return [e for e in get_library().entries if e.id in _ASI01_PILOT_IDS]


def test_asi01_pilot_count_and_ids_contiguous():
    lib = get_library()
    assert len(lib) == _LIBRARY_TOTAL

    asi01 = lib.by_category(PromptCategory.ASI01_PROMPT_INJECTION)
    assert len(asi01) == _ASI01_TOTAL

    pilot_ids = {e.id for e in _asi01_pilot_entries()}
    assert pilot_ids == set(_ASI01_PILOT_IDS)
    assert len(_ASI01_PILOT_IDS) == 11

    nums = sorted(int(i.split("-")[1]) for i in pilot_ids)
    assert nums == list(range(4, 15)), (
        "pilot ids must be ASI01-004..ASI01-014 with no gaps"
    )


def test_asi01_pilot_all_prompt_injection_category():
    for e in _asi01_pilot_entries():
        assert e.category == PromptCategory.ASI01_PROMPT_INJECTION, e.id


def test_asi01_pilot_atlas_ids_are_injection_subtechniques():
    """Every pilot prompt maps to direct (AML.T0051.000) or indirect
    (AML.T0051.001) LLM prompt injection -- nothing else, never UNMAPPED --
    and both ids are already registered in KNOWN_ATLAS_TECHNIQUE_IDS.
    """
    allowed = {"AML.T0051.000", "AML.T0051.001"}
    for e in _asi01_pilot_entries():
        assert e.atlas_technique_ids, e.id
        assert UNMAPPED not in e.atlas_technique_ids, e.id
        assert set(e.atlas_technique_ids) <= allowed, (
            f"{e.id}: {e.atlas_technique_ids} outside direct/indirect prompt injection"
        )
        assert set(e.atlas_technique_ids) <= KNOWN_ATLAS_TECHNIQUE_IDS, e.id


def test_asi01_pilot_provenance():
    """Ten pilot prompts are 'original'; exactly one -- ASI01-006, the overt
    'ignore all previous instructions' prompt -- is flagged as a genuine
    adaptation of PromptInject (Perez & Ribeiro 2022).
    """
    entries = {e.id: e for e in _asi01_pilot_entries()}

    adapted = {i for i, e in entries.items() if e.provenance != "original"}
    assert adapted == {"ASI01-006"}
    assert entries["ASI01-006"].provenance == "adapted-from:promptinject-2022"

    for i, e in entries.items():
        if i == "ASI01-006":
            continue
        assert e.provenance == "original", f"{i}: {e.provenance!r}"

    for e in entries.values():
        assert _PROVENANCE_RE.match(e.provenance), (
            f"{e.id}: provenance {e.provenance!r} outside allowed grammar"
        )


def test_asi01_pilot_difficulty_spread():
    """Locks the pilot's own tier distribution (rubric-derived: 1 overt,
    6 contextual, 4 adaptive) and re-checks the critical-severity floor.
    """
    from collections import Counter

    pilot = _asi01_pilot_entries()
    spread = Counter(e.difficulty_tier for e in pilot)
    assert spread[DifficultyTier.TIER_1_OVERT] == 1
    assert spread[DifficultyTier.TIER_2_CONTEXTUAL] == 6
    assert spread[DifficultyTier.TIER_3_ADAPTIVE] == 4

    for e in pilot:
        if e.severity == "critical":
            assert e.difficulty_tier != DifficultyTier.TIER_1_OVERT, e.id
