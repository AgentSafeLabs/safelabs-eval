"""
examples/google_adk_adapter_verify.py — one-shot real-API verification of
GoogleADKAdapter against the actual installed google-adk package.

Sends ONE adversarial prompt (ASI01-001, same as the CrewAI/AutoGen/
LlamaIndex/OpenAI Agents verifications for direct comparability) through
the real ADK agent via GoogleADKAdapter and prints the resulting
AgentResponse. Not a test file — a manual verification script, run once,
one real API call.

Usage:
    python examples/google_adk_adapter_verify.py
"""
import asyncio

from google_adk_example import agent

from safelabs.agents import GoogleADKAdapter
from safelabs.prompts import get_library


async def main() -> None:
    entry = next(e for e in get_library().entries if e.id == "ASI01-001")
    print(f"Prompt [{entry.id}] ({entry.severity}):\n{entry.prompt}\n")

    adapter = GoogleADKAdapter(agent=agent, timeout=60.0)
    response = await adapter.execute(entry.prompt)

    print("=== AgentResponse ===")
    print("output:", repr(response.output))
    print("latency_ms:", response.latency_ms)
    print("error:", response.error)
    print("raw:", response.raw)


if __name__ == "__main__":
    asyncio.run(main())
