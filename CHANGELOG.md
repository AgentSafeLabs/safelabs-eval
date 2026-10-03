# Changelog

All notable changes to safelabs-eval are recorded here. The format follows Keep a Changelog (https://keepachangelog.com/en/1.1.0/), and the project uses semantic versioning. Earlier versions are described on GitHub Releases: https://github.com/AgentSafeLabs/safelabs-eval/releases

## 0.11.2 - 2026-10-03

Packaging and README fixes only; no code behaviour changes.

### Fixed

- The README logo now uses an absolute URL, so the image renders on the PyPI project page.
- The README no longer shows a hard-coded test-count badge (it was out of date); the CI status badge remains.
- The package summary (PyPI description, README tagline, `CITATION.cff` abstract and package docstring) now says "built around an OWASP-inspired agent-security taxonomy". The README's taxonomy note still explains the ASI01-ASI10 labels.
- The source distribution is smaller: it no longer includes the PNG files in `docs/assets/`, the `docs/planning/` folder or `.env.example`.

## 0.11.0 - 2026-10-03

### Behaviour changes

Read these before upgrading. Each one can change a result file or a number you compare against 0.10.1.

- **Unrecoverable infrastructure errors are now `missing_infrastructure`, not UNCERTAIN.** A trial that still fails with a rate limit, timeout, connection error or provider outage after the last retry is recorded with `status="missing_infrastructure"` and a null verdict, confidence and weight, and is excluded from every aggregate (pass rates, verdict counts, weights, per-category coverage). In 0.10.1 such a trial was scored UNCERTAIN (weight 0.25). Anything that reads `verdict` or `attack_success_weight` from AgentPort-Bench result files must now tolerate null and should filter on `status`.
- **Retries are on by default.** Infrastructure errors are retried (3 attempts, 1 s base delay, exponential backoff with jitter, `Retry-After` honoured). A fully rate-limited run takes longer. `--max-attempts 1` (or `max_attempts=1` in `run_eval`) turns retries off; `--retry-profile benchmark` gives 6 attempts.
- **Tool-call-only responses are no longer errors.** A response with empty text and one or more tool calls used to get the error "provider returned no output text". It now has no error, is flagged `tool_call_only`, and is scored as before (UNCERTAIN from the empty text).
- **`run_eval` reads `AgentResponse` returns.** A callable that returns an `AgentResponse` (for example `run_eval(adapter.execute)`) used to have the object's repr scored. It now scores the `output` text and uses `error`, `metadata` and `tool_calls`. Scores for that usage can differ from 0.10.1. Plain-string callables behave as before.
- **`safelabs run` ASI03 verdicts can differ from 0.10.1.** The CLI had its own category table with no ASI03 entry, so ASI03 prompts were scored with the prompt-injection detector. It now goes through `run_eval`, which scores ASI03 with the scope-violation detector. Other categories use the same detectors as before. The CLI also retries infrastructure errors, reports persistent ones as MISSING (not scored), and lists `--category all` results category by category.
- **Google ADK error text is more specific.** A run with no text whose event carries `error_message` now returns that message as `error`, instead of the generic "provider returned no output text". ADK `AgentResponse.metadata` now has an `adk_error_events` entry whenever an event had an `error_code` or `error_message`.
- **`HttpAdapter` output for OpenAI-style bodies.** A chat-completions body with no common top-level output key now uses the `message.content` of its first `choices` entry as `output`. Bodies with a common key, other shapes, or a `content` that is null or not a string behave as before.

### Added

- `AgentResponse` optional fields `tool_calls` (list of `ToolCall`), `usage`, `stop_reason`, `error_code`, `non_text_parts`, `framework`, `framework_version` and `provenance` (`verified`, `inferred` or `unknown` for each field). `None` means the adapter does not expose a field; an empty list means exposed and empty. `ToolCall.result` stays in memory and is not serialized. The HTTP, LangChain, Google ADK and OpenAI Agents adapters fill the fields; the CrewAI, AutoGen, LlamaIndex and Semantic Kernel adapters leave them `None`. (#50, #52)
- A shared adapter conformance suite (`tests/test_adapter_conformance.py`) and real-framework tests with deterministic fake models and no network: LangChain, Google ADK (including streaming and error events) and OpenAI Agents. (#50, #52)
- Adapter policy and an Adapter Definition of Done in `CONTRIBUTING.md`. (#52)
- Infrastructure-error handling in `safelabs/agents/errors.py` (`classify_error`: infrastructure, content policy, no output text, other; subclasses `rate_limit_or_quota`, `timeout`, `provider_unavailable`, `connection_error`) and shared retry settings in `safelabs/agents/retry.py` (`default` and `benchmark` profiles, with flag overrides).
- AgentPort-Bench harness: result rows gain optional `status`, `error_class`, `error_subclass`, `attempts`, `attempt_errors`, `tool_call_only` and `rerun_passes`; `agentport-bench run` gains `--retry-profile`, `--max-attempts`, `--retry-base-delay-s`, `--retry-max-delay-s`, `--retry-after-cap-s` and prints a summary of missing, retried and tool-call-only trials; `agentport-bench compare` and `validate` report and exclude missing rows. Older result files load unchanged.
- `agentport-bench run --resume --rerun-missing` re-executes only the `missing_infrastructure` rows of an existing results file (same trial key, prompt and seed), rewrites the file atomically with cumulative attempt history, and prints how many rows were re-attempted, recovered and are still missing by framework and model. The workflow is first run with `--retry-profile benchmark`, a cool-down, then `--rerun-missing`, repeated as needed. Two processes must not write the same results file at once (there is no file locking).
- The run manifest keeps an append-only `rerun_history` (the initial run, later runs, and one entry per `--rerun-missing` pass, each with a UTC timestamp, retry profile and values, and counts by framework and model). Older manifests load unchanged.
- `run_eval` takes `retry_profile`, `max_attempts`, `base_delay_s`, `max_delay_s`, `max_retry_after_s`, `sleep`, `jitter_fn`, and progress callbacks `on_start` and `on_record`; `EvalRecord` gains `status`, `error_class`, `error_subclass`, `attempts`, `attempt_errors` and `tool_call_only`; `EvalResult` gains `scored_records`, `missing`, `missing_by_category`, `retries` and `tool_call_only`, and its report lists missing counts and says missing trials are excluded. A missing record has no scoring result. Saved results from before load unchanged.
- `safelabs run` gains `--retry-profile`, `--max-attempts` and `--retry-base-delay-s`.
- `docs/planning/`: the adapter-hardening assessment and the `AgentResponse` design. (#51)

### Changed

- `HttpAdapter` error responses record `retry_after_s` in `metadata` when the server sends `Retry-After`; `AgentAdapter.execute()` records the exception class names, status code and `Retry-After` in `metadata` on its error paths.
- `safelabs run` is built on `run_eval`; its per-prompt output and JSON keys are unchanged for normal runs, and new keys appear only on missing, retried or tool-call-only prompts.
- `README.md` documents that adapters return `AgentResponse` and how `run_eval(adapter.execute)` reads it. (#59)

### Fixed

- `agentport-bench validate --verify-sample` no longer raises on a malformed verification bundle; it reports the problem as a validation issue. (#49, fixes #48)
- `run_eval` no longer scores the repr of an `AgentResponse` return. (#57)
- `safelabs run` no longer scores ASI03 prompts with the prompt-injection detector.
- The Google ADK adapter captures `error_message` (see Behaviour changes). (#53)
