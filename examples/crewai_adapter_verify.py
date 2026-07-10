"""
examples/crewai_adapter_verify.py — one-shot real-API verification of
CrewAIAdapter against the actual installed crewai package.

Sends ONE adversarial prompt (ASI01-001) through a real Crew via
CrewAIAdapter and prints the resulting AgentResponse. Not a test file —
a manual verification script, run once, one real API call.

Usage:
    python examples/crewai_adapter_verify.py
"""
import asyncio

from crewai_example import crew

from safelabs.agents import CrewAIAdapter
from safelabs.prompts import get_library


async def main() -> None:
    entry = next(e for e in get_library().entries if e.id == "ASI01-001")
    print(f"Prompt [{entry.id}] ({entry.severity}):\n{entry.prompt}\n")

    adapter = CrewAIAdapter(crew=crew, input_key="input", timeout=60.0)
    response = await adapter.execute(entry.prompt)

    print("=== AgentResponse ===")
    print("output:", repr(response.output))
    print("latency_ms:", response.latency_ms)
    print("error:", response.error)
    print("raw:", response.raw)


if __name__ == "__main__":
    asyncio.run(main())
