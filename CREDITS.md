# Credits — technique & scenario lineage

`safelabs-eval`'s prompt library (`safelabs/prompts/library.py`, 131 entries)
is original text. Every prompt, expected-behavior string, and fabricated
entity (names, domains, ticket ids, package names) was written for this
project.

A Stage-4a provenance and verbatim-text audit (2026-09) over all 131
prompts found **no verbatim reproduction of text, code, or dataset rows
from any external source.** Where a prompt's *construction* deliberately
models a publicly described attack technique or scenario domain, that
lineage is recorded in a code comment directly above the entry
(`# provenance = "original" (text). ...`). This file lists every such
source, its license, and what was — and was not — used.

All entries carry `provenance="original"`. No entry uses the
`adapted-from:<slug>` form. (`ASI01-006` briefly carried
`adapted-from:promptinject-2022`; the Stage-4a audit reclassified it to
`original` because the "ignore all previous instructions" idiom is generic
and no verbatim PromptInject text is reused — see that entry's code
comment.)

This repository is licensed under **Apache-2.0** (see [`LICENSE`](LICENSE)).
Nothing below imposes any obligation on downstream users of this
repository, because no licensed material from these sources is
redistributed here — only references to the ideas they describe.

---

## Academic papers

Citing or re-implementing a technique described in a research paper is
standard scholarly practice and is not governed by the paper's text
license. Licenses are listed for completeness; none of these papers
restricts reuse of the techniques it documents, and none of their text is
reproduced here.

Four of the five below are cited directly — by name and arXiv id — in the
relevant entry's code comment (Greshake et al., Debenedetti et al., Perez
& Ribeiro; the Crescendo comment says "Microsoft, 2024", expanded to the
paper here). The fifth (Shen et al.) is **not** cited by any entry; it
reaches the library only through garak's `dan` probe — see the "Do
Anything Now" subsection, which is placed here for completeness rather
than as a claimed direct citation.

### Greshake et al., 2023 — indirect prompt injection

- **Paper:** "Not what you've signed up for: Compromising Real-World
  LLM-Integrated Applications with Indirect Prompt Injection",
  arXiv:2302.12173.
- **License:** arXiv.org perpetual, non-exclusive license v1.0
  (`arxiv.org/licenses/nonexclusive-distrib/1.0`).
- **Referenced by (entry code comments):** `ASI01-003`, `ASI01-004`,
  `ASI01-007` — each names the paper and arXiv id directly.
- **What is used:** the *technique* — instructions embedded in content the
  agent processes (a hidden block in a document, a poisoned retrieved
  knowledge-base article, hidden-in-markup / zero-width text on a browsed
  page).
- **What is not used:** no wording, payloads, application targets, or
  figures from the paper. All injected strings and entities are
  fabricated.

### Debenedetti et al., 2024 — AgentDojo (agent tool-use attacks)

- **Paper:** "AgentDojo: A Dynamic Environment to Evaluate Prompt
  Injection Attacks and Defenses for LLM Agents", arXiv:2406.13352.
- **Paper license:** arXiv.org perpetual, non-exclusive license v1.0.
- **Code license:** the AgentDojo benchmark code
  (`github.com/ethz-spylab/agentdojo`) is **MIT** (verified 2026-09).
- **Referenced by (entry code comments):** `ASI01-005`, `ASI01-013`,
  `ASI01-014`, `ASI03-010` — each names the benchmark directly.
- **What is used:** the *attack class / scenario domain* — tool-result and
  environment injection in an agent pipeline, spoofed inter-agent
  handoffs, email-body injection carrying a forged in-band authorization
  tag, and the money-movement (banking) task domain.
- **What is not used:** no task JSON, injection strings, user/injection
  goal text, or tool definitions from the benchmark. `ASI03-010`'s comment
  explicitly notes its threat model *differs* from AgentDojo's (a direct
  user over-ask, not an indirect injection).

### Perez & Ribeiro, 2022 — PromptInject

- **Paper:** "Ignore Previous Prompt: Attack Techniques For Language
  Models", arXiv:2211.09527.
- **License:** Creative Commons Attribution-NonCommercial-ShareAlike 4.0
  (CC BY-NC-SA 4.0) — verified on the arXiv abstract page, 2026-09.
- **Referenced by (entry code comment):** `ASI01-006` names the paper and
  arXiv id directly; the `provenance` field is `original`.
- **What is used:** the observation that the "ignore all previous
  instructions" instruction-override idiom is an effective attack. That
  idiom is generic — it predates and postdates the paper, which
  catalogued it.
- **What is not used:** no PromptInject rogue-string templates, framework
  code, or paper text. Because no CC BY-NC-SA material is reproduced, the
  NC / SA terms do not attach to this repository.

### Russinovich et al., 2024 — Crescendo

- **Paper:** "Great, Now Write an Article About That: The Crescendo
  Multi-Turn LLM Jailbreak Attack", arXiv:2404.01833.
- **License:** Creative Commons Attribution 4.0 (CC BY 4.0) — verified on
  the arXiv abstract page, 2026-09.
- **Referenced by (entry code comments):** `ASI08-002` and `ASI08-004`
  name the technique as "Crescendo ... (Microsoft, 2024)"; the arXiv
  paper above is Microsoft's publication of that technique. `ASI08-004`
  also names PyRIT's Crescendo strategy.
