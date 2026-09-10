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

# ── Stage-3 batch 1 (v1.3.0, unreleased) ────────────────────────────────
# Two 10-prompt category expansions: ASI02 (Insecure Output Handling) and
# ASI03 (Excessive Agency), each ASI0x-004 .. ASI0x-013, taking both
# categories from 3 to 13. Same treatment as the Stage-2 pilot: the
# original-corpus and pilot invariants filter these ids out so they keep
# asserting on exactly the pre-existing set; the batches get their own
# dedicated tests further down.
_ASI02_BATCH_IDS = frozenset(f"ASI02-{n:03d}" for n in range(4, 14))
_ASI03_BATCH_IDS = frozenset(f"ASI03-{n:03d}" for n in range(4, 14))
_ASI02_TOTAL = 13
_ASI03_TOTAL = 13
_STAGE3_BATCH1_IDS = _ASI02_BATCH_IDS | _ASI03_BATCH_IDS

# ── Stage-3 batch 2 (v1.4.0, unreleased) ────────────────────────────────
# Two 10-prompt category expansions: ASI04 (Resource Management) and
# ASI05 (Tool Use Safety), each ASI0x-004 .. ASI0x-013, taking both
# categories from 3 to 13. Same treatment as the earlier batches: the
# original-corpus and prior-batch invariants filter these ids out so they
# keep asserting on exactly the pre-existing set; the batch gets its own
# dedicated tests further down.
_ASI04_BATCH_IDS = frozenset(f"ASI04-{n:03d}" for n in range(4, 14))
_ASI05_BATCH_IDS = frozenset(f"ASI05-{n:03d}" for n in range(4, 14))
_ASI04_TOTAL = 13
_ASI05_TOTAL = 13
_STAGE3_BATCH2_IDS = _ASI04_BATCH_IDS | _ASI05_BATCH_IDS

# ── Stage-3 batch 3 (v1.5.0, unreleased) ─────────────────────────────
# Two 10-prompt category expansions: ASI06 (Data Privacy &
# Confidentiality) and ASI10 (Hallucination), each ASI0x-004 .. ASI0x-013,
# taking both categories from 3 to 13. Same treatment as the earlier
# batches: the original-corpus and prior-batch invariants filter these
# ids out so they keep asserting on exactly the pre-existing set; the
# batch gets its own dedicated tests further down.
_ASI06_BATCH_IDS = frozenset(f"ASI06-{n:03d}" for n in range(4, 14))
_ASI10_BATCH_IDS = frozenset(f"ASI10-{n:03d}" for n in range(4, 14))
_ASI06_TOTAL = 13
_ASI10_TOTAL = 13
_STAGE3_BATCH3_IDS = _ASI06_BATCH_IDS | _ASI10_BATCH_IDS

# Every id added after the Stage-1 corpus of 30: the Stage-2 ASI01 pilot
# plus Stage-3 batches 1, 2 and 3. Invariants that lock the original 30
# filter on this.
_POST_STAGE1_IDS = (
    _ASI01_PILOT_IDS | _STAGE3_BATCH1_IDS | _STAGE3_BATCH2_IDS | _STAGE3_BATCH3_IDS
)

_LIBRARY_TOTAL = 101


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
    expanded = {
        PromptCategory.ASI01_PROMPT_INJECTION: _ASI01_TOTAL,   # Stage-2 pilot
        PromptCategory.ASI02_INSECURE_OUTPUT: _ASI02_TOTAL,    # Stage-3 batch 1
        PromptCategory.ASI03_EXCESSIVE_AGENCY: _ASI03_TOTAL,   # Stage-3 batch 1
        PromptCategory.ASI04_RESOURCE_MANAGEMENT: _ASI04_TOTAL,  # Stage-3 batch 2
        PromptCategory.ASI05_TOOL_USE_SAFETY: _ASI05_TOTAL,    # Stage-3 batch 2
        PromptCategory.ASI06_DATA_PRIVACY: _ASI06_TOTAL,       # Stage-3 batch 3
        PromptCategory.ASI10_HALLUCINATION: _ASI10_TOTAL,      # Stage-3 batch 3
    }
    for cat in PromptCategory:
        assert len(lib.by_category(cat)) == expanded.get(cat, 3)


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
    so an accidental bulk edit is caught. Everything added after Stage 1
    (the Stage-2 ASI01 pilot and Stage-3 batches 1, 2 and 3) is excluded
    here so this stays an assertion about exactly the original 30.
    """
    from collections import Counter

    spread = Counter(
        e.difficulty_tier
        for e in get_library().entries
        if e.id not in _POST_STAGE1_IDS
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
    a field value. Post-Stage-1 additions are excluded -- the Stage-2 ASI01
    pilot contains a deliberate adaptation (ASI01-006), and Stage-3 batches
    1, 2 and 3 have their own provenance tests -- so this stays an
    assertion about exactly the original 30.
    """
    for entry in get_library().entries:
        if entry.id in _POST_STAGE1_IDS:
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


