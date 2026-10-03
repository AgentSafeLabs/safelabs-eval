"""safelabs/judge/packet.py: read a labelling packet (rater CSVs plus the key) and score it offline.

A packet item needs only item_id, prompt, response (rater file) and category (key). The judge never
sees the model, framework or detector verdict from the key. ``functional_content`` is Y if any
supplied rater file says Y; a blank value counts as Y (never sent to an external backend) unless
``blank_is_y`` is False.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

csv.field_size_limit(2**30)


@dataclass
class PacketItem:
    item_id: str
    prompt: str
    response: str
    category: str
    functional_content: bool = True
    base_item_id: str = ""
    variant: str = "orig"
    human_label: dict = field(default_factory=dict)  # rater name -> label

    def __post_init__(self) -> None:
        self.base_item_id = self.base_item_id or self.item_id


def read_csv(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_labels(rows: list[dict]) -> dict[str, str]:
    """item_id -> label (blank labels are dropped)."""
    return {r["item_id"]: r["label"].strip().upper() for r in rows if (r.get("label") or "").strip()}


def build_items(rater_files: dict[str, list[dict]], key_rows: list[dict], *, blank_is_y: bool = True) -> list[PacketItem]:
    """rater_files: name -> rows. The first file supplies prompt and response."""
    key = {r["item_id"]: r for r in key_rows}
    first = next(iter(rater_files.values()))
    out = []
    for r in first:
        iid = r["item_id"]
        flags = []
        for rows in rater_files.values():
            for rr in rows:
                if rr["item_id"] == iid:
                    flags.append((rr.get("functional_content") or "").strip().upper())
        y = any(f == "Y" for f in flags) or (blank_is_y and any(f == "" for f in flags))
        it = PacketItem(iid, r["prompt"], r["response"], key[iid]["category"], functional_content=y)
        for name, rows in rater_files.items():
            lab = load_labels(rows).get(iid)
            if lab:
                it.human_label[name] = lab
        out.append(it)
    return out


async def score_items(scorer, items: list[PacketItem], *, condition: str, system_prompt: str | None = None) -> list[dict]:
    """Run a HybridScorer (or any scorer with ``score_with_decision``) over the items, in order.
    Returns decision rows (ids, verdicts and judge metadata only; no response text)."""
    from safelabs.runner import CATEGORY_EVAL_TYPE
    out = []
    for it in items:
        eval_type = CATEGORY_EVAL_TYPE.get(it.category, "prompt_injection")
        md = {"item_id": it.item_id, "category": it.category, "functional_content": it.functional_content}
        if system_prompt is not None:
            md["system_prompt"] = system_prompt
        res, dec = await scorer.score_with_decision(eval_type, it.prompt, it.response, md)
        dec.update({"condition": condition, "variant": it.variant, "base_item_id": it.base_item_id})
        out.append(dec)
    return out
