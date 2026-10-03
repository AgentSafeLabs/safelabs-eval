# Contributing to safelabs-eval

Thanks for considering a contribution. `safelabs-eval` is an open-source
red-teaming and evaluation framework for AI agents, and it's used as
source data for published research — so correctness and reproducibility
matter more here than in a typical library.

## Getting started

```bash
git clone https://github.com/AgentSafeLabs/safelabs-eval.git
cd safelabs-eval
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v
```

Requires Python 3.11, 3.12, or 3.13. Always work inside `.venv` — do not install
or run against your system Python.

**Before you start any work session**, confirm you're actually running
against your local changes and not a stale build:

```bash
python -m safelabs.verify_source
```

If this fails, run `pip uninstall -y safelabs-eval && pip install -e . --break-system-packages`
before continuing. This has silently produced wrong results before —
see `DATA_INTEGRITY_RULES.md`.

## Opening an issue before a PR

Please open an issue before submitting a PR, especially for anything
touching `safelabs/scoring/` (the detector logic). This lets us agree on
the approach before you invest time, and avoids duplicate work — several
areas of the detector logic have known, tracked gaps (see the
Known Limitations section of the README and open issues tagged
`detector-gap`).

## What we're looking for

- **New framework adapters** (LangChain, CrewAI, AutoGen, LlamaIndex,
  OpenAI Agents SDK, and beyond) — the most valuable and welcome kind of
  contribution. Each adapter should include fast duck-typed unit tests and
  a real-framework test: real framework objects driven by the framework's
  own fake or mock model (no network, no paid API), guarded with
  `pytest.importorskip` so it skips when the framework is not installed.
  Live-model tests are optional and opt-in: mark them `@pytest.mark.live_model`
  (registered in `pyproject.toml`); they are excluded from the default run
  and run only with `pytest -m live_model`. Bugs in this project's own
  detector logic have historically only surfaced under real model output,
  so also record a manual `examples/<name>_adapter_verify.py` run
  (counts and versions, never raw model text) when you can.
- **Detector vocabulary and pattern improvements** — refusal phrasing,
  edge cases, encoding issues. If you find a response that should have
  scored PASS/FAIL but scored UNCERTAIN (or vice versa), please include
  the exact response text and which detector/category it fell under.
- **Additional ASI-category detector coverage** — see `safelabs/scoring/detectors/`
  for the current category-to-detector mapping.
- **Bug reports with a reproducible example** — a specific prompt,
  model, and response that misclassifies is far more useful than a
  general description.

## Code and test requirements

- All new detector logic needs test coverage using **real model
  responses**, not only hand-written fixture strings. Hand-written
  fixtures tend to unconsciously match whatever pattern you just wrote,
  which is exactly how two calibration bugs went undetected in this
  codebase for a full release cycle.
- Run the full test suite (`pytest tests/ -v`) and confirm it passes
  before opening a PR.
- If your change affects verdict output for existing behavior (not just
  adding new coverage), include before/after counts on a fixed prompt
  set in your PR description, the same way release notes for detector
  fixes do in this repo's CHANGELOG.

## Adapter Definition of Done

An adapter is done when:

1. it returns the normalized `AgentResponse`, with every optional field
   (`tool_calls`, `usage`, `stop_reason`, `error_code`, `non_text_parts`,
   `framework_version`) either filled or `None`;
2. `None` versus `[]` follows the rule *None = the adapter or framework path
   does not expose it; [] = exposed, and there were zero*, and is tested;
3. each filled field carries a `provenance` entry: `verified` (read from a
   typed attribute or method of the framework's result object), `inferred`
   (computed or parsed by the adapter) or `unknown` (the field is `None`);
4. `ToolCall.result` stays in memory only: it is never written to a result
   file or serialized by default;
5. it will pass the shared `AgentResponse` conformance tests once they are added (next adapter wave), has a duck-typed
   unit test (fast, no framework) and a real-framework test with a
   deterministic fake model and no network or paid API;
6. the framework version it was audited against is recorded in its
   docstring, and the `pyproject.toml` bounds match the audited range;
7. timeouts, exceptions and empty output keep the `AgentAdapter.execute()`
   behavior;
8. its docstring lists the framework API calls it depends on;
9. a manual `examples/<name>_adapter_verify.py` run is recorded, with its
   output stored without raw model text.

## Data integrity requirements

This project's evaluation output is used as source data for published
research. Two rules apply to any code that computes or reports verdicts,
including your own local testing:

1. **Archive raw responses before scoring.** Any script that runs
   prompts against a model should save the raw response text to disk
   before any detector logic runs on it. Verdicts can always be
   recomputed from raw responses; raw responses cannot be recovered
   after the fact if only verdict counts were saved.
2. **Verify source location before trusting output.** Use
   `safelabs.verify_source.assert_running_from_source()` (or the
   `@verified_source` decorator) in any entrypoint whose output might be
   cited externally.

Full detail and the reasoning behind both rules: see
`DATA_INTEGRITY_RULES.md` in the repo root.

## Reporting a detector calibration issue specifically

If you believe a detector is systematically misclassifying a class of
response (not just one-off), please:

1. Include the exact response text (copy-paste, not a paraphrase —
   invisible characters like Unicode punctuation variants are often the
   actual bug).
2. State which detector/category you believe is involved.
3. If possible, note whether the issue is a missing pattern (vocabulary
   gap) or a pattern that's too strict (e.g. requires adjacent words with
   nothing in between).

This project has a public track record of treating detector calibration
bugs as a real, reportable class of issue rather than something to
quietly patch — see the blog post
["We Were Wrong About the UNCERTAIN Results"](https://agentsafelabs.com/blog/we-were-wrong-about-the-uncertain-results-heres-what-actually-happened/)
for the kind of detail that's useful in a report like this.

## Questions

Open an issue, or reach out via the contact info on
[agentsafelabs.com](https://agentsafelabs.com).