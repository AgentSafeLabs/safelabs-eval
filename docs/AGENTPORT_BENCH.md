# AgentPort-Bench

**Status: v0.1.0, unreleased.** This document describes the `agentport_bench`
package in this repository: a schema, submission harness, validator, and CLI
for a public, multi-contributor, cross-framework agent-safety benchmark built
on safelabs-eval's OWASP ASI prompt library and scoring pipeline.

`agentport_bench` is implemented and tested (see `tests/test_agentport_bench_*.py`).
The separate public **results repository** that would actually host
contributor submissions and a leaderboard does not exist yet — that is future
work, not part of this package. This document describes what exists today.

## 1. What this is, and isn't

AgentPort-Bench packages the *design* piloted by `agentdojo-x`, a private
internal study (a sibling repository, not part of safelabs-eval) that ran a
7,020-trial cross-framework evaluation matrix against an earlier, 30-prompt
version of this library. `agentport_bench`:

- **Does not import, read, or depend on `agentdojo-x` at runtime.** Every
  taxonomy or constant that overlaps with it (`AttackFamily`, `VERDICT_WEIGHT`)
  is deliberately duplicated here as its own versioned, public definition, not
  imported — see `agentport_bench/schema.py`'s module docstring.
- **Never touches raw model output** in its public schema (`BenchTrialResult`
  has no field for it). A submission's integrity is instead verifiable via
  `payload_hash` (§4) without requiring publication of completions.
- **Reuses, never reimplements,** safelabs-eval's own prompt library
  (`safelabs.prompts.get_library()`), agent adapters (`safelabs.agents`), and
  scorer (`safelabs.scoring.Scorer`) — see `agentport_bench/harness.py`.

## 2. Package layout

```
agentport_bench/
├── __init__.py     # __version__ (harness_version stamped onto every row)
├── schema.py        # BenchTrialResult / BenchTrialResultWithRawOutput,
│                     # AttackFamily taxonomy, KNOWN_LIBRARY_VERSIONS,
│                     # is_library_version_comparable(), compute_payload_hash()
├── harness.py        # build_adapter(), run_trial(), run_matrix(), RunManifest
├── validate.py        # validate_submission() and the individual checks (§3)
└── cli.py              # `agentport-bench` console script: run / manifest /
                        # validate / compare
```

Tests: `tests/test_agentport_bench_schema.py`, `_validate.py`, `_harness.py`,
`_cli.py`.

## 3. Validation: reject vs. flag reference

`agentport_bench.validate.validate_submission()` runs every check below and
returns a `ValidationReport`. A submission is **accepted** iff it has zero
`reject`-severity issues; `flag`-severity issues are informational — the
submission is still accepted, marked for human review. This is the table
`validate.py`'s module docstring points to.

| Severity | Code | Raised by | Meaning |
|---|---|---|---|
| reject | `invalid_utf8` | `validate_submission` | file isn't valid UTF-8 |
| reject | `empty_file` | `validate_submission` | no non-blank JSONL rows |
| reject | `invalid_json` | `validate_schema` | a line isn't valid JSON |
| reject | `schema_validation_failed` | `validate_schema` | a line doesn't parse as `BenchTrialResult` |
| reject | `duplicate_key` | `validate_unique_keys` | `(model, framework, prompt_id, trial_seed)` repeated |
| reject | `unknown_prompt_id` | `validate_prompt_ids_against_library` | `prompt_id` not in the installed library |
| reject | `category_mismatch` | `validate_prompt_ids_against_library` | row's `category` disagrees with the library's for that `prompt_id` |
| reject | `payload_hash_mismatch` | `verify_payload_hash_sample` | recomputed hash from a `--verify-sample` bundle disagrees with the row's `payload_hash` (real evidence of tampering) |
| flag | `prompt_id_check_used_different_library_version` | `validate_prompt_ids_against_library` | prompt_id/category passed, but only against the *currently installed* library, not the row's claimed `library_version` |
| flag | `mixed_library_versions` | `check_library_version_comparability` | one file contains rows from more than one `library_version` |
| flag | `unregistered_library_version` | `check_library_version_comparability` | a `library_version` isn't in `KNOWN_LIBRARY_VERSIONS` |
| flag | `library_version_not_current` | `check_library_version_comparability` | a row's `library_version` isn't the currently installed one |
| flag | `payload_hash_unverified` | `verify_payload_hash_sample` | no `--verify-sample` bundle supplied at all |
| flag | `verify_sample_no_matching_row` | `verify_payload_hash_sample` | a `--verify-sample` entry doesn't match any row |
| flag | `verify_sample_empty` | `verify_payload_hash_sample` | `--verify-sample` bundle supplied but matched nothing |
| flag | `partial_coverage` | `validate_submission` | submission has no rows for one or more of the 10 ASI categories |

