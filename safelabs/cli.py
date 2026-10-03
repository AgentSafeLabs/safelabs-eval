"""safelabs/cli.py — command-line interface."""

from __future__ import annotations
import asyncio
import json
import logging
import sys
import click
from safelabs import __version__

logger = logging.getLogger(__name__)
from safelabs.prompts.loader import get_library
from safelabs.prompts.schemas import PromptCategory
from safelabs.scoring.models import VerdictLevel
from safelabs.runner import run_eval

_RED="[91m"; _YELLOW="[93m"; _GREEN="[92m"; _CYAN="[96m"; _BOLD="[1m"; _RESET="[0m"
_VERDICT_COLOUR = {VerdictLevel.VULNERABLE:_RED,VerdictLevel.FAIL:_YELLOW,VerdictLevel.UNCERTAIN:_CYAN,VerdictLevel.PASS:_GREEN}

@click.group()
@click.version_option(__version__, prog_name="safelabs")
def main(): """safelabs-eval — ASI-category red-teaming for AI agents."""

@main.command()
@click.option("--target","-t",required=True)
@click.option("--category","-c",default="ASI01",show_default=True)
@click.option("--timeout",default=30.0,show_default=True)
@click.option("--output","-o",type=click.Choice(["text","json"]),default="text",show_default=True)
@click.option("--auth-header",default=None)
@click.option("--retry-profile",default="default",show_default=True,type=click.Choice(["default","benchmark"]),
              help="Retries for infrastructure errors (rate limit, timeout, outage). default: 3 attempts, 1 s base delay; "
                   "benchmark: 6 attempts, 2 s. The flags below override the profile.")
@click.option("--max-attempts",default=None,type=click.IntRange(min=1),help="Attempts per prompt for infrastructure errors; 1 disables retries.")
@click.option("--retry-base-delay-s",default=None,type=click.FloatRange(min=0.0),help="First backoff delay in seconds (doubles each retry, with jitter).")
def run(target,category,timeout,output,auth_header,retry_profile,max_attempts,retry_base_delay_s):
    """Red-team an agent endpoint with ASI-category prompts.

    Prompts run through safelabs.runner.run_eval(). An infrastructure error (rate limit,
    timeout, connection error, provider outage) is retried; if it persists the prompt is
    reported as MISSING and excluded from the verdict counts. Any other error is reported as
    ERROR and is not scored.

    Examples:
      safelabs run --target http://localhost:8000/chat --category ASI01
      safelabs run --target http://localhost:8000/chat --category all --output json
      safelabs run --target http://localhost:8000/chat --category all --retry-profile benchmark
    """
    asyncio.run(_run_async(target,category,timeout,output,auth_header,
                           retry_profile=retry_profile,max_attempts=max_attempts,retry_base_delay_s=retry_base_delay_s))

def _result_row(rec):
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
    return row

async def _run_async(target,category,timeout,output,auth_header,retry_profile="default",max_attempts=None,retry_base_delay_s=None):
    from safelabs.agents.http_adapter import HttpAdapter
    headers = {"Authorization": auth_header} if auth_header else {}
    adapter = HttpAdapter(base_url=target, headers=headers, timeout=timeout)
    library = get_library()
    prompts = library.entries if category.lower()=="all" else library.by_category(PromptCategory(category.upper()))
    if not prompts: click.echo(f"No prompts for: {category}", err=True); sys.exit(1)
    if output=="text":
        click.echo(f"\n{_BOLD}safelabs-eval v{__version__}{_RESET}")
        click.echo(f"Target  : {target}\nCategory: {category.upper()} ({len(prompts)} prompts)\n" + "─"*60)

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

    result = await run_eval(
        adapter.execute, categories=None if category.lower()=="all" else [category.upper()],
        retry_profile=retry_profile, max_attempts=max_attempts, base_delay_s=retry_base_delay_s,
        on_start=on_start, on_record=on_record,
    )
    results_out = [_result_row(rec) for rec in result.records]
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
