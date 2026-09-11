"""
agentport_bench

AgentPort-Bench: schema, submission harness, and validator for a public,
multi-contributor benchmark built on safelabs-eval's prompt library and
scoring pipeline. See docs/AGENTPORT_BENCH.md for the full design.

This package does not read, modify, or depend on agentdojo-x (the private
internal study that piloted this benchmark's combinatorics) -- it is a
from-scratch public artifact informed by, not extracted from, that work.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
