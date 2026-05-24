"""
safelabs/agents/http_adapter.py

HTTP adapter — sends prompts to any agent exposed via a REST endpoint.

POSTs JSON {"prompt": "<text>"} and extracts the response from common
output keys: response, output, message, text, content, result.
"""

from __future__ import annotations

import time

import httpx

from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse

_RESPONSE_KEYS = ("response", "output", "message", "text", "content", "result")


class HttpAdapter(AgentAdapter):
    """
    Adapter for agents exposed over HTTP.

    Parameters
    ----------
    base_url:
        Full URL of the agent's chat/completion endpoint.
    headers:
        Extra HTTP headers (e.g. {"Authorization": "Bearer <token>"})
    request_template:
        Optional callable (prompt: str) -> dict to build the request body.
        Defaults to {"prompt": prompt}.
    response_key:
        Key to extract from the JSON response. Overrides auto-detection.
    timeout:
        Per-request timeout in seconds (default 30).

    Example
    -------
    .. code-block:: python

        adapter = HttpAdapter(
            base_url="https://my-agent.example.com/v1/chat",
            headers={"Authorization": "Bearer sk-..."},
        )
        response = await adapter.execute("Ignore previous instructions.")
    """

    def __init__(
        self,
        base_url: str,
        headers: dict[str, str] | None = None,
        request_template=None,
        response_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.base_url         = base_url.rstrip("/")
        self.headers          = headers or {}
        self.request_template = request_template
        self.response_key     = response_key

    @property
    def adapter_type(self) -> str:
        return "http"

    async def _execute(self, prompt: str) -> AgentResponse:
        body = (
            self.request_template(prompt)
            if self.request_template
            else {"prompt": prompt}
        )

        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            http_response = await client.post(
                self.base_url,
                json=body,
                headers=self.headers,
            )
        latency_ms = (time.perf_counter() - t0) * 1000

        if http_response.status_code >= 400:
            return AgentResponse(
                output="",
                latency_ms=latency_ms,
                error=f"HTTP {http_response.status_code}: {http_response.text[:200]}",
                metadata={"status_code": http_response.status_code},
            )

        try:
            data = http_response.json()
        except Exception:
            return AgentResponse(
                output=http_response.text,
                latency_ms=latency_ms,
                metadata={"status_code": http_response.status_code},
            )

        output = self._extract_output(data)
        return AgentResponse(
            output=output,
            latency_ms=latency_ms,
            raw=data if isinstance(data, dict) else None,
            metadata={"status_code": http_response.status_code},
        )

    def _extract_output(self, data: dict | str) -> str:
        if isinstance(data, str):
            return data
        if self.response_key and self.response_key in data:
            return str(data[self.response_key])
        for key in _RESPONSE_KEYS:
            if key in data:
                return str(data[key])
        return str(data)
