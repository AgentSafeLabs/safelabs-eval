"""
agentport_bench/validate.py

Validates a submitted AgentPort-Bench .jsonl file before it's accepted
into the public results repo. Three-tier outcome per row/file: reject
(bounced, not merged), flag (accepted, marked for human review), pass.
See docs/AGENTPORT_BENCH.md section 3 for the full reject/flag table.

library_version comparability is a dedicated check here
(check_library_version_comparability), not an incidental one -- it always
runs as part of validate_submission(). It only reasons about the
*rows within one submission file*. Leaderboard aggregation across
*multiple* submissions (outside this module, in the results repo) must
separately call schema.is_library_version_comparable() for every pair of
submissions before merging them into one ranking -- this module cannot
do that on its own, since it only ever sees one file at a time.

Known limitation, stated plainly rather than silently overclaimed:
validate_prompt_ids_against_library() can only check a row's prompt_id
and category against the *currently installed* safelabs-eval prompt
library (safelabs.prompts.get_library()) -- this package has no way to
reconstruct what prompt set existed at an older library_version, since
safelabs-eval only ships its current content, not historical snapshots.
A row whose library_version differs from the currently installed one
still gets this check run against today's library (the only data
available); when that check succeeds, the result is downgraded from a
silent pass to an explicit flag-severity issue
("prompt_id_check_used_different_library_version", raised by
validate_prompt_ids_against_library() itself, at that row's own index)
rather than being counted as fully confirmed for the row's claimed
version. check_library_version_comparability() separately raises a
file-level "library_version_not_current" flag for the same underlying
reason -- the two are complementary granularities (one row-specific,
one submission-wide), not the same fact reported twice for no reason.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from agentport_bench.schema import (
    KNOWN_LIBRARY_VERSIONS,
    BenchTrialResult,
    compute_payload_hash,
)
from safelabs.prompts import get_library
from safelabs.prompts.schemas import PromptCategory


class ValidationIssue(BaseModel):
    """One finding from validating a submission."""

    severity: Literal["reject", "flag"]
    code: str                       # short machine-readable id, e.g. "duplicate_key"
    message: str
    row_index: int | None = None    # None for file-level or cross-row issues


class CompletenessReport(BaseModel):
    """Informational, not pass/fail -- coverage of the full matrix this
    submission actually exercised."""

    total_rows: int
    categories_covered: list[str]
    categories_missing: list[str]
    cells_by_category: dict[str, int]   # category -> row count


class ValidationReport(BaseModel):
    """Result of validating one submission file."""

    accepted: bool
    issues: list[ValidationIssue]
    completeness: CompletenessReport | None = None

    @property
    def rejections(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "reject"]

    @property
    def flags(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "flag"]


# ── individual checks ─────────────────────────────────────────────────────

def load_submission(path: Path) -> list[BenchTrialResult]:
    """
    Strict parse: every line must be valid JSON and a valid BenchTrialResult,
    or this raises ValueError immediately, naming the offending line.

    This is the fail-fast counterpart to validate_schema() below, which
    tolerates row-level failures and collects them as issues instead --
    use this for a file you already trust (e.g. re-reading a submission
    this same run just wrote), and validate_schema()/validate_submission()
    for anything coming from an external contributor.
    """
    rows: list[BenchTrialResult] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
        try:
            rows.append(BenchTrialResult(**obj))
        except ValidationError as exc:
            raise ValueError(f"{path}:{line_no}: schema validation failed: {exc}") from exc
    return rows


def validate_schema(raw_lines: list[str]) -> tuple[list[BenchTrialResult], list[ValidationIssue]]:
    """
    Tolerant parse: a bad line becomes a reject-severity ValidationIssue at
    its row_index instead of aborting the rest of the file, so a submission
    with one malformed row still gets a complete report for every other row.
    """
    rows: list[BenchTrialResult] = []
    issues: list[ValidationIssue] = []
    for i, line in enumerate(raw_lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            issues.append(ValidationIssue(
                severity="reject", code="invalid_json",
                message=f"line is not valid JSON: {exc}", row_index=i,
            ))
            continue
        try:
            rows.append(BenchTrialResult(**obj))
        except ValidationError as exc:
            issues.append(ValidationIssue(
                severity="reject", code="schema_validation_failed",
                message=str(exc), row_index=i,
            ))
    return rows, issues


def validate_prompt_ids_against_library(rows: list[BenchTrialResult]) -> list[ValidationIssue]:
    """
    Reject any row whose prompt_id doesn't exist, or whose category
    doesn't match the real assigned category, in the currently installed
    safelabs-eval prompt library. See this module's docstring for the
    "currently installed, not the row's claimed library_version" caveat
    and how a pass that relied on that fallback is itself flagged below.
    """
    library = get_library()
    by_id = {e.id: e for e in library.entries}
    issues: list[ValidationIssue] = []
    for i, row in enumerate(rows):
        entry = by_id.get(row.prompt_id)
        if entry is None:
            issues.append(ValidationIssue(
                severity="reject", code="unknown_prompt_id",
                message=(
                    f"prompt_id {row.prompt_id!r} does not exist in the installed "
                    f"safelabs-eval prompt library (checked against version {library.version!r})"
                ),
                row_index=i,
            ))
            continue
        if entry.category != row.category:
            issues.append(ValidationIssue(
                severity="reject", code="category_mismatch",
                message=(
                    f"row claims category {row.category.value!r} for {row.prompt_id!r}, "
                    f"but the installed library assigns it {entry.category.value!r}"
                ),
                row_index=i,
            ))
            continue
        if row.library_version != library.version:
            issues.append(ValidationIssue(
                severity="flag", code="prompt_id_check_used_different_library_version",
                message=(
                    f"prompt_id {row.prompt_id!r} and its category passed validation, but "
                    f"only against the currently installed library ({library.version!r}) -- "
                    f"this row claims library_version={row.library_version!r}, which "
                    f"safelabs-eval cannot independently verify (no historical library "
                    f"snapshots are kept). This does not confirm {row.prompt_id!r} actually "
                    f"existed with this category in the {row.library_version!r} library."
                ),
                row_index=i,
            ))
    return issues


def validate_unique_keys(rows: list[BenchTrialResult]) -> list[ValidationIssue]:
    """Reject duplicate (model, framework, prompt_id, trial_seed) keys
    within the file -- mirrors the key-uniqueness check agentdojo-x's own
    verification_report.md used to reconcile full_run_7020.jsonl against
    its .raw.jsonl sibling."""
    seen: dict[tuple[str, str, str, int], int] = {}
    issues: list[ValidationIssue] = []
    for i, row in enumerate(rows):
        key = (row.model, row.framework, row.prompt_id, row.trial_seed)
        if key in seen:
            issues.append(ValidationIssue(
                severity="reject", code="duplicate_key",
                message=(
                    f"(model, framework, prompt_id, trial_seed) = {key!r} "
                    f"duplicates row {seen[key]}"
                ),
                row_index=i,
            ))
        else:
            seen[key] = i
    return issues


def check_library_version_comparability(rows: list[BenchTrialResult]) -> list[ValidationIssue]:
    """
    Flags (never rejects) when:
      - rows within one file carry more than one distinct library_version
        (should not happen -- a single run happens against one installed
        library version; if it does, it's worth a human look, not an
        auto-reject, since it could be a legitimate multi-session
        submission the contributor merged themselves).
      - the file's library_version is absent from schema.KNOWN_LIBRARY_VERSIONS
        (unrecognized version -- accepted provisionally, flagged so a
        human adds it to the registry rather than it silently being
        treated as comparable to anything).
      - the file's library_version differs from the currently installed
        safelabs-eval library -- see this module's docstring: the
        prompt_id/category check only ran against today's library, so
        this reduces that check's certainty for the affected rows.

    Does NOT compare this submission against other submissions'
    library_versions -- that comparison happens once, at
    leaderboard-aggregation time (outside this module), using
    schema.is_library_version_comparable() for every pair being merged
    into one ranking.
    """
    issues: list[ValidationIssue] = []
    versions = {row.library_version for row in rows}

    if len(versions) > 1:
        issues.append(ValidationIssue(
            severity="flag", code="mixed_library_versions",
            message=(
                f"submission contains rows from more than one library_version: "
                f"{sorted(versions)} -- confirm this is intentional (e.g. a merged "
                f"multi-session submission) before treating all rows as one comparable set"
            ),
        ))

    unknown = sorted(v for v in versions if v not in KNOWN_LIBRARY_VERSIONS)
    if unknown:
        issues.append(ValidationIssue(
            severity="flag", code="unregistered_library_version",
            message=(
                f"library_version(s) {unknown} are not in "
                f"agentport_bench.schema.KNOWN_LIBRARY_VERSIONS -- accepted provisionally; "
                f"comparability against other submissions cannot be established until registered"
            ),
        ))

    current = get_library().version
    stale = sorted(v for v in versions if v != current)
    if stale:
        issues.append(ValidationIssue(
            severity="flag", code="library_version_not_current",
            message=(
                f"library_version(s) {stale} differ from the currently installed "
                f"safelabs-eval library ({current!r}) -- prompt_id/category checks in "
                f"this validator only run against the currently installed library, so "
                f"rows at a different version received less certain verification"
            ),
        ))

    return issues


def verify_payload_hash_sample(
    rows: list[BenchTrialResult],
    verify_sample_path: Path | None,
) -> list[ValidationIssue]:
    """
    If verify_sample_path is given -- a JSON file containing a list of
    {"model", "framework", "prompt_id", "trial_seed", "raw_output"}
    objects for a subset of rows, never required by default -- recompute
    each sampled row's payload_hash via schema.compute_payload_hash() and
    compare. Any mismatch is a reject-severity issue (real evidence of
    tampering). No verify_sample_path at all is a flag-severity issue
    ("integrity unverified, accepted provisionally"), not a reject --
    requiring it for every submission would recreate the exact privacy
    problem payload_hash exists to avoid (see schema.py's module docstring).
    """
    if verify_sample_path is None:
        return [ValidationIssue(
            severity="flag", code="payload_hash_unverified",
            message=(
                "no --verify-sample bundle supplied -- payload_hash values are "
                "grammar-valid but their consistency with real raw output was not "
                "independently confirmed"
            ),
        )]

    by_key = {
        (row.model, row.framework, row.prompt_id, row.trial_seed): (i, row)
        for i, row in enumerate(rows)
    }
    sample = json.loads(verify_sample_path.read_text(encoding="utf-8"))

    issues: list[ValidationIssue] = []
    checked = 0
    for entry in sample:
        key = (entry["model"], entry["framework"], entry["prompt_id"], entry["trial_seed"])
        match = by_key.get(key)
        if match is None:
            issues.append(ValidationIssue(
                severity="flag", code="verify_sample_no_matching_row",
                message=f"--verify-sample entry {key!r} does not match any row in the submission",
            ))
            continue
        row_index, row = match
        recomputed = compute_payload_hash(
            prompt_id=row.prompt_id, model=row.model, framework=row.framework,
            trial_seed=row.trial_seed, raw_output=entry["raw_output"],
        )
        checked += 1
        if recomputed != row.payload_hash:
            issues.append(ValidationIssue(
                severity="reject", code="payload_hash_mismatch",
                message=(
                    f"row {row_index} ({key!r}): payload_hash {row.payload_hash!r} does not "
                    f"match the hash recomputed from the supplied --verify-sample raw output"
                ),
                row_index=row_index,
            ))

    if checked == 0 and not issues:
        issues.append(ValidationIssue(
            severity="flag", code="verify_sample_empty",
            message="--verify-sample bundle supplied but contained no entries matching any row",
        ))

    return issues


def build_completeness_report(rows: list[BenchTrialResult]) -> CompletenessReport:
    all_categories = [c.value for c in PromptCategory]
    cells: dict[str, int] = {c: 0 for c in all_categories}
    for row in rows:
        cells[row.category.value] += 1
    covered = [c for c in all_categories if cells[c] > 0]
    missing = [c for c in all_categories if cells[c] == 0]
    return CompletenessReport(
        total_rows=len(rows),
        categories_covered=covered,
        categories_missing=missing,
        cells_by_category=cells,
    )


# ── top-level entry point ────────────────────────────────────────────────

def validate_submission(
    path: Path,
    *,
    verify_sample_path: Path | None = None,
) -> ValidationReport:
    """
    Runs every check above and returns one ValidationReport. accepted is
    False iff any reject-severity issue was found; flag-severity issues
    never block acceptance on their own.
    """
    issues: list[ValidationIssue] = []

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        issues.append(ValidationIssue(
            severity="reject", code="invalid_utf8", message=str(exc),
        ))
        return ValidationReport(accepted=False, issues=issues, completeness=None)

    raw_lines = [line for line in text.splitlines() if line.strip()]
    if not raw_lines:
        issues.append(ValidationIssue(
            severity="reject", code="empty_file", message=f"{path} contains no JSONL rows",
        ))
        return ValidationReport(accepted=False, issues=issues, completeness=None)

    rows, schema_issues = validate_schema(raw_lines)
    issues.extend(schema_issues)

    completeness: CompletenessReport | None = None
    if rows:
        issues.extend(validate_unique_keys(rows))
        issues.extend(validate_prompt_ids_against_library(rows))
        issues.extend(check_library_version_comparability(rows))
        issues.extend(verify_payload_hash_sample(rows, verify_sample_path))

        completeness = build_completeness_report(rows)
        if completeness.categories_missing:
            issues.append(ValidationIssue(
                severity="flag", code="partial_coverage",
                message=(
                    f"no rows for categories: {completeness.categories_missing} -- "
                    f"submission is partial, not full-matrix"
                ),
            ))

    accepted = not any(i.severity == "reject" for i in issues)
    return ValidationReport(accepted=accepted, issues=issues, completeness=completeness)
