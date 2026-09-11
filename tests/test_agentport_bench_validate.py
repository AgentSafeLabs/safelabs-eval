"""
tests/test_agentport_bench_validate.py

Tests for agentport_bench.validate -- schema/key/prompt-id checks,
library-version comparability flags, payload_hash sample verification,
completeness reporting, and the top-level validate_submission() orchestrator.

All fixture data is fabricated -- fake model names, fake framework names,
real safelabs-eval prompt_ids/categories only where the check under test
requires a real one to pass.
"""

from __future__ import annotations

import json

import pytest

from agentport_bench.schema import compute_payload_hash
from agentport_bench.validate import (
    build_completeness_report,
    check_library_version_comparability,
    load_submission,
    validate_prompt_ids_against_library,
    validate_schema,
    validate_submission,
    validate_unique_keys,
    verify_payload_hash_sample,
)
from safelabs.prompts import get_library

_CURRENT_LIBRARY_VERSION = get_library().version


def _row(**overrides: object) -> dict:
    row = dict(
        model="claude-opus-4-8",
        provider="anthropic",
        framework="http",
        attack_family="injection_output",
        category="ASI01",
        prompt_id="ASI01-001",
        trial_seed=0,
        verdict="pass",
        confidence=0.9,
        payload_hash=compute_payload_hash(
            prompt_id="ASI01-001", model="claude-opus-4-8", framework="http",
            trial_seed=0, raw_output="some output",
        ),
        timestamp="2026-09-10T12:00:00Z",
        harness_version="0.1.0",
        library_version=_CURRENT_LIBRARY_VERSION,
    )
    row.update(overrides)
    return row


def _jsonl(*rows: dict) -> str:
    return "\n".join(json.dumps(r) for r in rows) + "\n"


# ── validate_schema ───────────────────────────────────────────────────────

def test_validate_schema_accepts_valid_lines():
    lines = [json.dumps(_row())]
    rows, issues = validate_schema(lines)
    assert len(rows) == 1
    assert issues == []


def test_validate_schema_flags_invalid_json_without_aborting_rest():
    lines = ["not json {{{", json.dumps(_row())]
    rows, issues = validate_schema(lines)
    assert len(rows) == 1
    assert len(issues) == 1
    assert issues[0].severity == "reject"
    assert issues[0].code == "invalid_json"
    assert issues[0].row_index == 0


def test_validate_schema_flags_schema_violation_without_aborting_rest():
    lines = [json.dumps(_row(confidence=1.5)), json.dumps(_row())]
    rows, issues = validate_schema(lines)
    assert len(rows) == 1
    assert len(issues) == 1
    assert issues[0].code == "schema_validation_failed"
    assert issues[0].row_index == 0


# ── load_submission (strict) ─────────────────────────────────────────────

def test_load_submission_parses_valid_file(tmp_path):
    p = tmp_path / "submission.jsonl"
    p.write_text(_jsonl(_row(), _row(trial_seed=1)))
    rows = load_submission(p)
    assert len(rows) == 2


def test_load_submission_raises_on_bad_line(tmp_path):
    p = tmp_path / "submission.jsonl"
    p.write_text(_jsonl(_row()) + "not json\n")
    with pytest.raises(ValueError, match="invalid JSON"):
        load_submission(p)


# ── validate_prompt_ids_against_library ──────────────────────────────────

def test_prompt_id_check_accepts_real_prompt():
    from agentport_bench.schema import BenchTrialResult
    row = BenchTrialResult(**_row())
    issues = validate_prompt_ids_against_library([row])
    assert issues == []


def test_prompt_id_check_rejects_unknown_prompt_id():
    from agentport_bench.schema import BenchTrialResult
    # ASI01-999 doesn't exist -- category is still a real one so the row
    # itself constructs fine; the library cross-check is what should fail.
    row = BenchTrialResult(**_row(prompt_id="ASI01-999"))
    issues = validate_prompt_ids_against_library([row])
    assert len(issues) == 1
    assert issues[0].severity == "reject"
    assert issues[0].code == "unknown_prompt_id"


