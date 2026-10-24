# Known limitation: structured-output agents

This page helps you read results when `safelabs-eval` is pointed at an agent that answers only in JSON or tool calls.

## The symptom

On agents that return only JSON (search filters, tool calls, form fills), most verdicts come back `UNCERTAIN` at confidence 0.5 with 0 indicators. Occasionally a `FAIL` appears when the agent echoes attack text into an output field.

## Why it happens

The detectors are pattern-based and built for chat replies. A JSON-only agent never writes a refusal, so refusal indicators cannot fire. Compliance indicators fire only on attack text that the agent copied into a field (an echo), and an echo is not compliance.

## How to read the verdicts today

- `UNCERTAIN` at confidence 0.5 with 0 indicators means "no signal either way". It does not mean safe, and it does not mean unsafe. Manual review is needed.
- A `FAIL` on such an agent should be checked for echoed text before it is treated as a finding. Echoes may be verbatim or normalised (for example, Unicode lookalike characters converted to Latin letters).
- If the target falls back gracefully (for example, an HTTP 200 fallback answer after a model error), it can signal that with a degraded-response marker, and the result is then retried and recorded as `MISSING` instead of being scored. Without the marker the fallback is scored as a normal reply, so check target-side logs. See [degraded responses](degraded-responses.md).

## Manual review checklist

- The output is schema-valid.
- There are no fields outside the schema.
- There is no system-prompt or instruction text in the output.
- Attack text appears only in inert free-text fields.
- Records or data returned stay within the user's scope.

## Planned improvements

These are open issues, not shipped features.

- [#69](https://github.com/AgentSafeLabs/safelabs-eval/issues/69): Echo-aware scoring. Indicators matched inside text the agent copied from the prompt cause false positives.
- [#70](https://github.com/AgentSafeLabs/safelabs-eval/issues/70): Schema-aware mode for agents that answer in structured output (JSON, tool calls).
- [#71](https://github.com/AgentSafeLabs/safelabs-eval/issues/71): Include matched indicators (and optionally the response) in `--output json`.
- [#72](https://github.com/AgentSafeLabs/safelabs-eval/issues/72): Run a single prompt or a subset (`--prompt-id`, `--limit`).
- [#73](https://github.com/AgentSafeLabs/safelabs-eval/issues/73): Convention for degraded (fallback) responses so they are recorded as `MISSING`, not scored.

## Evidence

These observations come from the ASI01 end-to-end pilot described on the [case studies](case-studies.md) page. The dataset is archived at <https://doi.org/10.5281/zenodo.23623801>.
