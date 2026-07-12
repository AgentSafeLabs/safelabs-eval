# Data Integrity Rules — Safe Labs AI / AgentSafeLabs

Added July 12, 2026, following two incidents in the Paper A results
reconstruction that produced (or nearly produced) silently wrong
numbers. These are standing rules for any script whose output could be
cited in a paper, blog post, report, or compliance artifact — not
suggestions.

## Rule 1 — Archive raw responses before computing any verdict

**What happened:** The original 30-prompt Claude Haiku baseline (the
"10/30 UNCERTAIN" result) only ever had its aggregate verdict counts
saved. The raw per-prompt model responses were never written to disk.
When that data was needed later to build a peer-reviewed paper's
evidence table, it was unrecoverable — not from a bug, but because it
was simply never captured.

**Rule:** Any evaluation run — trial, baseline, benchmark, validation —
must write raw model responses (full text, not just verdicts) to a
timestamped file *before* any detector or scoring logic runs on them.
Verdicts are derived data and can always be recomputed from raw
responses; the reverse is not true.

**Minimum implementation:** every run of `safelabs.runner.run_eval()`
(or equivalent in agentdojo-x) should write to something like
`runs/{timestamp}_{run_id}/raw_responses.jsonl` before scoring begins,
regardless of whether the run is "just a quick check."

## Rule 2 — Verify source location before trusting output

**What happened:** A stale, non-editable `safelabs-eval` install in
`.venv/site-packages` silently shadowed the actual source tree during
offline detector reconstruction, producing incorrect verdict counts on
the first attempt. It was caught only by chance, via a manual 30/30
self-consistency check — not by any automated safeguard.

**Rule:** Any entrypoint script that produces numbers destined for a
paper, blog post, or report must call `assert_running_from_source()`
(see `verify_source.py`) before running, or use the `@verified_source`
decorator on the function that computes scored output. This turns a
silent failure mode into a loud, immediate one.

**Minimum implementation:** add the import and check to the top of
`safelabs/runner.py`, `safelabs/cli.py`, and any standalone reporting
script (e.g. the one used to build `docs/paper-a-results-data.md`).

## Periodic check

Run `audit_editable_installs.sh` from the parent directory of all repos
before starting any session that will produce numbers for external use
(a paper draft, a blog post, a customer-facing report). Takes seconds,
catches the exact class of bug that has now happened twice.