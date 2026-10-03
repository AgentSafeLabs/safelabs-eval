# Addendum to `1a1_design/agentresponse_design.md` (wave 1 decisions; the original design file is untouched)

Author decisions of 2026-10-02 that supplement the design's section 9 (open questions):

- **`ToolCall.result` is in memory only and is never persisted.** Implemented by excluding the field from serialization and from `repr` (`Field(exclude=True, repr=False)`), so `model_dump()`, `model_dump_json()` and logging never contain it; the attribute stays readable in memory.
- **1B may revisit this with redaction.** If a later phase wants tool results in stored records, it must add a redaction step (the 1B redaction rule) before any persistence; wave 1 deliberately does not.
- **`tool_calls=None` where no stable source exists** (CrewAI, AutoGen, and any adapter or framework path that does not expose calls); no free text is parsed to guess.
- **Integration tests** run in the default suite, guarded by `pytest.importorskip`; live-model tests are opt-in under the registered marker `live_model` (`pytest -m live_model`).
