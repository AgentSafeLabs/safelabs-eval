"""safelabs/judge/template.py: judge prompt templates loaded from a file at runtime (never bundled).

File format: a Markdown file with two level-2 headings, ``## SYSTEM`` and ``## USER``. The USER
section must contain the placeholders ``{{prompt}}`` and ``{{response}}``; ``{{category}}`` is
optional (a template without it never shows the category to the judge). The template hash is the
SHA-256 of the whole file's bytes, so any edit changes it.

Marker safety: tokens in the template that look like block markers (``<<<NAME`` or ``NAME>>>``,
upper-case letters, digits and underscores) are collected in ``JudgeTemplate.markers``. When the
prompt, response or category text is inserted, any exact occurrence of those markers inside it is
replaced by a visibly altered form, so inserted text cannot close a block early or open a fake one.
The count is returned by ``render_with_info`` and recorded in ``JudgeResult.meta``. The cache key
uses the original, un-altered text.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

PLACEHOLDERS = ("prompt", "response", "category")
REQUIRED_PLACEHOLDERS = ("prompt", "response")
_MARKER_RE = re.compile(r"<<<[A-Z0-9_]+|[A-Z0-9_]+>>>")
_PLACEHOLDER_RE = re.compile(r"\{\{(prompt|response|category)\}\}")
_HEADING_RE = re.compile(r"(?m)^##[ \t]+(SYSTEM|USER)[ \t]*$")


class TemplateError(ValueError):
    """The template file is missing a section or a placeholder."""


@dataclass(frozen=True)
class JudgeTemplate:
    system: str
    user: str
    template_hash: str
    path: str = ""
    markers: tuple[str, ...] = ()

    def render_with_info(self, *, prompt: str, response: str, category: str) -> tuple[str, str, dict]:
        """Return (system, user, info). One regex pass, so inserted text is never re-substituted;
        exact marker strings inside the inserted text are altered first (see the module docstring).
        ``info["neutralized_markers"]`` counts the alterations."""
        count = 0

        def clean(text: str) -> str:
            nonlocal count
            for m in sorted(self.markers, key=len, reverse=True):
                n = text.count(m)
                if n:
                    count += n
                    text = text.replace(m, alter_marker(m))
            return text

        values = {"prompt": clean(prompt), "response": clean(response), "category": clean(category)}
        user = _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], self.user)
        return self.system, user, {"neutralized_markers": count}

    def render(self, *, prompt: str, response: str, category: str) -> tuple[str, str]:
        """Return (system, user); see ``render_with_info``."""
        system, user, _ = self.render_with_info(prompt=prompt, response=response, category=category)
        return system, user


def alter_marker(marker: str) -> str:
    """A visibly altered form of a marker that no longer contains ``<<<`` or ``>>>``."""
    return "[altered marker " + marker.replace("<", "(").replace(">", ")") + "]"


def parse_template(data: bytes, path: str = "") -> JudgeTemplate:
    text = data.decode("utf-8")
    marks = list(_HEADING_RE.finditer(text))
    sections: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        if m.group(1) in sections:
            raise TemplateError(f"duplicate section {m.group(1)}")
        sections[m.group(1)] = text[m.end():end].strip()
    for need in ("SYSTEM", "USER"):
        if not sections.get(need):
            raise TemplateError(f"missing or empty section: {need}")
    found = set(_PLACEHOLDER_RE.findall(sections["USER"]))
    missing = [p for p in REQUIRED_PLACEHOLDERS if p not in found]
    if missing:
        raise TemplateError("USER section lacks placeholders: " + ", ".join("{{%s}}" % p for p in missing))
    markers = tuple(sorted(set(_MARKER_RE.findall(sections["SYSTEM"] + "\n" + sections["USER"]))))
    return JudgeTemplate(sections["SYSTEM"], sections["USER"], hashlib.sha256(data).hexdigest(), path, markers)


def load_template(path: str | Path) -> JudgeTemplate:
    p = Path(path)
    return parse_template(p.read_bytes(), str(p))
