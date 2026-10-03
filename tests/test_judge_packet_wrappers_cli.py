import json

from click.testing import CliRunner

from safelabs.judge import packet as P
from safelabs.judge.backends import CachedBackend, FakeBackend
from safelabs.judge.cache import JudgeCache
from safelabs.judge.cli import main
from safelabs.judge.hybrid import HybridScorer
from safelabs.judge.template import load_template
from safelabs.judge.wrappers import Wording, load_wordings, make_variants
from tests.judge_helpers import TEMPLATE_TEXT, StubDetector, make_template

import asyncio
import csv
import pytest

HEAD = ["item_id", "prompt", "response", "label", "confidence", "functional_content", "note"]


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def packet(tmp_path):
    r1 = [["a1", "prompt one", "[P] REFUSE here", "SAFE_REFUSAL", "3", "N", ""],
          ["a2", "prompt two", "[U] COMPLY here", "UNSAFE_COMPLIANCE", "2", "", ""],
          ["a3", "prompt three", "[U] PARTIAL here", "PARTIAL_COMPLIANCE", "2", "Y", ""],
          ["a4", "prompt four", "[F] COMPLY there", "UNSAFE_COMPLIANCE", "3", "N", ""]]
    r2 = [["a2", "prompt two", "[U] COMPLY here", "UNSAFE_COMPLIANCE", "2", "N", ""], ["a3", "prompt three", "[U] PARTIAL here", "UNSAFE_COMPLIANCE", "1", "N", ""]]
    key = [["a1", "ASI01"], ["a2", "ASI01"], ["a3", "ASI02"], ["a4", "ASI01"]]
    write_csv(tmp_path / "r1.csv", HEAD, r1)
    write_csv(tmp_path / "r2.csv", HEAD, r2)
    write_csv(tmp_path / "key.csv", ["item_id", "category"], key)
    return tmp_path / "r1.csv", tmp_path / "r2.csv", tmp_path / "key.csv"


def test_build_items_merges_flags_labels_and_category(tmp_path):
    r1, r2, key = packet(tmp_path)
    items = P.build_items({"rater1": P.read_csv(r1), "rater2": P.read_csv(r2)}, P.read_csv(key))
    by = {i.item_id: i for i in items}
    assert [i.item_id for i in items] == ["a1", "a2", "a3", "a4"]
    assert by["a1"].functional_content is False
    assert by["a2"].functional_content is True  # rater 1 left it blank: treated as Y
    assert by["a3"].functional_content is True  # rater 1 said Y even though rater 2 said N
    assert by["a3"].category == "ASI02" and by["a3"].human_label == {"rater1": "PARTIAL_COMPLIANCE", "rater2": "UNSAFE_COMPLIANCE"}
    blank_ok = {i.item_id: i for i in P.build_items({"rater1": P.read_csv(r1)}, P.read_csv(key), blank_is_y=False)}
    assert blank_ok["a2"].functional_content is False and blank_ok["a3"].functional_content is True


async def test_score_items_returns_ids_and_verdicts_without_text(tmp_path):
    r1, _, key = packet(tmp_path)
    items = P.build_items({"rater1": P.read_csv(r1)}, P.read_csv(key), blank_is_y=False)
    items = [i for i in items if not i.functional_content]
    sc = HybridScorer(FakeBackend(make_template(), local=True), mode="hybrid", detectors=[StubDetector("prompt_injection")])
    rows = await P.score_items(sc, items, condition="hybrid")
    assert [r["item_id"] for r in rows] == ["a1", "a2", "a4"]
    assert {r["item_id"]: r["path"] for r in rows} == {"a1": "pattern", "a2": "judge_uncertain", "a4": "pattern"}
    assert all(r["condition"] == "hybrid" and r["variant"] == "orig" for r in rows)
    dumped = json.dumps(rows)
    assert "REFUSE here" not in dumped and "prompt one" not in dumped


def test_wrappers_prefix_suffix_ids_and_filtering(tmp_path):
    f = tmp_path / "w.json"
    f.write_text(json.dumps([{"name": "W1", "position": "prefix", "text": "synthetic opening line"}, {"name": "W2", "position": "suffix", "text": "synthetic closing line"}]))
    ws = load_wordings(f)
    assert ws == [Wording("W1", "prefix", "synthetic opening line"), Wording("W2", "suffix", "synthetic closing line")]
    base = [P.PacketItem("a1", "p", "body one", "ASI01", False), P.PacketItem("a2", "p", "body two", "ASI01", True)]
    out = make_variants(base, ws)
    assert [o.item_id for o in out] == ["a1::W1", "a1::W2", "a2::W1", "a2::W2"]
    assert out[0].response == "synthetic opening line\n\nbody one" and out[1].response == "body one\n\nsynthetic closing line"
    assert out[0].base_item_id == "a1" and out[0].variant == "W1" and out[2].functional_content is True
    assert [o.item_id for o in make_variants(base, ws, only_ids={"a2"})] == ["a2::W1", "a2::W2"]
    assert base[0].response == "body one"  # originals untouched


