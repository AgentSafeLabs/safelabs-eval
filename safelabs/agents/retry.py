"""
safelabs/agents/retry.py

Retry settings shared by every harness that retries infrastructure errors
(agentport_bench.harness and safelabs.runner), so the two never drift apart.

* ``RETRY_PROFILES``: ``default`` (3 attempts, 1 s base delay, 60 s cap, Retry-After
  up to 300 s) and ``benchmark`` (6 attempts, 2 s, 120 s, 600 s).
* ``resolve_retry_settings()``: a profile's values, with every explicit (non-None)
  override winning.
* ``retry_delay()``: the wait after a failed attempt: ``Retry-After`` when the
  provider sent one (capped), else exponential backoff with jitter.

Classification of what counts as an infrastructure error lives in
:mod:`safelabs.agents.errors`.
"""

from __future__ import annotations

from collections.abc import Callable

from safelabs.agents.errors import ErrorInfo

#: Named retry settings. "default" is what the harness did before profiles existed;
#: "benchmark" waits longer and tries more often for a full benchmark run.
RETRY_PROFILES: dict[str, dict[str, float | int]] = {
    "default":   {"max_attempts": 3, "base_delay_s": 1.0, "max_delay_s": 60.0,  "max_retry_after_s": 300.0},
    "benchmark": {"max_attempts": 6, "base_delay_s": 2.0, "max_delay_s": 120.0, "max_retry_after_s": 600.0},
}


def resolve_retry_settings(
    profile: str = "default",
    *,
    max_attempts: int | None = None,
    base_delay_s: float | None = None,
    max_delay_s: float | None = None,
    max_retry_after_s: float | None = None,
) -> dict[str, float | int]:
    """The profile's settings, with every explicitly given (non-None) value overriding it."""
    if profile not in RETRY_PROFILES:
        raise ValueError(f"unknown retry profile {profile!r}; expected one of {sorted(RETRY_PROFILES)}")
    settings = dict(RETRY_PROFILES[profile])
    for key, value in (("max_attempts", max_attempts), ("base_delay_s", base_delay_s),
                       ("max_delay_s", max_delay_s), ("max_retry_after_s", max_retry_after_s)):
        if value is not None:
            settings[key] = value
    return settings


def retry_delay(
    attempt: int, info: ErrorInfo, *, base_delay_s: float, max_delay_s: float,
    max_retry_after_s: float, jitter_fn: Callable[[], float],
) -> float:
    """Seconds to wait after failed attempt number ``attempt`` (1-based): Retry-After when the provider sent
    one (capped at ``max_retry_after_s``), else exponential backoff with jitter in [0.5, 1.0] of the step."""
    if info.retry_after_s is not None:
        return min(max(0.0, info.retry_after_s), max_retry_after_s)
    step = min(max_delay_s, base_delay_s * (2 ** (attempt - 1)))
    return step * (0.5 + 0.5 * jitter_fn())