Two limitations stated plainly rather than silently overclaimed (see
`validate.py`'s own module docstring for the full reasoning):

- `validate_prompt_ids_against_library` can only check a row's `prompt_id`/
  `category` against the *currently installed* `safelabs-eval` library —
  there is no historical-snapshot store to check an older claimed
  `library_version` against. A pass that only worked via that fallback is
  flagged (`prompt_id_check_used_different_library_version`), not silently
  counted as fully confirmed.
- Requiring `--verify-sample` for every submission would recreate the exact
  privacy problem `payload_hash` exists to avoid (forcing raw-output
  publication), so it's optional; its absence is a flag, not a reject.

## 4. Schema: `BenchTrialResult`

One JSON object per line of a `.jsonl` submission. Full field list and
validators live in `agentport_bench/schema.py`; summary:

| Field | Notes |
|---|---|
| `model`, `provider` (optional), `framework` | free-text identity of what was run |
| `attack_family`, `category` | `category` must be one of the two ASI categories `attack_family` aggregates (enforced) |
| `prompt_id` | must match `^ASI\d{2}-\d{3}$` |
| `trial_seed` | replicate index, `>= 0` |
| `verdict`, `confidence`, `attack_success_weight` | `attack_success_weight` follows the fixed pass=0 / uncertain=0.25 / fail=0.5 / vulnerable=1.0 mapping (`VERDICT_WEIGHT`) |
| `latency_ms`, `error`, `indicators`, `usage` | `indicators` are short detector-tag strings, never raw text; `usage` is always `None` from this harness (out of scope for v0.1.0, see §7) |
| `payload_hash` | 64-char lowercase sha256 hex; see below |
| `timestamp`, `harness_version`, `library_version` | `harness_version`/`library_version` are always stamped live by the harness, never caller-supplied |

**`payload_hash`** binds a trial's full identity, not just its text:

```
sha256(prompt_id + model + framework + str(trial_seed) + "\n" + raw_output)
```

so a hash computed for one `(model, framework, trial_seed)` can't be replayed
against a different one. The construction lives in `schema.compute_payload_hash()`
— a single canonical implementation both the harness (writing it) and the
validator (recomputing it against an optional `--verify-sample` bundle) call,
so they can never drift apart.

**`BenchTrialResultWithRawOutput`** is a separate subclass adding a
`raw_output: str` field — only ever constructed when a run uses
`--include-raw-output` (default off). A file in this shape is not the public
submission format; treat it as sensitive and do not submit it as-is.

## 5. Submission harness

`agentport_bench.harness` runs the full (or a filtered) prompt-library ×
category × seed matrix against one adapter/model and writes one
`BenchTrialResult` per completed trial, appending as each finishes (so an
interrupted run loses nothing already written) with `--resume` skipping any
`(model, framework, prompt_id, trial_seed)` key already present in the output
file.

`build_adapter(name, **kwargs)` constructs a `safelabs.agents.AgentAdapter`:

- `"http"` → `HttpAdapter(**kwargs)`
- `"custom"` → imports `kwargs["module"]` (`"import.path:ClassName"`), verifies
  it's an `AgentAdapter` subclass, and constructs it with the remaining kwargs
- any of the 7 built-in framework names (`langchain`, `crewai`, `autogen`,
  `llamaindex`, `openai-agents`, `google-adk`, `semantic-kernel`) → that
  framework's real `safelabs.agents.*Adapter`, called with the caller's raw
  kwargs. Each of these seven has a genuinely different first constructor
  argument (`runnable=`, `crew=`, `agent=`/`recipient=`, `workflow=`, `agent=`,
  `agent=`, `agent=`) — this factory intentionally does not invent a fictional
  uniform interface across frameworks that don't share one.

Every built-in adapter module imports its underlying framework package lazily
(inside methods, not at module import time), so importing `agentport_bench`
itself never requires all seven optional framework extras to be installed —
only actually *using* a given adapter does.

`run_trial()` always scores whatever text comes back, including `""` on an
adapter error (`AgentAdapter.execute()` never raises), matching the
empty-text-scores-`UNCERTAIN` behaviour `agentdojo-x`'s own run documented,
rather than special-casing errors.

**Scope note (v0.1.0):** token `usage` capture is out of scope — `agentdojo-x`
needed a bespoke per-framework hook to get real numbers; a generic equivalent
for a public multi-framework harness is future work. `BenchTrialResult.usage`
is always `None` from this harness today.

## 6. CLI reference (`agentport-bench`)

Installed as a console script once this package is installed (see §8);
runnable in-tree today via `python -m agentport_bench.cli`.

### `agentport-bench run`

```
agentport-bench run --adapter {http,custom} --model MODEL [--provider P]
  [--target URL] [--module import.path:ClassName]
  [--adapter-kwarg KEY=VALUE ...] [--categories ASI01,ASI06,...]
  [--seeds N] --output PATH [--resume/--no-resume] [--dry-run]
  [--include-raw-output] [--max-concurrency N] [--timeout-s N]
```

`--adapter` only accepts `http` and `custom` — not all 7 framework names. A
CLI string flag can't carry a live Python object (a LangChain `Runnable`, a
CrewAI `Crew`, ...) as its value, so there is no way for
`--adapter langchain --some-flag ...` to construct a real chain from bare
command-line flags. To use a framework adapter from the CLI, write a small
`AgentAdapter` subclass that builds your native object internally and points
`--adapter custom --module "your.module:YourAdapterSubclass"` at it; the full
`harness.build_adapter()` table (all 7 frameworks) remains directly usable
from Python for anyone driving the harness programmatically instead.

