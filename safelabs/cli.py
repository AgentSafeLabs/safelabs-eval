"""safelabs/cli.py — command-line interface."""

from __future__ import annotations
import asyncio
import json
import logging
import sys
from pathlib import Path
import click
from safelabs import __version__

logger = logging.getLogger(__name__)
from safelabs.prompts.loader import get_library
from safelabs.prompts.schemas import PromptCategory
from safelabs.scoring.models import VerdictLevel
from safelabs.scoring.schema_mode import SchemaModeError
from safelabs.scoring.scorer import Scorer
from safelabs.runner import PromptSelectionError, run_eval, select_prompts

_RED="[91m"; _YELLOW="[93m"; _GREEN="[92m"; _CYAN="[96m"; _BOLD="[1m"; _RESET="[0m"
_VERDICT_COLOUR = {VerdictLevel.VULNERABLE:_RED,VerdictLevel.FAIL:_YELLOW,VerdictLevel.UNCERTAIN:_CYAN,VerdictLevel.PASS:_GREEN}

@click.group()
@click.version_option(__version__, prog_name="safelabs")
def main(): """safelabs-eval — ASI-category red-teaming for AI agents."""

@main.command()
@click.option("--target","-t",required=True)
@click.option("--category","-c",default="ASI01",show_default=True)
@click.option("--prompt-id","prompt_ids",multiple=True,help="Run only this prompt ID (e.g. ASI01-018); repeatable, case-insensitive. Without an explicit --category the IDs may come from any category; with one, every ID must belong to it.")
@click.option("--limit",default=None,type=click.IntRange(min=1),help="Run only the first N prompts (library order), applied after --category and --prompt-id.")
@click.option("--timeout",default=30.0,show_default=True)
@click.option("--output","-o",type=click.Choice(["text","json"]),default="text",show_default=True)
@click.option("--auth-header",default=None)
@click.option("--retry-profile",default="default",show_default=True,type=click.Choice(["default","benchmark"]),
              help="Retries for infrastructure errors (rate limit, timeout, outage). default: 3 attempts, 1 s base delay; "
                   "benchmark: 6 attempts, 2 s. The flags below override the profile.")
@click.option("--no-echo-aware",is_flag=True,default=False,help="Count indicator matches even inside text the agent copied from the prompt (the scoring used before echo-aware discounting).")
@click.option("--output-schema",default=None,type=click.Path(dir_okay=False),help="JSON file declaring the shape of a structured-output agent's reply (a JSON Schema subset with x-safelabs-action fields). Turns on schema-aware verdicts; see docs/schema-aware-mode.md.")
@click.option("--system-prompt-file",default=None,type=click.Path(dir_okay=False),help="Text file with the agent's system prompt; with --output-schema, a response that repeats it is VULNERABLE.")
@click.option("--include-responses",is_flag=True,default=False,help="Add each agent response to the JSON output (--output json). Responses to adversarial prompts may contain harmful content; do not commit or share results files that contain responses without reviewing them.")
@click.option("--max-attempts",default=None,type=click.IntRange(min=1),help="Attempts per prompt for infrastructure errors; 1 disables retries.")
@click.option("--retry-base-delay-s",default=None,type=click.FloatRange(min=0.0),help="First backoff delay in seconds (doubles each retry, with jitter).")
def run(target,category,prompt_ids,limit,timeout,output,auth_header,retry_profile,no_echo_aware,output_schema,system_prompt_file,include_responses,max_attempts,retry_base_delay_s):
    """Red-team an agent endpoint with ASI-category prompts.

    Prompts run through safelabs.runner.run_eval(). An infrastructure error (rate limit,
    timeout, connection error, provider outage) is retried; if it persists the prompt is
    reported as MISSING and excluded from the verdict counts. Any other error is reported as
    ERROR and is not scored.

    Examples:
      safelabs run --target http://localhost:8000/chat --category ASI01
      safelabs run --target http://localhost:8000/chat --category all --output json
      safelabs run --target http://localhost:8000/chat --category all --retry-profile benchmark
      safelabs run --target http://localhost:8000/chat --prompt-id ASI01-018 --prompt-id ASI03-002
      safelabs run --target http://localhost:8000/chat --category ASI01 --limit 3
    """
    category_explicit = click.get_current_context().get_parameter_source("category") != click.core.ParameterSource.DEFAULT
    asyncio.run(_run_async(target,category,timeout,output,auth_header,
                           prompt_ids=list(prompt_ids),limit=limit,category_explicit=category_explicit,
                           retry_profile=retry_profile,max_attempts=max_attempts,retry_base_delay_s=retry_base_delay_s,
                           echo_aware=not no_echo_aware,include_responses=include_responses,
                           output_schema=output_schema,system_prompt_file=system_prompt_file))

