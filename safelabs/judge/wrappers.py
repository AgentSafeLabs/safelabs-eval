"""safelabs/judge/wrappers.py: refusal-prefix and refusal-suffix variants of packet items.

The wording is read from a file at runtime (no wording is bundled): a JSON list of objects
``{"name": ..., "position": "prefix" | "suffix", "text": ...}``. Controls (neutral text) are just
more entries in the file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from safelabs.judge.packet import PacketItem


@dataclass(frozen=True)
class Wording:
    name: str
    position: str
    text: str


def load_wordings(path: str | Path) -> list[Wording]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out, seen = [], set()
    for d in data:
        w = Wording(str(d["name"]), str(d["position"]), str(d["text"]))
        if w.position not in ("prefix", "suffix"):
            raise ValueError(f"position must be prefix or suffix, got {w.position!r}")
        if not w.name or w.name in seen or "::" in w.name:
            raise ValueError(f"wording names must be unique, non-empty and not contain '::': {w.name!r}")
        seen.add(w.name)
        out.append(w)
    return out


def make_variants(items: list[PacketItem], wordings: list[Wording], *, only_ids: set[str] | None = None, separator: str = "\n\n") -> list[PacketItem]:
    out = []
    for it in items:
        if only_ids is not None and it.item_id not in only_ids:
            continue
        for w in wordings:
            resp = f"{w.text}{separator}{it.response}" if w.position == "prefix" else f"{it.response}{separator}{w.text}"
            out.append(replace(it, item_id=f"{it.item_id}::{w.name}", response=resp, base_item_id=it.item_id, variant=w.name, human_label=dict(it.human_label)))
    return out
