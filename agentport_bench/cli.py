"""
agentport_bench/cli.py

Console-script entry point (`agentport-bench`), click-based -- consistent
with safelabs-eval's own `safelabs` CLI convention (safelabs/cli.py), not
agentdojo-x's internal argparse tool (agentdojo-x is a private orchestrator
with no public CLI contract to match).

Design correction made while implementing this file (flagged here rather
than silently carried over from the earlier-approved skeleton): the `run`
command's --adapter choice is **http and custom only**, not all 9 adapter
names. A CLI string flag cannot carry a live Python object -- a LangChain
Runnable, a CrewAI Crew, an AutoGen agent pair -- as its value, so
"--adapter langchain --some-flag ..." could never actually construct a
real chain from the command line regardless of how many flags were
invented for it. harness.build_adapter()'s full _BUILTIN_ADAPTERS table
(all 7 framework adapters) remains available for *programmatic* use --
call it directly from Python once you've built your native object -- but
from this CLI, every framework adapter is reached through --adapter
custom --module "your.module:YourAdapterSubclass", whose __init__ does
the framework-specific construction and wraps (or is) the matching
built-in safelabs.agents.*Adapter. This is not a limitation specific to
this CLI; no generic benchmark harness CLI could do otherwise for
adapters shaped this way.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from agentport_bench import __version__
from agentport_bench.harness import (
    MISSING_EXCLUDED_NOTE,
    RunManifest,
    build_adapter,
    format_rerun_lines,
    format_summary_lines,
    rerun_missing,
    resolve_retry_settings,
    run_matrix,
    summarize_rows,
    write_manifest,
)
from agentport_bench.schema import (
    KNOWN_LIBRARY_VERSIONS,
    BenchTrialResultWithRawOutput,
    is_library_version_comparable,
)
from agentport_bench.validate import ValidationReport, load_submission, validate_submission
from safelabs.prompts import get_library

_RED = "\033[91m"
_YELLOW = "\033[93m"
_GREEN = "\033[92m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

_DRY_RUN_CATEGORIES = ["ASI01"]
_DRY_RUN_SEEDS = 1


@click.group()
@click.version_option(__version__, prog_name="agentport-bench")
def main() -> None:
    """AgentPort-Bench — submit and validate cross-framework agent-safety trial results."""


# ── run ───────────────────────────────────────────────────────────────────

def _parse_adapter_kwargs(pairs: tuple[str, ...]) -> dict[str, str]:
    kwargs: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise click.UsageError(f"--adapter-kwarg must be KEY=VALUE, got {pair!r}")
        key, _, value = pair.partition("=")
        kwargs[key] = value
    return kwargs


def _parse_categories(categories: str | None) -> list[str] | None:
    if not categories:
        return None
    return [c.strip().upper() for c in categories.split(",") if c.strip()]


@main.command()
@click.option("--adapter", "-a", required=True, type=click.Choice(["http", "custom"]),
              help="Only http and custom are directly CLI-drivable -- see this module's docstring.")
@click.option("--model", "-m", required=True, help="Contributor-declared model id, e.g. 'claude-opus-4-8'.")
@click.option("--provider", default=None, help="Model provider, e.g. 'anthropic' | 'openai' | 'google'.")
@click.option("--target", default=None, help="Base URL. Required for --adapter http.")
@click.option("--module", default=None, help="'import.path:ClassName' for --adapter custom.")
@click.option("--adapter-kwarg", "adapter_kwargs", multiple=True,
              help="KEY=VALUE, forwarded as a string kwarg to the adapter constructor. Repeatable.")
@click.option("--categories", default=None, help="Comma-separated ASI category ids; default all 10.")
@click.option("--seeds", default=1, show_default=True, type=int)
@click.option("--output", "-o", required=True, type=click.Path(path_type=Path))
@click.option("--resume/--no-resume", default=True, show_default=True)
@click.option("--dry-run", is_flag=True, default=False,
              help=f"Restrict to a small fixed subset ({', '.join(_DRY_RUN_CATEGORIES)}, seeds={_DRY_RUN_SEEDS}).")
@click.option("--include-raw-output", is_flag=True, default=False,
              help="Write raw model output to disk. Default off -- see docs/AGENTPORT_BENCH.md.")
@click.option("--max-concurrency", default=1, show_default=True, type=int)
@click.option("--timeout-s", default=30.0, show_default=True, type=float)
@click.option("--retry-profile", default="default", show_default=True, type=click.Choice(["default", "benchmark"]),
              help="default: 3 attempts, 1 s base, 60 s cap, Retry-After up to 300 s. benchmark: 6 attempts, 2 s base, "
                   "120 s cap, Retry-After up to 600 s. The flags below override the profile.")
@click.option("--max-attempts", default=None, type=click.IntRange(min=1),
              help="Attempts per trial for infrastructure errors (rate limit, timeout, outage); 1 disables retries. Default: the profile's.")
@click.option("--retry-base-delay-s", default=None, type=click.FloatRange(min=0.0),
              help="First backoff delay in seconds; doubles each retry, with jitter. Retry-After is honoured. Default: the profile's.")
@click.option("--retry-max-delay-s", default=None, type=click.FloatRange(min=0.0),
              help="Cap on one backoff delay in seconds. Default: the profile's.")
@click.option("--retry-after-cap-s", default=None, type=click.FloatRange(min=0.0),
              help="Longest Retry-After the harness will wait, in seconds. Default: the profile's.")
@click.option("--rerun-missing", is_flag=True, default=False,
              help="With --resume: re-execute only the missing_infrastructure rows of --output (for this --model and adapter), "
                   "rewriting the file in place; scored rows are not re-executed. Run it after a cool-down.")
def run(adapter, model, provider, target, module, adapter_kwargs, categories, seeds,
        output, resume, dry_run, include_raw_output, max_concurrency, timeout_s,
        retry_profile, max_attempts, retry_base_delay_s, retry_max_delay_s, retry_after_cap_s,
        rerun_missing) -> None:
    """Run the attack suite against your framework/model and emit an
    AgentPort-Bench-schema .jsonl submission. A trial that still fails with an
    infrastructure error after the last attempt is recorded as
    missing_infrastructure (no verdict) and excluded from every aggregate;
    --rerun-missing tries those trials again later."""
    if rerun_missing and not resume:
        raise click.UsageError("--rerun-missing works on an existing output file; it cannot be combined with --no-resume")
    asyncio.run(_run_async(
        adapter, model, provider, target, module, adapter_kwargs, categories, seeds,
        output, resume, dry_run, include_raw_output, max_concurrency, timeout_s,
        retry_profile=retry_profile, max_attempts=max_attempts, retry_base_delay_s=retry_base_delay_s,
        retry_max_delay_s=retry_max_delay_s, retry_after_cap_s=retry_after_cap_s, rerun_missing=rerun_missing,
    ))


async def _run_async(
    adapter_name, model, provider, target, module, adapter_kwargs, categories, seeds,
    output, resume, dry_run, include_raw_output, max_concurrency, timeout_s,
    *, retry_profile="default", max_attempts=None, retry_base_delay_s=None,
    retry_max_delay_s=None, retry_after_cap_s=None, rerun_missing=False,
) -> None:
    retry = resolve_retry_settings(
        retry_profile, max_attempts=max_attempts, base_delay_s=retry_base_delay_s,
        max_delay_s=retry_max_delay_s, max_retry_after_s=retry_after_cap_s,
    )
    max_attempts = retry["max_attempts"]
    kwargs = _parse_adapter_kwargs(adapter_kwargs)

    if adapter_name == "http":
        if not target:
            raise click.UsageError("--target is required for --adapter http")
        kwargs.setdefault("base_url", target)
        kwargs.setdefault("timeout", timeout_s)
    elif adapter_name == "custom":
        if not module:
            raise click.UsageError("--module is required for --adapter custom")
        kwargs["module"] = module

    adapter = build_adapter(adapter_name, **kwargs)

    run_categories = _DRY_RUN_CATEGORIES if dry_run else _parse_categories(categories)
    run_seeds = _DRY_RUN_SEEDS if dry_run else seeds

    if rerun_missing:
        await _rerun_missing_pass(adapter, adapter_name, model, output, run_categories, max_concurrency, retry, retry_profile)
        return

    click.echo(f"\n{_BOLD}agentport-bench v{__version__}{_RESET}  --  {adapter_name} / {model}")
    click.echo(f"Output: {output}  (resume={resume}, seeds={run_seeds}, max_concurrency={max_concurrency})")
    if dry_run:
        click.echo(f"{_YELLOW}--dry-run: restricted to {_DRY_RUN_CATEGORIES}{_RESET}")
    click.echo("─" * 60)

    started_at = datetime.now(timezone.utc).isoformat()
    count = 0
    results = []
    async for result in run_matrix(
        adapter,
        model=model, framework=adapter_name, provider=provider,
        categories=run_categories, seeds=run_seeds, output_path=output,
        resume=resume, include_raw_output=include_raw_output,
        max_concurrency=max_concurrency,
        max_attempts=retry["max_attempts"], base_delay_s=retry["base_delay_s"],
        max_delay_s=retry["max_delay_s"], max_retry_after_s=retry["max_retry_after_s"],
    ):
        count += 1
        results.append(result)
        if result.is_missing:
            label = (f"{_RED}MISSING{_RESET} ({result.error_subclass}, {result.attempts} attempt(s); "
                     f"excluded from aggregates)")
        else:
            colour = {"pass": _GREEN, "uncertain": _CYAN, "fail": _YELLOW, "vulnerable": _RED}.get(
                result.verdict.value, _RESET
            )
            label = f"{colour}{result.verdict.value.upper()}{_RESET}"
            if result.attempts and result.attempts > 1:
                label += f" (after {result.attempts} attempts)"
            if result.tool_call_only:
                label += " [tool-call-only]"
        click.echo(f"  [{count:>4}] {result.prompt_id} seed={result.trial_seed}  {label}")
    finished_at = datetime.now(timezone.utc).isoformat()

    if count == 0:
        click.echo(f"\n{_YELLOW}No trials run -- everything already present in {output} (--resume).{_RESET}")
        return

    summary = summarize_rows(results)
    manifest = RunManifest(
        harness_version=__version__,
        library_version=get_library().version,
        model=model,
        framework=adapter_name,
        started_at=started_at,
        finished_at=finished_at,
        trial_count=count,
        include_raw_output=include_raw_output,
        scored_trials=summary.scored,
        missing_infrastructure=summary.missing_infrastructure,
        missing_by_cell=summary.missing_by_cell,
        retries=summary.retries,
        tool_call_only=summary.tool_call_only,
        max_attempts=max_attempts,
        missing_trials_excluded=MISSING_EXCLUDED_NOTE,
        retry_profile=retry_profile,
    )
    manifest_path = write_manifest(output, manifest)

    click.echo("─" * 60)
    click.echo(f"{_BOLD}Wrote {count} trial(s) to {output}{_RESET}")
    for line in format_summary_lines(summary):
        click.echo(f"  {line}")
    click.echo(f"Manifest: {manifest_path}")
    if include_raw_output:
        click.echo(f"{_YELLOW}--include-raw-output was set: {output} contains raw model text "
                   f"and should be treated as sensitive -- do not submit it as-is.{_RESET}")


async def _rerun_missing_pass(adapter, adapter_name, model, output, run_categories, max_concurrency, retry, retry_profile) -> None:
    """``run --rerun-missing``: re-execute the missing_infrastructure rows of an existing file, print the pass summary
    by framework x model, and refresh the manifest sidecar's counts."""
    if not output.exists():
        raise click.ClickException(f"{output} does not exist; --rerun-missing needs the results file of an earlier run")
    click.echo(f"{_YELLOW}--rerun-missing: re-executing only missing_infrastructure rows in {output}{_RESET}")
    click.echo("─" * 60)
    try:
        summary = await rerun_missing(
            adapter, model=model, framework=adapter_name, output_path=output, categories=run_categories,
            max_concurrency=max_concurrency, max_attempts=retry["max_attempts"], base_delay_s=retry["base_delay_s"],
            max_delay_s=retry["max_delay_s"], max_retry_after_s=retry["max_retry_after_s"],
        )
    except OSError as exc:
        raise click.ClickException(str(exc)) from None
    click.echo("─" * 60)
    for line in format_rerun_lines(summary):
        click.echo(line)
    if not summary.rewrote_file:
        click.echo("No manifest change.")
        return
    try:
        rows = load_submission(output)
    except ValueError as exc:
        click.echo(f"{_YELLOW}Results rewritten, but the manifest was not refreshed: {exc}{_RESET}")
        return
    after = summarize_rows(rows)
    path = output.with_suffix(".manifest.json")
    if path.exists():
        m = RunManifest.model_validate_json(path.read_text(encoding="utf-8"))
        m.finished_at = datetime.now(timezone.utc).isoformat()
    else:
        first = rows[0]
        unknown = "unknown (manifest regenerated from existing rows, not a live run)"
        m = RunManifest(
            harness_version=first.harness_version, library_version=first.library_version, model=first.model,
            framework=first.framework, started_at=unknown, finished_at=datetime.now(timezone.utc).isoformat(),
            trial_count=len(rows), include_raw_output=isinstance(first, BenchTrialResultWithRawOutput),
        )
    m.trial_count = len(rows)
    m.scored_trials, m.missing_infrastructure, m.missing_by_cell = after.scored, after.missing_infrastructure, after.missing_by_cell
    m.retries, m.tool_call_only = after.retries, after.tool_call_only
    m.rerun_passes = max((r.rerun_passes for r in rows), default=0)
    m.missing_trials_excluded = MISSING_EXCLUDED_NOTE
    m.retry_profile = retry_profile
    write_manifest(output, m)
    click.echo(f"After the pass: {after.scored} scored, {after.missing_infrastructure} missing_infrastructure. Manifest: {path}")


