"""scripts/rescore_pilot_dataset.py: reproduction check and echo-aware before/after (issue #69).

Uses a tiny synthetic dataset built in tmp_path (same file layout and row shape as the pilot
dataset), never the real one.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from safelabs.prompts.loader import get_library
from safelabs.scoring.scorer import Scorer

_SPEC = importlib.util.spec_from_file_location(
    "rescore_pilot_dataset", Path(__file__).resolve().parent.parent / "scripts" / "rescore_pilot_dataset.py")
rescore = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rescore)

ECHOED_ID = "ASI01-002"   # prompt copied once into the response: echo-aware discounts its indicators
PLAIN_IDS = ("ASI01-001", "ASI01-003")


def _prompt(pid: str) -> str:
    return next(e.prompt for e in get_library().entries if e.id == pid)


def _bridge_row(prompt: str, keyword: str, chip: str = "Interprété : \"\" · 0") -> dict:
    return {
        "at": "2026-01-01T00:00:00.000Z", "prompt": prompt, "sent_to_ai": True, "api_status": 200,
        "api_response": {"ok": True, "filter": {"keyword": keyword, "name": ""}},
        "ui_interpreted": chip, "patients_shown": 0, "patient_names": [],
    }


def _expected(row: dict, pid: str) -> dict:
    res = asyncio.run(Scorer(echo_aware=False).score(
        "prompt_injection", row["prompt"], rescore.build_response(row)))
    return {"id": pid, "category": "ASI01", "verdict": res.verdict.value,
            "confidence": res.confidence, "reasoning": res.reasoning, "latency_ms": 1.0}


@pytest.fixture
def dataset(tmp_path):
    rows = {pid: _bridge_row(_prompt(pid), "") for pid in PLAIN_IDS}
    rows[ECHOED_ID] = _bridge_row(_prompt(ECHOED_ID), _prompt(ECHOED_ID))
    data = tmp_path / "data"
    (data / "final-run").mkdir(parents=True)
    for _name, log, res in rescore.RUNS:
        # Real logs hold one JSON object per line and start with a UTF-8 BOM in some files.
        (data / log).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows.values()) + "\n",
                                encoding="utf-8-sig")
        (data / res).write_text(json.dumps({"results": [_expected(r, pid) for pid, r in rows.items()]}),
                                encoding="utf-8-sig")
    return tmp_path


def test_reproduced_runs_pass_and_show_only_changed_rows(dataset, capsys):
    assert rescore.main([str(dataset)]) == 0
    out = capsys.readouterr().out
    for name, _l, _r in rescore.RUNS:
        assert f"## {name}: reproduced 3/3" in out
    assert out.count(f"| {ECHOED_ID} |") == 3
    assert "| ASI01-001 |" not in out and "| ASI01-003 |" not in out
    assert "Totals before: FAIL=1, UNCERTAIN=2" in out
    assert "Totals after: UNCERTAIN=3" in out


def test_tampered_expected_verdict_fails_and_reports_row(dataset, capsys):
    path = dataset / "data" / "retest-asi01.json"
    doc = json.loads(path.read_text("utf-8-sig"))
    doc["results"][0]["verdict"] = "vulnerable"
    path.write_text(json.dumps(doc), encoding="utf-8")

    assert rescore.main([str(dataset)]) == 1
    out = capsys.readouterr().out
    assert "## retest: reproduced 2/3" in out
    assert "MISMATCH ASI01-001" in out and "vulnerable" in out
    retest = out.split("## retest")[1].split("## final")[0]
    assert "| id |" not in retest and "Totals" not in retest
    assert "## baseline: reproduced 3/3" in out and "## final: reproduced 3/3" in out


def test_json_out_written_only_when_requested(dataset, tmp_path):
    assert rescore.main([str(dataset)]) == 0
    assert not list(tmp_path.glob("*.json"))
    target = tmp_path / "out" / "rescore.json"
    target.parent.mkdir()
    assert rescore.main([str(dataset), "--json-out", str(target)]) == 0
    dump = json.loads(target.read_text("utf-8"))
    assert set(dump) == {"baseline", "retest", "final"}
    assert dump["baseline"][ECHOED_ID]["before"]["verdict"] == "fail"
    assert dump["baseline"][ECHOED_ID]["after"]["verdict"] == "uncertain"
