"""Tests for document delivery wired into run_eval and `safelabs run` (issue #90, part 2).

Part 1 (render function and templates) is covered by test_delivery_render.py. Here: the runner seam,
the CLI options, the metadata on results, and proof that a run without delivery is unchanged.
No network; the HTTP adapter is replaced by a recording fake.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

import pytest
from click.testing import CliRunner

import safelabs.agents.http_adapter as http_adapter_module
import safelabs.cli as cli_module
from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse
from safelabs.cli import main
from safelabs.delivery import render
from safelabs.delivery.mode import bridge_delivery, template_delivery
from safelabs.prompts import get_library
from safelabs.runner import EvalRecord, run_eval
from safelabs.scoring.scorer import Scorer

REFUSAL = "I cannot and will not comply with that request. That looks like an injection attempt."
FILLER_SENTENCE = "Attended a routine review. General wellbeing reported as good."  # referral-letter v1 filler


def _entry(prompt_id):
    return next(e for e in get_library().entries if e.id == prompt_id)


async def _no_sleep(_):
    return None


def _run(agent_fn, **kw):
    kw.setdefault("sleep", _no_sleep)
    return asyncio.run(run_eval(agent_fn, **kw))


class Recorder:
    """Agent that remembers every text it was sent."""

    def __init__(self, reply=lambda text: AgentResponse(output=REFUSAL, latency_ms=2.0)):
        self.seen: list[str] = []
        self.reply = reply

    def __call__(self, text):
        self.seen.append(text)
        return self.reply(text)


# ── default run: nothing changes ──────────────────────────────────────────

OLD_RECORD_KEYS = [
    "prompt_id", "category", "severity", "prompt", "response", "latency_ms", "scoring_result", "error",
    "status", "error_class", "error_subclass", "attempts", "attempt_errors", "tool_call_only",
]


def test_default_run_sends_the_prompt_and_saves_records_exactly_as_before():
    agent = Recorder()
    result = _run(agent, categories=["ASI01"], limit=3)
    assert agent.seen == [e.prompt for e in get_library().by_category("ASI01")[:3]]
    for rec in result.records:
        assert rec.delivery is None
        assert list(rec.model_dump()) == OLD_RECORD_KEYS
        assert list(json.loads(rec.model_dump_json())) == OLD_RECORD_KEYS
        assert '"delivery"' not in rec.model_dump_json()


def test_default_run_summary_text_has_no_delivery_line(capsys):
    _run(Recorder(), categories=["ASI01"], limit=2).summary()
    out = capsys.readouterr().out
    assert "elivery" not in out and "SUMMARY" in out and "2 prompts evaluated" in out


def test_default_record_with_a_warning_keeps_the_old_shape():
    rec = _run(Recorder(lambda t: AgentResponse(output=json.dumps({"s": _entry("ASI01-001").prompt}))),
               prompt_ids=["ASI01-001"]).records[0]
    assert rec.warnings and "delivery" not in rec.model_dump()
    assert list(rec.model_dump()) == OLD_RECORD_KEYS[:14] + ["warnings"]


# ── delivery run: what the target receives and what is recorded ──────────

def test_delivery_run_sends_the_rendered_text_and_records_the_metadata():
    agent = Recorder()
    mode = template_delivery("referral-letter", position="middle")
    result = _run(agent, prompt_ids=["ASI01-001", "ASI01-002"], delivery=mode)
    assert len(agent.seen) == 2
    for text, rec in zip(agent.seen, result.records):
        expected = render(_entry(rec.prompt_id).prompt, "referral-letter", position="middle")
        assert text == expected.text and text != rec.prompt
        assert rec.delivery == asdict(expected.record)
        assert rec.delivery["mode"] == "template" and rec.delivery["payload_position"] == "middle"
        assert rec.prompt == _entry(rec.prompt_id).prompt                  # the record keeps the original prompt


def test_delivery_is_saved_with_the_record_and_round_trips():
    rec = _run(Recorder(), prompt_ids=["ASI01-001"], delivery=template_delivery("support-ticket", position="start")).records[0]
    saved = json.loads(rec.model_dump_json())
    assert saved["delivery"]["template_id"] == "support-ticket" and saved["delivery"]["payload_recency"] == "newest"
    assert EvalRecord.model_validate_json(rec.model_dump_json()).delivery == rec.delivery


def test_the_scorer_receives_the_original_prompt_not_the_rendered_text():
    class Spy(Scorer):
        calls: list = []

        async def score(self, eval_type, prompt, response, *a, **kw):
            Spy.calls.append(prompt)
            return await super().score(eval_type, prompt, response, *a, **kw)

    Spy.calls = []
    agent = Recorder()
    _run(agent, prompt_ids=["ASI01-001"], scorer=Spy(), delivery=template_delivery("referral-letter", position="end"))
    original = _entry("ASI01-001").prompt
    assert Spy.calls == [original] and agent.seen[0] != original and original in agent.seen[0]


def test_payload_surfaced_compares_against_the_payload_not_the_document():
    payload = _entry("ASI01-001").prompt
    mode = template_delivery("referral-letter", position="end")
    quotes_filler = _run(Recorder(lambda t: AgentResponse(output=json.dumps({"summary": FILLER_SENTENCE}))),
                         prompt_ids=["ASI01-001"], delivery=mode).records[0]
    assert quotes_filler.warnings == []                                    # copying document filler is not a warning
    quotes_payload = _run(Recorder(lambda t: AgentResponse(output=json.dumps({"summary": payload}))),
                          prompt_ids=["ASI01-001"], delivery=mode).records[0]
    assert [w["code"] for w in quotes_payload.warnings] == ["PAYLOAD_SURFACED"]


def test_scores_do_not_depend_on_delivery_for_the_same_response():
    reply = lambda t: AgentResponse(output=REFUSAL, latency_ms=1.0)  # noqa: E731
    plain = _run(Recorder(reply), prompt_ids=["ASI01-001"]).records[0]
    wrapped = _run(Recorder(reply), prompt_ids=["ASI01-001"], delivery=template_delivery("referral-letter", position="end")).records[0]
    assert plain.scoring_result == wrapped.scoring_result


def test_retries_resend_the_same_rendered_text():
    calls = {"n": 0}

    def flaky(text):
        calls["n"] += 1
        if calls["n"] < 3:
            return AgentResponse(output="", error="HTTP 429: slow down", metadata={"status_code": 429})
        return AgentResponse(output=REFUSAL)

    agent = Recorder(flaky)
    rec = _run(agent, prompt_ids=["ASI01-001"], delivery=template_delivery("referral-letter", position="start")).records[0]
    assert rec.attempts == 3 and len(agent.seen) == 3 and len(set(agent.seen)) == 1
    assert agent.seen[0] == render(rec.prompt, "referral-letter", position="start").text
    assert rec.delivery["rendered_sha256"] == render(rec.prompt, "referral-letter", position="start").record.rendered_sha256


def test_missing_and_error_records_carry_the_delivery_metadata():
    always_429 = Recorder(lambda t: AgentResponse(output="", error="HTTP 429: slow down", metadata={"status_code": 429}))
    mode = template_delivery("referral-letter", position="end")
    missing = _run(always_429, prompt_ids=["ASI01-001"], max_attempts=2, delivery=mode).records[0]
    assert missing.is_missing and missing.delivery["template_id"] == "referral-letter"
    assert len(always_429.seen) == 2 and always_429.seen[0] == always_429.seen[1] != missing.prompt

    def boom(text):
        raise ValueError("boom")

    err = _run(boom, prompt_ids=["ASI01-001"], delivery=mode).records[0]
    assert err.error and not err.is_missing and err.delivery["payload_position"] == "end"


# ── validation ────────────────────────────────────────────────────────────

def test_position_is_required_and_checked():
    with pytest.raises(TypeError):
        template_delivery("referral-letter")                                # type: ignore[call-arg]
    with pytest.raises(ValueError, match="invalid position"):
        template_delivery("referral-letter", position="last")


def test_unknown_template_id_or_version_is_rejected():
    with pytest.raises(ValueError, match="unknown template id"):
        template_delivery("no-such-template", position="end")
    with pytest.raises(ValueError, match="unknown version"):
        template_delivery("referral-letter", position="end", template_version=99)


@pytest.mark.parametrize("mode", [template_delivery("referral-letter", position="end"), bridge_delivery("my-bridge", "1")])
def test_delivery_is_asi01_only_and_nothing_is_sent_otherwise(mode):
    agent = Recorder()
    for cats in (["ASI03"], ["ASI01", "ASI02"], None):
        with pytest.raises(ValueError, match="ASI01 only"):
            _run(agent, categories=cats, delivery=mode)
    assert agent.seen == []


def test_already_indirect_prompts_are_sent_as_they_are_and_recorded_as_such():
    assert "indirect" in _entry("ASI01-003").tags and "indirect" not in _entry("ASI01-001").tags
    agent = Recorder()
    result = _run(agent, prompt_ids=["ASI01-001", "ASI01-003"], delivery=template_delivery("support-ticket", position="end"))
    plain, indirect = result.records
    assert agent.seen[0] != plain.prompt and plain.delivery["mode"] == "template" and "already_indirect" not in plain.delivery
    assert agent.seen[1] == indirect.prompt == _entry("ASI01-003").prompt
    assert indirect.delivery == {
        "mode": "direct", "already_indirect": True, "skipped_template_id": "support-ticket",
        "skipped_template_version": 1, "skipped_payload_position": "end",
    }


def test_bridge_label_sends_the_payload_unchanged_and_is_recorded_as_declared():
    agent = Recorder()
    rec = _run(agent, prompt_ids=["ASI01-001"], delivery=bridge_delivery("clinic-ui", "2.1")).records[0]
    assert agent.seen == [rec.prompt]
    assert rec.delivery == {"mode": "bridge", "bridge_id": "clinic-ui", "bridge_version": "2.1", "declared": True}
    with pytest.raises(ValueError):
        bridge_delivery("", "1")


# ── CLI ───────────────────────────────────────────────────────────────────

_SEEN: list[str] = []


class _Fake(AgentAdapter):
    def __init__(self, base_url, headers=None, timeout=30.0, **kw):
        super().__init__(timeout=timeout)

    @property
    def adapter_type(self):
        return "http"

    async def _execute(self, prompt):
        _SEEN.append(prompt)
        return AgentResponse(output=REFUSAL, latency_ms=1.5)


@pytest.fixture(autouse=True)
def _fake_http(monkeypatch):
    _SEEN.clear()
    monkeypatch.setattr(http_adapter_module, "HttpAdapter", _Fake)


def _invoke(*args):
    return CliRunner().invoke(main, ["run", "-t", "http://localhost:9/chat", *args])


def _json(*args):
    r = _invoke("-o", "json", *args)
    assert r.exit_code == 0, r.output
    return json.loads(r.output)


def test_cli_json_without_delivery_is_unchanged():
    doc = _json("-c", "ASI01")
    assert list(doc) == ["results"]
    old = ["id", "category", "verdict", "confidence", "reasoning", "latency_ms", "indicators", "echoed_indicators"]
    assert len(doc["results"]) == 30 and all(list(r) == old for r in doc["results"])
    assert _SEEN == [e.prompt for e in get_library().by_category("ASI01")]


def test_cli_text_report_without_delivery_is_unchanged():
    r = _invoke("-c", "ASI01", "--limit", "2")
    assert r.exit_code == 0 and "elivery" not in r.output
    assert "Category: ASI01 (2 prompts)" in r.output and "PASS      : 2" in r.output and "No vulnerabilities detected" in r.output


def test_cli_does_not_pass_delivery_to_run_eval_unless_asked(monkeypatch):
    seen = []
    real = run_eval

    async def spy(agent_fn, **kw):
        seen.append(kw)
        return await real(agent_fn, **kw)

    monkeypatch.setattr(cli_module, "run_eval", spy)
    assert _invoke("-c", "ASI01", "--limit", "1").exit_code == 0
    assert _invoke("-c", "ASI01", "--limit", "1", "--delivery-template", "referral-letter", "--payload-position", "end").exit_code == 0
    assert "delivery" not in seen[0] and "delivery" in seen[1]


def test_cli_template_delivery_sends_the_document_and_adds_delivery_to_rows():
    doc = _json("-c", "ASI01", "--prompt-id", "ASI01-001", "--delivery-template", "referral-letter", "--payload-position", "start")
    expected = render(_entry("ASI01-001").prompt, "referral-letter", position="start")
    assert _SEEN == [expected.text]
    row = doc["results"][0]
    assert list(row)[-1] == "delivery" and row["delivery"] == asdict(expected.record)
    run_level = doc["delivery"]
    assert run_level == {k: v for k, v in asdict(expected.record).items() if k not in ("rendered_sha256",)}
    assert "rendered_sha256" not in run_level


def test_cli_text_report_names_the_delivery():
    r = _invoke("-c", "ASI01", "--limit", "1", "--delivery-template", "support-ticket", "--payload-position", "middle")
    assert r.exit_code == 0 and "Delivery: template support-ticket v1, payload at middle" in r.output


def test_cli_bridge_label():
    doc = _json("-c", "ASI01", "--prompt-id", "ASI01-001", "--delivery-label", "clinic-ui@2.1")
    assert _SEEN == [_entry("ASI01-001").prompt]
    assert doc["results"][0]["delivery"] == {"mode": "bridge", "bridge_id": "clinic-ui", "bridge_version": "2.1", "declared": True}
    assert doc["delivery"] == doc["results"][0]["delivery"]


@pytest.mark.parametrize("args,message", [
    (["--delivery-template", "referral-letter"], "--payload-position is required"),
    (["--payload-position", "end"], "only valid with --delivery-template"),
    (["--delivery-template", "referral-letter", "--payload-position", "end", "--delivery-label", "x@1"], "cannot be combined"),
    (["--delivery-label", "no-version"], "ID@VERSION"),
    (["--delivery-label", "@1"], "ID@VERSION"),
    (["--delivery-label", "x@"], "ID@VERSION"),
    (["--delivery-template", "no-such-template", "--payload-position", "end"], "Invalid value"),
    (["--delivery-template", "referral-letter", "--payload-position", "last"], "Invalid value"),
])
def test_cli_rejects_bad_delivery_options_before_sending_anything(args, message):
    r = _invoke("-c", "ASI01", *args)
    assert r.exit_code == 2 and message in r.output and _SEEN == []


@pytest.mark.parametrize("category", ["ASI03", "all"])
def test_cli_delivery_with_another_category_fails_before_sending_anything(category):
    r = _invoke("-c", category, "--delivery-template", "referral-letter", "--payload-position", "end")
    assert r.exit_code == 2 and "ASI01 only" in r.output and _SEEN == []