@pytest.mark.parametrize("entry", [{"name": "W", "position": "middle", "text": "x"}, {"name": "", "position": "prefix", "text": "x"}, {"name": "a::b", "position": "prefix", "text": "x"}])
def test_wording_validation(tmp_path, entry):
    f = tmp_path / "w.json"
    f.write_text(json.dumps([entry]))
    with pytest.raises(ValueError):
        load_wordings(f)


def test_wording_names_must_be_unique(tmp_path):
    f = tmp_path / "w.json"
    f.write_text(json.dumps([{"name": "W", "position": "prefix", "text": "x"}, {"name": "W", "position": "suffix", "text": "y"}]))
    with pytest.raises(ValueError):
        load_wordings(f)


async def fill_cache(tmp_path, items, tpl, backend_id="fake:rules-v1"):
    cache = JudgeCache(tmp_path / "cache.jsonl")
    be = CachedBackend(FakeBackend(tpl, backend_id=backend_id, local=True), cache)
    for it in items:
        await be.judge(it.prompt, it.response, it.category, functional_content=it.functional_content)
    return cache


def test_cli_replay_then_analyze_round_trip(tmp_path):
    r1, r2, key = packet(tmp_path)
    (tmp_path / "t.md").write_text(TEMPLATE_TEXT)
    wf = tmp_path / "w.json"
    wf.write_text(json.dumps([{"name": "W1", "position": "prefix", "text": "synthetic opening line"}]))
    items = P.build_items({"rater1": P.read_csv(r1), "rater2": P.read_csv(r2)}, P.read_csv(key))
    items += make_variants(items, load_wordings(wf), only_ids={"a4"})
    asyncio.run(fill_cache(tmp_path, items, load_template(tmp_path / "t.md")))
    out = tmp_path / "dec.jsonl"
    res = CliRunner().invoke(main, ["replay", "--rater1", str(r1), "--rater2", str(r2), "--key", str(key), "--cache", str(tmp_path / "cache.jsonl"),
                                    "--template", str(tmp_path / "t.md"), "--backend-id", "fake:rules-v1", "--condition", "pattern_only",
                                    "--condition", "judge_only", "--condition", "hybrid", "--condition", "hybrid_plus:1.0",
                                    "--wordings", str(wf), "--wrap-ids", str(_ids(tmp_path, "a4")), "--out", str(out)])
    assert res.exit_code == 0, res.output
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert {r["condition"] for r in rows} == {"pattern_only", "judge_only", "hybrid", "hybrid_plus:1.0"}
    assert sum(1 for r in rows if r["variant"] == "W1") == 4 and all("response" not in r for r in rows)
    jo = {r["item_id"]: r for r in rows if r["condition"] == "judge_only" and r["variant"] == "orig"}
    assert jo["a2"]["verdict"] == "vulnerable" and jo["a1"]["verdict"] == "pass"
    ana = tmp_path / "rep.json"
    res = CliRunner().invoke(main, ["analyze", "--rater1", str(r1), "--rater2", str(r2), "--key", str(key), "--decisions", str(out), "--out", str(ana), "--min-class", "1"])
    assert res.exit_code == 0 and "pending adjudication: 1" in res.output, res.output
    rep = json.loads(ana.read_text())
    assert rep["pending_adjudication"] == ["a3"] and rep["inter_rater"]["n_overlap"] == 2
    assert set(rep["conditions"]) == {"pattern_only", "judge_only", "hybrid", "hybrid_plus:1.0"}
    assert rep["cost"]["judge_only"]["judge_calls"] == 5 and rep["cost"]["pattern_only"]["judge_calls"] == 0


def _ids(tmp_path, *ids):
    p = tmp_path / "ids.txt"
    p.write_text("\n".join(ids))
    return p


def test_cli_replay_stops_on_a_cache_miss(tmp_path):
    r1, _, key = packet(tmp_path)
    (tmp_path / "t.md").write_text(TEMPLATE_TEXT)
    (tmp_path / "cache.jsonl").write_text("")
    res = CliRunner().invoke(main, ["replay", "--rater1", str(r1), "--key", str(key), "--cache", str(tmp_path / "cache.jsonl"), "--template", str(tmp_path / "t.md"),
                                    "--backend-id", "fake:rules-v1", "--condition", "judge_only", "--out", str(tmp_path / "o.jsonl")])
    assert res.exit_code == 2 and "cache miss" in res.output
    assert not (tmp_path / "o.jsonl").exists()


def test_cli_has_no_live_command_and_rejects_bad_condition(tmp_path):
    assert set(main.commands) == {"replay", "analyze"}
    r1, _, key = packet(tmp_path)
    (tmp_path / "t.md").write_text(TEMPLATE_TEXT)
    (tmp_path / "cache.jsonl").write_text("")
    res = CliRunner().invoke(main, ["replay", "--rater1", str(r1), "--key", str(key), "--cache", str(tmp_path / "cache.jsonl"), "--template", str(tmp_path / "t.md"),
                                    "--backend-id", "b", "--condition", "live", "--out", str(tmp_path / "o.jsonl")])
    assert res.exit_code != 0
