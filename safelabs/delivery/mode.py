"""safelabs/delivery/mode.py: how ``run_eval`` hands a prompt to the target as a document.

A delivery mode turns the library prompt (the payload) into the text the target receives and
describes how it did so. Scoring never sees that text: the runner keeps using the original prompt.

Two modes exist:

``template_delivery``
    Renders the payload into a built-in template with :func:`safelabs.delivery.render`.
``bridge_delivery``
    Sends the payload unchanged and only labels the run with the evaluator's own bridge id and
    version. The label is a claim by the evaluator; nothing here checks what the bridge did.

A prompt the library already tags ``indirect`` is never wrapped: it is sent as it is and the record
says so (``already_indirect``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol

from safelabs.delivery.registry import get_template
from safelabs.delivery.render import POSITIONS, render

# Delivery is written for ASI01 (prompt injection) only. Other categories fail before anything is sent.
SUPPORTED_CATEGORIES = ("ASI01",)

MODE_DIRECT = "direct"
MODE_BRIDGE = "bridge"


@dataclass(frozen=True)
class Prepared:
    """The text to send to the target and the delivery record stored on the result."""

    text: str
    record: dict[str, Any]


class DeliveryMode(Protocol):
    def prepare(self, prompt_id: str, payload: str, *, already_indirect: bool = False) -> Prepared: ...

    def describe(self) -> dict[str, Any]:
        """Run-level description (everything in the record that does not depend on the prompt)."""
        ...


def check_categories(categories: list[str] | tuple[str, ...]) -> None:
    """Raise ``ValueError`` when delivery is requested for a category other than ASI01."""
    other = sorted({c for c in categories if c not in SUPPORTED_CATEGORIES})
    if other:
        raise ValueError(
            f"document delivery supports {', '.join(SUPPORTED_CATEGORIES)} only; "
            f"the selected prompts include {', '.join(other)}"
        )


class _TemplateDelivery:
    def __init__(self, template_id: str, position: str, template_version: int | None):
        if position not in POSITIONS:
            raise ValueError(f"invalid position {position!r}; expected one of: {', '.join(POSITIONS)}")
        template = get_template(template_id, template_version)  # unknown id or version raises ValueError
        self.template_id = template["id"]
        self.template_version = template["version"]
        self.position = position

    def prepare(self, prompt_id: str, payload: str, *, already_indirect: bool = False) -> Prepared:
        if already_indirect:
            record = {
                "mode": MODE_DIRECT,
                "already_indirect": True,
                "skipped_template_id": self.template_id,
                "skipped_template_version": self.template_version,
                "skipped_payload_position": self.position,
            }
            return Prepared(text=payload, record=record)
        rendered = render(
            payload, self.template_id, position=self.position, template_version=self.template_version
        )
        return Prepared(text=rendered.text, record=asdict(rendered.record))

    def describe(self) -> dict[str, Any]:
        record = asdict(
            render("", self.template_id, position=self.position, template_version=self.template_version).record
        )
        record.pop("rendered_sha256")
        return record


class _BridgeDelivery:
    def __init__(self, bridge_id: str, bridge_version: str):
        if not bridge_id or not bridge_version:
            raise ValueError("a bridge label needs both an id and a version")
        self.bridge_id = bridge_id
        self.bridge_version = bridge_version

    def prepare(self, prompt_id: str, payload: str, *, already_indirect: bool = False) -> Prepared:
        record = self.describe()
        if already_indirect:
            record["already_indirect"] = True
        return Prepared(text=payload, record=record)

    def describe(self) -> dict[str, Any]:
        return {
            "mode": MODE_BRIDGE,
            "bridge_id": self.bridge_id,
            "bridge_version": self.bridge_version,
            "declared": True,
        }


def template_delivery(
    template_id: str, *, position: str, template_version: int | None = None
) -> DeliveryMode:
    """Deliver each prompt inside a built-in template. ``position`` has no default.

    Raises ``ValueError`` for an unknown template id or version or an invalid position."""
    return _TemplateDelivery(template_id, position, template_version)


def bridge_delivery(bridge_id: str, bridge_version: str) -> DeliveryMode:
    """Send prompts unchanged and label the run with the evaluator's declared bridge."""
    return _BridgeDelivery(bridge_id, bridge_version)
