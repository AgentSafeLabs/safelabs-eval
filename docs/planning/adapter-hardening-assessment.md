# Adapter hardening assessment (roadmap Phase 1A): findings before any code

Date: 2026-10-02. Scope: assessment only; nothing implemented. Tags: **VERIFIED** (read in the repo or checked against the installed packages with `.venv/bin/python`, no network, no API calls), **INFERRED** (reasoned from verified facts), **UNKNOWN** (could not be checked).
Location note: this file was asked to live at `docs/planning/adapter-hardening-assessment.md`. `docs/planning/` does not exist in the repo (VERIFIED: `docs/` holds only `AGENTPORT_BENCH.md`, `DATASET_CARD.md`, `assets/`, `paper-a-results-data.md`), and the instruction was to create no other files or directories, so I wrote it here instead. Move it when you create the directory. Nothing in the repo was changed (VERIFIED: no file or directory in the repo outside `.venv` and `.git` was modified in the last three hours).

## Summary (15 lines)

1. A normalized result type already exists: `AgentResponse` in `safelabs/agents/schemas.py` (fields `output`, `latency_ms`, `error`, `raw`, `metadata`), and `AgentAdapter.execute()` already normalizes timeouts, exceptions and empty output into `error` (VERIFIED).
2. It carries no tool calls, no token usage, no stop/finish reason and no framework version. No adapter distinguishes `None` (not exposed) from `[]` (exposed, zero calls) because no tool-call field exists (VERIFIED).
3. Seven of the eight adapters return `AgentResponse(output=..., latency_ms=...)` only; only `HttpAdapter` fills `raw` and `metadata`, and only with the HTTP status code (VERIFIED).
4. The expected finding is half confirmed: "no normalized result type" is **refuted**; "no normalized *execution* result, so populating tool calls, usage and stop reason touches every adapter" is **confirmed**. The change can be additive (new optional fields default to `None`), so Phase 1A.1 is "extend and harden", not "build new".
5. All seven framework adapters are tested only with hand-written `_Fake*` classes; none imports its framework in tests (VERIFIED). `HttpAdapter` has no test file of its own (VERIFIED: only `tests/test_agentport_bench_harness.py` mentions it).
6. The installed google-adk is 2.9.0, so the ADK audit could be done: every call the adapter makes matches the installed API (VERIFIED), and `Event.get_function_calls()`, `usage_metadata`, `finish_reason`, `error_code`, `error_message` exist for the new fields.
7. The installed environment breaks the OpenAI Agents adapter: `openai==2.45.0` with `openai-agents==0.18.0` violates the repo's own pin (`openai<2.45`), and `RunContextWrapper(context=None)` raises a `ValidationError` here (VERIFIED).
8. Installed package metadata says Semantic Kernel is succeeded by Microsoft Agent Framework (VERIFIED, semantic-kernel 1.44.1) and that AG2's current framework is heading to maintenance mode (VERIFIED, ag2 0.14.0). The roadmap should not deepen investment in those two adapters before an audit.
9. Recommended real-framework integration tests: **LangChain** and **Google ADK**, using the installed fake-model seams (`GenericFakeChatModel`; a `BaseLlm` subclass), no network, no paid API.
10. Estimated effort for Phase 1A as scoped below: about 19 working days (INFERRED estimate, section 5).
11. CONTRIBUTING currently asks adapter contributions to test "against a real, live model response"; that conflicts with a no-paid-API policy and needs a decision.

## Decisions you must make

1. Accept "extend `AgentResponse` additively" (recommended) or introduce a separate `AgentExecutionResult` class that `AgentResponse` subclasses or wraps.
2. Cap on first-party adapters (the policy text below uses "the current eight" and needs your number).
3. Whether to rewrite the CONTRIBUTING sentence that requires a live model response for adapter tests.
4. Whether to fix the environment drift (`openai` 2.45.0 against the repo pin) before the OpenAI Agents adapter is touched; I changed nothing.
5. Which two adapters get real-framework tests (recommendation: LangChain and Google ADK).
6. Whether `usage` (currently hard-coded `None` in `agentport_bench/harness.py` line 247) should start being populated from adapters in this phase.
7. How CrewAI and AutoGen (audit needed) and Semantic Kernel (successor named in metadata) are treated in the roadmap.

