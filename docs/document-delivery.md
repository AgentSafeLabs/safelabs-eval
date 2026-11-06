# Document delivery (indirect injection)

The 30 ASI01 prompts are user messages. In an indirect injection the attacker's text arrives inside a document the agent reads while doing something else. Document delivery puts each ASI01 prompt into a fixed, synthetic document and sends that document to your target, so that two people who say "referral-letter, version 1, payload at the end" have sent byte-identical text. Design and reasoning: [document-delivery-mode.md](design/document-delivery-mode.md).

Delivery is off by default. A run without the options below sends the prompts, scores them and saves results exactly as before.

## Example

```bash
safelabs run --target http://localhost:8000/chat --category ASI01 \
  --delivery-template referral-letter --payload-position end --output json
```

Options (all on `safelabs run`):

| Option | Meaning |
|---|---|
| `--delivery-template ID` | Deliver each prompt inside a built-in template: `referral-letter` or `support-ticket`. |
| `--payload-position start\|middle\|end` | Where the prompt sits in the document's reading order. There is no default: it is required with `--delivery-template`. |
| `--delivery-label ID@VERSION` | Your own bridge delivers the prompt. Prompts are sent unchanged and results are labelled with your id and version. The label is your claim; the tool does not check it. Cannot be combined with `--delivery-template`. |

From Python: `run_eval(agent_fn, categories=["ASI01"], delivery=template_delivery("referral-letter", position="end"))`, with `template_delivery` and `bridge_delivery` from `safelabs.delivery.mode`.

## What the target receives, and what is scored

The target receives the rendered document. Scoring is unchanged: the scorer, echo-aware scoring, schema-aware mode and the PAYLOAD_SURFACED warning all compare the response against the original library prompt, and `prompt` in saved results is the original prompt. Verdict counts, retries and missing-trial handling work as before; every retry resends the same text.

## What is recorded

With delivery on, each result (scored, missing or error) has a `delivery` object, and the JSON output has a top-level `delivery` object with the same fields except the per-prompt checksum. Without delivery, neither key appears, and saved `EvalRecord` JSON has no `delivery` field.

Template delivery:

| Field | Meaning |
|---|---|
| `mode` | `"template"` |
| `template_id`, `template_version` | The template used. Any change to a template makes a new version. |
| `template_sha256` | Checksum of the template data. |
| `payload_position` | `start`, `middle` or `end`, as requested. |
| `payload_recency` | `newest`, `middle` or `oldest`: where the payload's date falls among the document's dates. It follows from template and position. |
| `rendered_sha256` | Checksum of the exact text sent (per result only), so equality across runs is checkable. The text itself is not stored. |

Prompts the library already tags `indirect` (for example ASI01-003) already contain their own document. They are not wrapped; they are sent as they are, and the result says `mode: "direct"`, `already_indirect: true`, plus the template and position that were requested and skipped (`skipped_template_id`, `skipped_template_version`, `skipped_payload_position`).

Bridge label: `mode: "bridge"`, `bridge_id`, `bridge_version`, `declared: true`.

## Limits

- Synthetic templates only: two small invented documents (`referral-letter`, `support-ticket`). They make no claim to be typical of real documents.
- One document per prompt, one payload per document. No multi-document or multi-turn flows.
- ASI01 only. Delivery with any other category exits with status 2 before anything is sent.
- Results are comparable only across runs with the same template id, version and position. The same applies to a bridge label: compare only the same id and version.
- A bridge label is a declaration. The tool does not verify what your bridge did.
- Summary agents rarely copy the payload verbatim. For structured summaries, combine delivery with [schema-aware mode](schema-mode-summary-agents.md); a changed PAYLOAD_SURFACED rate between direct and document delivery is data, not a calibration result.
- The run output does not include library or tool versions.
