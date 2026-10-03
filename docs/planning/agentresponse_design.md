# 1A.1 design: extending `AgentResponse` (DESIGN ONLY; 🚦 awaiting author approval; nothing implemented)

Input: `_release-staging/adapter-hardening-assessment.md` (treated as claims; each is re-verified in `report.md`, Step 2). Tags: VERIFIED (file:line, or package:file:line in `.venv/lib/python3.12/site-packages`, written `SP/`), INFERRED, UNKNOWN. Every API name below was read from the installed package (never from memory). Package versions: langchain-core 1.4.0, crewai 1.15.2, ag2 0.14.0, llama-index-core 0.14.23, openai-agents 0.18.0, google-adk 2.9.0, google-genai 2.23.0, semantic-kernel 1.44.1, httpx 0.28.1 (VERIFIED, `importlib.metadata`).

## 1. New optional fields on `AgentResponse` (additive; no parallel type)

Added after `metadata` in `safelabs/agents/schemas.py` (current type: `schemas.py:12-30`, fields `output`, `latency_ms`, `error`, `raw`, `metadata`, property `succeeded`). All new fields default to `None`.

| Field | Type | Default | Meaning | Adapters that can fill it |
|---|---|---|---|---|
| `tool_calls` | `list[ToolCall] \| None` | `None` | the tool/function calls the framework reports for this execution, in order | LangChain (when the result is a message), Google ADK, LlamaIndex, OpenAI Agents, Semantic Kernel (partly), HTTP (OpenAI-compatible bodies only); CrewAI and AutoGen after audit |
| `usage` | `dict[str, int \| None] \| None` | `None` | token usage with the same keys as `BenchTrialResult.usage`: `prompt_tokens`, `completion_tokens`, `reasoning_tokens` | LangChain, Google ADK, OpenAI Agents, CrewAI, HTTP (OpenAI-compatible bodies); AutoGen after audit |
| `stop_reason` | `str \| None` | `None` | the framework's own finish/stop reason string, unmodified (for example `STOP`, `MAX_TOKENS`, `stop`, `tool_calls`) | Google ADK, Semantic Kernel, LangChain (provider-dependent), HTTP (OpenAI-compatible bodies) |
| `error_code` | `str \| None` | `None` | provider/framework error code when the framework reports one | Google ADK (`Event.error_code`) |
| `non_text_parts` | `list[str] \| None` | `None` | kinds of non-text parts seen in the final output (for example `function_call`, `thought`) | Google ADK, LangChain (content blocks) |
| `framework` | `str \| None` | `None` | adapter type name (`adapter_type`) | all (set by `AgentAdapter.execute()` from `adapter_type`) |
| `framework_version` | `str \| None` | `None` | `importlib.metadata.version(<distribution>)` of the framework package | all framework adapters; `None` for HTTP |
| `provenance` | `dict[str, Literal["verified", "inferred", "unknown"]] \| None` | `None` | per-field record of how each new field's value was obtained (section 3) | all |

Why `error_code` and `non_text_parts` (the evidence supports them): the installed ADK `Event` carries `error_code` and `error_message` (VERIFIED: `Event` inherits `finish_reason`, `error_code` and `usage_metadata` from `LlmResponse`, `SP/google/adk/models/llm_response.py:117,120,139`, and `get_function_calls` / `get_function_responses` / `is_final_response` exist on `Event`, field and method listing in the probe), and the adapter ignores both today (`google_adk_adapter.py:164-173` reads only the final event's text). `non_text_parts` mirrors the SafeAgent-300 history: stored `stop_reason` and `non_text_parts` fields were what separated a function-call attempt from an empty response (VERIFIED, `safeagent-300` datasheet section on spontaneous function calls; the assessment cites the private research harness's `Completion` as the precedent). Not proposed (no evidence an adapter can fill them): per-call latency, cost in currency, full message history.

**`ToolCall`** (new small model, `safelabs/agents/schemas.py`), built only from what adapters can expose:

