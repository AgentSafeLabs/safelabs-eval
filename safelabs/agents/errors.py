"""
safelabs/agents/errors.py

Classify a failed agent call as infrastructure, content-policy, no-output-text
or other, so a harness can retry what is worth retrying and record what could
not be obtained as missing rather than as a model response.

Classes (``ErrorInfo.error_class``):

* ``infrastructure``: the request did not reach a model answer. Subclasses
  ``rate_limit_or_quota``, ``timeout``, ``provider_unavailable`` (the names the
  AgentPort-Bench data release uses) and ``connection_error``.
* ``content_policy``: the provider blocked the request or the answer.
* ``no_output_text``: the call returned normally with no text.
* ``other``: anything else (kept and scored as before).

Sources, in priority order: exception class names (the MRO, so a litellm
``RateLimitError`` is also an ``openai.RateLimitError``), an HTTP status code
(``status_code`` or google-genai's ``code``), then the message markers the
data-release classifier used (``flagged for possible cybersecurity risk``,
``provider returned no output text``, ``timed out``, ``exceeded your cur``,
``UNAVAILABLE``). Exception names are matched, never imported, so no provider
SDK is needed. ``AgentAdapter.execute()`` turns exceptions into an
``AgentResponse`` and records ``exception_mro``, ``status_code`` and
``retry_after_s`` in its metadata for this module to read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

INFRASTRUCTURE = "infrastructure"
CONTENT_POLICY = "content_policy"
NO_OUTPUT_TEXT = "no_output_text"
OTHER = "other"
ERROR_CLASSES = (INFRASTRUCTURE, CONTENT_POLICY, NO_OUTPUT_TEXT, OTHER)

RATE_LIMIT = "rate_limit_or_quota"
TIMEOUT = "timeout"
PROVIDER_UNAVAILABLE = "provider_unavailable"
CONNECTION_ERROR = "connection_error"
INFRASTRUCTURE_SUBCLASSES = (RATE_LIMIT, TIMEOUT, PROVIDER_UNAVAILABLE, CONNECTION_ERROR)

#: Exception class names (anywhere in the MRO) by subclass. Verified against the installed
#: openai, anthropic, google-genai, httpx and litellm packages; see harness_reliability/design.md.
_CONTENT_POLICY_TYPES = {"ContentPolicyViolationError", "ContentFilterFinishReasonError"}
_RATE_LIMIT_TYPES = {"RateLimitError", "ResourceExhausted", "TooManyRequests"}
_TIMEOUT_TYPES = {
    "APITimeoutError", "Timeout", "TimeoutException", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
    "PoolTimeout", "TimeoutError", "DeadlineExceededError", "DeadlineExceeded",
}
_UNAVAILABLE_TYPES = {
    "ServiceUnavailableError", "OverloadedError", "InternalServerError", "BadGatewayError", "ServerError",
    "ServiceUnavailable", "InternalServerError",
}
_CONNECTION_TYPES = {
    "APIConnectionError", "ConnectError", "NetworkError", "ReadError", "WriteError", "RemoteProtocolError",
    "ConnectionError", "ConnectionResetError", "ConnectionRefusedError", "ConnectionAbortedError",
}

_MARKERS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (CONTENT_POLICY, CONTENT_POLICY, re.compile(r"flagged for possible cybersecurity risk|content[_ ]policy", re.I)),
    (NO_OUTPUT_TEXT, NO_OUTPUT_TEXT, re.compile(r"provider returned no output text")),
    (INFRASTRUCTURE, TIMEOUT, re.compile(r"timed out|timeout", re.I)),
    (INFRASTRUCTURE, RATE_LIMIT, re.compile(r"exceeded your cur|rate[_ -]?limit|RESOURCE_EXHAUSTED|Too Many Requests|HTTP 429")),
    (INFRASTRUCTURE, PROVIDER_UNAVAILABLE, re.compile(r"UNAVAILABLE|Service Unavailable|overloaded|HTTP 5\d\d")),
    (INFRASTRUCTURE, CONNECTION_ERROR, re.compile(r"Connection error|Connection refused|Connection reset|ConnectError", re.I)),
)


@dataclass(frozen=True)
class ErrorInfo:
    """Result of classifying one failed call."""

    error_class: str
    error_subclass: str
    retry_after_s: float | None = None

    @property
    def is_infrastructure(self) -> bool:
        return self.error_class == INFRASTRUCTURE


def retry_after_seconds(value: object, *, now: datetime | None = None) -> float | None:
    """Parse a ``Retry-After`` header value: delay-seconds or an HTTP date. None when absent or invalid."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if value >= 0 else None
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
        if seconds < 0:
            return None                       # a negative delay-seconds value is invalid
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
    if seconds != seconds or seconds == float("inf"):
        return None
    return max(0.0, seconds)


