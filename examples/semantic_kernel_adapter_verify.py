"""
examples/semantic_kernel_adapter_verify.py — one-shot real-API
verification of SemanticKernelAdapter against the actual installed
semantic-kernel package.

Sends ONE adversarial prompt (ASI01-001, same as the CrewAI/AutoGen/
LlamaIndex/OpenAI Agents/Google ADK verifications for direct
comparability) through the real ChatCompletionAgent via
SemanticKernelAdapter and prints the resulting AgentResponse. Not a test
file — a manual verification script, run once, one real API call.

Usage:
    python examples/semantic_kernel_adapter_verify.py
"""
import asyncio

from semantic_kernel_example import agent

from safelabs.agents import SemanticKernelAdapter
from safelabs.prompts import get_library


async def main() -> None:
    entry = next(e for e in get_library().entries if e.id == "ASI01-001")
    print(f"Prompt [{entry.id}] ({entry.severity}):\n{entry.prompt}\n")

    adapter = SemanticKernelAdapter(agent=agent, timeout=60.0)
    response = await adapter.execute(entry.prompt)

    print("=== AgentResponse ===")
    print("output:", repr(response.output))
    print("latency_ms:", response.latency_ms)
    print("error:", response.error)
    print("raw:", response.raw)


if __name__ == "__main__":
    asyncio.run(main())
