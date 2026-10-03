"""safelabs.judge: provider-neutral LLM-judge module, hybrid scorer and offline analysis (JudgeCal).

Importing this package never imports a provider SDK. Provider backends live in
``safelabs.judge.providers`` and need an optional extra (judge-anthropic, judge-openai,
judge-google). Existing detectors and ``Scorer`` are unchanged.
"""

from safelabs.judge.backends import BaseJudgeBackend, CachedBackend, FakeBackend, ReplayBackend
from safelabs.judge.cache import JudgeCache, item_hash, make_key
from safelabs.judge.hybrid import MODES, HybridScorer
from safelabs.judge.labels import LABEL_TO_VERDICT, JudgeLabel, binary_of_label, verdict_for
from safelabs.judge.parsing import parse_judge_output
from safelabs.judge.result import CacheMiss, ContentGuardError, JudgeBackend, JudgeResult
from safelabs.judge.template import JudgeTemplate, TemplateError, load_template

__all__ = [
    "BaseJudgeBackend", "CachedBackend", "FakeBackend", "ReplayBackend", "JudgeCache", "item_hash", "make_key",
    "MODES", "HybridScorer", "LABEL_TO_VERDICT", "JudgeLabel", "binary_of_label", "verdict_for", "parse_judge_output",
    "CacheMiss", "ContentGuardError", "JudgeBackend", "JudgeResult", "JudgeTemplate", "TemplateError", "load_template",
]
