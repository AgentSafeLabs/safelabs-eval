"""
tests/test_cli_run_eval.py

`safelabs run` goes through safelabs.runner.run_eval(): infrastructure errors are retried and, when
they persist, reported as MISSING and excluded from the counts; other errors stay unscored ERROR rows;
normal output keeps its shape. The HTTP adapter is replaced by a scripted fake; no network, zero delays.
"""

from __future__ import annotations

import json
import zlib

import pytest
from click.testing import CliRunner

import safelabs.agents.http_adapter as http_adapter_module
import safelabs.cli as cli_module
from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse, ToolCall
from safelabs.cli import main
from safelabs.prompts import get_library
from safelabs.runner import EvalRecord, run_eval

_REFUSAL = "I cannot and will not comply with that request. That looks like an injection attempt."
_SCRIPT = {"respond": None, "calls": 0}


class _Fake(AgentAdapter):
    """Stands in for HttpAdapter(base_url=, headers=, timeout=); behaviour comes from _SCRIPT['respond'](prompt)."""

    def __init__(self, base_url, headers=None, timeout=30.0, **kw):
        super().__init__(timeout=timeout)

    @property
    def adapter_type(self):
        return "http"

    async def _execute(self, prompt):
        _SCRIPT["calls"] += 1
        return _SCRIPT["respond"](prompt)


@pytest.fixture(autouse=True)
def _fake_http(monkeypatch):
    _SCRIPT.update(respond=lambda p: AgentResponse(output=_REFUSAL, latency_ms=1.5), calls=0)
    monkeypatch.setattr(http_adapter_module, "HttpAdapter", _Fake)


def _flaky(prompt):
    if zlib.crc32(prompt.encode()) % 10 != 0:
        return AgentResponse(output="", error="HTTP 429: slow down", metadata={"status_code": 429})
    return AgentResponse(output=_REFUSAL, latency_ms=1.5)


def _invoke(*args):
    return CliRunner().invoke(main, ["run", "-t", "http://localhost:9/chat", *args])


def _json(*args):
    r = _invoke("-o", "json", *args)
    assert r.exit_code == 0, r.output
    return json.loads(r.output)["results"]


# ── normal runs keep their shape ──────────────────────────────────────────

def test_text_output_for_a_clean_run_has_the_usual_lines():
    r = _invoke("-c", "ASI01")
    assert r.exit_code == 0, r.output
    out = r.output
    assert "safelabs-eval v" in out and "Category: ASI01 (30 prompts)" in out and "[ASI01-001]" in out and "Prompt :" in out
    assert out.index("[ASI01-001]") < out.index("Verdict:") < out.index("Reason :")
    assert "SUMMARY (30 prompts)" in out and "PASS      : 30" in out and "No vulnerabilities detected" in out
    assert "MISSING" not in out and "RETRIES" not in out and "ERRORS" not in out and "tool-call-only" not in out


def test_json_rows_keep_exactly_the_old_keys_and_the_adapters_latency():
    rows = _json("-c", "ASI01")
    assert len(rows) == 30 and _SCRIPT["calls"] == 30
    assert all(set(r) == {"id", "category", "verdict", "confidence", "reasoning", "latency_ms"} for r in rows)
    assert all(r["verdict"] == "pass" and r["latency_ms"] == 1.5 for r in rows)


def test_category_all_runs_every_prompt():
    rows = _json("-c", "all")
    assert sorted(r["id"] for r in rows) == sorted(e.id for e in get_library().entries)


def test_the_cli_calls_run_eval_with_the_adapters_execute(monkeypatch):
    seen = {}
    real = run_eval

    async def spy(agent_fn, **kw):
        seen["fn"], seen["kw"] = agent_fn, kw
        return await real(agent_fn, **kw)

    monkeypatch.setattr(cli_module, "run_eval", spy)
    assert _invoke("-c", "asi09", "--retry-profile", "benchmark", "--max-attempts", "4", "--retry-base-delay-s", "0.5").exit_code == 0
    assert seen["fn"].__name__ == "execute" and seen["kw"]["categories"] == ["ASI09"]
    assert (seen["kw"]["retry_profile"], seen["kw"]["max_attempts"], seen["kw"]["base_delay_s"]) == ("benchmark", 4, 0.5)
    assert callable(seen["kw"]["on_start"]) and callable(seen["kw"]["on_record"])
    assert not hasattr(cli_module, "_CAT_EVAL")                                 # one category-to-detector mapping now: the runner's


# ── infrastructure errors: retried, missing, excluded ─────────────────────

def test_a_mostly_rate_limited_endpoint_reports_missing_and_excludes_it_from_the_counts():
    _SCRIPT["respond"] = _flaky
    rows = _json("-c", "ASI01", "--max-attempts", "2", "--retry-base-delay-s", "0")
    missing = [r for r in rows if r.get("status") == "missing_infrastructure"]
    scored = [r for r in rows if "verdict" in r]
    assert len(rows) == 30 and len(missing) + len(scored) == 30 and 0 < len(scored) < 10
    assert all(r["verdict"] == "pass" for r in scored)                          # no UNCERTAIN from the missing prompts
    assert all((r["error_class"], r["error_subclass"], r["attempts"]) == ("infrastructure", "rate_limit_or_quota", 2) for r in missing)
    assert all("verdict" not in r and "HTTP 429" in r["error"] for r in missing)
    assert _SCRIPT["calls"] == 2 * len(missing) + len(scored)
    _SCRIPT["calls"] = 0
    r = _invoke("-c", "ASI01", "--max-attempts", "2", "--retry-base-delay-s", "0")
    out = r.output
    assert f"MISSING   : {len(missing)} (missing_infrastructure)" in out and f"RETRIES   : {len(missing)} extra attempt(s)" in out
    assert "excluded from the counts above" in out and f"PASS      : {len(scored)}" in out and "UNCERTAIN : 0" in out
    assert f"among the {len(scored)} scored prompts" in out and "MISSING" in out.split("SUMMARY")[0]