| Field | Type | Meaning | Source examples (VERIFIED field names) |
|---|---|---|---|
| `name` | `str` | tool/function name | LangChain `ToolCall["name"]`; ADK `FunctionCall.name`; LlamaIndex `ToolCallResult.tool_name`; OpenAI Agents `ResponseFunctionToolCall.name`; SK `FunctionCallContent.name` |
| `arguments` | `dict[str, Any] \| None` | arguments as a dict when the source gives a dict or JSON text that parses | LangChain `args`; ADK `args`; LlamaIndex `tool_kwargs`; OpenAI Agents `arguments` (JSON string, parsed); SK `arguments` |
| `arguments_raw` | `str \| None` | the original string only when it was not parseable | OpenAI Agents / SK string arguments |
| `call_id` | `str \| None` | the framework's id for the call | LangChain `id`; ADK `FunctionCall.id`; LlamaIndex `tool_id`; OpenAI Agents `call_id`; SK `id` / `call_id` |
| `result` | `str \| None` | the tool's output when the framework exposes it | ADK `get_function_responses()`; LlamaIndex `tool_output`; OpenAI Agents `ToolCallOutputItem.output`; `None` otherwise |

## 2. `None` versus `[]` (one rule for every collection field)

- **`None` = the adapter or framework path does not expose it** (including: the call failed before the framework produced a result; the result object has the wrong shape; the framework cannot report it).
- **`[]` = exposed, and there were zero**: the adapter saw the complete sequence the framework reports (all ADK events; an `AIMessage` whose `tool_calls` attribute is present; a LlamaIndex `AgentOutput` with an empty `tool_calls` list) and none occurred.
- Applies to `tool_calls` and `non_text_parts`. For scalar fields (`usage`, `stop_reason`, `error_code`, `framework_version`) `None` is "not exposed"; a framework that exposes usage with zero tokens gives `{"prompt_tokens": 0, ...}`. Inside `usage`, a missing key is `None` (as `BenchTrialResult.usage` already allows, `agentport_bench/schema.py:214`).
- The paths that never reach a framework result (`AgentAdapter.execute()` timeout and exception branches, `base.py:74-96`) return all new fields `None` except `framework` (known from `adapter_type`), with provenance `unknown`.
- Enforced by a shared conformance test (section 8) and a model validator (section 3).

## 3. Provenance

A per-field map `provenance: dict[str, "verified" | "inferred" | "unknown"]`, keyed by the new field names (`tool_calls`, `usage`, `stop_reason`, `error_code`, `non_text_parts`, `framework_version`).
- `verified`: read directly from a typed attribute or method of the framework's result object, on a package version covered by a test (for example `Event.get_function_calls()`).
- `inferred`: computed or parsed by the adapter (summing usage across events, parsing JSON arguments, reading a provider-specific key of a metadata dict, `message.get("tool_calls")` from a chat-history dict).
- `unknown`: the value is `None` because the path does not expose it.
- Invariants (model validator, to be written): a field whose value is not `None` must have provenance `verified` or `inferred`; a field whose value is `None` has provenance `unknown` or no entry (absent is read as `unknown`). `[]` counts as not `None`.
- Example (Google ADK, one tool call): `provenance = {"tool_calls": "verified", "usage": "inferred", "stop_reason": "verified", "error_code": "unknown", "non_text_parts": "inferred", "framework_version": "verified"}` with `error_code=None`. Example (HTTP, OpenAI-compatible body without tool calls): `{"tool_calls": "inferred", "usage": "inferred", "stop_reason": "inferred", "framework_version": "unknown"}`; `tool_calls=[]` is allowed there only because the adapter saw a `choices[0].message` without `tool_calls`.

## 4. Backward compatibility