## What I could not verify

- Commit-message evidence for the duck-typed tests: not obtainable without git (no git operations allowed). I give test docstrings, README and CONTRIBUTING quotes instead (section 1). UNKNOWN.
- Real end-to-end behaviour of every adapter against a live framework run: not executed (no network, no paid APIs). The ADK, CrewAI, LlamaIndex, LangChain, AutoGen, Semantic Kernel and OpenAI Agents API shapes were checked by importing the installed packages and reading signatures and model fields only.
- Upstream status of Microsoft Agent Framework beyond what is in the installed semantic-kernel metadata: UNKNOWN. Upstream status of AG2 beyond its installed metadata: UNKNOWN.
- Whether tool calls can be extracted from `CrewOutput` (fields are `raw`, `tasks_output`, `token_usage`) or from AutoGen's `chat_history` dicts: UNKNOWN.
- The Development Roadmap file: not found in the workspace, so Phase 1A.1 wording is taken from your message only.
- Test run results: tests were not run (running them would create cache files in the repo).

## 1. Inventory of adapters

Source files in `safelabs/agents/` (VERIFIED, byte and line counts from the files):

| Adapter | File | Bytes / lines | Test file | Test bytes / lines / tests | Test style |
|---|---|---|---|---|---|
| HTTP | `http_adapter.py` | 3,608 / 118 | none (only referenced in `tests/test_agentport_bench_harness.py`, 2 mentions) | n/a | no direct tests |
| LangChain | `langchain_adapter.py` | 4,261 / 107 | `tests/test_langchain_adapter.py` | 12,531 / 302 / 15 | duck-typed fakes (`_FakeMessage`, `_FakeRunnable`) |
| CrewAI | `crewai_adapter.py` | 3,758 / 116 | `tests/test_crewai_adapter.py` | 5,367 / 137 / 8 | duck-typed fakes (`_FakeCrewOutput`, `_FakeCrew`) |
| AutoGen / ag2 | `autogen_adapter.py` | 4,230 / 128 | `tests/test_autogen_adapter.py` | 6,922 / 183 / 10 | duck-typed fakes |
| LlamaIndex | `llamaindex_adapter.py` | 4,443 / 122 | `tests/test_llamaindex_adapter.py` | 6,815 / 170 / 10 | duck-typed fakes |
| OpenAI Agents SDK | `openai_agents_adapter.py` | 6,008 / 150 | `tests/test_openai_agents_adapter.py` | 7,260 / 191 / 10 | duck-typed fakes (`_FakeRunner` injected through the `runner=` argument) |
| Google ADK | `google_adk_adapter.py` | 7,521 / 195 | `tests/test_google_adk_adapter.py` | 8,337 / 242 / 13 | duck-typed fakes (`_FakeRunner`, `_FakeSessionService`, injected `content_factory`) |
| Semantic Kernel | `semantic_kernel_adapter.py` | 4,739 / 128 | `tests/test_semantic_kernel_adapter.py` | 5,310 / 145 / 8 | duck-typed fakes |

Shared: `base.py` (3,575 B, 96 lines), `schemas.py` (957 B, 30 lines), `__init__.py` (901 B, 25 lines); base-class tests in `tests/test_agents.py` (1,699 B, 46 lines, 6 tests) using three small fake adapters. There is no "other" adapter in `safelabs/agents/`; `agentport_bench/harness.py` builds adapters through `build_adapter()` (http, custom, or a built-in name) and `agentport-bench --adapter` accepts only `http` and `custom` (VERIFIED: `agentport_bench/cli.py` lines 87-92).