def test_prompt_id_check_rejects_category_mismatch():
    from agentport_bench.schema import AttackFamily, BenchTrialResult
    # ASI01-001 is really ASI01, but claim ASI02 (a category the
    # injection_output family also aggregates, so the row itself is
    # constructible) -- the library cross-check must still catch it.
    row = BenchTrialResult(**_row(
        prompt_id="ASI01-001",
        category="ASI02",
        attack_family=AttackFamily.INJECTION_OUTPUT,
    ))
    issues = validate_prompt_ids_against_library([row])
    assert len(issues) == 1
    assert issues[0].code == "category_mismatch"


def test_prompt_id_check_flags_pass_against_noncurrent_claimed_version():
    """
    A row whose prompt_id/category genuinely pass -- but only because they
    were checked against the currently installed library, not the row's
    own claimed (older) library_version -- must not be silently counted
    as a fully confirmed pass. This is the gap flagged in review: without
    this check, a row claiming library_version="1.0.0" (30-prompt era)
    with a prompt_id that only exists from 1.6.0 onward would pass with
    zero indication its claimed version was never actually verified.
    """
    from agentport_bench.schema import BenchTrialResult
    row = BenchTrialResult(**_row(library_version="1.0.0"))  # ASI01-001, real in both eras
    issues = validate_prompt_ids_against_library([row])
    assert len(issues) == 1
    assert issues[0].severity == "flag"
    assert issues[0].code == "prompt_id_check_used_different_library_version"
    assert issues[0].row_index == 0


def test_prompt_id_check_no_version_flag_when_claimed_version_matches_current():
    from agentport_bench.schema import BenchTrialResult
    row = BenchTrialResult(**_row(library_version=_CURRENT_LIBRARY_VERSION))
    issues = validate_prompt_ids_against_library([row])
    assert issues == []


def test_prompt_id_check_reject_takes_priority_over_version_flag():
    """
    A row that's both unknown *and* at a non-current claimed version must
    report only the reject -- not also the (less severe, and in this case
    irrelevant) version-mismatch flag, since there's nothing to have
    "passed only against the current library" when it didn't pass at all.
    """
    from agentport_bench.schema import BenchTrialResult
    row = BenchTrialResult(**_row(prompt_id="ASI01-999", library_version="1.0.0"))
    issues = validate_prompt_ids_against_library([row])
    assert len(issues) == 1
    assert issues[0].code == "unknown_prompt_id"


# ── validate_unique_keys ──────────────────────────────────────────────────

def test_unique_keys_accepts_distinct_rows():
    from agentport_bench.schema import BenchTrialResult
    rows = [BenchTrialResult(**_row(trial_seed=0)), BenchTrialResult(**_row(trial_seed=1))]
    assert validate_unique_keys(rows) == []


def test_unique_keys_rejects_duplicate():
    from agentport_bench.schema import BenchTrialResult
    rows = [BenchTrialResult(**_row()), BenchTrialResult(**_row())]
    issues = validate_unique_keys(rows)
    assert len(issues) == 1
    assert issues[0].severity == "reject"
    assert issues[0].code == "duplicate_key"
    assert issues[0].row_index == 1


# ── check_library_version_comparability ──────────────────────────────────

def test_comparability_no_issues_for_single_current_version():
    from agentport_bench.schema import BenchTrialResult
    rows = [BenchTrialResult(**_row())]
    issues = check_library_version_comparability(rows)
    assert issues == []


def test_comparability_flags_mixed_versions_within_one_file():
    from agentport_bench.schema import BenchTrialResult
    rows = [
        BenchTrialResult(**_row(library_version="1.0.0")),
        BenchTrialResult(**_row(library_version=_CURRENT_LIBRARY_VERSION)),
    ]
    issues = check_library_version_comparability(rows)
    codes = {i.code for i in issues}
    assert "mixed_library_versions" in codes
    assert all(i.severity == "flag" for i in issues)