def describe_exception(exc: BaseException) -> dict:
    """Metadata describing an exception for later classification: MRO class names, status, Retry-After.

    Follows ``__cause__`` / ``__context__`` (at most 5 levels) when the exception itself carries
    no status code, so a wrapper such as CrewAI's around a litellm ``RateLimitError`` is classified
    by the inner error.
    """
    names: list[str] = []
    status: int | None = None
    retry_after: float | None = None
    seen: set[int] = set()
    cur: BaseException | None = exc
    depth = 0
    while cur is not None and id(cur) not in seen and depth < 5:
        seen.add(id(cur))
        for klass in type(cur).__mro__:
            if klass.__name__ not in ("object", "BaseException", "Exception") and klass.__name__ not in names:
                names.append(klass.__name__)
        if status is None:
            status = _status_of(cur)
        if retry_after is None:
            retry_after = _retry_after_of(cur)
        cur = cur.__cause__ or cur.__context__
        depth += 1
    meta: dict = {"exception_mro": names}
    if status is not None:
        meta["status_code"] = status
    if retry_after is not None:
        meta["retry_after_s"] = retry_after
    return meta


def _status_of(exc: BaseException) -> int | None:
    for attr in ("status_code", "code", "http_status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int) and not isinstance(v, bool) and 100 <= v <= 599:
            return v
    resp = getattr(exc, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) and not isinstance(v, bool) and 100 <= v <= 599 else None


def _retry_after_of(exc: BaseException) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get("retry-after", headers.get("Retry-After"))
    except Exception:  # noqa: BLE001  (a headers object without .get)
        return None
    return retry_after_seconds(value)


def classify_error(error: str | None, metadata: dict | None = None) -> ErrorInfo | None:
    """Classify a failed call from its error text and the metadata ``execute()`` recorded. None when ``error`` is None."""
    if error is None:
        return None
    meta = metadata or {}
    names = set(meta.get("exception_mro") or ())
    status = meta.get("status_code") if isinstance(meta.get("status_code"), int) else None
    retry_after = meta.get("retry_after_s") if isinstance(meta.get("retry_after_s"), (int, float)) else None

    def info(cls: str, sub: str) -> ErrorInfo:
        return ErrorInfo(cls, sub, float(retry_after) if (cls == INFRASTRUCTURE and retry_after is not None) else None)

    if names & _CONTENT_POLICY_TYPES:
        return info(CONTENT_POLICY, CONTENT_POLICY)
    if re.search(_MARKERS[0][2], error):
        return info(CONTENT_POLICY, CONTENT_POLICY)
    if re.search(_MARKERS[1][2], error):
        return info(NO_OUTPUT_TEXT, NO_OUTPUT_TEXT)
    if status == 429 or names & _RATE_LIMIT_TYPES:
        return info(INFRASTRUCTURE, RATE_LIMIT)
    if status in (408, 504) or names & _TIMEOUT_TYPES:
        return info(INFRASTRUCTURE, TIMEOUT)
    if (status is not None and 500 <= status <= 599) or names & _UNAVAILABLE_TYPES:
        return info(INFRASTRUCTURE, PROVIDER_UNAVAILABLE)
    if names & _CONNECTION_TYPES:
        return info(INFRASTRUCTURE, CONNECTION_ERROR)
    for cls, sub, pattern in _MARKERS[2:]:
        if pattern.search(error):
            return info(cls, sub)
    return info(OTHER, OTHER)
