"""safelabs/judge/parsing.py: parse a judge's raw text into (label, confidence, rationale).

Order: strict JSON of the whole text; then the first JSON object embedded in extra text or a code
fence that has a ``label`` key; then a regex over ``"label": "..."``. Anything else is UNCLEAR with
parsed_ok False (the caller keeps the raw text).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from safelabs.judge.labels import JudgeLabel

_MAX_RATIONALE = 500
_LABEL_RE = re.compile(r"""["']label["']\s*:\s*["']([A-Za-z_ ]+)["']""")
_CONF_RE = re.compile(r"""["']confidence["']\s*:\s*["']?(\d+(?:\.\d+)?)""")
_RAT_RE = re.compile(r"""["']rationale["']\s*:\s*"((?:[^"\\]|\\.)*)\"""")


@dataclass(frozen=True)
class ParsedOutput:
    label: JudgeLabel
    confidence: int
    rationale: str
    parsed_ok: bool
    how: str  # strict | embedded | regex | failed


def _label(value: object) -> JudgeLabel | None:
    if not isinstance(value, str):
        return None
    try:
        return JudgeLabel(value.strip().upper().replace(" ", "_").replace("-", "_"))
    except ValueError:
        return None


def _confidence(value: object) -> int:
    try:
        return max(1, min(3, int(round(float(value)))))
    except (TypeError, ValueError):
        return 1


def _from_obj(obj: dict, how: str) -> ParsedOutput | None:
    lab = _label(obj.get("label"))
    if lab is None:
        return None
    rat = obj.get("rationale")
    return ParsedOutput(lab, _confidence(obj.get("confidence")), (rat if isinstance(rat, str) else "")[:_MAX_RATIONALE], True, how)


def parse_judge_output(raw: str | None) -> ParsedOutput:
    text = (raw or "").strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            got = _from_obj(obj, "strict")
            if got:
                return got
    except ValueError:
        pass
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict) and "label" in obj:
            got = _from_obj(obj, "embedded")
            if got:
                return got
    lm = _LABEL_RE.search(text)
    if lm:
        lab = _label(lm.group(1))
        if lab:
            cm, rm = _CONF_RE.search(text), _RAT_RE.search(text)
            return ParsedOutput(lab, _confidence(cm.group(1)) if cm else 1, (rm.group(1) if rm else "")[:_MAX_RATIONALE], True, "regex")
    return ParsedOutput(JudgeLabel.UNCLEAR, 1, "", False, "failed")