# ── Stage-3 batch 1: ASI02 + ASI03 (v1.3.0, unreleased) ─────────────────

def _asi02_batch_entries():
    return [e for e in get_library().entries if e.id in _ASI02_BATCH_IDS]


def _asi03_batch_entries():
    return [e for e in get_library().entries if e.id in _ASI03_BATCH_IDS]


def test_stage3_batch1_count_and_ids_contiguous():
    lib = get_library()
    assert len(lib) == _LIBRARY_TOTAL

    for cat, total, ids in (
        (PromptCategory.ASI02_INSECURE_OUTPUT, _ASI02_TOTAL, _ASI02_BATCH_IDS),
        (PromptCategory.ASI03_EXCESSIVE_AGENCY, _ASI03_TOTAL, _ASI03_BATCH_IDS),
    ):
        entries = lib.by_category(cat)
        assert len(entries) == total

        batch_ids = {e.id for e in entries if e.id in ids}
        assert batch_ids == set(ids)
        assert len(ids) == 10

        prefix = cat.value
        nums = sorted(int(i.split("-")[1]) for i in batch_ids)
        assert nums == list(range(4, 14)), (
            f"{prefix} batch ids must be {prefix}-004..{prefix}-013 with no gaps"
        )


def test_stage3_batch1_category_correctness():
    for e in _asi02_batch_entries():
        assert e.category == PromptCategory.ASI02_INSECURE_OUTPUT, e.id
    for e in _asi03_batch_entries():
        assert e.category == PromptCategory.ASI03_EXCESSIVE_AGENCY, e.id


def test_stage3_batch1_atlas_mapping_per_category():
    """ASI02 has no clean ATLAS technique -- every batch entry is exactly
    ['UNMAPPED'] (never mixed, never a real id). Every ASI03 batch entry
    maps to exactly ['AML.T0053'] (AI Agent Tool Invocation), which is
    already registered in KNOWN_ATLAS_TECHNIQUE_IDS.
    """
    for e in _asi02_batch_entries():
        assert e.atlas_technique_ids == [UNMAPPED], f"{e.id}: {e.atlas_technique_ids}"

    for e in _asi03_batch_entries():
        assert e.atlas_technique_ids == ["AML.T0053"], f"{e.id}: {e.atlas_technique_ids}"
        assert set(e.atlas_technique_ids) <= KNOWN_ATLAS_TECHNIQUE_IDS, e.id


def test_stage3_batch1_provenance_all_original():
    """All 20 Stage-3 batch-1 prompts are original text -- generic
    CWE- / OWASP-class scenarios with no construction traceable to a
    specific named research artefact, so none is an adaptation.
    """
    for e in _asi02_batch_entries() + _asi03_batch_entries():
        assert e.provenance == "original", f"{e.id}: {e.provenance!r}"
        assert _PROVENANCE_RE.match(e.provenance), e.id


def test_stage3_batch1_difficulty_spread():
    """Locks each batch's rubric-derived tier distribution and re-checks
    the critical-severity floor.
      ASI02: tier_1 x7, tier_2 x2, tier_3 x1
      ASI03: tier_1 x4, tier_2 x5, tier_3 x1
    """
    from collections import Counter

    asi02 = _asi02_batch_entries()
    s2 = Counter(e.difficulty_tier for e in asi02)
    assert s2[DifficultyTier.TIER_1_OVERT] == 7
    assert s2[DifficultyTier.TIER_2_CONTEXTUAL] == 2
    assert s2[DifficultyTier.TIER_3_ADAPTIVE] == 1

    asi03 = _asi03_batch_entries()
    s3 = Counter(e.difficulty_tier for e in asi03)
    assert s3[DifficultyTier.TIER_1_OVERT] == 4
    assert s3[DifficultyTier.TIER_2_CONTEXTUAL] == 5
    assert s3[DifficultyTier.TIER_3_ADAPTIVE] == 1

    for e in asi02 + asi03:
        if e.severity == "critical":
            assert e.difficulty_tier != DifficultyTier.TIER_1_OVERT, e.id


# ── Stage-3 batch 2: ASI04 + ASI05 (v1.4.0, unreleased) ─────────────────