Manual real-framework scripts (not tests, VERIFIED by listing and their docstrings): `examples/{autogen,crewai,google_adk,llamaindex,openai_agents,semantic_kernel}_adapter_verify.py` and `*_example.py` for the same six plus `quickstart.py`. There is no LangChain `*_adapter_verify.py` and no `langchain_example.py` in `examples/` (VERIFIED by listing).

Evidence that the tests are duck-typed by design (commit messages: UNKNOWN, no git):
- Every adapter test module opens with "Tests for <Adapter> using duck-typed fake objects" (VERIFIED: `tests/test_{crewai,autogen,llamaindex,openai_agents,semantic_kernel,google_adk}_adapter.py` line 4); `tests/test_langchain_adapter.py` line 6: "Section A -- duck-typed fake objects" and line 46: "Section A -- duck-typed fakes (no langchain installation required)".
- README line 411: "the current adapter tests use duck-typed fakes and do not install real framework packages. Tests that run against actual LangChain, CrewAI, AutoGen, LlamaIndex, OpenAI Agents SDK, Google ADK, and Semantic Kernel objects (in an optional CI job) are a real gap."
- `openai_agents_adapter.py` lines 39-41 and `google_adk_adapter.py` lines 48-52: injected fakes exist as a test seam (`runner=`, `content_factory=`).

## 2. Existing interfaces

- `AgentAdapter` (`base.py`): `adapter_type` property, abstract `_execute(prompt) -> AgentResponse`, public `execute()` which applies `asyncio.wait_for(timeout)`, turns exceptions into `AgentResponse(output="", error=str(exc))`, and sets `error = "provider returned no output text"` when `_execute` returns an empty output with no error (VERIFIED, `base.py` lines 56-96; this was added for error-reporting parity across adapters).
- `AgentResponse` (`schemas.py`, pydantic): `output: str`, `latency_ms: float = 0.0`, `error: str | None`, `raw: dict | None`, `metadata: dict | None`, property `succeeded` (VERIFIED). So a partial normalized interface exists.
- What is not in it (VERIFIED by reading the file): tool calls, token usage, stop/finish reason, thinking or other non-text parts, framework name and version, any marker of "not exposed".
- `None` versus `[]` for tool calls: no adapter distinguishes them, because no tool-call field exists (VERIFIED). The only adapter that fills `raw` and `metadata` is `HttpAdapter` (`raw` = decoded JSON dict, `metadata` = `{"status_code": ...}`); the other seven return only `output` and `latency_ms` (VERIFIED).
- Consumers: `agentport_bench/harness.py::run_trial` uses `response.output`, `response.latency_ms` and `response.error`, and writes `usage=None` (line 247, VERIFIED). `BenchTrialResult` (`agentport_bench/schema.py`) has a `usage` dict field but no tool-call or stop-reason field (VERIFIED).
- Reference for what a richer record looked like in the research code: the private research harness's `model_clients.Completion` has `text`, `usage`, `stop_reason` and `non_text_parts` (VERIFIED by reading; read-only). That is the shape the SafeAgent-300 data gaps (`stop_reason` missing in some files) came from; the safelabs-eval adapters do not carry it.

## 3. Google ADK audit and classification

Installed: `google-adk==2.9.0`, `google-genai==2.23.0` (VERIFIED). The adapter docstring says "verified against v2.8.0, 2026-09" (one minor version older). Checks against the installed package (all VERIFIED by import and `inspect`, no network):

| Adapter call | Installed API | Result |
|---|---|---|
| `InMemoryRunner(agent=..., app_name=...)` | signature `(self, agent=None, *, node=None, app_name=None, plugins=None, app=None, plugin_close_timeout=5.0)` | match |
| `runner.session_service` and `runner.app_name` | present on an instance (`InMemorySessionService`; constructing a runner with an `Agent` made no network call) | match |
| `await session_service.create_session(app_name=..., user_id=...)` | coroutine, keyword-only `app_name`, `user_id`, optional `state`, `session_id` | match |
| `runner.run_async(user_id=..., session_id=..., new_message=...)` | async generator, keyword-only, extra optional parameters (`invocation_id`, `state_delta`, `run_config`, `yield_user_message`, ...) | match |
| `event.is_final_response()` | `Event.is_final_response(self) -> bool` | match |
| `types.Content(role="user", parts=[types.Part(text=...)])` | constructs | match |