def test_comparability_flags_unregistered_version():
    from agentport_bench.schema import BenchTrialResult
    rows = [BenchTrialResult(**_row(library_version="9.9.9"))]
    issues = check_library_version_comparability(rows)
    codes = {i.code for i in issues}
    assert "unregistered_library_version" in codes
    assert "library_version_not_current" in codes  # 9.9.9 != installed version too
    assert all(i.severity == "flag" for i in issues)


def test_comparability_flags_registered_but_not_current_version():
    from agentport_bench.schema import BenchTrialResult
    rows = [BenchTrialResult(**_row(library_version="1.0.0"))]
    issues = check_library_version_comparability(rows)
    codes = {i.code for i in issues}
    assert "library_version_not_current" in codes
    assert "unregistered_library_version" not in codes  # 1.0.0 IS registered


# ── verify_payload_hash_sample ────────────────────────────────────────────

def test_payload_hash_sample_flags_when_no_sample_given():
    from agentport_bench.schema import BenchTrialResult
    rows = [BenchTrialResult(**_row())]
    issues = verify_payload_hash_sample(rows, None)
    assert len(issues) == 1
    assert issues[0].severity == "flag"
    assert issues[0].code == "payload_hash_unverified"


def test_payload_hash_sample_accepts_matching_hash(tmp_path):
    from agentport_bench.schema import BenchTrialResult
    rows = [BenchTrialResult(**_row())]
    sample = [{
        "model": "claude-opus-4-8", "framework": "http",
        "prompt_id": "ASI01-001", "trial_seed": 0,
        "raw_output": "some output",   # matches the hash baked into _row()
    }]
    sample_path = tmp_path / "sample.json"
    sample_path.write_text(json.dumps(sample))
    issues = verify_payload_hash_sample(rows, sample_path)
    assert issues == []


def test_payload_hash_sample_rejects_mismatched_hash(tmp_path):
    from agentport_bench.schema import BenchTrialResult
    rows = [BenchTrialResult(**_row())]
    sample = [{
        "model": "claude-opus-4-8", "framework": "http",
        "prompt_id": "ASI01-001", "trial_seed": 0,
        "raw_output": "a DIFFERENT output than what the hash was built from",
    }]
    sample_path = tmp_path / "sample.json"
    sample_path.write_text(json.dumps(sample))
    issues = verify_payload_hash_sample(rows, sample_path)
    assert len(issues) == 1
    assert issues[0].severity == "reject"
    assert issues[0].code == "payload_hash_mismatch"


def test_payload_hash_sample_flags_entry_with_no_matching_row(tmp_path):
    from agentport_bench.schema import BenchTrialResult
    rows = [BenchTrialResult(**_row())]
    sample = [{
        "model": "some-other-model", "framework": "http",
        "prompt_id": "ASI01-001", "trial_seed": 0, "raw_output": "x",
    }]
    sample_path = tmp_path / "sample.json"
    sample_path.write_text(json.dumps(sample))
    issues = verify_payload_hash_sample(rows, sample_path)
    assert len(issues) == 1
    assert issues[0].severity == "flag"
    assert issues[0].code == "verify_sample_no_matching_row"


# ── build_completeness_report ─────────────────────────────────────────────

def test_completeness_report_full_coverage():
    from agentport_bench.schema import AttackFamily, BenchTrialResult
    rows = [
        BenchTrialResult(**_row(prompt_id=f"{cat}-001", category=cat, attack_family=family))
        for family, (cat_a, cat_b) in [
            (AttackFamily.INJECTION_OUTPUT, ("ASI01", "ASI02")),
            (AttackFamily.AGENCY_SCOPE, ("ASI03", "ASI09")),
            (AttackFamily.RESOURCE_TOOL, ("ASI04", "ASI05")),
            (AttackFamily.TRUST_PRIVACY, ("ASI06", "ASI07")),
            (AttackFamily.DRIFT_MISINFORMATION, ("ASI08", "ASI10")),
        ]
        for cat in (cat_a, cat_b)
    ]
    report = build_completeness_report(rows)
    assert report.total_rows == 10
    assert report.categories_missing == []
    assert len(report.categories_covered) == 10


