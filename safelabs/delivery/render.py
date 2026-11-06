"""safelabs/delivery/render.py: pure rendering of a payload into a document template.

``render`` has no I/O, no network, no clock and no randomness. The payload is
placed verbatim: no wrapping, escaping, quoting or truncation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from safelabs.delivery.registry import get_template, template_sha256

POSITIONS = ("start", "middle", "end")
RECENCIES = ("oldest", "middle", "newest")
MODE_TEMPLATE = "template"


@dataclass(frozen=True)
class DeliveryRecord:
    """What a result should say about how the payload was delivered."""

    mode: str
    template_id: str
    template_version: int
    template_sha256: str
    payload_position: str
    payload_recency: str
    rendered_sha256: str


@dataclass(frozen=True)
class Rendered:
    text: str
    record: DeliveryRecord


def _slot(position: str, count: int) -> int:
    """Index of the payload in reading order for ``count`` entries in total."""
    return {"start": 0, "middle": count // 2, "end": count - 1}[position]


def _recency_for(position: str, order: str) -> str:
    """Which date the payload must carry so that it agrees with where it is shown."""
    if position == "middle":
        return "middle"
    first_is_oldest = order == "oldest_first"
    if position == "start":
        return "oldest" if first_is_oldest else "newest"
    return "newest" if first_is_oldest else "oldest"


def derive_recency(payload_date: str, filler_dates: list[str]) -> str:
    """Recency of the payload entry, computed from the dates (ISO strings sort correctly)."""
    if payload_date > max(filler_dates):
        return "newest"
    if payload_date < min(filler_dates):
        return "oldest"
    return "middle"


def render(payload: str, template_id: str, *, position: str, template_version: int | None = None) -> Rendered:
    """Place ``payload`` into a template at ``position`` and describe the result.

    ``position`` is ``"start"``, ``"middle"`` or ``"end"`` of the document's reading order.
    ``template_version`` defaults to the latest version of the template. Raises
    ``ValueError`` for an unknown template id or version, or an invalid position, and
    ``TypeError`` when ``payload`` is not a string."""
    if not isinstance(payload, str):
        raise TypeError(f"payload must be a str, got {type(payload).__name__}")
    if position not in POSITIONS:
        raise ValueError(f"invalid position {position!r}; expected one of: {', '.join(POSITIONS)}")
    template = get_template(template_id, template_version)

    order: str = template["order"]
    filler: list[dict[str, Any]] = list(template["entries"])  # stored oldest first
    reading = list(filler) if order == "oldest_first" else filler[::-1]

    date = template["payload_entry"]["dates"][_recency_for(position, order)]
    payload_entry = {"date": date, "label": template["payload_entry"]["label"], "body": payload}
    reading.insert(_slot(position, len(reading) + 1), payload_entry)

    entries_text = template["entry_separator"].join(
        template["entry_format"].format(date=e["date"], label=e["label"], body=e["body"]) for e in reading
    )
    text = template["layout"].format(
        task_instruction=template["task_instruction"],
        header=template["header"],
        entries=entries_text,
        footer=template["footer"],
    )
    record = DeliveryRecord(
        mode=MODE_TEMPLATE,
        template_id=template["id"],
        template_version=template["version"],
        template_sha256=template_sha256(template),
        payload_position=position,
        payload_recency=derive_recency(date, [e["date"] for e in filler]),
        rendered_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    return Rendered(text=text, record=record)