Available for the new result fields (VERIFIED as present on the installed `Event`): `get_function_calls()`, `get_function_responses()`, `usage_metadata`, `finish_reason`, `error_code`, `error_message`, `custom_metadata`, `partial`. The adapter currently ignores `error_code` and `error_message`: an event that carries only an error produces `output=""` and surfaces as the generic "provider returned no output text" (VERIFIED by reading `_execute`; whether ADK emits such events for a given failure: UNKNOWN).

Classification (one primary label each; none is BUILD NEW):

| Adapter | Label | Reason |
|---|---|---|
| `base.py`, `schemas.py` | HARDEN | extend `AgentResponse` additively; keep `execute()` normalization |
| HTTP | HARDEN | no tests of its own; `raw`/`metadata` already used; tool calls would be `None` (not exposed) |
| LangChain | HARDEN | `langchain-core==1.4.0`: `AIMessage` has `tool_calls`, `usage_metadata`, `response_metadata` (VERIFIED); most bespoke parsing (`_text_from_content`) |
| Google ADK | HARDEN | API matches 2.9.0; add function calls, usage, finish reason, error fields |
| LlamaIndex | HARDEN | `llama-index-core==0.14.23` equals the version the docstring was verified against; `AgentOutput` has `tool_calls` (VERIFIED field) |
| OpenAI Agents SDK | AUDIT | environment drift (below); `RunResult` has `new_items`, `raw_responses`, `context_wrapper` (VERIFIED); tool-call extraction from `new_items`: INFERRED |
| CrewAI | AUDIT | docstring covers `crewai` 0.2x-0.6x; installed `crewai==1.15.2`; `kickoff()` now returns `CrewOutput | CrewStreamingOutput`; `CrewOutput` fields: `raw`, `json_dict`, `pydantic`, `tasks_output`, `token_usage` (VERIFIED); tool calls: UNKNOWN |
| AutoGen / ag2 | AUDIT | `ag2==0.14.0`; `ChatResult` fields `chat_id`, `chat_history`, `summary`, `cost`, `human_input`; `a_initiate_chat(..., max_turns=...)` present (VERIFIED); package metadata says the current framework moves to maintenance mode (VERIFIED) |
| Semantic Kernel | REUSE (keep as is, re-audit before extending) | `semantic-kernel==1.44.1` equals the docstring's verified version; `get_response(messages=...)` present; `ChatMessageContent.items` and `FunctionCallContent` exist (VERIFIED); metadata names Microsoft Agent Framework as successor |

OpenAI Agents environment finding (VERIFIED): the repo pins `openai<2.45` for the `openai-agents` extra (`pyproject.toml`), but `.venv` has `openai==2.45.0` with `openai-agents==0.18.0`. In that pair, `InputTokensDetails` requires `cache_write_tokens`, `InputTokensDetails(cached_tokens=0)` raises `ValidationError`, and `RunContextWrapper(context=None)` raises the same error, as the adapter docstring describes. So any real `Runner.run()` in this environment is expected to fail before model dispatch (that last step is INFERRED from the docstring; I did not call `Runner.run`). I changed nothing.

## 4. Microsoft Agent Framework: what the AutoGen and Semantic Kernel adapters depend on

