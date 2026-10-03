"""safelabs/judge/cli.py: ``safelabs-judge replay | analyze``. Offline only: there is no live-run command."""

from __future__ import annotations

import asyncio
import json
import sys

import click

from safelabs.judge import analysis as A
from safelabs.judge import packet as P
from safelabs.judge.backends import ReplayBackend
from safelabs.judge.cache import JudgeCache
from safelabs.judge.hybrid import MODES, HybridScorer
from safelabs.judge.result import CacheMiss
from safelabs.judge.template import load_template
from safelabs.judge.wrappers import load_wordings, make_variants


def _parse_condition(spec: str) -> tuple[str, str, float]:
    name, _, frac = spec.partition(":")
    if name not in MODES:
        raise click.BadParameter(f"condition must be one of {MODES}, optionally hybrid_plus:<fraction>")
    return spec, name, float(frac) if frac else 0.0


@click.group()
def main() -> None:
    """Offline JudgeCal tools."""


@main.command()
@click.option("--rater1", required=True, type=click.Path(exists=True), help="rater 1 CSV (prompt, response, functional_content)")
@click.option("--rater2", type=click.Path(exists=True), help="rater 2 CSV (adds its functional_content flags)")
@click.option("--key", required=True, type=click.Path(exists=True), help="key CSV (item_id, category)")
@click.option("--cache", required=True, type=click.Path(exists=True), help="judge cache JSONL to replay")
@click.option("--template", required=True, type=click.Path(exists=True))
@click.option("--backend-id", required=True)
@click.option("--condition", "conditions", multiple=True, required=True, help="pattern_only | judge_only | hybrid | hybrid_plus:<fraction>")
@click.option("--seed", default=42, show_default=True)
@click.option("--wordings", type=click.Path(exists=True), help="wrapper wording JSON; adds wrapped variants")
@click.option("--wrap-ids", type=click.Path(exists=True), help="file of item ids to wrap (default: all)")
@click.option("--out", required=True, type=click.Path())
def replay(rater1, rater2, key, cache, template, backend_id, conditions, seed, wordings, wrap_ids, out) -> None:
    """Score a packet from cached judge outputs. A cache miss stops the run."""
    files = {"rater1": P.read_csv(rater1)}
    if rater2:
        files["rater2"] = P.read_csv(rater2)
    items = P.build_items(files, P.read_csv(key))
    if wordings:
        ids = {l.strip() for l in open(wrap_ids) if l.strip()} if wrap_ids else None
        items = items + make_variants(items, load_wordings(wordings), only_ids=ids)
    tpl = load_template(template)
    backend = ReplayBackend(JudgeCache(cache), backend_id, tpl.template_hash)

    async def run() -> list[dict]:
        rows = []
        for spec in conditions:
            name_spec, mode, frac = _parse_condition(spec)
            sc = HybridScorer(None if mode == "pattern_only" else backend, mode=mode, audit_fraction=frac, seed=seed)
            rows += await P.score_items(sc, items, condition=name_spec)
        return rows

    try:
        rows = asyncio.run(run())
    except CacheMiss as exc:
        click.echo(f"cache miss (replay needs every judged item cached): {exc}", err=True)
        sys.exit(2)
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    click.echo(f"wrote {len(rows)} decisions to {out}")


@main.command()
@click.option("--rater1", required=True, type=click.Path(exists=True))
@click.option("--rater2", type=click.Path(exists=True))
@click.option("--key", required=True, type=click.Path(exists=True))
@click.option("--decisions", required=True, type=click.Path(exists=True))
@click.option("--adjudicated", type=click.Path(exists=True), help="CSV with item_id,label for overlap disagreements")
@click.option("--prices", type=click.Path(exists=True), help="JSON: backend_id -> {input_per_mtok, output_per_mtok}")
@click.option("--min-class", default=10, show_default=True)
@click.option("--out", required=True, type=click.Path())
def analyze(rater1, rater2, key, decisions, adjudicated, prices, min_class, out) -> None:
    """Metrics, agreement, cost and robustness from decisions and human labels."""
    r1 = P.load_labels(P.read_csv(rater1))
    r2 = P.load_labels(P.read_csv(rater2)) if rater2 else None
    adj = {r["item_id"]: r["label"].strip().upper() for r in P.read_csv(adjudicated)} if adjudicated else None
    rep = A.analyze(rater1=r1, rater2=r2, key={r["item_id"]: r for r in P.read_csv(key)}, decisions=A.load_decisions(decisions),
                    adjudicated=adj, prices=json.load(open(prices)) if prices else None, min_class=min_class)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, sort_keys=True)
    click.echo(f"wrote {out}; pending adjudication: {len(rep['pending_adjudication'])}")