def test_completeness_report_partial_coverage():
    from agentport_bench.schema import BenchTrialResult
    rows = [BenchTrialResult(**_row())]  # ASI01 only
    report = build_completeness_report(rows)
    assert report.total_rows == 1
    assert report.categories_covered == ["ASI01"]
    assert "ASI10" in report.categories_missing
    assert report.cells_by_category["ASI01"] == 1
    assert report.cells_by_category["ASI10"] == 0


# ── validate_submission (top-level orchestrator) ─────────────────────────

def test_validate_submission_accepts_clean_file(tmp_path):
    p = tmp_path / "submission.jsonl"
    p.write_text(_jsonl(_row()))
    report = validate_submission(p)
    assert report.accepted is True
    assert report.rejections == []
    # still flagged: no --verify-sample, partial coverage (only ASI01)
    codes = {i.code for i in report.flags}
    assert "payload_hash_unverified" in codes
    assert "partial_coverage" in codes


def test_validate_submission_rejects_on_duplicate_key(tmp_path):
    p = tmp_path / "submission.jsonl"
    p.write_text(_jsonl(_row(), _row()))
    report = validate_submission(p)
    assert report.accepted is False
    assert any(i.code == "duplicate_key" for i in report.rejections)


def test_validate_submission_rejects_empty_file(tmp_path):
    p = tmp_path / "submission.jsonl"
    p.write_text("")
    report = validate_submission(p)
    assert report.accepted is False
    assert report.issues[0].code == "empty_file"
    assert report.completeness is None


def test_validate_submission_rejects_whitespace_only_file(tmp_path):
    p = tmp_path / "submission.jsonl"
    p.write_text("   \n\n  \n")
    report = validate_submission(p)
    assert report.accepted is False
    assert report.issues[0].code == "empty_file"


def test_validate_submission_flag_only_issues_do_not_block_acceptance(tmp_path):
    p = tmp_path / "submission.jsonl"
    p.write_text(_jsonl(_row(library_version="1.0.0")))
    report = validate_submission(p)
    assert report.accepted is True
    assert any(i.code == "library_version_not_current" for i in report.flags)
    assert report.rejections == []


def test_validate_submission_with_verify_sample_clears_the_unverified_flag(tmp_path):
    p = tmp_path / "submission.jsonl"
    p.write_text(_jsonl(_row()))
    sample_path = tmp_path / "sample.json"
    sample_path.write_text(json.dumps([{
        "model": "claude-opus-4-8", "framework": "http",
        "prompt_id": "ASI01-001", "trial_seed": 0, "raw_output": "some output",
    }]))
    report = validate_submission(p, verify_sample_path=sample_path)
    assert report.accepted is True
    codes = {i.code for i in report.issues}
    assert "payload_hash_unverified" not in codes


def test_validate_submission_mixed_valid_and_invalid_rows(tmp_path):
    p = tmp_path / "submission.jsonl"
    lines = [
        json.dumps(_row()),
        "not json",
        json.dumps(_row(trial_seed=1, confidence=2.0)),   # fails schema
        json.dumps(_row(trial_seed=2, prompt_id="ASI01-999")),  # unknown prompt_id
    ]
    p.write_text("\n".join(lines) + "\n")
    report = validate_submission(p)
    assert report.accepted is False
    codes = [i.code for i in report.rejections]
    assert "invalid_json" in codes
    assert "schema_validation_failed" in codes
    assert "unknown_prompt_id" in codes
    # the one genuinely valid row should still be reflected in completeness
    assert report.completeness is not None
    assert report.completeness.total_rows == 2  # trial_seed=0 row + trial_seed=2 row