Inside this repo (VERIFIED by reading):
- `AutoGenAdapter`: `import autogen` namespace of the **ag2** distribution (`pyproject.toml` extra `autogen = ["ag2>=0.2"]`); calls `recipient.a_initiate_chat(agent, message=prompt, max_turns=1)`; reads `ChatResult.chat_history[-1]["content"]`, then `.summary`. The docstring states it does not target the Microsoft `autogen_agentchat` namespace.
- `SemanticKernelAdapter`: extra `semantic-kernel>=1.26`; calls `agent.get_response(messages=prompt)`; reads `result.message.content`, then `result.content`, then `str(result)`. No framework class is imported (pure duck typing).
- The private research harness builds both agents for the benchmark (`build_autogen_agent`, `build_semantic_kernel_agent`); it also depends on these two APIs (read-only observation).

Upstream status: UNKNOWN beyond installed metadata. The installed semantic-kernel 1.44.1 README text says "Semantic Kernel is now Microsoft Agent Framework ... Microsoft Agent Framework (MAF) is the enterprise-ready successor to Semantic Kernel ... now available at version 1.0" (VERIFIED, `importlib.metadata`). The installed ag2 0.14.0 README text says "The current framework will be tidied up through deprecations over the next few minor versions and moved to maintenance mode" (VERIFIED). Neither `agent-framework` nor `microsoft-agent-framework` is installed (VERIFIED), so no MAF API could be checked.

## 5. Recommendations

### 5.1 Scope of 1A.1
Extend, do not rebuild: add optional fields to the existing result type (or a subclass), keep `output`/`latency_ms`/`error` unchanged for current callers, populate the new fields adapter by adapter, and add a conformance test that every adapter's result passes. Out of scope for 1A.1: new adapters, multi-turn, real-model runs, changes to scoring. Prerequisite: resolve the `openai`/`openai-agents` environment mismatch (decision 4).

### 5.2 Design outline: `AgentExecutionResult` (field tags)
| Field | Type and meaning | Tag |
|---|---|---|
| `output` | final visible text | VERIFIED (exists as `AgentResponse.output`) |
| `error` | failure message; empty-output rule kept | VERIFIED (exists) |
| `error_code` | provider or framework error code | INFERRED (ADK `Event.error_code` is VERIFIED; other frameworks UNKNOWN) |
| `latency_ms` | round trip | VERIFIED (exists) |
| `tool_calls` | `list[ToolCall] | None`; `None` = not exposed by this adapter or framework path, `[]` = exposed and zero calls; `ToolCall(name, arguments, id)` | INFERRED (design); availability: LangChain `AIMessage.tool_calls`, ADK `get_function_calls()`, LlamaIndex `AgentOutput.tool_calls` VERIFIED as present; OpenAI Agents `new_items`, SK `FunctionCallContent` INFERRED; CrewAI, AutoGen UNKNOWN; HTTP `None` |
| `usage` | `{prompt_tokens, completion_tokens, reasoning_tokens} | None` | INFERRED; sources: LangChain `usage_metadata`, ADK `usage_metadata`, CrewAI `token_usage` VERIFIED as fields; others UNKNOWN |
| `stop_reason` | normalized finish reason | INFERRED; ADK `finish_reason` VERIFIED; LangChain `response_metadata` field VERIFIED but its keys vary by provider (UNKNOWN) |
| `non_text_parts` | list of part kinds seen (for example `function_call`, `thought`) | INFERRED (mirrors the private research harness `Completion.non_text_parts`) |
| `raw`, `metadata` | as today | VERIFIED (exist) |
| `framework`, `framework_version` | adapter name and `importlib.metadata.version(...)` of the framework | INFERRED (design) |
| `schema_version` | integer for the result shape | INFERRED (design) |

Rule: a field an adapter cannot fill is `None`, never an empty default, so downstream code can tell "not exposed" from "none happened".

