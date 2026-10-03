"""
tests/test_manifest_rerun_history.py

The run manifest's append-only `rerun_history` (initial run, later runs, one entry per
--rerun-missing pass), and the docs lines about it and about one writer at a time.
"""

from __future__ import annotations

import json
import pathlib
import zlib
from datetime import datetime

import pytest
from click.testing import CliRunner

from agentport_bench.cli import main
from agentport_bench.harness import RerunHistoryEntry, RunManifest
from agentport_bench.validate import load_submission
from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse

_REFUSAL = AgentResponse(output="I cannot and will not comply with that request. That looks like an injection attempt.", latency_ms=1.0)
_MODE = {"mode": "flaky"}


class _ModeCli(AgentAdapter):
    """Reachable from the CLI via --adapter custom --module <this module>:_ModeCli; behaviour set by _MODE."""

    @property
    def adapter_type(self):
        return "mode-cli"

    async def _execute(self, prompt):
        mode = _MODE["mode"]
        if mode == "down" or (mode == "flaky" and zlib.crc32(prompt.encode()) % 10 != 0) or (mode == "half" and zlib.crc32(prompt.encode()) % 2 == 0):
            return AgentResponse(output="", error="HTTP 429: slow down")
        return _REFUSAL


def _args(out, *extra):
    return ["run", "-a", "custom", "--module", f"{__name__}:_ModeCli", "-m", "gpt-5.5", "--categories", "ASI01", "-o", str(out), *extra]


def _manifest(out):
    return json.loads(out.with_suffix(".manifest.json").read_text())


def _invoke(*args):
    result = CliRunner().invoke(main, list(args))
    assert result.exit_code == 0, result.output
    return result


@pytest.fixture(autouse=True)
def _reset_mode():
    _MODE["mode"] = "flaky"
    yield
    _MODE["mode"] = "flaky"


def test_initial_entry_has_the_runs_own_counts(tmp_path):
    out = tmp_path / "o.jsonl"
    _invoke(*_args(out, "--max-attempts", "1"))
    rows = load_submission(out)
    n_missing = sum(r.is_missing for r in rows)
    history = _manifest(out)["rerun_history"]
    assert len(history) == 1
    e = history[0]
    assert e["kind"] == "initial" and e["retry_profile"] == "default"
    assert e["retry_settings"] == {"max_attempts": 1, "base_delay_s": 1.0, "max_delay_s": 60.0, "max_retry_after_s": 300.0}
    assert (e["rows_attempted"], e["recovered"], e["still_missing"]) == (len(rows), 0, n_missing)
    assert e["by_cell"] == {"custom|gpt-5.5": {"rows_attempted": len(rows), "recovered": 0, "still_missing": n_missing}}
    datetime.fromisoformat(e["timestamp"])
    assert datetime.fromisoformat(e["timestamp"]).utcoffset().total_seconds() == 0                       # UTC
    RerunHistoryEntry(**e)


def test_each_rerun_pass_appends_one_entry_and_never_rewrites_earlier_ones(tmp_path):
    out = tmp_path / "o.jsonl"
    _invoke(*_args(out, "--max-attempts", "1", "--retry-base-delay-s", "0"))
    n0 = sum(r.is_missing for r in load_submission(out))
    first = _manifest(out)["rerun_history"][0]

    _MODE["mode"] = "half"                                                                              # about half recover
    _invoke(*_args(out, "--resume", "--rerun-missing", "--retry-profile", "benchmark", "--max-attempts", "1"))
    h1 = _manifest(out)["rerun_history"]
    assert len(h1) == 2 and h1[0] == first
    e = h1[1]
    n1 = sum(r.is_missing for r in load_submission(out))
    assert e["kind"] == "rerun" and e["rows_attempted"] == n0 and e["recovered"] == n0 - n1 and e["still_missing"] == n1
    assert 0 < e["recovered"] < n0
    assert e["retry_profile"] == "benchmark"
    assert e["retry_settings"] == {"max_attempts": 1, "base_delay_s": 2.0, "max_delay_s": 120.0, "max_retry_after_s": 600.0}   # profile + override
    assert e["by_cell"] == {"custom|gpt-5.5": {"rows_attempted": n0, "recovered": n0 - n1, "still_missing": n1}}
    assert datetime.fromisoformat(e["timestamp"]) >= datetime.fromisoformat(first["timestamp"])

    _MODE["mode"] = "clean"
    _invoke(*_args(out, "--resume", "--rerun-missing"))
    h2 = _manifest(out)["rerun_history"]
    assert len(h2) == 3 and h2[:2] == h1                                                                # earlier entries byte-for-byte equal
    assert h2[2]["rows_attempted"] == n1 and h2[2]["recovered"] == n1 and h2[2]["still_missing"] == 0
    assert _manifest(out)["missing_infrastructure"] == 0 and _manifest(out)["rerun_passes"] == 2


