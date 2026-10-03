"""safelabs/judge/cache.py: JSONL cache of judge results.

Key = template hash | backend id | item hash. The item hash covers prompt, response and category;
the cache stores the hash only, never the prompt or response text. Appends are one ``write`` call
followed by fsync under a lock; a torn last line is skipped on load. ``compact`` rewrites via a
temporary file and ``os.replace``.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import unicodedata
from pathlib import Path

from safelabs.judge.result import JudgeResult


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text).replace("\r\n", "\n")


def item_hash(prompt: str, response: str, category: str) -> str:
    payload = json.dumps([_norm(prompt), _norm(response), category], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def make_key(template_hash: str, backend_id: str, ihash: str) -> str:
    return f"{template_hash}|{backend_id}|{ihash}"


class JudgeCache:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self.bad_lines = 0
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    self._data[rec["key"]] = rec
                except (ValueError, KeyError, TypeError):
                    self.bad_lines += 1

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def get(self, key: str) -> JudgeResult | None:
        rec = self._data.get(key)
        if rec is None:
            return None
        return JudgeResult(**{k: v for k, v in rec["result"].items()})

    def put(self, key: str, ihash: str, result: JudgeResult) -> None:
        rec = {"key": key, "item_hash": ihash, "result": result.model_dump(mode="json", exclude={"cached"})}
        line = (json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "ab") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            self._data[key] = rec

    def compact(self) -> None:
        """Rewrite the file with one line per key (last write wins), atomically."""
        with self._lock:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with open(tmp, "wb") as f:
                for rec in self._data.values():
                    f.write((json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