### 5.3 Migration touch list (VERIFIED by `grep AgentResponse` counts)
- `safelabs/agents/schemas.py` (the type), `base.py` (7 references: timeout and exception paths build the response), `__init__.py` (export).
- Eight adapters' `_execute` return statements (`http`, `langchain`, `crewai`, `autogen`, `llamaindex`, `openai_agents`, `google_adk`, `semantic_kernel`; the Semantic Kernel file has 10 references because its docstring discusses the type).
- Consumers: `agentport_bench/harness.py` (`usage=None`, line 247), `agentport_bench/schema.py` (`BenchTrialResult`), `agentport_bench/validate.py` and the CLI if new fields enter submissions.
- Tests: the seven adapter test files (fake results and assertions), `tests/test_agents.py`, `tests/test_agentport_bench_harness.py`, `tests/test_agentport_bench_cli.py`.
- Examples: six `examples/*_adapter_verify.py` print the response.
- Docs: README section on contributing and adapters, `docs/AGENTPORT_BENCH.md` if the submission schema changes.
With additive optional fields, no consumer breaks; the work is populating and testing.

### 5.4 Which two adapters get real-framework integration tests
1. **LangChain.** `langchain-core==1.4.0` ships `GenericFakeChatModel` and `FakeListChatModel` (VERIFIED present); a scripted `AIMessage` with `tool_calls` goes through a real `Runnable.ainvoke` (VERIFIED: `GenericFakeChatModel(messages=iter([AIMessage(..., tool_calls=[...])])).ainvoke(...)` returns an `AIMessage` whose `tool_calls` is populated). The adapter has the most bespoke content parsing and the largest test file (15 tests), and LangChain is the most widely used framework. Network: none.
2. **Google ADK.** `google-adk==2.9.0` is installed and audited above; a `BaseLlm` subclass implementing the single abstract method `generate_content_async(llm_request, stream=False)` and yielding scripted `LlmResponse` objects can drive a real `InMemoryRunner`, real sessions and real `Event` objects, including function-call events (`Agent.model` accepts a `BaseLlm`, VERIFIED). This exercises the most structurally complex adapter (runner, session, `Content`, events). Network: none.
Not chosen first: OpenAI Agents (blocked by the environment mismatch above; `agents.models.interface.Model` exists for a later fake), CrewAI (needs LLM wiring; audit first), AutoGen and Semantic Kernel (successor and maintenance-mode signals in metadata). Other fake-model seams present in the installed packages for later (VERIFIED present): `llama_index.core.llms.MockLLM`, `agents.models.interface.Model`, `semantic_kernel...ChatCompletionClientBase`, `crewai.llms.base_llm.BaseLLM`. Whether each can drive its framework's tool loop was not tested.

### 5.5 Adapter Definition of Done
An adapter is done when: (1) it returns the normalized result with every field either filled or `None`; (2) `None` versus `[]` for tool calls follows the rule above and is tested; (3) it passes the shared conformance test; (4) it has a duck-typed unit test (fast, no framework) and, for the two chosen adapters, a real-framework test with a deterministic fake model and no network or paid API; (5) the framework version it was audited against is recorded in its docstring and in `evidence/`; (6) `pyproject.toml` bounds match the audited version range and CI resolves inside it; (7) timeouts, exceptions and empty output keep the `execute()` behavior; (8) its docstring lists the framework API calls it depends on; (9) an `examples/<name>_adapter_verify.py` run is recorded, with its output stored without raw model text.

### 5.6 Adapter-cap policy text for CONTRIBUTING (proposed)
> **Adapter policy.** safelabs-eval maintains first-party adapters for HTTP, LangChain, CrewAI, AutoGen (ag2), LlamaIndex, the OpenAI Agents SDK, Google ADK and Semantic Kernel, and does not add further first-party adapters until each meets the Adapter Definition of Done. A new first-party adapter is considered only if the framework has a stable public release, the contributor commits to maintaining the adapter for twelve months, and the adapter ships a real-framework integration test that uses a deterministic fake model (no network, no paid API) and passes the normalized-result conformance test. Frameworks outside this set are supported through the custom adapter: `agentport-bench --adapter custom --module package.module:ClassName`, where the class subclasses `AgentAdapter`.
The cap number and the twelve-month term are decisions for you. The custom-adapter route exists today (VERIFIED: `agentport_bench/cli.py` and `harness.py::build_adapter`). Note the existing CONTRIBUTING sentence that new adapters "should include integration tests that run against a real, live model response" conflicts with this text (decision 3).