def test_only_missing_prompts_is_reported_as_unreachable():
    _SCRIPT["respond"] = lambda p: AgentResponse(output="", error="HTTP 503: down", metadata={"status_code": 503})
    r = _invoke("-c", "ASI09", "--max-attempts", "1")
    assert r.exit_code == 0 and "All prompts errored" in r.output and "excluded from the counts" in r.output
    assert "PASS      : 0" in r.output and "No vulnerabilities detected" not in r.output


@pytest.mark.parametrize("flags,attempts", [
    ([], 3), (["--retry-profile", "default"], 3), (["--retry-profile", "benchmark"], 6),
    (["--retry-profile", "benchmark", "--max-attempts", "2"], 2), (["--max-attempts", "1"], 1),
])
def test_retry_profile_and_overrides(flags, attempts):
    _SCRIPT["respond"] = lambda p: AgentResponse(output="", error="HTTP 429: slow down", metadata={"status_code": 429})
    rows = _json("-c", "ASI09", "--retry-base-delay-s", "0", *flags)
    assert all(r["attempts"] == attempts for r in rows) and _SCRIPT["calls"] == attempts * len(rows)


def test_a_prompt_that_succeeds_after_a_retry_is_scored_and_marked_with_its_attempts():
    seen = set()

    def once(prompt):
        if prompt not in seen:
            seen.add(prompt)
            return AgentResponse(output="", error="HTTP 429: slow down", metadata={"status_code": 429})
        return AgentResponse(output=_REFUSAL, latency_ms=1.5)

    _SCRIPT["respond"] = once
    rows = _json("-c", "ASI09", "--retry-base-delay-s", "0")
    assert all(r["verdict"] == "pass" and r["attempts"] == 2 for r in rows)
    seen.clear()
    out = _invoke("-c", "ASI09", "--retry-base-delay-s", "0").output
    assert "RETRIES   :" in out and "MISSING   : 0" in out and "excluded from the counts above" not in out


# ── other errors: unscored ERROR rows, as before ──────────────────────────

@pytest.mark.parametrize("error,status", [
    ("HTTP 400: bad request", 400), ("request flagged for possible cybersecurity risk", None), ("provider returned no output text", None),
])
def test_non_infrastructure_errors_stay_unscored_error_rows_and_are_not_retried(error, status):
    _SCRIPT["respond"] = lambda p: AgentResponse(output="", error=error, metadata={"status_code": status} if status else None)
    rows = _json("-c", "ASI09")
    assert all(set(r) == {"id", "error"} and r["error"] == error for r in rows)
    assert _SCRIPT["calls"] == len(rows)                                       # one attempt each
    out = _invoke("-c", "ASI09").output
    assert "ERROR" in out and "could not reach" in out and f"ERRORS    : {len(rows)}" in out and "All prompts errored" in out
    assert "MISSING   :" not in out


# ── tool-call-only ────────────────────────────────────────────────────────

def test_a_tool_call_only_response_is_scored_and_flagged_not_an_error():
    _SCRIPT["respond"] = lambda p: AgentResponse(output="", tool_calls=[ToolCall.from_arguments("lookup", {"q": "x"})], provenance={"tool_calls": "inferred"})
    rows = _json("-c", "ASI09")
    assert all(r["verdict"] == "uncertain" and r["tool_call_only"] is True and "error" not in r for r in rows)
    out = _invoke("-c", "ASI09").output
    assert "[tool-call-only]" in out and "ERRORS" not in out


# ── runner: callbacks and latency ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_eval_progress_callbacks_fire_in_order_and_do_not_change_the_result():
    events = []

    async def agent(prompt):
        events.append(("call", prompt[:10]))
        return _REFUSAL

    plain = await run_eval(agent, categories=["ASI09"])
    events.clear()
    result = await run_eval(agent, categories=["ASI09"], on_start=lambda e: events.append(("start", e.id)),
                            on_record=lambda r: events.append(("record", r.prompt_id)))
    kinds = [k for k, _ in events]
    assert kinds[:3] == ["start", "call", "record"] and kinds.count("start") == kinds.count("record") == len(result.records)
    assert [r.verdict for r in result.records] == [r.verdict for r in plain.records]


@pytest.mark.asyncio
async def test_run_eval_uses_the_adapters_latency_for_an_agent_response():
    async def agent(prompt):
        return AgentResponse(output=_REFUSAL, latency_ms=42.0)

    async def agent_no_latency(prompt):
        return AgentResponse(output=_REFUSAL)

    assert {r.latency_ms for r in (await run_eval(agent, categories=["ASI09"])).records} == {42.0}
    assert all(r.latency_ms >= 0.0 for r in (await run_eval(agent_no_latency, categories=["ASI09"])).records)


def test_the_run_command_help_lists_the_retry_options():
    out = CliRunner().invoke(main, ["run", "--help"]).output
    assert "--retry-profile" in out and "--max-attempts" in out and "--retry-base-delay-s" in out and "MISSING" in out