`--dry-run` restricts the run to a small fixed subset (`ASI01`, 1 seed) for a
quick end-to-end smoke test before committing to a full run. `--include-raw-output`
writes `BenchTrialResultWithRawOutput` rows and prints an explicit
"treat as sensitive" warning; the run's `.manifest.json` sidecar (written
alongside the output as `<stem>.manifest.json`) also records whether it was set.

### `agentport-bench manifest --output PATH`

(Re-)writes the `.manifest.json` sidecar for an existing submission file by
reading the file's own rows, for when the original manifest was lost —
`started_at`/`finished_at` are not recoverable after the fact and are marked
`"unknown (manifest regenerated from existing rows, not a live run)"` rather
than fabricated.

### `agentport-bench validate SUBMISSION [--verify-sample PATH] [-o text|json]`

Runs §3's checks and prints (or emits as JSON) a `ValidationReport`. Exits
non-zero if the submission is rejected — meant to gate opening a PR against
the results repo, once that repo exists.

### `agentport-bench compare SUBMISSION...`

Groups already-validated submissions by `schema.is_library_version_comparable()`
and prints each group's pass rate, never averaging or ranking incomparable
groups together (see §7). Uses strict parsing (`load_submission`) — run
`validate` first; a malformed file here fails loudly rather than silently
dropping bad rows into a comparison.

## 7. `library_version` comparability

`library_version` is a real comparability boundary, not inert metadata.
`schema.KNOWN_LIBRARY_VERSIONS` maps known `safelabs-eval` content-library
versions to their total prompt count:

| Version | Prompt count | Note |
|---|---|---|
| `1.0.0` | 30 | `agentdojo-x`'s original 7,020-trial run scored against this |
| `1.1.0` | 30 | metadata-only migration (difficulty_tier/provenance/atlas_technique_ids); same 30 prompts |
| `1.6.0` | 131 | Stage 1-4 expansion to 13 prompts/category; the floor for new AgentPort-Bench submissions |

`is_library_version_comparable(a, b)` is `True` only when both versions are
registered **and** have the same prompt count. It is deliberately **not**
reflexive for an unregistered version: two submissions claiming the exact
same but unregistered `library_version` string are still treated as
not-confirmed-comparable to each other (`compare` puts them in separate
groups) — an unregistered version string carries no confirmed guarantee that
two claims of it mean the same prompt set. Same total count is a necessary,
not sufficient, check — it doesn't by itself prove identical per-category
composition.

`KNOWN_LIBRARY_VERSIONS` is kept honest by
`test_current_library_version_is_registered`, which asserts the live
`safelabs.prompts.get_library().version` is a key in this dict — so a future
prompt-library version bump that forgets to register itself here fails the
test suite loudly instead of silently producing an "unregistered version"
flag on every new submission.

## 8. Installing

`agentport_bench` ships from this same repository/package (not a separate
distribution) and needs no extra dependencies beyond `safelabs-eval`'s base
install (`pydantic`, `click`) — the optional framework packages
(`langchain-core`, `crewai`, `ag2`, ...) are only imported lazily, inside the
relevant adapter's own methods, when that specific adapter is actually used.

```bash
pip install -e ".[dev]"
```

registers the `agentport-bench` console script (`agentport_bench.cli:main`)
alongside the existing `safelabs` one, and includes `agentport_bench` in the
built wheel (`[tool.hatch.build.targets.wheel] packages`). To use a specific
framework adapter via `--adapter custom --module ...`, install that
framework's own extra too, e.g. `pip install -e ".[dev,langchain]"`.

## 9. Not yet built

- The separate public results repository (leaderboard, PR-based submission
  intake gated by `agentport-bench validate`) — mentioned throughout this
  document as the eventual home for submissions, but does not exist yet.
- Token `usage` capture (§5).
- Historical prompt-library snapshots, which would let `validate` check a
  row's `prompt_id`/`category` against its *claimed* `library_version`
  instead of only the currently installed one (§3).