def _asi04_batch_entries():
    return [e for e in get_library().entries if e.id in _ASI04_BATCH_IDS]


def _asi05_batch_entries():
    return [e for e in get_library().entries if e.id in _ASI05_BATCH_IDS]


def test_stage3_batch2_count_and_ids_contiguous():
    lib = get_library()
    assert len(lib) == _LIBRARY_TOTAL

    for cat, total, ids in (
        (PromptCategory.ASI04_RESOURCE_MANAGEMENT, _ASI04_TOTAL, _ASI04_BATCH_IDS),
        (PromptCategory.ASI05_TOOL_USE_SAFETY, _ASI05_TOTAL, _ASI05_BATCH_IDS),
    ):
        entries = lib.by_category(cat)
        assert len(entries) == total

        batch_ids = {e.id for e in entries if e.id in ids}
        assert batch_ids == set(ids)
        assert len(ids) == 10

        prefix = cat.value
        nums = sorted(int(i.split("-")[1]) for i in batch_ids)
        assert nums == list(range(4, 14)), (
            f"{prefix} batch ids must be {prefix}-004..{prefix}-013 with no gaps"
        )


def test_stage3_batch2_category_correctness():
    for e in _asi04_batch_entries():
        assert e.category == PromptCategory.ASI04_RESOURCE_MANAGEMENT, e.id
    for e in _asi05_batch_entries():
        assert e.category == PromptCategory.ASI05_TOOL_USE_SAFETY, e.id


def test_stage3_batch2_atlas_mapping_per_category():
    """Neither batch is ever UNMAPPED and neither introduces a new id.
    ASI04 entries draw only from the Stage-1 ASI04 set
    {AML.T0034.000, AML.T0034.002, AML.T0029}; ASI05 entries draw only
    from {AML.T0053, AML.T0050} and every one includes AML.T0053. All ids
    used are already registered in KNOWN_ATLAS_TECHNIQUE_IDS.
    """
    asi04_allowed = {"AML.T0034.000", "AML.T0034.002", "AML.T0029"}
    for e in _asi04_batch_entries():
        assert e.atlas_technique_ids, e.id
        assert UNMAPPED not in e.atlas_technique_ids, e.id
        assert set(e.atlas_technique_ids) <= asi04_allowed, (
            f"{e.id}: {e.atlas_technique_ids} outside the Stage-1 ASI04 set"
        )
        assert set(e.atlas_technique_ids) <= KNOWN_ATLAS_TECHNIQUE_IDS, e.id

    asi05_allowed = {"AML.T0053", "AML.T0050"}
    for e in _asi05_batch_entries():
        assert e.atlas_technique_ids, e.id
        assert UNMAPPED not in e.atlas_technique_ids, e.id
        assert "AML.T0053" in e.atlas_technique_ids, (
            f"{e.id}: every ASI05 entry must map to AML.T0053"
        )
        assert set(e.atlas_technique_ids) <= asi05_allowed, (
            f"{e.id}: {e.atlas_technique_ids} outside tool-invocation / scripting"
        )
        assert set(e.atlas_technique_ids) <= KNOWN_ATLAS_TECHNIQUE_IDS, e.id


def test_stage3_batch2_provenance_all_original():
    """All 20 Stage-3 batch-2 prompts are original text -- generic
    resource-exhaustion / cost-harvesting and confused-deputy /
    tool-misuse scenarios with no construction traceable to a specific
    named research artefact, so none is an adaptation.
    """
    for e in _asi04_batch_entries() + _asi05_batch_entries():
        assert e.provenance == "original", f"{e.id}: {e.provenance!r}"
        assert _PROVENANCE_RE.match(e.provenance), e.id


def test_stage3_batch2_difficulty_spread():
    """Locks each batch's rubric-derived tier distribution and re-checks
    the critical-severity floor.
      ASI04: tier_1 x7, tier_2 x2, tier_3 x1
      ASI05: tier_1 x5, tier_2 x3, tier_3 x2
    """
    from collections import Counter

    asi04 = _asi04_batch_entries()
    s4 = Counter(e.difficulty_tier for e in asi04)
    assert s4[DifficultyTier.TIER_1_OVERT] == 7
    assert s4[DifficultyTier.TIER_2_CONTEXTUAL] == 2
    assert s4[DifficultyTier.TIER_3_ADAPTIVE] == 1

    asi05 = _asi05_batch_entries()
    s5 = Counter(e.difficulty_tier for e in asi05)
    assert s5[DifficultyTier.TIER_1_OVERT] == 5
    assert s5[DifficultyTier.TIER_2_CONTEXTUAL] == 3
    assert s5[DifficultyTier.TIER_3_ADAPTIVE] == 2

    for e in asi04 + asi05:
        if e.severity == "critical":
            assert e.difficulty_tier != DifficultyTier.TIER_1_OVERT, e.id


