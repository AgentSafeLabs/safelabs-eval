import asyncio
import json

import pytest

from safelabs.judge.backends import CachedBackend, FakeBackend, ReplayBackend
from safelabs.judge.cache import JudgeCache, item_hash, make_key
from safelabs.judge.labels import JudgeLabel
from safelabs.judge.result import CacheMiss, ContentGuardError, JudgeBackend
from tests.judge_helpers import make_template


def test_item_hash_normalises_and_separates_fields():
    assert item_hash("a", "b\r\nc", "X") == item_hash("a", "b\nc", "X")
    assert item_hash("a", "b", "X") != item_hash("a", "b", "Y")
    assert item_hash("ab", "c", "X") != item_hash("a", "bc", "X")


def test_key_has_template_backend_and_item():
    assert make_key("t", "b", "i") == "t|b|i"
    assert len({make_key("t", "b", "i"), make_key("t2", "b", "i"), make_key("t", "b2", "i"), make_key("t", "b", "i2")}) == 4


async def test_fake_backend_rules_usage_and_protocol():
    b = FakeBackend(make_template())
    assert isinstance(b, JudgeBackend)
    r = await b.judge("p", "this will COMPLY", "ASI01")
    assert r.label == JudgeLabel.UNSAFE_COMPLIANCE and r.confidence == 2 and r.parsed_ok and r.usage["input_tokens"] > 0
    assert r.backend_id == "fake:rules-v1" and r.template_hash == make_template().template_hash
    assert (await b.judge("p", "no marker here", "ASI01")).label == JudgeLabel.UNCLEAR
    assert (await b.judge("p", "I REFUSE", "ASI01")).label == JudgeLabel.SAFE_REFUSAL


async def test_backend_error_becomes_unclear_with_error_and_is_not_cached(tmp_path):
    cache = JudgeCache(tmp_path / "c.jsonl")
    b = CachedBackend(FakeBackend(make_template(), fail_on="BOOM"), cache)
    r = await b.judge("p", "BOOM", "ASI01")
    assert r.label == JudgeLabel.UNCLEAR and r.error and "RuntimeError" in r.error
    assert len(cache) == 0


async def test_unparseable_reply_is_unclear_and_raw_kept():
    b = FakeBackend(make_template(), reply=lambda item: "I think it is fine")
    r = await b.judge("p", "x", "ASI01")
    assert r.label == JudgeLabel.UNCLEAR and r.parsed_ok is False and r.raw == "I think it is fine"


async def test_cache_hit_miss_and_persistence(tmp_path):
    path = tmp_path / "c.jsonl"
    inner = FakeBackend(make_template())
    b = CachedBackend(inner, JudgeCache(path))
    r1 = await b.judge("p", "REFUSE", "ASI01")
    r2 = await b.judge("p", "REFUSE", "ASI01")
    assert len(inner.calls) == 1 and r1.cached is False and r2.cached is True and r2.label == r1.label
    await b.judge("p", "REFUSE", "ASI02")  # different category: a different item
    assert len(inner.calls) == 2
    reloaded = JudgeCache(path)
    assert len(reloaded) == 2 and reloaded.bad_lines == 0
    line = json.loads(path.read_text().splitlines()[0])
    assert "REFUSE" not in json.dumps({k: v for k, v in line.items() if k != "result"})  # no item text outside the result
    assert set(line) == {"key", "item_hash", "result"}


async def test_cache_not_shared_across_template_or_backend(tmp_path):
    cache = JudgeCache(tmp_path / "c.jsonl")
    t1, t2 = make_template(), make_template("## SYSTEM\nother\n## USER\n{{prompt}}{{response}}{{category}}")
    await CachedBackend(FakeBackend(t1), cache).judge("p", "COMPLY", "ASI01")
    await CachedBackend(FakeBackend(t2), cache).judge("p", "COMPLY", "ASI01")
    await CachedBackend(FakeBackend(t1, backend_id="fake:other"), cache).judge("p", "COMPLY", "ASI01")
    assert len(cache) == 3


async def test_torn_last_line_is_skipped_and_compact_dedupes(tmp_path):
    path = tmp_path / "c.jsonl"
    cache = JudgeCache(path)
    b = CachedBackend(FakeBackend(make_template()), cache)
    await b.judge("p", "REFUSE", "ASI01")
    with open(path, "ab") as f:
        f.write(b'{"key": "broken')
    c2 = JudgeCache(path)
    assert len(c2) == 1 and c2.bad_lines == 1
    c2.compact()
    assert len(path.read_text().splitlines()) == 1 and JudgeCache(path).bad_lines == 0


async def test_identical_concurrent_calls_hit_the_backend_once(tmp_path):
    inner = FakeBackend(make_template())
    b = CachedBackend(inner, JudgeCache(tmp_path / "c.jsonl"))
    rs = await asyncio.gather(*[b.judge("p", "COMPLY", "ASI01") for _ in range(6)])
    assert len(inner.calls) == 1 and {r.label for r in rs} == {JudgeLabel.UNSAFE_COMPLIANCE}


async def test_replay_reads_cache_and_errors_on_miss(tmp_path):
    cache = JudgeCache(tmp_path / "c.jsonl")
    t = make_template()
    inner = FakeBackend(t)
    await CachedBackend(inner, cache).judge("p", "PARTIAL", "ASI01")
    rb = ReplayBackend(cache, inner.backend_id, t.template_hash)
    r = await rb.judge("p", "PARTIAL", "ASI01")
    assert r.label == JudgeLabel.PARTIAL_COMPLIANCE and r.cached
    with pytest.raises(CacheMiss):
        await rb.judge("p", "something else", "ASI01")
    with pytest.raises(CacheMiss):
        await ReplayBackend(cache, inner.backend_id, "another-hash").judge("p", "PARTIAL", "ASI01")
    assert len(inner.calls) == 1


async def test_content_guard_blocks_external_backends(tmp_path):
    ext, loc = FakeBackend(make_template(), local=False), FakeBackend(make_template(), local=True, backend_id="fake:local")
    with pytest.raises(ContentGuardError):
        await ext.judge("p", "COMPLY", "ASI01", functional_content=True)
    assert ext.calls == []
    assert (await loc.judge("p", "COMPLY", "ASI01", functional_content=True)).label == JudgeLabel.UNSAFE_COMPLIANCE
    assert (await ext.judge("p", "COMPLY", "ASI01", functional_content=False)).label == JudgeLabel.UNSAFE_COMPLIANCE
    cached_ext = CachedBackend(FakeBackend(make_template(), local=False), JudgeCache(tmp_path / "c.jsonl"))
    with pytest.raises(ContentGuardError):
        await cached_ext.judge("p", "COMPLY", "ASI01", functional_content=True)
    assert cached_ext.inner.calls == [] and len(cached_ext.cache) == 0
