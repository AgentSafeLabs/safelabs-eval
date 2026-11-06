"""safelabs/delivery/registry.py: template lookup and pinned checksums.

The checksum of a template is the sha256 of its canonical JSON: keys sorted,
no whitespace, non-ASCII kept as is, encoded as UTF-8. ``PINNED_SHA256`` holds
the expected checksum for every released ``(id, version)``. ``get_template``
refuses a template whose data no longer matches, so editing a released
template without adding a new version cannot go unnoticed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from safelabs.delivery.templates import REFERRAL_LETTER_V1, SUPPORT_TICKET_V1

TEMPLATES: dict[tuple[str, int], dict[str, Any]] = {
    (t["id"], t["version"]): t for t in (REFERRAL_LETTER_V1, SUPPORT_TICKET_V1)
}

PINNED_SHA256: dict[tuple[str, int], str] = {
    ("referral-letter", 1): "b29ce83995a3c32846b53138118bc49a9979d03e8713106a8b7f168c226b8046",
    ("support-ticket", 1): "fdc669feb78106a74c4cad136d6549cd8dfa9c4ed31463a6292c59b126215faf",
}


@dataclass(frozen=True)
class TemplateInfo:
    id: str
    version: int
    sha256: str
    description: str


def canonical_json(data: Any) -> str:
    """Canonical serialisation used for checksums."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def template_sha256(template: dict[str, Any]) -> str:
    """sha256 (hex) of the canonical JSON of ``template``, encoded as UTF-8."""
    return hashlib.sha256(canonical_json(template).encode("utf-8")).hexdigest()


def get_template(template_id: str, version: int | None = None) -> dict[str, Any]:
    """Return the template data for ``template_id`` (latest version when ``version`` is None).

    Raises ``ValueError`` for an unknown id or version, and for a template whose
    data does not match its pinned checksum."""
    versions = sorted(v for (tid, v) in TEMPLATES if tid == template_id)
    if not versions:
        known = ", ".join(sorted({tid for tid, _ in TEMPLATES}))
        raise ValueError(f"unknown template id {template_id!r}; known ids: {known}")
    if version is None:
        version = versions[-1]
    elif version not in versions:
        raise ValueError(
            f"unknown version {version!r} for template {template_id!r}; "
            f"known versions: {', '.join(str(v) for v in versions)}"
        )
    template = TEMPLATES[(template_id, version)]
    expected = PINNED_SHA256.get((template_id, version))
    actual = template_sha256(template)
    if expected != actual:
        raise ValueError(
            f"template {template_id!r} v{version} does not match its pinned checksum "
            f"(expected {expected}, got {actual}); bump the version instead of editing it"
        )
    return template


def list_templates() -> list[TemplateInfo]:
    """All released templates, sorted by id then version."""
    return [
        TemplateInfo(tid, v, template_sha256(TEMPLATES[(tid, v)]), TEMPLATES[(tid, v)]["description"])
        for (tid, v) in sorted(TEMPLATES)
    ]
