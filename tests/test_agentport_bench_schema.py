"""
tests/test_agentport_bench_schema.py

Tests for agentport_bench.schema -- BenchTrialResult, the attack-family
taxonomy, and the library-version comparability registry.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentport_bench.schema import (
    ATTACK_FAMILY_CATEGORIES,
    CATEGORY_ATTACK_FAMILY,
    KNOWN_LIBRARY_VERSIONS,
    VERDICT_WEIGHT,
    AttackFamily,
    BenchTrialResult,
    compute_payload_hash,
    is_library_version_comparable,
)
from safelabs.prompts import load_library
from safelabs.prompts.schemas import PromptCategory
from safelabs.scoring.models import VerdictLevel


def _valid_row(**overrides: object) -> dict:
    row = dict(
        model="claude-opus-4-8",
        provider="anthropic",
        framework="http",
        attack_family=AttackFamily.INJECTION_OUTPUT,
        category=PromptCategory.ASI01_PROMPT_INJECTION,
        prompt_id="ASI01-001",
        trial_seed=0,
        verdict=VerdictLevel.PASS,
        confidence=0.9,
        payload_hash="a" * 64,
        timestamp="2026-09-10T12:00:00Z",
        harness_version="0.1.0",
        library_version="1.6.0",
    )
    row.update(overrides)
    return row


# ── happy path ────────────────────────────────────────────────────────────

def test_valid_row_constructs():
    r = BenchTrialResult(**_valid_row())
    assert r.model == "claude-opus-4-8"
    assert r.verdict == VerdictLevel.PASS
    assert r.attack_family == AttackFamily.INJECTION_OUTPUT


def test_optional_fields_default_correctly():
    r = BenchTrialResult(**_valid_row())
    assert r.provider == "anthropic"  # explicitly set above
    r2 = BenchTrialResult(**{k: v for k, v in _valid_row().items() if k != "provider"})
    assert r2.provider is None
    assert r2.attack_success_weight is None
    assert r2.latency_ms is None
    assert r2.error is None
    assert r2.indicators == []
    assert r2.usage is None


# ── prompt_id grammar ────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_id", ["asi01-001", "ASI1-001", "ASI01_001", "ASI01-01", "ASI01-0001", ""])
def test_prompt_id_rejects_bad_grammar(bad_id):
    with pytest.raises(ValidationError):
        BenchTrialResult(**_valid_row(prompt_id=bad_id))


def test_prompt_id_accepts_all_ten_category_prefixes():
    for n in range(1, 11):
        BenchTrialResult(**_valid_row(prompt_id=f"ASI{n:02d}-001"))


# ── payload_hash grammar ─────────────────────────────────────────────────

@pytest.mark.parametrize("bad_hash", [
    "A" * 64,           # uppercase not allowed
    "a" * 63,           # too short
    "a" * 65,           # too long
    "g" * 64,           # non-hex char
    "",
])
def test_payload_hash_rejects_bad_grammar(bad_hash):
    with pytest.raises(ValidationError):
        BenchTrialResult(**_valid_row(payload_hash=bad_hash))


def _reference_payload_hash(prompt_id: str, model: str, framework: str, trial_seed: int, raw_output: str) -> str:
    """
    Mirrors BenchTrialResult.payload_hash's documented construction --
    sha256(prompt_id + model + framework + str(trial_seed) + "\n" +
    raw_model_output) -- exactly, field by field, so this test fails if
    that documented order/separator ever drifts from what's actually
    checked here.

    The real implementation is schema.compute_payload_hash() --
    test_compute_payload_hash_matches_reference_construction below
    asserts the two never drift apart. This function stays as an
    independent, deliberately-duplicated oracle (not a call to
    compute_payload_hash() itself) so a bug introduced into the real
    implementation cannot also silently corrupt the thing checking it.
    """
    import hashlib
    payload = prompt_id + model + framework + str(trial_seed) + "\n" + raw_output
    return hashlib.sha256(payload.encode()).hexdigest()


def test_payload_hash_accepts_documented_construction():
    row = _valid_row()
    digest = _reference_payload_hash(
        prompt_id=row["prompt_id"],
        model=row["model"],
        framework=row["framework"],
        trial_seed=row["trial_seed"],
        raw_output="some output",
    )
    r = BenchTrialResult(**_valid_row(payload_hash=digest))
    assert r.payload_hash == digest


def test_payload_hash_is_sensitive_to_full_trial_identity():
    """
    Confirms the hash is bound to model/framework/trial_seed, not just
    prompt_id + raw_output -- the exact strengthening this field's
    description documents. Changing any one identity component (holding
    raw_output fixed) must change the hash, and swapping the
    documented field order must also change it -- otherwise the
    construction isn't actually binding the full trial identity, just
    concatenating the same substrings in some order.
    """
    base = _reference_payload_hash("ASI01-001", "claude-opus-4-8", "http", 0, "some output")

    different_model    = _reference_payload_hash("ASI01-001", "gpt-5.5", "http", 0, "some output")
    different_framework = _reference_payload_hash("ASI01-001", "claude-opus-4-8", "langchain", 0, "some output")
    different_seed       = _reference_payload_hash("ASI01-001", "claude-opus-4-8", "http", 1, "some output")
    different_prompt_id   = _reference_payload_hash("ASI01-002", "claude-opus-4-8", "http", 0, "some output")

    assert len({base, different_model, different_framework, different_seed, different_prompt_id}) == 5

    import hashlib
    reordered = hashlib.sha256(
        ("claude-opus-4-8" + "ASI01-001" + "http" + "0" + "\n" + "some output").encode()
    ).hexdigest()
    assert reordered != base


def test_compute_payload_hash_matches_reference_construction():
    """
    Ties schema.compute_payload_hash() -- the real implementation harness.py
    and validate.py both use -- to the independent reference oracle above.
    If the two ever disagree, either the implementation drifted from its
    documented construction or the reference (and the documentation) is
    wrong; either way this must fail loudly.
    """
    cases = [
        ("ASI01-001", "claude-opus-4-8", "http", 0, "some output"),
        ("ASI10-013", "gpt-5.5", "langchain", 3, ""),
        ("ASI06-005", "gemini-3.5-flash", "crewai", 7, "multi\nline\noutput"),
    ]
    for prompt_id, model, framework, trial_seed, raw_output in cases:
        expected = _reference_payload_hash(prompt_id, model, framework, trial_seed, raw_output)
        actual = compute_payload_hash(
            prompt_id=prompt_id, model=model, framework=framework,
            trial_seed=trial_seed, raw_output=raw_output,
        )
        assert actual == expected


def test_compute_payload_hash_output_satisfies_field_grammar():
    """The real implementation's output must itself pass payload_hash's
    own grammar validator, round-tripped through the model."""
    digest = compute_payload_hash(
        prompt_id="ASI01-001", model="claude-opus-4-8", framework="http",
        trial_seed=0, raw_output="some output",
    )
    r = BenchTrialResult(**_valid_row(payload_hash=digest))
    assert r.payload_hash == digest


# ── harness_version / library_version grammar ────────────────────────────

@pytest.mark.parametrize("bad_version", ["v1.6.0", "1.6", "1.6.0-beta", "1.6.0.1", "latest", ""])
def test_harness_version_rejects_bad_grammar(bad_version):
    with pytest.raises(ValidationError):
        BenchTrialResult(**_valid_row(harness_version=bad_version))


@pytest.mark.parametrize("bad_version", ["v1.6.0", "1.6", "1.6.0-beta", ""])
def test_library_version_rejects_bad_grammar(bad_version):
    with pytest.raises(ValidationError):
        BenchTrialResult(**_valid_row(library_version=bad_version))


# ── numeric bounds ────────────────────────────────────────────────────────

@pytest.mark.parametrize("field,bad_value", [
    ("confidence", -0.01),
    ("confidence", 1.01),
    ("trial_seed", -1),
    ("attack_success_weight", -0.01),
    ("attack_success_weight", 1.01),
    ("latency_ms", -1.0),
])
def test_numeric_bounds_enforced(field, bad_value):
    with pytest.raises(ValidationError):
        BenchTrialResult(**_valid_row(**{field: bad_value}))


def test_numeric_bounds_accept_edges():
    r = BenchTrialResult(**_valid_row(confidence=0.0, trial_seed=0, attack_success_weight=1.0, latency_ms=0.0))
    assert r.confidence == 0.0
    assert r.attack_success_weight == 1.0


# ── category / attack_family cross-field validation ──────────────────────

def test_category_must_belong_to_attack_family():
    with pytest.raises(ValidationError):
        BenchTrialResult(**_valid_row(
            attack_family=AttackFamily.TRUST_PRIVACY,        # ASI06, ASI07
            category=PromptCategory.ASI01_PROMPT_INJECTION,   # not in that pair
        ))


def test_every_attack_family_accepts_both_its_categories():
    for family, (cat_a, cat_b) in ATTACK_FAMILY_CATEGORIES.items():
        for cat in (cat_a, cat_b):
            prompt_id = f"{cat.value}-001"
            BenchTrialResult(**_valid_row(attack_family=family, category=cat, prompt_id=prompt_id))


def test_attack_family_taxonomy_has_five_members_covering_all_ten_categories():
    assert len(AttackFamily) == 5
    assert set(ATTACK_FAMILY_CATEGORIES.keys()) == set(AttackFamily)
    covered: set[PromptCategory] = set()
    for cat_a, cat_b in ATTACK_FAMILY_CATEGORIES.values():
        covered |= {cat_a, cat_b}
    assert covered == set(PromptCategory)


def test_category_attack_family_is_a_true_inverse_of_the_forward_mapping():
    assert len(CATEGORY_ATTACK_FAMILY) == len(PromptCategory)
    for family, (cat_a, cat_b) in ATTACK_FAMILY_CATEGORIES.items():
        assert CATEGORY_ATTACK_FAMILY[cat_a] == family
        assert CATEGORY_ATTACK_FAMILY[cat_b] == family


def test_verdict_weight_covers_every_verdict_level_in_expected_order():
    assert set(VERDICT_WEIGHT.keys()) == set(VerdictLevel)
    assert VERDICT_WEIGHT[VerdictLevel.PASS] == 0.00
    assert VERDICT_WEIGHT[VerdictLevel.UNCERTAIN] == 0.25
    assert VERDICT_WEIGHT[VerdictLevel.FAIL] == 0.50
    assert VERDICT_WEIGHT[VerdictLevel.VULNERABLE] == 1.00


# ── library-version comparability ────────────────────────────────────────

def test_same_version_is_comparable_to_itself():
    assert is_library_version_comparable("1.6.0", "1.6.0") is True


def test_different_versions_same_prompt_count_are_comparable():
    """1.0.0 and 1.1.0 both shipped the same 30 prompts -- 1.1.0 only added
    metadata fields, no prompt text changed."""
    assert is_library_version_comparable("1.0.0", "1.1.0") is True


def test_different_prompt_counts_are_not_comparable():
    assert is_library_version_comparable("1.1.0", "1.6.0") is False


def test_unknown_version_is_never_comparable():
    assert is_library_version_comparable("1.6.0", "9.9.9") is False
    assert is_library_version_comparable("9.9.9", "1.6.0") is False
    assert is_library_version_comparable("9.9.9", "9.9.9") is False


# ── KNOWN_LIBRARY_VERSIONS staleness guard ───────────────────────────────

def test_current_library_version_is_registered():
    """
    Fails loudly the next time safelabs.prompts.library's content version
    bumps without KNOWN_LIBRARY_VERSIONS being updated to match -- the
    live version returned by load_library() must always be a key here.
    """
    current_version = load_library().version
    assert current_version in KNOWN_LIBRARY_VERSIONS, (
        f"load_library().version == {current_version!r} is not registered in "
        f"agentport_bench.schema.KNOWN_LIBRARY_VERSIONS "
        f"({sorted(KNOWN_LIBRARY_VERSIONS)}) -- add it (with the correct "
        f"total prompt count) before merging a prompt-library version bump."
    )


def test_current_library_version_count_matches_registry():
    """Stronger corollary of the above: not just that the version is
    registered, but that the registered count is actually correct."""
    current_version = load_library().version
    registered_count = KNOWN_LIBRARY_VERSIONS.get(current_version)
    assert registered_count == len(load_library()), (
        f"KNOWN_LIBRARY_VERSIONS[{current_version!r}] == {registered_count} "
        f"but load_library() actually has {len(load_library())} entries"
    )