- Every existing caller keeps working unchanged (VERIFIED by search): callers read only `output`, `latency_ms`, `error`, and the `succeeded` property (`agentport_bench/harness.py:223,230,244,245,255`; `safelabs/cli.py:58-70`; `safelabs/__init__.py:20`; `examples/quickstart.py:26-27`), plus `raw`/`metadata` in `tests/test_agents.py:40` and the six `examples/*_adapter_verify.py` scripts. Adapters build `AgentResponse(output=..., latency_ms=...)` (VERIFIED: eight adapter files, `base.py:80,92`, seven test constructions). New fields are appended with defaults, so positional or keyword construction of the old fields is unchanged. `AgentResponse` is a pydantic `BaseModel` with default config (extra ignored); no change to the config.
- `AgentAdapter.execute()` (`base.py:66-96`) keeps its normalization: timeout, exception, and the `provider returned no output text` rule (`base.py:71-72`) are unchanged; it additionally fills `framework` (and, if the adapter set none, leaves the rest `None`).
- **Serialization:** no code persists `AgentResponse` (VERIFIED: `model_dump` / `model_dump_json` are applied only to `BenchTrialResult`, the manifest and the validation report: `agentport_bench/harness.py:162,321`, `cli.py:256`, `safelabs/cli.py:110`, which dumps prompt entries). `safelabs/cli.py:63-70` builds its own dict. So there are no old `AgentResponse` result files to load. If `AgentResponse` is ever serialized, `model_dump(exclude_none=True)` reproduces the old shape for old adapters, and a reader of old JSON gets the new fields as `None` by default.
- **Old result files (`.jsonl` submissions):** these are `BenchTrialResult` rows, a separate model with `extra="forbid"` (`agentport_bench/schema.py`). This design adds nothing to `BenchTrialResult`; old files validate exactly as before. Only `BenchTrialResult.usage` (which already exists, same keys) would start being filled from `AgentResponse.usage` if decision 5 is approved (`harness.py:247` currently hard-codes `usage=None`).

## 5. Per-adapter migration table

Sources are the installed APIs (VERIFIED by import and field listing unless marked). Effort S/M/L = about 0.5-1 / 1-1.5 / 2+ days, including unit tests.

| Adapter | Fields it will fill | Source API | Effort |
|---|---|---|---|
| HTTP (`http_adapter.py`) | `usage`, `stop_reason`, `tool_calls` only for OpenAI-compatible bodies; `framework`; version `None` | decoded JSON already in `raw` (`http_adapter.py:93-108`): `usage.prompt_tokens/completion_tokens`, `choices[0].finish_reason`, `choices[0].message.tool_calls`: all INFERRED (key names are the OpenAI-compatible convention, not verified against any installed package) | S (plus its missing unit tests) |
| LangChain | `tool_calls`, `usage`, `stop_reason`, `non_text_parts`, `framework_version` (when the runnable returns an `AIMessage`; `None` for str or dict results) | `AIMessage.tool_calls` (TypedDict `name`, `args`, `id`, `type`), `AIMessage.usage_metadata` (`input_tokens`, `output_tokens`, `total_tokens`, `output_token_details`), `AIMessage.response_metadata` (provider-specific keys, UNKNOWN set), content blocks; langchain-core 1.4.0 | M |
| Google ADK | `tool_calls` (with `result`), `usage`, `stop_reason`, `error_code`, `non_text_parts`, `framework_version` | per `Event`: `get_function_calls()`, `get_function_responses()`, `usage_metadata` (`prompt_token_count`, `candidates_token_count`, `thoughts_token_count`), `finish_reason`, `error_code`, `error_message`, `partial`; demonstrated end to end with a fake model (section 7) | M |
| LlamaIndex | `tool_calls` (with `result`), `framework_version`; `usage` and `stop_reason` stay `None` | `AgentOutput.tool_calls: list[ToolCallResult]` (`tool_name`, `tool_kwargs`, `tool_id`, `tool_output`); no usage or finish-reason field on `AgentOutput` (fields: `response`, `structured_response`, `current_agent_name`, `raw`, `tool_calls`); `raw` content provider-specific, UNKNOWN | M |
| OpenAI Agents | `tool_calls` (with `result`), `usage`, `framework_version`; `stop_reason` stays `None` | `RunResult.new_items` filtered to `ToolCallItem.raw_item` (`ResponseFunctionToolCall`: `name`, `arguments`, `call_id`) and `ToolCallOutputItem.output`; `RunResult.context_wrapper.usage` (`Usage`: `requests`, `input_tokens`, `output_tokens`, `output_tokens_details`); `ModelResponse` has `output`, `usage`, `response_id`, `request_id` and no finish reason. **Prerequisite: the environment fix** (`env_fix_plan.md`); with openai 2.45.0 `Runner.run` cannot start | M |
| Semantic Kernel | `stop_reason`; `tool_calls` only when the reply message carries `FunctionCallContent` items; `framework_version`; `usage` stays `None` | `AgentResponseItem.message` = `ChatMessageContent` (`finish_reason` enum `STOP`, `LENGTH`, `CONTENT_FILTER`, `TOOL_CALLS`, `FUNCTION_CALL`; `items`; `metadata`); `FunctionCallContent` (`name`, `arguments`, `id`, `call_id`). Auto-invoked calls happen inside the thread, so the final message likely has none (INFERRED). A usage key in `metadata` was not found (UNKNOWN). The package metadata names Microsoft Agent Framework as successor (VERIFIED earlier), so keep the work minimal | S to M |
| CrewAI | `usage`, `framework_version`; `tool_calls` only if the audit finds them in `TaskOutput.messages` | `CrewOutput.token_usage` (`UsageMetrics`: `prompt_tokens`, `completion_tokens`, `reasoning_tokens`, `cached_prompt_tokens`, `successful_requests`); `CrewOutput.tasks_output[*].messages: list[LLMMessage]` (content shape UNKNOWN) | M (audit first) |
| AutoGen (ag2) | `framework_version`; `usage` and `tool_calls` after audit | `ChatResult.cost: CostDict` with `usage_including_cached_inference` / `usage_excluding_cached_inference` (`dict[str, Any]`, inner keys UNKNOWN); `ChatResult.chat_history: list[dict]` (message dicts may carry `tool_calls`, INFERRED from `conversable_agent.py:106`) | M (audit first) |

