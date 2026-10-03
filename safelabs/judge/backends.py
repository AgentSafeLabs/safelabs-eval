"""safelabs/judge/backends.py: backend base class, FakeBackend, ReplayBackend, CachedBackend."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Callable

from safelabs.judge.cache import JudgeCache, item_hash, make_key
from safelabs.judge.labels import JudgeLabel
from safelabs.judge.parsing import parse_judge_output
from safelabs.judge.result import CacheMiss, JudgeResult, enforce_content_guard
from safelabs.judge.template import JudgeTemplate


class BaseJudgeBackend:
    """Renders the template, calls ``_complete``, parses the reply. Provider errors become an
    UNCLEAR result with ``error`` set (only the exception type and a short message are kept)."""

    is_local = False

    def __init__(self, backend_id: str, template: JudgeTemplate) -> None:
        self.backend_id = backend_id
        self.template = template
        self.template_hash = template.template_hash

    async def _complete(self, system: str, user: str, item: dict) -> tuple[str, dict]:
        raise NotImplementedError

    async def judge(self, prompt: str, response: str, category: str, *, functional_content: bool = False) -> JudgeResult:
        enforce_content_guard(self, functional_content)
        system, user, info = self.template.render_with_info(prompt=prompt, response=response, category=category)
        t0 = time.perf_counter()
        try:
            raw, usage = await self._complete(system, user, {"prompt": prompt, "response": response, "category": category})
        except Exception as exc:  # noqa: BLE001 - recorded, never raised, so one bad call cannot stop a run
            return JudgeResult(
                label=JudgeLabel.UNCLEAR, confidence=1, backend_id=self.backend_id, template_hash=self.template_hash,
                latency_ms=(time.perf_counter() - t0) * 1000, error=f"{type(exc).__name__}: {str(exc)[:200]}", meta=info,
            )
        parsed = parse_judge_output(raw)
        return JudgeResult(
            label=parsed.label, confidence=parsed.confidence, rationale=parsed.rationale, raw=raw,
            backend_id=self.backend_id, template_hash=self.template_hash,
            latency_ms=(time.perf_counter() - t0) * 1000, usage=dict(usage or {}), parsed_ok=parsed.parsed_ok, meta=info,
        )


DEFAULT_FAKE_RULES = (
    ("REFUSE", JudgeLabel.SAFE_REFUSAL),
    ("COMPLY", JudgeLabel.UNSAFE_COMPLIANCE),
    ("PARTIAL", JudgeLabel.PARTIAL_COMPLIANCE),
    ("OFFTOPIC", JudgeLabel.BENIGN_OFF_TARGET),
)


class FakeBackend(BaseJudgeBackend):
    """Deterministic rules for tests: the first rule whose marker occurs in the response text wins;
    no marker gives UNCLEAR. ``fail_on`` makes a call raise when its marker occurs."""

    def __init__(self, template: JudgeTemplate, *, rules=DEFAULT_FAKE_RULES, backend_id: str = "fake:rules-v1",
                 local: bool = False, fail_on: str | None = None, confidence: int = 2,
                 reply: Callable[[dict], str] | None = None) -> None:
        super().__init__(backend_id, template)
        self.rules, self.is_local, self.fail_on, self.confidence, self.reply = tuple(rules), local, fail_on, confidence, reply
        self.calls: list[str] = []

    async def _complete(self, system: str, user: str, item: dict) -> tuple[str, dict]:
        self.calls.append(item_hash(item["prompt"], item["response"], item["category"]))
        if self.fail_on and self.fail_on in item["response"]:
            raise RuntimeError("fake backend failure")
        if self.reply is not None:
            raw = self.reply(item)
        else:
            label = next((l for marker, l in self.rules if marker in item["response"]), JudgeLabel.UNCLEAR)
            raw = json.dumps({"label": label.value, "confidence": self.confidence, "rationale": "rule match"})
        return raw, {"input_tokens": len(user.split()), "output_tokens": len(raw.split())}


class ReplayBackend:
    """Reads the cache only. A missing key raises CacheMiss. No provider is ever contacted, so it
    counts as local for the content guard (a functional_content item has no cache entry anyway)."""

    is_local = True

    def __init__(self, cache: JudgeCache, backend_id: str, template_hash: str) -> None:
        self.cache, self.backend_id, self.template_hash = cache, backend_id, template_hash

    async def judge(self, prompt: str, response: str, category: str, *, functional_content: bool = False) -> JudgeResult:
        key = make_key(self.template_hash, self.backend_id, item_hash(prompt, response, category))
        hit = self.cache.get(key)
        if hit is None:
            raise CacheMiss(key)
        return hit.model_copy(update={"cached": True})


class CachedBackend:
    """Wraps a backend with the cache: a hit never reaches the inner backend; a miss calls it and
    stores the result unless it carries an error. Identical in-flight calls are collapsed."""

    def __init__(self, inner, cache: JudgeCache) -> None:
        self.inner, self.cache = inner, cache
        self.backend_id, self.template_hash, self.is_local = inner.backend_id, inner.template_hash, inner.is_local
        self._locks: dict[str, asyncio.Lock] = {}

    async def judge(self, prompt: str, response: str, category: str, *, functional_content: bool = False) -> JudgeResult:
        enforce_content_guard(self.inner, functional_content)
        ih = item_hash(prompt, response, category)
        key = make_key(self.template_hash, self.backend_id, ih)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self.cache.get(key)
            if hit is not None:
                return hit.model_copy(update={"cached": True})
            res = await self.inner.judge(prompt, response, category, functional_content=functional_content)
            if not res.error:
                self.cache.put(key, ih, res)
            return res
