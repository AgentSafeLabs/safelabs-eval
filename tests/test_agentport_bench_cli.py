"""
tests/test_agentport_bench_cli.py

Tests for agentport_bench.cli -- the `agentport-bench` console script's
run / manifest / validate / compare commands. Uses click.testing.CliRunner
throughout (the correct way to invoke a click command in tests -- `python
-m agentport_bench.cli` would only work through the installed console
script or the module's own __main__ guard, not plain import).
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from agentport_bench.cli import main
from agentport_bench.schema import BenchTrialResult, compute_payload_hash
from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse
from safelabs.prompts import get_library

_CURRENT_LIBRARY_VERSION = get_library().version


class _FakeCliAdapter(AgentAdapter):
    """A fixed-refusal fake, reachable from the CLI via
    --adapter custom --module <this module>:_FakeCliAdapter."""

    @property
    def adapter_type(self) -> str:
        return "fake-cli"

    async def _execute(self, prompt: str) -> AgentResponse:
        return AgentResponse(output="I cannot comply with that request.", latency_ms=1.0)


_FAKE_MODULE = f"{__name__}:_FakeCliAdapter"


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


def _write_jsonl(path, *rows: dict) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ── main group ────────────────────────────────────────────────────────────

def test_main_help(runner):
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.output and "validate" in result.output and "compare" in result.output


def test_main_version(runner):
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "agentport-bench" in result.output


# ── run ───────────────────────────────────────────────────────────────────

def test_run_missing_model_is_usage_error(runner, tmp_path):
    result = runner.invoke(main, ["run", "-a", "http", "--target", "http://x", "-o", str(tmp_path / "o.jsonl")])
    assert result.exit_code == 2
    assert "Missing option" in result.output


def test_run_http_without_target_is_usage_error(runner, tmp_path):
    result = runner.invoke(main, ["run", "-a", "http", "-m", "m", "-o", str(tmp_path / "o.jsonl")])
    assert result.exit_code != 0
    assert "--target" in result.output


def test_run_custom_without_module_is_usage_error(runner, tmp_path):
    result = runner.invoke(main, ["run", "-a", "custom", "-m", "m", "-o", str(tmp_path / "o.jsonl")])
    assert result.exit_code != 0
    assert "--module" in result.output


def test_run_happy_path_writes_submission_and_manifest(runner, tmp_path):
    out = tmp_path / "sub.jsonl"
    result = runner.invoke(main, [
        "run", "-a", "custom", "--module", _FAKE_MODULE,
        "-m", "fake-model", "--provider", "fake-provider",
        "--categories", "ASI06", "--seeds", "1", "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    assert "Wrote 13 trial(s)" in result.output

    lines = out.read_text().splitlines()
    assert len(lines) == 13
    rows = [BenchTrialResult(**json.loads(line)) for line in lines]
    assert all(r.model == "fake-model" for r in rows)
    assert all(r.provider == "fake-provider" for r in rows)
    assert all(r.category.value == "ASI06" for r in rows)

    manifest_path = tmp_path / "sub.manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["trial_count"] == 13
    assert manifest["model"] == "fake-model"
    assert manifest["library_version"] == _CURRENT_LIBRARY_VERSION


def test_run_dry_run_restricts_scope(runner, tmp_path):
    out = tmp_path / "sub.jsonl"
    result = runner.invoke(main, [
        "run", "-a", "custom", "--module", _FAKE_MODULE,
        "-m", "m", "--dry-run", "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert all(r["category"] == "ASI01" for r in rows)
    assert len(rows) == 14  # ASI01 has 14 prompts


def test_run_resume_default_skips_second_invocation(runner, tmp_path):
    out = tmp_path / "sub.jsonl"
    args = ["run", "-a", "custom", "--module", _FAKE_MODULE, "-m", "m", "--categories", "ASI09", "-o", str(out)]

    first = runner.invoke(main, args)
    assert first.exit_code == 0

    second = runner.invoke(main, args)
    assert second.exit_code == 0
    assert "No trials run" in second.output
    # file unchanged -- not truncated/duplicated
    n_prompts = len(get_library().by_category("ASI09"))
    assert len(out.read_text().splitlines()) == n_prompts


def test_run_include_raw_output_writes_raw_text_and_warns(runner, tmp_path):
    out = tmp_path / "sub.jsonl"
    result = runner.invoke(main, [
        "run", "-a", "custom", "--module", _FAKE_MODULE,
        "-m", "m", "--categories", "ASI09", "--include-raw-output", "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    assert "should be treated as sensitive" in result.output

    first_row = json.loads(out.read_text().splitlines()[0])
    assert first_row["raw_output"] == "I cannot comply with that request."


# ── manifest ──────────────────────────────────────────────────────────────

def test_manifest_regenerate_from_existing_file(runner, tmp_path):
    out = tmp_path / "sub.jsonl"
    _write_jsonl(out, _row(), _row(trial_seed=1))

    result = runner.invoke(main, ["manifest", "-o", str(out)])
    assert result.exit_code == 0, result.output

    manifest = json.loads((tmp_path / "sub.manifest.json").read_text())
    assert manifest["trial_count"] == 2
    assert manifest["model"] == "claude-opus-4-8"
    assert "unknown" in manifest["started_at"]


def test_manifest_empty_file_is_click_exception(runner, tmp_path):
    out = tmp_path / "sub.jsonl"
    out.write_text("")
    result = runner.invoke(main, ["manifest", "-o", str(out)])
    assert result.exit_code != 0
    assert "no rows" in result.output


def test_manifest_malformed_file_is_click_exception(runner, tmp_path):
    out = tmp_path / "sub.jsonl"
    out.write_text("not json\n")
    result = runner.invoke(main, ["manifest", "-o", str(out)])
    assert result.exit_code != 0


# ── validate ──────────────────────────────────────────────────────────────

def test_validate_accepted_file_exits_zero(runner, tmp_path):
    out = tmp_path / "sub.jsonl"
    _write_jsonl(out, _row())
    result = runner.invoke(main, ["validate", str(out)])
    assert result.exit_code == 0
    assert "Accepted" in result.output


def test_validate_rejected_file_exits_nonzero(runner, tmp_path):
    out = tmp_path / "sub.jsonl"
    _write_jsonl(out, _row(), _row())  # duplicate key
    result = runner.invoke(main, ["validate", str(out)])
    assert result.exit_code == 1
    assert "REJECTED" in result.output
    assert "duplicate_key" in result.output


def test_validate_json_output_is_parseable_report(runner, tmp_path):
    out = tmp_path / "sub.jsonl"
    _write_jsonl(out, _row())
    result = runner.invoke(main, ["validate", str(out), "-o", "json"])
    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["accepted"] is True
    assert isinstance(report["issues"], list)


def test_validate_with_verify_sample_clears_unverified_flag(runner, tmp_path):
    out = tmp_path / "sub.jsonl"
    _write_jsonl(out, _row())
    sample_path = tmp_path / "sample.json"
    sample_path.write_text(json.dumps([{
        "model": "claude-opus-4-8", "framework": "http",
        "prompt_id": "ASI01-001", "trial_seed": 0, "raw_output": "some output",
    }]))
    result = runner.invoke(main, ["validate", str(out), "--verify-sample", str(sample_path), "-o", "json"])
    assert result.exit_code == 0
    report = json.loads(result.output)
    codes = {i["code"] for i in report["issues"]}
    assert "payload_hash_unverified" not in codes


# ── compare ───────────────────────────────────────────────────────────────

def test_compare_single_submission_one_group(runner, tmp_path):
    out = tmp_path / "sub.jsonl"
    _write_jsonl(out, _row())
    result = runner.invoke(main, ["compare", str(out)])
    assert result.exit_code == 0
    assert "Group 1" in result.output
    assert "Group 2" not in result.output


def test_compare_two_submissions_same_current_version_merge_into_one_group(runner, tmp_path):
    out_a = tmp_path / "a.jsonl"
    out_b = tmp_path / "b.jsonl"
    _write_jsonl(out_a, _row(model="model-a"))
    _write_jsonl(out_b, _row(model="model-b", trial_seed=1))
    result = runner.invoke(main, ["compare", str(out_a), str(out_b)])
    assert result.exit_code == 0
    assert "Group 1" in result.output
    assert "Group 2" not in result.output
    assert "model-a" in result.output
    assert "model-b" in result.output


def test_compare_incomparable_versions_are_separate_groups(runner, tmp_path):
    out_old = tmp_path / "old.jsonl"
    out_new = tmp_path / "new.jsonl"
    _write_jsonl(out_old, _row(library_version="1.0.0"))
    _write_jsonl(out_new, _row(library_version=_CURRENT_LIBRARY_VERSION))
    result = runner.invoke(main, ["compare", str(out_old), str(out_new)])
    assert result.exit_code == 0
    assert "Group 1" in result.output
    assert "Group 2" in result.output
    assert "incomparable" in result.output.lower()


def test_compare_same_unregistered_version_still_separate_groups(runner, tmp_path):
    """Locks the documented non-reflexive behaviour (see schema.py /
    is_library_version_comparable's own tests) at the CLI layer too: two
    submissions claiming the identical but unregistered version string
    are NOT merged."""
    out_a = tmp_path / "a.jsonl"
    out_b = tmp_path / "b.jsonl"
    _write_jsonl(out_a, _row(library_version="9.9.9"))
    _write_jsonl(out_b, _row(library_version="9.9.9", trial_seed=1))
    result = runner.invoke(main, ["compare", str(out_a), str(out_b)])
    assert result.exit_code == 0
    assert "Group 1" in result.output
    assert "Group 2" in result.output
    assert result.output.count("not registered in") == 2


def test_compare_malformed_submission_is_click_exception(runner, tmp_path):
    out = tmp_path / "bad.jsonl"
    out.write_text("not json\n")
    result = runner.invoke(main, ["compare", str(out)])
    assert result.exit_code != 0
