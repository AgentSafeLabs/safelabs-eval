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
from safelabs.agents.schemas import AgentResponse, ToolCall, normalize_usage

_RESPONSE_KEYS = ("response", "output", "message", "text", "content", "result")


def _openai_compatible_fields(data: dict) -> dict:
    """
    Fill the optional AgentResponse fields from an OpenAI-compatible JSON body.

    An HTTP endpoint has no framework contract, so every value here is
    ``inferred`` from the OpenAI chat-completions key convention, and a field
    is filled only when its keys are present:

    * ``tool_calls``: ``choices[0].message`` exists -> the parsed
      ``message.tool_calls`` list (``[]`` when the message has none). No
      ``choices[0].message`` -> ``None`` (not exposed).
    * ``stop_reason``: ``choices[0].finish_reason``, else a top-level
      ``finish_reason`` / ``stop_reason`` string.
    * ``usage``: ``usage.prompt_tokens`` / ``completion_tokens`` (or
      ``input_tokens`` / ``output_tokens``) and the reasoning-token detail.
    """
    fields: dict = {}
    prov: dict = {}

    choices = data.get("choices")
    first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    message = first.get("message") if first is not None else None
    if isinstance(message, dict):
        raw_calls = message.get("tool_calls")
        if raw_calls is None or isinstance(raw_calls, list):
            calls: list[ToolCall] = []
            for tc in raw_calls or []:
                fn = tc.get("function") if isinstance(tc, dict) else None
                if isinstance(fn, dict) and isinstance(fn.get("name"), str):
                    call_id = tc.get("id") if isinstance(tc.get("id"), str) else None
                    calls.append(ToolCall.from_arguments(fn["name"], fn.get("arguments"), call_id=call_id))
            fields["tool_calls"] = calls
            prov["tool_calls"] = "inferred"

    stop = first.get("finish_reason") if first is not None else None
    if not isinstance(stop, str) or not stop:
        stop = next((data[k] for k in ("finish_reason", "stop_reason") if isinstance(data.get(k), str) and data[k]), None)
    if isinstance(stop, str) and stop:
        fields["stop_reason"] = stop
        prov["stop_reason"] = "inferred"

    u = data.get("usage")
    if isinstance(u, dict):
        details = u.get("completion_tokens_details") or u.get("output_tokens_details")
        reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
        usage = normalize_usage(
            u.get("prompt_tokens", u.get("input_tokens")),
            u.get("completion_tokens", u.get("output_tokens")),
            reasoning,
        )
        if usage is not None:
            fields["usage"] = usage
            prov["usage"] = "inferred"

    if prov:
        fields["provenance"] = prov
    return fields


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
        extra = _openai_compatible_fields(data) if isinstance(data, dict) else {}
        return AgentResponse(
            output=output,
            latency_ms=latency_ms,
            raw=data if isinstance(data, dict) else None,
            metadata={"status_code": http_response.status_code},
            **extra,
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