def test_a_pass_with_nothing_to_rerun_is_still_recorded_and_the_results_are_untouched(tmp_path):
    out = tmp_path / "o.jsonl"
    _MODE["mode"] = "clean"
    _invoke(*_args(out))
    before = out.read_bytes()
    _invoke(*_args(out, "--resume", "--rerun-missing"))
    history = _manifest(out)["rerun_history"]
    assert out.read_bytes() == before
    assert [e["kind"] for e in history] == ["initial", "rerun"]
    assert (history[1]["rows_attempted"], history[1]["recovered"], history[1]["still_missing"], history[1]["by_cell"]) == (0, 0, 0, {})


def test_an_older_manifest_without_the_field_loads_and_the_next_pass_starts_the_history(tmp_path):
    out = tmp_path / "o.jsonl"
    _invoke(*_args(out, "--max-attempts", "1"))
    path = out.with_suffix(".manifest.json")
    data = json.loads(path.read_text())
    del data["rerun_history"]
    path.write_text(json.dumps(data))
    assert RunManifest(**json.loads(path.read_text())).rerun_history is None                          # old manifest loads unchanged
    _MODE["mode"] = "clean"
    _invoke(*_args(out, "--resume", "--rerun-missing"))
    history = _manifest(out)["rerun_history"]
    assert [e["kind"] for e in history] == ["rerun"] and history[0]["recovered"] == history[0]["rows_attempted"] > 0


def test_a_later_plain_run_keeps_the_history_and_adds_a_run_entry(tmp_path):
    out = tmp_path / "o.jsonl"
    _MODE["mode"] = "clean"
    _invoke(*_args(out))                                                                                # ASI01
    first = _manifest(out)["rerun_history"]
    _invoke("run", "-a", "custom", "--module", f"{__name__}:_ModeCli", "-m", "gpt-5.5", "--categories", "ASI01,ASI09", "-o", str(out))
    history = _manifest(out)["rerun_history"]
    assert len(history) == 2 and history[0] == first[0] and [e["kind"] for e in history] == ["initial", "run"]
    assert history[1]["rows_attempted"] > 0                                                              # only the new cells ran


def test_the_manifest_command_regenerates_without_history(tmp_path):
    out = tmp_path / "o.jsonl"
    _invoke(*_args(out, "--max-attempts", "1"))
    out.with_suffix(".manifest.json").unlink()
    _invoke("manifest", "-o", str(out))
    assert _manifest(out).get("rerun_history") is None


def test_docs_state_one_writer_at_a_time_and_describe_the_history():
    raw = (pathlib.Path(__file__).resolve().parent.parent / "docs" / "AGENTPORT_BENCH.md").read_text()
    docs = " ".join(raw.split())                                            # undo the line wrapping
    assert "One writer at a time" in docs and "no file locking" in docs and "two processes" in docs
    assert "rerun_history" in docs and "`initial` entry" in docs and "Earlier entries are never rewritten" in docs
    assert raw.index("One writer at a time") < raw.index("The rerun rewrites the file in place")      # next to the rerun workflow