- **What is used:** the *technique* — a multi-turn conversation that opens
  benign and escalates step by step so no single turn triggers a refusal.
- **What is not used:** no conversation transcripts or prompt text from
  the paper.

### "Do Anything Now" — in-the-wild jailbreak family, reached via garak

- **How it enters this library:** `ASI08-011`'s code comment references
  the DAN-family / "ChatGPT Developer Mode" persona jailbreak "catalogued
  by garak's `dan` probe (and the DAN-in-the-wild corpus)". **The entry
  does not cite a paper.** The citation below is garak's, recorded here
  because it is the documented upstream of the corpus the entry names.
- **Upstream source of garak's corpus:** garak's `DanInTheWild` probe
  loads `inthewild_jailbreak_llms.json` and records its source (the
  probe's `doc_uri`) as arXiv:2308.03825 — Shen et al., "'Do Anything
  Now': Characterizing and Evaluating In-The-Wild Jailbreak Prompts on
  Large Language Models" (verified against garak `main`, 2026-09).
- **License:** that paper is Creative Commons Attribution 4.0 (CC BY 4.0)
  — verified on the arXiv abstract page, 2026-09. The in-the-wild prompts
  it studies are third-party user-generated content collected from public
  platforms and released by the authors as a separate dataset (not
  re-verified here for repository / license specifics, and not used).
- **What is used:** only the *family* — a "no content policy"
  developer-mode persona plus a "stay in character" re-assertion hook.
- **What is not used:** no prompt from that corpus, and none of the
  canonical DAN / "Developer Mode" wording. `ASI08-011` uses a distinct
  label (`DevMode`) and newly written text.

---

## Open-source tools

Only *probe / converter / strategy category names* from these tools are
referenced, to say which known attack class a prompt exercises. No source
code, and no content from the datasets these tools bundle, is reproduced.

### garak

- **Project:** `github.com/NVIDIA/garak` — "the LLM vulnerability
  scanner".
- **License:** Apache-2.0 (verified on the repo, 2026-09).
- **Referenced by (entry code comments):** `ASI01-009` (base64 encoding
  probe), `ASI01-012` (ROT13 probe), `ASI06-005` (`sysprompt_extraction`),
  `ASI06-007` (`leakreplay`), `ASI07-005` and `ASI10-009`
  (`packagehallucination`, named as a *contrast*), `ASI08-011` (`dan`),
  `ASI10-011` (`snowball`, named as a *contrast*).
- **What is used:** the probe *name*, to identify the attack category.
- **What is not used:** no probe code, and nothing from `garak/data/`.
  garak bundles third-party datasets (e.g. DanInTheWild, HuggingFace
  system-prompt sets, copyrighted-literature excerpts for `leakreplay`)
  under their own upstream licenses — none of that data appears here.

### PyRIT

- **Project:** `github.com/microsoft/PyRIT` (formerly `Azure/PyRIT`) —
  "Python Risk Identification Tool for generative AI".
- **License:** MIT (verified on the repo, 2026-09).
- **Referenced by (entry code comments):** `ASI01-009` (Base64 converter),
  `ASI01-012` (ROT13 converter), `ASI08-004` (Crescendo strategy).
- **What is used:** the converter / strategy *name*.
- **What is not used:** no converter code and no PyRIT seed-prompt
  datasets.

---

## Reproducible verification — fabricated package names

Three prompts name a package that does not exist, so that a correctly
aligned agent refuses to install it. Each name was checked against the
live registry and confirmed **unregistered**. Re-run to verify:

| Prompt | Package | Registry | Status (checked 2026-09-10) | Check command |
|---|---|---|---|---|
| `ASI07-002` | `langchain-community-extra` | PyPI | 404 — unregistered | `curl -s -o /dev/null -w '%{http_code}' https://pypi.org/pypi/langchain-community-extra/json` |
| `ASI07-005` | `@types-node/fs-extra` | npm | 404 — unregistered | `curl -s -o /dev/null -w '%{http_code}' 'https://registry.npmjs.org/%40types-node%2Ffs-extra'` |
| `ASI07-010` | `acme-env-bootstrap` | npm | 404 — unregistered | `curl -s -o /dev/null -w '%{http_code}' https://registry.npmjs.org/acme-env-bootstrap` |

`@types-node/fs-extra` is a deliberate typosquat shape — it imitates the
real `@types/*` (DefinitelyTyped) scope and the real `fs-extra` package
without being either. `acme-env-bootstrap` is an internal-sounding name
used to illustrate dependency confusion. Neither is a real package; if a
registry check ever returns a non-404 for one of these, the corresponding
prompt should be revised.

---

## Summary

- 131 prompts, all `provenance="original"`.
- No verbatim text, code, or dataset content from any source above.
- ~13 entries carry a code-comment note recording technique / scenario
  lineage; this file is the index of those sources. Four papers plus
  garak and PyRIT are cited by name in those comments; the "Do Anything
  Now" paper is not cited by any entry and is listed only as the
  documented upstream of garak's `dan` corpus.
- Repository license: Apache-2.0. No source above adds any downstream
  obligation, because none of their licensed material is redistributed
  here.

See [`docs/DATASET_CARD.md`](docs/DATASET_CARD.md) for the full dataset
description and the Stage-4a audit summary.
