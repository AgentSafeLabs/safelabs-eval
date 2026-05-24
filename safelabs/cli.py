"""safelabs/cli.py — command-line interface."""

from __future__ import annotations
import asyncio, json, sys
import click
from safelabs import __version__
from safelabs.prompts.loader import get_library, get_prompts_for_category
from safelabs.prompts.schemas import PromptCategory
from safelabs.scoring.models import VerdictLevel
from safelabs.scoring.scorer import Scorer

_RED="[91m"; _YELLOW="[93m"; _GREEN="[92m"; _CYAN="[96m"; _BOLD="[1m"; _RESET="[0m"
_VERDICT_COLOUR = {VerdictLevel.VULNERABLE:_RED,VerdictLevel.FAIL:_YELLOW,VerdictLevel.UNCERTAIN:_CYAN,VerdictLevel.PASS:_GREEN}
_CAT_EVAL = {"ASI01":"prompt_injection","ASI09":"scope_violation","ASI06":"data_leakage","ASI10":"hallucination","ASI08":"jailbreak"}

@click.group()
@click.version_option(__version__, prog_name="safelabs")
def main(): """safelabs-eval — OWASP ASI red-teaming for AI agents."""

@main.command()
@click.option("--target","-t",required=True)
@click.option("--category","-c",default="ASI01",show_default=True)
@click.option("--timeout",default=30.0,show_default=True)
@click.option("--output","-o",type=click.Choice(["text","json"]),default="text",show_default=True)
@click.option("--auth-header",default=None)
def run(target,category,timeout,output,auth_header):
    """Red-team an agent endpoint with OWASP ASI prompts.

    
    Examples:
      safelabs run --target http://localhost:8000/chat --category ASI01
      safelabs run --target http://localhost:8000/chat --category all --output json
    """
    asyncio.run(_run_async(target,category,timeout,output,auth_header))

async def _run_async(target,category,timeout,output,auth_header):
    from safelabs.agents.http_adapter import HttpAdapter
    headers = {"Authorization": auth_header} if auth_header else {}
    adapter = HttpAdapter(base_url=target, headers=headers, timeout=timeout)
    scorer  = Scorer(); library = get_library()
    prompts = library.entries if category.lower()=="all" else library.by_category(PromptCategory(category.upper()))
    if not prompts: click.echo(f"No prompts for: {category}", err=True); sys.exit(1)
    if output=="text":
        click.echo(f"\n{_BOLD}safelabs-eval v{__version__}{_RESET}")
        click.echo(f"Target  : {target}\nCategory: {category.upper()} ({len(prompts)} prompts)\n" + "─"*60)
    results_out = []
    for entry in prompts:
        eval_type = _CAT_EVAL.get(entry.category.value, "prompt_injection")
        if output=="text":
            click.echo(f"\n{_BOLD}[{entry.id}]{_RESET} {entry.severity.upper()}")
            click.echo(f"Prompt : {entry.prompt[:80]}...")
        response = await adapter.execute(entry.prompt)
        if response.error:
            if output=="text": click.echo(f"{_RED}ERROR{_RESET}: {response.error}")
            results_out.append({"id":entry.id,"error":response.error}); continue
        result = await scorer.score(eval_type, entry.prompt, response.output)
        c = _VERDICT_COLOUR.get(result.verdict, _RESET)
        if output=="text":
            click.echo(f"Verdict: {c}{result.verdict.value.upper()}{_RESET}  (confidence: {result.confidence:.0%})")
            click.echo(f"Reason : {result.reasoning}")
            if result.remediation_hint: click.echo(f"Fix    : {result.remediation_hint}")
        results_out.append({"id":entry.id,"category":entry.category.value,"verdict":result.verdict.value,"confidence":result.confidence,"reasoning":result.reasoning,"latency_ms":response.latency_ms})
    if output=="text": _summary(results_out)
    else: click.echo(json.dumps({"results":results_out},indent=2))

def _summary(results):
    from collections import Counter
    v = Counter(r.get("verdict","error") for r in results); total=len(results)
    click.echo("\n"+"─"*60+f"\n{_BOLD}SUMMARY{_RESET} ({total} prompts)")
    click.echo(f"  {_RED}VULNERABLE{_RESET}: {v.get('vulnerable',0)}")
    click.echo(f"  {_YELLOW}FAIL{_RESET}      : {v.get('fail',0)}")
    click.echo(f"  {_CYAN}UNCERTAIN{_RESET} : {v.get('uncertain',0)}")
    click.echo(f"  {_GREEN}PASS{_RESET}      : {v.get('pass',0)}")
    if v.get('vulnerable',0): click.echo(f"\n{_RED}⚠  {v['vulnerable']} VULNERABLE finding(s){_RESET}")
    elif v.get('fail',0): click.echo(f"\n{_YELLOW}⚠  Review FAIL findings{_RESET}")
    else: click.echo(f"\n{_GREEN}✓  No vulnerabilities detected{_RESET}")

@main.command("list")
def list_categories():
    """List all OWASP ASI categories."""
    library = get_library()
    LABELS = {"ASI01":"Prompt Injection","ASI02":"Insecure Output Handling","ASI03":"Excessive Agency","ASI04":"Resource Management","ASI05":"Tool Use Safety","ASI06":"Data Privacy & Confidentiality","ASI07":"Trust Boundaries","ASI08":"Behavioral Drift","ASI09":"Scope Violations","ASI10":"Hallucination & Misinformation"}
    click.echo(f"\n{_BOLD}OWASP Agentic Security Initiative (ASI) Top 10{_RESET}\n")
    for cat in PromptCategory:
        count = len(library.by_category(cat))
        click.echo(f"  {_BOLD}{cat.value}{_RESET}  {LABELS.get(cat.value,cat.value):<38}  {count} prompts")
    click.echo()

@main.command("prompts")
@click.option("--category","-c",default=None)
@click.option("--severity","-s",default=None)
@click.option("--output","-o",type=click.Choice(["text","json"]),default="text")
def list_prompts(category,severity,output):
    """List prompts from the OWASP ASI library."""
    library = get_library(); entries = library.entries
    if category:
        entries = library.by_category(PromptCategory(category.upper()))
    if severity: entries = [e for e in entries if e.severity==severity.lower()]
    if output=="json": click.echo(json.dumps([e.model_dump() for e in entries],indent=2)); return
    click.echo(f"\n{_BOLD}OWASP ASI Prompt Library — {len(entries)} prompt(s){_RESET}\n")
    for e in entries:
        click.echo(f"  [{e.id}] {e.category.value}  severity={e.severity}")
        click.echo(f"          {e.prompt[:90]}...\n")
