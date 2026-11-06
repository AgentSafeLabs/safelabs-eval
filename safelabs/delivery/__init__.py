"""safelabs.delivery: deterministic document templates for indirect-injection delivery.

Pure rendering only. Nothing in the rest of the package imports this yet.
"""

from safelabs.delivery.registry import TemplateInfo, get_template, list_templates, template_sha256
from safelabs.delivery.render import POSITIONS, DeliveryRecord, Rendered, render

__all__ = [
    "POSITIONS",
    "DeliveryRecord",
    "Rendered",
    "TemplateInfo",
    "get_template",
    "list_templates",
    "render",
    "template_sha256",
]