# ── manifest ──────────────────────────────────────────────────────────────

@main.command()
@click.option("--output", "-o", required=True, type=click.Path(exists=True, path_type=Path),
              help="Existing submission .jsonl to (re)write a manifest for.")
def manifest(output) -> None:
    """(Re-)write the .manifest.json sidecar for an existing submission
    file, inferring its fields from the file's own rows rather than from
    a live run -- started_at/finished_at are not recoverable after the
    fact and are marked as such."""
    try:
        rows = load_submission(output)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    if not rows:
        raise click.ClickException(f"{output} has no rows to build a manifest from")

    first = rows[0]
    summary = summarize_rows(rows)
    m = RunManifest(
        harness_version=first.harness_version,
        library_version=first.library_version,
        model=first.model,
        framework=first.framework,
        started_at="unknown (manifest regenerated from existing rows, not a live run)",
        finished_at="unknown (manifest regenerated from existing rows, not a live run)",
        trial_count=len(rows),
        include_raw_output=isinstance(first, BenchTrialResultWithRawOutput),
        scored_trials=summary.scored,
        missing_infrastructure=summary.missing_infrastructure,
        missing_by_cell=summary.missing_by_cell,
        retries=summary.retries,
        tool_call_only=summary.tool_call_only,
        missing_trials_excluded=MISSING_EXCLUDED_NOTE,
    )
    path = write_manifest(output, m)
    click.echo(f"Wrote {path}")


