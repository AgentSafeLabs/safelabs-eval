#!/usr/bin/env python
"""Rescore the frozen ASI01 pilot dataset with the current scorer.

Dataset: Zenodo, DOI 10.5281/zenodo.23623801 (read-only; point this script at a local copy).

For each run (baseline, retest, final) the script rebuilds the exact string SafeLabs scored for
each prompt from the run's bridge log, then scores it twice: with ``Scorer(echo_aware=False)``
and with the default echo-aware ``Scorer()`` (issue #69).

The first scoring must reproduce the run's recorded verdict, confidence and reasoning for every
prompt. A run that does not reproduce is reported row by row and no before/after table is
printed for it; the script then exits non-zero. Only reproduced runs get the before/after view.

The scored response is the bridge's captured fields serialized as compact JSON, in this key
order: sent_to_ai, api_status, api_response, ui_interpreted, patients_shown, patient_names.
Prompts are matched to IDs through the repo's prompt library; when a prompt appears more than
once in a bridge log (a retry), the last attempt is the one that was scored.

No network calls and no agent or model calls: scoring only.

Usage:
    python scripts/rescore_pilot_dataset.py <dataset_dir> [--json-out PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from safelabs.runner import CATEGORY_EVAL_TYPE, select_prompts
from safelabs.scoring.scorer import Scorer

RUNS = (
    ("baseline", "bridge-log.jsonl", "baseline-asi01.json"),
    ("retest", "retest-bridge-log.jsonl", "retest-asi01.json"),
    ("final", "final-run/final-bridge-log.jsonl", "final-run/final-asi01.json"),
)
RESPONSE_KEYS = ("sent_to_ai", "api_status", "api_response", "ui_interpreted", "patients_shown", "patient_names")
CATEGORY = "ASI01"


def build_response(row: dict) -> str:
    """The string SafeLabs scored: the bridge's captured fields as one compact JSON string."""
    return json.dumps({k: row[k] for k in RESPONSE_KEYS}, ensure_ascii=False, separators=(",", ":"))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def load_run(data_dir: Path, log_name: str, result_name: str, prompts_by_text: dict):
    """Return ({prompt_id: (prompt, response)}, {prompt_id: expected result row})."""
    scored: dict[str, tuple[str, str]] = {}
    for line in _read(data_dir / log_name).splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        entry = prompts_by_text.get(row["prompt"])
        if entry is None:
            raise SystemExit(f"{log_name}: prompt not found in the {CATEGORY} library: {row['prompt'][:60]!r}")
        scored[entry.id] = (entry.prompt, build_response(row))  # later attempts overwrite earlier ones
    expected = {r["id"]: r for r in json.loads(_read(data_dir / result_name))["results"]}
    return scored, expected


async def score_run(scored: dict[str, tuple[str, str]]) -> dict[str, dict]:
    eval_type = CATEGORY_EVAL_TYPE[CATEGORY]
    before_scorer, after_scorer = Scorer(echo_aware=False), Scorer()
    out = {}
    for pid in sorted(scored):
        prompt, response = scored[pid]
        before = await before_scorer.score(eval_type, prompt, response)
        after = await after_scorer.score(eval_type, prompt, response)
        out[pid] = {"before": before, "after": after}
    return out


def _brief(r) -> dict:
    return {"verdict": r.verdict.value, "confidence": r.confidence, "reasoning": r.reasoning}


def mismatches(results: dict[str, dict], expected: dict[str, dict]) -> list[tuple[str, dict, dict]]:
    bad = []
    for pid in sorted(set(results) | set(expected)):
        got = _brief(results[pid]["before"]) if pid in results else None
        want = {k: expected[pid][k] for k in ("verdict", "confidence", "reasoning")} if pid in expected else None
        if got != want:
            bad.append((pid, want, got))
    return bad


def _changed(row: dict) -> bool:
    b, a = row["before"], row["after"]
    return (b.verdict, b.confidence, b.indicators) != (a.verdict, a.confidence, a.indicators) or bool(a.echoed)


def print_table(results: dict[str, dict]) -> None:
    rows = [(pid, r) for pid, r in results.items() if _changed(r)]
    if rows:
        print("| id | before (verdict / conf / indicators) | after (verdict / conf) | discounted as echo |")
        print("|---|---|---|---|")
        for pid, r in rows:
            b, a = r["before"], r["after"]
            print(f"| {pid} | {b.verdict.value.upper()} / {b.confidence} / {len(b.indicators)} "
                  f"| {a.verdict.value.upper()} / {a.confidence} | {len(a.echoed)} |")
    else:
        print("No row changed under echo-aware scoring.")
    for label in ("before", "after"):
        totals = Counter(r[label].verdict.value for r in results.values())
        print(f"Totals {label}: " + ", ".join(f"{v.upper()}={n}" for v, n in sorted(totals.items())))


def _row_json(r: dict) -> dict:
    out = {}
    for label in ("before", "after"):
        s = r[label]
        out[label] = {**_brief(s), "indicators": s.indicators, "echoed": s.echoed}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dataset_dir", type=Path)
    ap.add_argument("--json-out", type=Path, default=None, help="write full per-row results here (default: nothing written)")
    args = ap.parse_args(argv)

    data_dir = args.dataset_dir.expanduser() / "data"
    prompts_by_text = {e.prompt: e for e in select_prompts([CATEGORY])}
    failed = False
    dump: dict[str, dict] = {}

    for name, log_name, result_name in RUNS:
        scored, expected = load_run(data_dir, log_name, result_name, prompts_by_text)
        results = asyncio.run(score_run(scored))
        bad = mismatches(results, expected)
        total = len(expected)
        print(f"\n## {name}: reproduced {total - len(bad)}/{total}")
        if bad:
            failed = True
            for pid, want, got in bad:
                print(f"  MISMATCH {pid}: expected {want} got {got}")
            print("  (no before/after table: the run did not reproduce)")
            continue
        print_table(results)
        dump[name] = {pid: _row_json(r) for pid, r in results.items()}

    if args.json_out and dump:
        args.json_out.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
