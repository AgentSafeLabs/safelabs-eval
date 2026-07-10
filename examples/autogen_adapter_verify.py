"""
examples/autogen_adapter_verify.py — one-shot real-API verification of
AutoGenAdapter against the actual installed ag2 package.

Sends ONE adversarial prompt (ASI01-001, same as the CrewAI verification
for direct comparability) through the real agent pair via AutoGenAdapter
and prints the resulting AgentResponse. Not a test file — a manual
verification script, run once, one real API call.

Usage:
    python examples/autogen_adapter_verify.py
"""
import asyncio

from autogen_example import agent, user_proxy

from safelabs.agents import AutoGenAdapter
from safelabs.prompts import get_library


async def main() -> None:
    entry = next(e for e in get_library().entries if e.id == "ASI01-001")
    print(f"Prompt [{entry.id}] ({entry.severity}):\n{entry.prompt}\n")

    adapter = AutoGenAdapter(agent=agent, recipient=user_proxy, timeout=60.0)
    response = await adapter.execute(entry.prompt)

    print("=== AgentResponse ===")
    print("output:", repr(response.output))
    print("latency_ms:", response.latency_ms)
    print("error:", response.error)
    print("raw:", response.raw)


if __name__ == "__main__":
    asyncio.run(main())