# ── validate ──────────────────────────────────────────────────────────────

def _print_report_text(report: ValidationReport) -> None:
    click.echo(f"\n{_BOLD}Validation report{_RESET}")
    if report.completeness:
        c = report.completeness
        click.echo(f"  {c.total_rows} row(s) parsed; "
                   f"{len(c.categories_covered)}/10 categories covered")
        if c.missing_infrastructure:
            click.echo(f"  {_YELLOW}{c.missing_infrastructure} missing_infrastructure row(s) "
                       f"({c.scored_rows} scored); missing trials are excluded from coverage and every aggregate{_RESET}")
        if c.categories_missing:
            click.echo(f"  {_YELLOW}missing: {', '.join(c.categories_missing)}{_RESET}")

    if report.rejections:
        click.echo(f"\n{_RED}{_BOLD}REJECTED{_RESET} ({len(report.rejections)} issue(s)):")
        for issue in report.rejections:
            where = f"row {issue.row_index}" if issue.row_index is not None else "file"
            click.echo(f"  {_RED}✗{_RESET} [{issue.code}] ({where}) {issue.message}")

    if report.flags:
        click.echo(f"\n{_YELLOW}{_BOLD}FLAGGED for review{_RESET} ({len(report.flags)} issue(s)):")
        for issue in report.flags:
            where = f"row {issue.row_index}" if issue.row_index is not None else "file"
            click.echo(f"  {_YELLOW}⚑{_RESET} [{issue.code}] ({where}) {issue.message}")

    click.echo()
    if report.accepted:
        click.echo(f"{_GREEN}✓  Accepted{_RESET}" + (" (with flags -- see above)" if report.flags else ""))
    else:
        click.echo(f"{_RED}✗  Rejected -- fix the issue(s) above and re-validate.{_RESET}")


