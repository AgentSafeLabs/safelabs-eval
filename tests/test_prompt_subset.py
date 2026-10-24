"""--prompt-id and --limit: run a single prompt or a subset (issue #72)."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

import safelabs.agents.http_adapter as http_adapter_module
from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse
from safelabs.cli import main
from safelabs.prompts.loader import get_library
from safelabs.prompts.schemas import PromptCategory
from safelabs.runner import PromptSelectionError, run_eval

URL = "http://localhost:9/chat"


class _Calls(list):
    pass


@pytest.fixture
def calls(monkeypatch):
    seen = _Calls()

    class _Adapter(AgentAdapter):
        def __init__(self, base_url, headers=None, timeout=30.0, **kw):
            super().__init__(timeout=timeout)

        @property
        def adapter_type(self):
            return "http"

        async def _execute(self, prompt):
            seen.append(prompt)
            return AgentResponse(output="I cannot help with that.", latency_ms=1.0)

    monkeypatch.setattr(http_adapter_module, "HttpAdapter", _Adapter)
    return seen


def _run(*args):
    return CliRunner().invoke(main, ["run", "-t", URL, "--output", "json", *args])


def _ids(res):
    assert res.exit_code == 0, res.output
    return [r["id"] for r in json.loads(res.output)["results"]]


def _lib_ids(category=None):
    lib = get_library()
    entries = lib.by_category(PromptCategory(category)) if category else lib.entries
    return [e.id for e in entries]


def test_single_prompt_id(calls):
    assert _ids(_run("--prompt-id", "ASI01-018")) == ["ASI01-018"]
    assert len(calls) == 1


def test_ids_are_case_insensitive_and_follow_library_order(calls):
    assert _ids(_run("--prompt-id", "asi01-018", "--prompt-id", "ASI01-002")) == ["ASI01-002", "ASI01-018"]


def test_duplicate_id_counts_once(calls):
    assert _ids(_run("--prompt-id", "ASI01-018", "--prompt-id", "asi01-018")) == ["ASI01-018"]
    assert len(calls) == 1


def test_default_category_allows_ids_from_any_category(calls):
    other = _lib_ids("ASI03")[0]
    assert _ids(_run("--prompt-id", other)) == [other]


def test_unknown_id_fails_before_any_prompt_is_sent(calls):
    res = _run("--prompt-id", "ASI01-002", "--prompt-id", "ASI99-999")
    assert res.exit_code != 0
    assert "ASI99-999" in res.output
    assert "ASI01-002" not in res.output.replace("Unknown prompt ID(s): ASI99-999", "")
    assert calls == []


def test_id_from_another_category_with_explicit_category_fails(calls):
    other = _lib_ids("ASI03")[0]
    res = _run("--category", "ASI01", "--prompt-id", other)
    assert res.exit_code != 0
    assert other in res.output and "not in the selected categories" in res.output
    assert calls == []


def test_explicit_category_with_matching_ids(calls):
    assert _ids(_run("--category", "ASI01", "--prompt-id", "ASI01-018")) == ["ASI01-018"]


def test_limit_takes_first_n_of_category(calls):
    assert _ids(_run("--limit", "3", "--category", "ASI01")) == _lib_ids("ASI01")[:3]
    assert len(calls) == 3


def test_limit_applies_after_prompt_ids(calls):
    ids = ["ASI01-018", "ASI01-002", "ASI01-007"]
    args = [a for i in ids for a in ("--prompt-id", i)]
    assert _ids(_run(*args, "--limit", "2")) == ["ASI01-002", "ASI01-007"]


@pytest.mark.parametrize("value", ["0", "-1"])
def test_limit_below_one_is_rejected(calls, value):
    res = _run("--limit", value)
    assert res.exit_code == 2
    assert calls == []


def test_help_lists_new_options():
    out = CliRunner().invoke(main, ["run", "--help"]).output
    assert "--prompt-id" in out and "--limit" in out


async def test_run_eval_defaults_are_unchanged():
    plain = await run_eval(lambda p: "no", categories=["ASI01"])
    explicit = await run_eval(lambda p: "no", categories=["ASI01"], prompt_ids=None, limit=None)
    assert [r.prompt_id for r in plain.records] == [r.prompt_id for r in explicit.records] == _lib_ids("ASI01")
    everything = await run_eval(lambda p: "no", prompt_ids=None, limit=None)
    assert [r.prompt_id for r in everything.records] == _lib_ids()


async def test_run_eval_prompt_ids_and_limit():
    res = await run_eval(lambda p: "no", prompt_ids=["asi01-018", "ASI01-002", "ASI01-007"], limit=2)
    assert [r.prompt_id for r in res.records] == ["ASI01-002", "ASI01-007"]


async def test_run_eval_rejects_bad_selection_before_calling_the_agent():
    seen = []

    def agent(p):
        seen.append(p)
        return "no"

    with pytest.raises(PromptSelectionError, match="NOPE-1"):
        await run_eval(agent, prompt_ids=["ASI01-002", "nope-1"])
    with pytest.raises(PromptSelectionError, match="not in the selected categories"):
        await run_eval(agent, categories=["ASI01"], prompt_ids=[_lib_ids("ASI03")[0]])
    with pytest.raises(PromptSelectionError):
        await run_eval(agent, limit=0)
    assert seen == []