def _result_row(rec,include_responses=False):
    """One entry of the CLI's results list for an EvalRecord. Normal scored rows and unscored error rows keep the
    shape they always had; a missing row and the rare retried or tool-call-only row add keys."""
    if rec.is_missing:
        return {"id":rec.prompt_id,"status":"missing_infrastructure","error":rec.error,"error_class":rec.error_class,
                "error_subclass":rec.error_subclass,"attempts":rec.attempts}
    if rec.error:
        return {"id":rec.prompt_id,"error":rec.error}
    s = rec.scoring_result
    row = {"id":rec.prompt_id,"category":rec.category,"verdict":s.verdict.value,"confidence":s.confidence,"reasoning":s.reasoning,"latency_ms":rec.latency_ms}
    if rec.tool_call_only: row["tool_call_only"] = True
    if rec.attempts and rec.attempts > 1: row["attempts"] = rec.attempts
    row["indicators"] = list(s.indicators)
    row["echoed_indicators"] = list(s.echoed)
    if include_responses: row["response"] = rec.response
    return row

async def _run_async(target,category,timeout,output,auth_header,prompt_ids=None,limit=None,category_explicit=True,retry_profile="default",max_attempts=None,retry_base_delay_s=None,echo_aware=True,include_responses=False,output_schema=None,system_prompt_file=None):
    scorer = None
    if output_schema or system_prompt_file:
        try:
            if system_prompt_file and not output_schema: raise SchemaModeError("--system-prompt-file requires --output-schema")
            system_prompt = None
            if system_prompt_file:
                try: system_prompt = Path(system_prompt_file).read_text(encoding="utf-8-sig")
                except OSError as exc: raise SchemaModeError(f"cannot read system prompt file {system_prompt_file}: {exc.strerror or exc}") from exc
            scorer = Scorer(echo_aware=echo_aware,output_schema=output_schema,system_prompt=system_prompt)
        except SchemaModeError as exc:
            click.echo(f"Error: {exc}", err=True); sys.exit(2)
    from safelabs.agents.http_adapter import HttpAdapter
    headers = {"Authorization": auth_header} if auth_header else {}
    adapter = HttpAdapter(base_url=target, headers=headers, timeout=timeout)
    # --prompt-id with the default --category may pick IDs from any category; an explicit --category restricts them.
    all_categories = category.lower()=="all" or (bool(prompt_ids) and not category_explicit)
    categories = None if all_categories else [category.upper()]
    try:
        prompts = select_prompts(categories, prompt_ids, limit)
    except PromptSelectionError as exc:
        click.echo(f"Error: {exc}", err=True); sys.exit(2)
    if not prompts: click.echo(f"No prompts for: {category}", err=True); sys.exit(1)
    cat_label = "ALL" if all_categories else category.upper()
    if output=="text":
        click.echo(f"\n{_BOLD}safelabs-eval v{__version__}{_RESET}")
        click.echo(f"Target  : {target}\nCategory: {cat_label} ({len(prompts)} prompts)\n" + "─"*60)

    def on_start(entry):
        if output=="text":
            click.echo(f"\n{_BOLD}[{entry.id}]{_RESET} {entry.severity.upper()}")
            click.echo(f"Prompt : {entry.prompt[:80]}...")

    def on_record(rec):
        if output!="text": return
        if rec.is_missing:
            short_err = (rec.error or "").splitlines()[0][:120] if rec.error else ""
            click.echo(f"  {_RED}MISSING{_RESET} — {rec.error_subclass}, {rec.attempts} attempt(s), excluded from the counts: {short_err}")
        elif rec.error:
            short_err = rec.error.splitlines()[0][:120]
            logger.debug("agent error for %s: %s", rec.prompt_id, rec.error)
            click.echo(f"  {_RED}ERROR{_RESET} — could not reach {target}: {short_err}")
        else:
            s = rec.scoring_result
            c = _VERDICT_COLOUR.get(s.verdict, _RESET)
            flag = "  [tool-call-only]" if rec.tool_call_only else ""
            click.echo(f"Verdict: {c}{s.verdict.value.upper()}{_RESET}  (confidence: {s.confidence:.0%}){flag}")
            click.echo(f"Reason : {s.reasoning}")
            if s.remediation_hint: click.echo(f"Fix    : {s.remediation_hint}")

    if scorer is None and not echo_aware: scorer = Scorer(echo_aware=False)  # default Scorer() is echo-aware
    extra = {} if scorer is None else {"scorer": scorer}
    result = await run_eval(
        adapter.execute, categories=categories,
        prompt_ids=prompt_ids or None, limit=limit,
        retry_profile=retry_profile, max_attempts=max_attempts, base_delay_s=retry_base_delay_s,
        on_start=on_start, on_record=on_record, **extra,
    )
    results_out = [_result_row(rec,include_responses) for rec in result.records]
    if output=="text": _summary(results_out)
    else: click.echo(json.dumps({"results":results_out},indent=2))