## 6. Where tool calls will honestly be `None`
CrewAI and AutoGen if the audit cannot find a stable source; LlamaIndex and Semantic Kernel for `usage`; OpenAI Agents and LlamaIndex for `stop_reason`; HTTP when the endpoint is not OpenAI-compatible. `None` is the correct value there, with provenance `unknown`.

## 7. Test plan (no network, no paid API)

**Real-framework integration tests (two adapters, as the assessment recommended; both seams verified to run):**
- **LangChain:** `langchain_core.language_models.fake_chat_models.GenericFakeChatModel(messages=iter([AIMessage(...)]))` (also available: `FakeListChatModel`, `FakeMessagesListChatModel`, `FakeChatModel`, `ParrotFakeChatModel`; VERIFIED import list). VERIFIED: with an `AIMessage(content="ok", tool_calls=[...], usage_metadata={...}, response_metadata={"finish_reason": ...})`, `ainvoke` returns the same fields intact, and `LangChainAdapter(runnable=GenericFakeChatModel(...), input_key=None).execute("hi")` returns `output="ok"`. Note (VERIFIED): the adapter's default `input_key="input"` sends a dict, which a chat model rejects ("Invalid input type <class 'dict'>"), so these tests must pass `input_key=None` (a usage note for the docs, not a bug in 1A.1 scope). Scenarios: tool calls present; message with no tool calls (`[]`); `usage_metadata` absent (`usage=None`); `response_metadata` with and without `finish_reason`; list-of-blocks content.
- **Google ADK:** `google.adk.models.base_llm.BaseLlm` subclass implementing the single abstract method `generate_content_async(llm_request, stream=False)` (VERIFIED: `BaseLlm.__abstractmethods__` is exactly that), yielding `google.adk.models.llm_response.LlmResponse` objects with `google.genai.types.Part(function_call=FunctionCall(...))`, `GenerateContentResponseUsageMetadata` and `FinishReason`; agent `google.adk.agents.Agent(name=..., model=FakeLlm(), tools=[python_function])`; driven by the real `google.adk.runners.InMemoryRunner`. VERIFIED run: three events (function call with usage 3/4 and finish STOP; function response; final text with usage 9/2), and today's `GoogleADKAdapter(agent=...).execute("hi")` returns only `output='done: found'` with `raw=None`, `metadata=None`. Scenarios: tool round trip; no tools (`[]`); streaming partial events excluded from usage sums; an error event (`error_code`) with no text.