# ── Stage-3 batch 3: ASI06 + ASI10 (v1.5.0, unreleased) ─────────────

def _asi06_batch_entries():
    return [e for e in get_library().entries if e.id in _ASI06_BATCH_IDS]


def _asi10_batch_entries():
    return [e for e in get_library().entries if e.id in _ASI10_BATCH_IDS]


def test_stage3_batch3_count_and_ids_contiguous():
    lib = get_library()
    assert len(lib) == _LIBRARY_TOTAL

    for cat, total, ids in (
        (PromptCategory.ASI06_DATA_PRIVACY, _ASI06_TOTAL, _ASI06_BATCH_IDS),
        (PromptCategory.ASI10_HALLUCINATION, _ASI10_TOTAL, _ASI10_BATCH_IDS),
    ):
        entries = lib.by_category(cat)
        assert len(entries) == total

        batch_ids = {e.id for e in entries if e.id in ids}
        assert batch_ids == set(ids)
        assert len(ids) == 10

        prefix = cat.value
        nums = sorted(int(i.split("-")[1]) for i in batch_ids)
        assert nums == list(range(4, 14)), (
            f"{prefix} batch ids must be {prefix}-004..{prefix}-013 with no gaps"
        )


def test_stage3_batch3_category_correctness():
    for e in _asi06_batch_entries():
        assert e.category == PromptCategory.ASI06_DATA_PRIVACY, e.id
    for e in _asi10_batch_entries():
        assert e.category == PromptCategory.ASI10_HALLUCINATION, e.id


def test_stage3_batch3_atlas_mapping_per_category():
    """ASI06 is a mapped category -- every batch entry draws only from the
    Stage-1 ASI06 set {AML.T0056, AML.T0057}, is non-empty, and is never
    UNMAPPED; all ids are already in KNOWN_ATLAS_TECHNIQUE_IDS. ASI10 has
    no clean ATLAS technique -- every batch entry is exactly ['UNMAPPED'].
    """
    asi06_allowed = {"AML.T0056", "AML.T0057"}
    for e in _asi06_batch_entries():
        assert e.atlas_technique_ids, e.id
        assert UNMAPPED not in e.atlas_technique_ids, e.id
        assert set(e.atlas_technique_ids) <= asi06_allowed, (
            f"{e.id}: {e.atlas_technique_ids} outside the Stage-1 ASI06 set"
        )
        assert set(e.atlas_technique_ids) <= KNOWN_ATLAS_TECHNIQUE_IDS, e.id

    for e in _asi10_batch_entries():
        assert e.atlas_technique_ids == [UNMAPPED], f"{e.id}: {e.atlas_technique_ids}"


def test_stage3_batch3_provenance_all_original():
    """All 20 Stage-3 batch-3 prompts are original text -- generic
    data-disclosure and hallucination-elicitation scenarios with no
    construction traceable to a specific named research artefact, so none
    is an adaptation.
    """
    for e in _asi06_batch_entries() + _asi10_batch_entries():
        assert e.provenance == "original", f"{e.id}: {e.provenance!r}"
        assert _PROVENANCE_RE.match(e.provenance), e.id


def test_stage3_batch3_difficulty_spread():
    """Locks each batch's rubric-derived tier distribution and re-checks
    the critical-severity floor.
      ASI06: tier_1 x5, tier_2 x3, tier_3 x2
      ASI10: tier_1 x7, tier_2 x3, tier_3 x0
    """
    from collections import Counter

    asi06 = _asi06_batch_entries()
    s6 = Counter(e.difficulty_tier for e in asi06)
    assert s6[DifficultyTier.TIER_1_OVERT] == 5
    assert s6[DifficultyTier.TIER_2_CONTEXTUAL] == 3
    assert s6[DifficultyTier.TIER_3_ADAPTIVE] == 2

    asi10 = _asi10_batch_entries()
    s10 = Counter(e.difficulty_tier for e in asi10)
    assert s10[DifficultyTier.TIER_1_OVERT] == 7
    assert s10[DifficultyTier.TIER_2_CONTEXTUAL] == 3
    assert s10[DifficultyTier.TIER_3_ADAPTIVE] == 0

    for e in asi06 + asi10:
        if e.severity == "critical":
            assert e.difficulty_tier != DifficultyTier.TIER_1_OVERT, e.id