def _summary(results):
    from collections import Counter
    missing = sum(1 for r in results if r.get("status")=="missing_infrastructure")
    errors = sum(1 for r in results if "error" in r and "verdict" not in r) - missing
    retries = sum((r.get("attempts") or 1) - 1 for r in results)
    v = Counter(r.get("verdict") for r in results if "verdict" in r); total=len(results)
    click.echo("\n"+"─"*60+f"\n{_BOLD}SUMMARY{_RESET} ({total} prompts)")
    click.echo(f"  {_RED}VULNERABLE{_RESET}: {v.get('vulnerable',0)}")
    click.echo(f"  {_YELLOW}FAIL{_RESET}      : {v.get('fail',0)}")
    click.echo(f"  {_CYAN}UNCERTAIN{_RESET} : {v.get('uncertain',0)}")
    click.echo(f"  {_GREEN}PASS{_RESET}      : {v.get('pass',0)}")
    if errors: click.echo(f"  {_RED}ERRORS{_RESET}    : {errors}")
    if missing or retries:
        click.echo(f"  MISSING   : {missing} (missing_infrastructure)")
        click.echo(f"  RETRIES   : {retries} extra attempt(s)")
    if missing: click.echo(f"  {_YELLOW}Note: missing_infrastructure prompts persisted as infrastructure errors after every retry and are excluded from the counts above.{_RESET}")
    if errors + missing == total: click.echo(f"\n{_RED}⚠  All prompts errored — agent endpoint was not reachable{_RESET}")
    elif v.get('vulnerable',0): click.echo(f"\n{_RED}⚠  {v['vulnerable']} VULNERABLE finding(s){_RESET}")
    elif v.get('fail',0): click.echo(f"\n{_YELLOW}⚠  Review FAIL findings{_RESET}")
    elif missing: click.echo(f"\n{_GREEN}✓  No vulnerabilities detected{_RESET} among the {total-missing-errors} scored prompts ({missing} missing prompt(s) excluded)")
    else: click.echo(f"\n{_GREEN}✓  No vulnerabilities detected{_RESET}")

@main.command("list")
def list_categories():
    """List all ASI categories."""
    library = get_library()
    LABELS = {"ASI01":"Prompt Injection","ASI02":"Insecure Output Handling","ASI03":"Excessive Agency","ASI04":"Resource Management","ASI05":"Tool Use Safety","ASI06":"Data Privacy & Confidentiality","ASI07":"Trust Boundaries","ASI08":"Behavioral Drift","ASI09":"Scope Violations","ASI10":"Hallucination & Misinformation"}
    click.echo(f"\n{_BOLD}ASI-category taxonomy (OWASP-inspired){_RESET}\n")
    for cat in PromptCategory:
        count = len(library.by_category(cat))
        click.echo(f"  {_BOLD}{cat.value}{_RESET}  {LABELS.get(cat.value,cat.value):<38}  {count} prompts")
    click.echo()

@main.command("prompts")
@click.option("--category","-c",default=None)
@click.option("--severity","-s",default=None)
@click.option("--output","-o",type=click.Choice(["text","json"]),default="text")
def list_prompts(category,severity,output):
    """List prompts from the ASI library."""
    library = get_library(); entries = library.entries
    if category:
        entries = library.by_category(PromptCategory(category.upper()))
    if severity: entries = [e for e in entries if e.severity==severity.lower()]
    if output=="json": click.echo(json.dumps([e.model_dump() for e in entries],indent=2)); return
    click.echo(f"\n{_BOLD}ASI Prompt Library — {len(entries)} prompt(s){_RESET}\n")
    for e in entries:
        click.echo(f"  [{e.id}] {e.category.value}  severity={e.severity}")
        click.echo(f"          {e.prompt[:90]}...\n")
