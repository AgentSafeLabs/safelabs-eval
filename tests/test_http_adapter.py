"""
tests/test_http_adapter.py

HttpAdapter hands its timeout to httpx (issue #48, claim C1): the client is
built with timeout=self.timeout, so the post() call carries it. The httpx
client is replaced by a fake, so no network is used.
"""

from __future__ import annotations

import pytest

from safelabs.agents import http_adapter
from safelabs.agents.http_adapter import HttpAdapter


class _FakeResponse:
    status_code = 200
    text = "ok"

    def json(self):
        return {"response": "ok"}


def _install_fake_client(monkeypatch):
    seen: dict = {}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            seen["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **kwargs):
            seen["url"] = url
            return _FakeResponse()

    monkeypatch.setattr(http_adapter.httpx, "AsyncClient", _FakeClient)
    return seen


@pytest.mark.asyncio
async def test_http_adapter_passes_custom_timeout_to_httpx_client(monkeypatch):
    seen = _install_fake_client(monkeypatch)
    result = await HttpAdapter(base_url="http://localhost:9/chat", timeout=7.5).execute("hi")
    assert seen["client_kwargs"]["timeout"] == 7.5
    assert result.output == "ok" and result.error is None


@pytest.mark.asyncio
async def test_http_adapter_default_timeout_is_30_seconds(monkeypatch):
    seen = _install_fake_client(monkeypatch)
    await HttpAdapter(base_url="http://localhost:9/chat").execute("hi")
    assert seen["client_kwargs"]["timeout"] == 30.0