**Unit tests:** `None` versus `[]` per section 2 (timeout and exception paths give all `None`); provenance validator (the invariants in section 3); `ToolCall` parsing (JSON string arguments, unparseable string goes to `arguments_raw`); backward compatibility (`AgentResponse(output="x")` equals the old behavior, `model_dump(exclude_none=True)` keys equal the old five); a shared conformance test parametrized over every adapter with its duck-typed fake (the existing `_Fake*` classes), asserting the invariants.

**HttpAdapter** gets its own test file (it has none today; the timeout tests are in the `issue48_fix` patch): OpenAI-compatible body with `tool_calls`, `usage` and `finish_reason`; a body without them (`tool_calls=[]` only when `choices[0].message` exists without the key); non-OpenAI body (all `None`); 4xx error path (`error` set, new fields `None`); non-JSON body.

## 8. Order of implementation and effort (INFERRED estimates, working days)
1. Environment fix by the author (`env_fix_plan.md`), 0.5 (author's time; included for the OpenAI Agents work).
2. `ToolCall`, the new fields, provenance validator, `execute()` wiring (`framework`), conformance test and unit tests: 1.5.
3. HTTP adapter plus its own tests: 1.0.
4. LangChain adapter 1.0 plus integration test 1.0.
5. Google ADK adapter 1.5 plus integration test 2.0.
6. LlamaIndex 1.0.
7. OpenAI Agents 1.5 (after the environment fix).
8. Semantic Kernel 1.0.
9. CrewAI 1.5 and AutoGen 1.5, each starting with a short audit (may end with `tool_calls=None` permanently).
10. Harness: fill `BenchTrialResult.usage` from `response.usage` (if approved), 0.5.
11. Docs (README adapter section, Definition of Done, CONTRIBUTING wording decision), 1.0; review and fixes, 1.0.
**Total about 17.5 days** (the assessment estimated about 19 for all of 1A; this is the 1A.1 slice).

## 9. Risks and open questions (each with a recommended answer)
| # | Risk or question | Recommended answer |
|---|---|---|
| 1 | Should `stop_reason` be normalized across frameworks? | No in 1A.1: store the framework's own string unmodified (the the private research harness `Completion.stop_reason` precedent stored the provider value); revisit if a cross-framework comparison needs it. |
| 2 | Usage keys | Reuse `BenchTrialResult.usage` keys exactly (`prompt_tokens`, `completion_tokens`, `reasoning_tokens`), so the harness can copy it with no mapping layer. |
| 3 | ADK usage is per event; streaming `partial` events could be double-counted | Sum usage over non-`partial` events only; provenance `inferred`; unit test with a partial event. |
| 4 | Tool results (`ToolCall.result`) may contain sensitive tool output | Capture in memory; do not persist: no harness field stores it, and the harness must not copy `tool_calls` into `BenchTrialResult` in 1A.1. |
| 5 | Schema version on `AgentResponse` | None: `AgentResponse` is never persisted (VERIFIED), so a version field has no reader; `BenchTrialResult` (`extra="forbid"`, library-version comparability) is untouched this phase. Add one only when `AgentResponse` is serialized. |
| 6 | Fake models test parsing, not live behavior | Keep the manual `examples/*_adapter_verify.py` runs; label the integration tests "fake model, real framework objects". |
| 7 | CrewAI and AutoGen audits may find no stable tool-call source | Accept `tool_calls=None` with provenance `unknown`; do not parse free text to guess. |
| 8 | Semantic Kernel and AG2 maintenance-mode signals in package metadata | Fill only what is cheap (version, stop reason); no deeper investment before the roadmap decides. |
| 9 | The LangChain adapter's default `input_key="input"` fails for chat models (VERIFIED) | Out of scope for 1A.1; document `input_key=None` in the new integration test and in the README. |
| 10 | HTTP fields rely on the OpenAI-compatible key convention (INFERRED) | Mark provenance `inferred`; fill only when the keys are present; otherwise `None`. |
| 11 | Pydantic field ordering and `extra` | Append new fields; no config change; add a test that the old five-field construction and dump are unchanged. |
