"""safelabs/judge/template.py: judge prompt templates loaded from a file at runtime (never bundled).

File format: a Markdown file with two level-2 headings, ``## SYSTEM`` and ``## USER``. The USER
section must contain the placeholders ``{{prompt}}``, ``{{response}}`` and ``{{category}}``. The
template hash is the SHA-256 of the whole file's bytes, so any edit changes it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

PLACEHOLDERS = ("prompt", "response", "category")
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

    def render(self, *, prompt: str, response: str, category: str) -> tuple[str, str]:
        """Return (system, user). One regex pass, so inserted text is never re-substituted."""
        values = {"prompt": prompt, "response": response, "category": category}
        return self.system, _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], self.user)


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
    missing = [p for p in PLACEHOLDERS if p not in found]
    if missing:
        raise TemplateError("USER section lacks placeholders: " + ", ".join("{{%s}}" % p for p in missing))
    return JudgeTemplate(sections["SYSTEM"], sections["USER"], hashlib.sha256(data).hexdigest(), path)


def load_template(path: str | Path) -> JudgeTemplate:
    p = Path(path)
    return parse_template(p.read_bytes(), str(p))
