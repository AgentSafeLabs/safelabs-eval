"""
examples/llamaindex_adapter_verify.py — one-shot real-API verification of
LlamaIndexAdapter against the actual installed llama-index-core package.

Sends ONE adversarial prompt (ASI01-001, same as the CrewAI/AutoGen
verifications for direct comparability) through the real AgentWorkflow
via LlamaIndexAdapter and prints the resulting AgentResponse. Not a test
file — a manual verification script, run once, one real API call.

Usage:
    python examples/llamaindex_adapter_verify.py
"""
import asyncio

from llamaindex_example import workflow

from safelabs.agents import LlamaIndexAdapter
from safelabs.prompts import get_library


async def main() -> None:
    entry = next(e for e in get_library().entries if e.id == "ASI01-001")
    print(f"Prompt [{entry.id}] ({entry.severity}):\n{entry.prompt}\n")

    adapter = LlamaIndexAdapter(workflow=workflow, timeout=60.0)
    response = await adapter.execute(entry.prompt)

    print("=== AgentResponse ===")
    print("output:", repr(response.output))
    print("latency_ms:", response.latency_ms)
    print("error:", response.error)
    print("raw:", response.raw)


if __name__ == "__main__":
    asyncio.run(main())