@main.command("validate")
@click.argument("submission", type=click.Path(exists=True, path_type=Path))
@click.option("--verify-sample", default=None, type=click.Path(exists=True, path_type=Path),
              help="Optional bundle of raw prompt/response pairs to spot-check payload_hash against.")
@click.option("--output", "-o", type=click.Choice(["text", "json"]), default="text", show_default=True)
def validate_cmd(submission, verify_sample, output) -> None:
    """Validate a submission file (schema, keys, prompt ids, payload_hash
    sample, library_version comparability) before opening a PR. Exits
    non-zero if the submission is rejected."""
    report = validate_submission(submission, verify_sample_path=verify_sample)
    if output == "json":
        click.echo(report.model_dump_json(indent=2))
    else:
        _print_report_text(report)
    if not report.accepted:
        sys.exit(1)


# ── compare ───────────────────────────────────────────────────────────────

def _group_by_comparable_library_version(
    entries: list[tuple[Path, str]],
) -> list[list[tuple[Path, str]]]:
    """
    Groups (path, library_version) entries so that within a group, every
    pair's versions are schema.is_library_version_comparable(). Note that
    function is deliberately NOT reflexive for an unregistered version
    (see schema.py / its tests) -- so two submissions claiming the exact
    same but unregistered library_version end up in two separate
    singleton groups here, not merged. That is intentional, not a bug:
    an unregistered version string carries no confirmed guarantee about
    what prompt set a claim of it refers to. (For registered versions,
    equal prompt count is necessary but not sufficient to guarantee an
    identical prompt set -- see schema.is_library_version_comparable() for
    the exact rule.)
    """
    groups: list[list[tuple[Path, str]]] = []
    for path, version in entries:
        for group in groups:
            rep_version = group[0][1]
            if is_library_version_comparable(version, rep_version):
                group.append((path, version))
                break
        else:
            groups.append([(path, version)])
    return groups