### 5.7 Recommended `evidence/` structure (not created)
```
evidence/
  README.md                  what lives here and what never does
  adapters/<framework>/<YYYY-MM-DD>_<package>-<version>.md   audit notes: API calls checked, result, who ran it
  fixtures/                  scripted fake-model scenarios used by the integration tests (no secrets, no real model output)
  runs/                      summaries of manual verify-script runs: counts, versions, hashes only
```
Rule for `evidence/README.md`: security disclosures never go in the public repo. A suspected vulnerability in a third-party framework, a working exploit, credentials, or unredacted model output is recorded privately, reported to the vendor through its security channel, and only a post-resolution, non-sensitive summary may be added here. Evidence files hold no raw model outputs (the same rule as the SafeAgent-300 release) and no API keys.

### 5.8 Effort (working days; INFERRED estimates)
| Item | Days |
|---|---|
| Design review and the additive result type, `execute()` wiring, conformance test | 2.0 |
| Populate: LangChain 1.0, ADK 1.5, LlamaIndex 1.0, Semantic Kernel 1.0, HTTP 0.5 plus its missing unit tests 0.5 | 5.5 |
| Audit then populate: CrewAI 1.5, AutoGen 1.5, OpenAI Agents 1.5 plus environment fix 0.5 | 5.0 |
| Real-framework tests: LangChain 1.0, ADK 2.0 | 3.0 |
| Harness and schema consumption (`usage`, optional fields, validation, CLI) | 1.5 |
| Docs: CONTRIBUTING policy, Definition of Done, `evidence/` README, README sections | 1.0 |
| Review and fixes | 1.0 |
| **Total** | **about 19** |

### 5.9 Risks
- Version drift: `.venv` already violates the repo's own `openai<2.45` pin (VERIFIED); adapter docstrings cite versions older than installed (ADK 2.8.0 vs 2.9.0, CrewAI 0.30-0.6x vs 1.15.2).
- Tool-call extraction differs by framework and may be impossible for CrewAI and AutoGen from the objects the adapters receive today (UNKNOWN); the `None` rule keeps this honest.
- Successor and maintenance-mode signals for Semantic Kernel and AG2 (VERIFIED in installed metadata) may make deeper work on those two adapters short-lived.
- Fake models test the adapter's parsing and plumbing, not live-provider behavior; bugs that only appear under real model output (as the project's history shows for detectors) can still escape. Keep the manual `*_adapter_verify.py` runs.
- `AgentResponse` is used by `agentport_bench` submissions; a schema change must stay optional so existing submissions validate.
- No test run was possible here, so the baseline pass/fail state of the current 74 adapter tests (plus 6 base-class tests) is UNKNOWN.

## 6. Is the expected plan-changing finding confirmed or refuted?

**Partly confirmed, partly refuted.**
- "No normalized result type": **refuted.** `AgentResponse` (`schemas.py`) is a normalized result used by every adapter, and `AgentAdapter.execute()` normalizes errors, timeouts and empty output (VERIFIED).
- "No normalized result carrying tool calls, usage or stop reason; no `None` versus `[]` distinction": **confirmed.** The type has none of those fields, no adapter fills `raw` or `metadata` except HTTP's status code, and the harness hard-codes `usage=None` (VERIFIED).
- "Migration touches all adapters": **confirmed in scope, softened in risk.** Populating the new fields touches every adapter's `_execute` plus base, schema, harness, seven test files and six example scripts (VERIFIED counts above), but additive optional fields leave current callers working. Plan effect: Phase 1A.1 is extend-and-harden, with the audit-first adapters (OpenAI Agents, CrewAI, AutoGen) as the schedule risk.
