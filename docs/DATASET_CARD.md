# Dataset card — OWASP ASI adversarial prompt library

The adversarial prompt corpus that ships inside
[`safelabs-eval`](https://github.com/AgentSafeLabs/safelabs-eval) as the
Python module `safelabs/prompts/library.py`.

## About this document

This card follows the section layout of a Hugging Face dataset card but
omits the HF-specific YAML front-matter and dataset-viewer `configs:`
block. **Rationale:** this corpus is not published on the HF Hub and has
no parquet/CSV/loader-script form — it is a Python data structure loaded
via `safelabs.prompts.load_library()` and browsed with the `safelabs`
CLI. The HF viewer directives would do nothing here, so including them
would be misleading. The familiar section headings are kept so the card
is portable if the corpus is ever mirrored to the Hub.

Every factual claim below is marked:

- **[verified]** — read directly from the repository this session
  (`safelabs/prompts/library.py`, `safelabs/prompts/schemas.py`,
  `LICENSE`, `pyproject.toml`, `git log`) or computed from
  `load_library()` at runtime.
- **[from changelog]** — quoted or condensed from the changelog in the
  `library.py` module docstring (in-repo, but a prose summary written by
  the authors, not an independent check).
- **[summarized]** — carried over from earlier build/audit stages and
  this project's development history; not independently re-derived here.

---

## Dataset description

**[verified]** 131 single-turn adversarial prompts, each targeting one of
the 10 OWASP Agentic Security Initiative (ASI) Top-10 categories. Each
entry is a `PromptEntry` (`safelabs/prompts/schemas.py`) with: `id`,
`category`, `severity`, `prompt`, `expected_behavior`, `tags`,
`difficulty_tier`, `provenance`, `atlas_technique_ids`.

**[verified]** The corpus is designed for *refusal / safe-handling*
evaluation: `expected_behavior` describes what a correctly aligned agent
should do (refuse, ask for scope, escalate to a human, sanitize output,
decline to fabricate). It is **not** a jailbreak how-to collection — no
entry contains working harmful content; the payloads are fabricated
illustrations.

**[verified]** All named entities (people, companies, domains, ticket
ids, IBANs, package names) are fabricated. Domains use the reserved
`example.com` / `.net` / `.org` space.

### Versions

**[verified]** `load_library()` (`library.py:3423`) returns
`version="1.6.0"`, `schema_version="1.1.0"`.

**[verified]** Content version **`1.6.0`**. It was built in six
changelog steps (`1.1.0` through `1.6.0`); Stage 4 review is complete
and the `library.py` docstring no longer marks any of them held or
unreleased. The content version advances with each prompt batch; the
schema version advances only when `PromptEntry`'s shape changes
(still `1.1.0`).

**[verified]** Schema version `1.1.0` (`schemas.py` docstring: *"Schema
version 1.1.0 (2026-09) added three required metadata fields to
`PromptEntry`: `difficulty_tier`, `provenance`, `atlas_technique_ids`"*).

---

## Categories and counts

**[verified]** Computed from `load_library()` this session — total **131**:

| Category | id | Prompts |
|---|---|---|
| Prompt Injection | ASI01 | 14 |
| Insecure Output Handling | ASI02 | 13 |
| Excessive Agency | ASI03 | 13 |
| Resource Management | ASI04 | 13 |
| Tool Use Safety | ASI05 | 13 |
| Data Privacy & Confidentiality | ASI06 | 13 |
| Trust Boundaries | ASI07 | 13 |
| Behavioral Drift | ASI08 | 13 |
| Scope Violation | ASI09 | 13 |
| Hallucination | ASI10 | 13 |

13 per category, plus one extra in ASI01 (the Stage-2 pilot batch is one
prompt larger than the later category batches).

**[verified]** Severity distribution (`Literal["low","medium","high",
"critical"]`): `critical` 35, `high` 90, `medium` 6, `low` 0.

---

## Metadata schema

### `difficulty_tier`

**[verified]** Enum `tier_1` / `tier_2` / `tier_3` (`schemas.py`):

- `tier_1` overt — single-turn, self-evident adversarial ask
- `tier_2` contextual — surface-legitimate; needs scope / impact
  reasoning to refuse
- `tier_3` adaptive — attack actively works to defeat refusal

**[verified]** Rubric, quoted from the `library.py` module docstring:

```
  base tier    severity "critical" -> 2 ; "high" / "medium" -> 1
  +1           if an "evasion vehicle" is present: indirect / embedded
               instruction, retroactive or multi-turn conditioning,
               identity spoofing with an operational pretext,
               task-piggybacking (the violation buried inside a
               sanctioned task), or -- added in v1.2.0 --
               obfuscation / encoding of the injected instruction
               (base64, ROT13, homoglyph, zero-width, etc.) to bypass
               surface-level refusal
  +2 instead   if two independent evasion vehicles are stacked
  clamp        to [1, 3]
```

It is deliberately a corpus-internal scale (severity floor + evasion-
vehicle bumps), **not** an imported external difficulty metric.

**[verified]** Distribution across all 131 entries: `tier_1` 52,
`tier_2` 59, `tier_3` 20. This matches the per-stage "resulting spread"
figures summed from the `library.py` docstring exactly
(10/16/4 + 1/6/4 + 11/7/2 + 12/5/3 + 12/6/2 + 6/19/5).

### `provenance`

**[verified]** Grammar (`schemas.py` `_PROVENANCE_RE`):

```
^(original|adapted-from:<slug>|derived-from-cve:CVE-YYYY-NNNN)$
    slug  = [a-z0-9][a-z0-9-]*
    CVE   = CVE-\d{4}-\d{4,}
```

**[verified]** All 131 entries have `provenance="original"` and match the
grammar. No entry uses `adapted-from:` or `derived-from-cve:`.

**[verified]** Where a prompt's *construction* models a publicly
described attack technique, that lineage is a code comment above the
entry (`# provenance = "original" (text). ...`), not a field value.
About 13 entries carry such a note. See
[`CREDITS.md`](../CREDITS.md) for the full source list and licenses.

### `atlas_technique_ids`

**[verified]** A list of MITRE ATLAS technique ids
(`_ATLAS_ID_RE = ^AML\.T\d{4}(\.\d{3})?$`), **or** the single literal
`["UNMAPPED"]` when no ATLAS technique cleanly applies. `UNMAPPED` is
never mixed with real ids (`schemas.py` validator).

**[verified]** ATLAS mapping was built against **MITRE ATLAS v5.6.0**
(atlas-data release `v2026.08`) — stated in the `schemas.py` docstring.

**[verified]** `schemas.py` carries a curated allow-list,
`KNOWN_ATLAS_TECHNIQUE_IDS`; every real id used in the corpus is in it,
and every id in it is used (checked this session):

| id | name (per `schemas.py` comment) |
|---|---|
| `AML.T0010` | AI Supply Chain Compromise |
| `AML.T0011.001` | User Execution: Malicious Package |
| `AML.T0029` | Denial of AI Service |
| `AML.T0034.000` | Cost Harvesting: Excessive Queries |
| `AML.T0034.002` | Cost Harvesting: Agentic Resource Consumption |
| `AML.T0050` | Command and Scripting Interpreter |
| `AML.T0051.000` | LLM Prompt Injection: Direct |
| `AML.T0051.001` | LLM Prompt Injection: Indirect |
| `AML.T0053` | AI Agent Tool Invocation |
| `AML.T0054` | LLM Jailbreak |
| `AML.T0056` | Extract LLM System Prompt |
| `AML.T0057` | LLM Data Leakage |

**[verified]** `["UNMAPPED"]` is used for **every** entry in exactly two
categories — **ASI02** (Insecure Output Handling) and **ASI10**
(Hallucination) — and nowhere else.

**[from changelog / summarized]** Rationale, per the changelog and the
authoring notes: no ATLAS technique describes a victim model *emitting*
exploitable output (ASI02) or *hallucinating* harmfully (ASI10). Some
categories reuse the nearest applicable id under a documented caveat —
ASI03/ASI09 map to `AML.T0053` (tool invocation) since ATLAS has no
dedicated excessive-agency / scope-violation technique; ASI08 maps to
`AML.T0054` (jailbreak) since session-level "drift" has none; ASI07's
MCP / plugin / peer-agent prompts map to `AML.T0010` as the nearest
supply-chain fit. Individual stretch mappings are flagged in the
relevant entry's code comment (e.g. `ASI07-009`).

### `tags`

**[verified]** Free-form `list[str]`; the first tag is the category slug
(e.g. `"prompt-injection"`), the rest name the vector / vehicle
(`"indirect"`, `"obfuscation"`, `"task-piggybacking"`, ...). Not
validated against a controlled vocabulary.

---

## How it was built

Commit hashes and dates below are **[verified]** from `git log`; the
descriptions of what each stage's prompts *cover* are **[from changelog]**
(the `library.py` docstring changelog) unless marked otherwise.

| Stage | Commit(s) | Date | Change |
|---|---|---|---|
| **1** — original corpus | (in `v0.1.0`, `2d82dda`) | 2026-05-24 | 30 prompts, 3 per category (`1.0.0`). |
| **1** — metadata migration | `049abf8` | 2026-09-10 | Added the three required metadata fields; migrated the 30 prompts to `1.1.0`. No prompt text added or changed. ATLAS mapping built against ATLAS v5.6.0. |
| **2** — ASI01 pilot | `0f02fcd` | 2026-09-10 | +11 ASI01 (`ASI01-004..014`); added the obfuscation / encoding evasion vehicle to the rubric. Changelog `1.2.0`. |
| **3 batch 1** | `8b83d59` | 2026-09-10 | +10 ASI02, +10 ASI03 (library 41 -> 61). `1.3.0`. |
| **3 batch 2** | `134cce8` | 2026-09-10 | +10 ASI04, +10 ASI05 (61 -> 81). `1.4.0`. |
| **3 batch 3** | `822c556` | 2026-09-10 | +10 ASI06, +10 ASI10 (81 -> 101). `1.5.0`. |
| **3 batch 4** (final) | `08ae8b1` | 2026-09-10 | +10 ASI07, +10 ASI08, +10 ASI09 (101 -> 131). `1.6.0`. |
| **4a** — provenance audit | `ad01b4a` | 2026-09-10 | **[summarized]** Full provenance + verbatim-text audit of all 131 prompts. Reclassified `ASI01-006` from `adapted-from:promptinject-2022` to `original` and moved its lineage to a code comment; fixed two stale tool links. |
| **4b** — release docs | (uncommitted at time of writing) | 2026-09-10 | This card, `CREDITS.md`, README updates, and the content-version bump to `1.6.0` (the six changelog steps no longer marked held / unreleased). |

**[verified]** There is no standalone `CHANGELOG` file; the changelog is
the `library.py` module docstring.

Each expansion batch also added dedicated frozenset-scoped tests in
`tests/test_prompts.py` (contiguous-id, per-category ATLAS mapping,
provenance, difficulty-spread) and updated the hard-coded corpus-size
assertions in `tests/test_runner.py`. **[verified]** The full test suite
is green at 160 passed / 1 skipped / 1 deselected this session.

---

## Provenance & verbatim audit (Stage 4a summary)

**[summarized]** from the Stage-4a audit, recorded in
[`CREDITS.md`](../CREDITS.md) and this project's development history:

- A full read of the flagged entries plus a broad spot-check (58 of 131
  entries read in full), and a regex scan of **all 131** prompts for
  canonical verbatim-jailbreak and copyrighted-content markers (DAN /
  AIM / "Developer Mode enabled" / grandma / `leakreplay` strings /
  song-lyric and known-passage fragments).
- **Finding: no prompt reproduces verbatim text, code, or dataset rows
  from any external source.** Marker scan returned zero hits. The two
  obfuscated payloads (`ASI01-009` base64, `ASI01-012` ROT13) decode to
  the library's own newly written sentences.
- ~13 entries carry a code-comment note recording the *technique or
  scenario domain* they draw on. Sources and their licenses:
  Greshake et al. 2023 (arXiv non-exclusive), Debenedetti et al. 2024 /
  AgentDojo (paper: arXiv non-exclusive; code: MIT), Perez & Ribeiro
  2022 / PromptInject (CC BY-NC-SA 4.0), the Crescendo paper
  (CC BY 4.0), the "Do Anything Now" paper reached via garak's `dan`
  probe (CC BY 4.0), garak (`NVIDIA/garak`, Apache-2.0), PyRIT
  (`microsoft/PyRIT`, MIT). Full detail, per-entry mapping, and the
  "what is / is not used" notes are in [`CREDITS.md`](../CREDITS.md).
- **[verified this session]** The three fabricated package names
  (`langchain-community-extra` on PyPI, `@types-node/fs-extra` and
  `acme-env-bootstrap` on npm) all return HTTP 404 — unregistered — as
  of 2026-09-10. Re-check commands are in `CREDITS.md`.

---

## Uses and limitations

**Intended use.** Automated refusal / safe-handling evaluation of AI
agents and LLM applications: run the corpus against an agent, score each
response with the bundled pattern detectors (or your own), get a
per-category verdict breakdown. Framework-agnostic (HTTP endpoint or
Python callable).

**Out of scope / limitations.**

- **[verified]** Single-turn only. Multi-turn drift (ASI08) is *described
  within one prompt* ("across 20 turns... we're now at turn 20"), not
  executed as a real multi-turn exchange.
- **[verified]** English only.
- **[summarized]** The bundled detectors are regex/pattern based (no LLM
  grading); they can miss subtle partial compliance. This is a known,
  documented gap in the project (see the README and the project's
  detector-reliability writeups).
- **[verified]** `difficulty_tier` is a corpus-internal rubric, not
  calibrated against measured model refusal rates.
- **[verified]** `atlas_technique_ids` uses nearest-fit mappings with
  documented caveats for several categories; it is not an authoritative
  ATLAS classification.
- Coverage is breadth-first (13 per category); it is not exhaustive of
  any category's attack surface.

**Risk / dual-use.** The prompts are adversarial by construction but
contain no working exploit payloads, malware, or step-by-step harmful
instructions; each is paired with the safe expected behaviour. Standard
responsible-use expectations apply.

---

## Licensing

**[verified — read from `LICENSE` this session]** The repository's
`LICENSE` file is the **Apache License, Version 2.0**, standard full
text (201 lines; `sha256 c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4`).
Its appendix copyright placeholder (`Copyright [yyyy] [name of copyright
owner]`) is left unfilled, which is normal.

**[verified]** `pyproject.toml`: `license = { text = "Apache-2.0" }`;
`name = "safelabs-eval"`; `version = "0.2.2"`; author
`Waqar Javed <waqar@agentsafelabs.io>`. **[verified]** The README
attributes maintenance to *Safe Labs AI Inc.*

This dataset is distributed as part of `safelabs-eval` under that same
Apache-2.0 license. Per the Stage-4a audit, no third-party licensed
material is redistributed in the corpus (see `CREDITS.md`), so no
upstream source adds a downstream obligation.

---

## Citation

**[verified]** There is no `CITATION.cff` in the repository. Suggested
form (not yet formalized by the maintainers):

```bibtex
@software{safelabs_eval_asi_prompts,
  title  = {safelabs-eval: OWASP ASI adversarial prompt library},
  author = {Javed, Waqar and {Safe Labs AI Inc.}},
  year   = {2026},
  note   = {Content version 1.6.0; 131 prompts across the 10 OWASP ASI Top-10 categories},
  url    = {https://github.com/AgentSafeLabs/safelabs-eval}
}
```

When citing a specific run or finding, also cite the exact commit of
`safelabs/prompts/library.py` you evaluated against: the content version
(`1.6.0`) advances only per prompt batch, so a commit hash pins the exact
prompt set.

---

## Maintenance

Issues and contributions: <https://github.com/AgentSafeLabs/safelabs-eval>.
See `CONTRIBUTING.md` for the prompt-addition conventions (dedicated
tests per batch, provenance discipline, fabricated entities only) and
`DATA_INTEGRITY_RULES.md` for the rules that apply to any run producing
numbers for external use.