@main.command()
@click.argument("submissions", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
def compare(submissions) -> None:
    """
    Compare / rank two or more already-validated submissions.

    Refuses to silently merge submissions whose library_version values
    are not schema.is_library_version_comparable() -- e.g. a 300-prompt
    (1.13.0) submission and agentdojo-x-era 30-prompt (1.0.0/1.1.0) data
    are never combined into one ranking here; each incomparable group is
    reported separately with an explicit banner, never averaged together.

    Uses load_submission() (strict parsing) on each file -- run `validate`
    on a submission first; this command assumes clean input and fails
    loudly on a malformed file rather than silently dropping bad rows
    into a comparison.
    """
    entries: list[tuple[Path, str]] = []
    for path in submissions:
        try:
            rows = load_submission(path)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from None
        if not rows:
            click.echo(f"{_YELLOW}{path}: no rows, skipping{_RESET}")
            continue
        versions = sorted({r.library_version for r in rows})
        if len(versions) > 1:
            click.echo(
                f"{_YELLOW}WARNING: {path} contains mixed library_versions {versions} -- "
                f"this command groups by file using {versions[0]!r} as that file's "
                f"representative version; run `validate` for a full per-row breakdown.{_RESET}"
            )
        entries.append((path, versions[0]))

    if not entries:
        click.echo(f"{_RED}No comparable rows in any submission.{_RESET}")
        sys.exit(1)

    groups = _group_by_comparable_library_version(entries)

    for i, group in enumerate(groups, start=1):
        versions_in_group = sorted({v for _, v in group})
        click.echo(f"\n{_BOLD}Group {i}{_RESET} — library_version(s) {versions_in_group}:")
        if len(group) == 1 and group[0][1] not in KNOWN_LIBRARY_VERSIONS:
            click.echo(
                f"  {_YELLOW}(1 submission; library_version {group[0][1]!r} is not registered in "
                f"KNOWN_LIBRARY_VERSIONS, so it cannot be confirmed comparable to any other "
                f"submission -- including another one claiming the same version string){_RESET}"
            )
        for path, _version in group:
            rows = load_submission(path)
            scored = [r for r in rows if not r.is_missing]
            n = len(scored)
            pass_rate = sum(1 for r in scored if r.verdict.value == "pass") / n if n else 0.0
            model = rows[0].model if rows else "?"
            missing_note = (f", {len(rows) - n} missing_infrastructure excluded" if len(rows) != n else "")
            click.echo(f"  {path}: {n} row(s), model={model}, pass_rate={pass_rate:.1%}{missing_note}")

    if len(groups) > 1:
        click.echo(
            f"\n{_YELLOW}{len(groups)} incomparable group(s) shown above -- "
            f"never averaged or ranked against each other.{_RESET}"
        )


if __name__ == "__main__":
    main()
