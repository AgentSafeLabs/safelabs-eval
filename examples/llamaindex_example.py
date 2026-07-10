"""
examples/llamaindex_example.py — minimal real AgentWorkflow setup for
adapter verification.

Builds the smallest possible AgentWorkflow: a single FunctionAgent with
no tools, backed by a real Claude Haiku call via llama-index-llms-anthropic.

Usage:
    python examples/llamaindex_example.py
"""
import os

from dotenv import load_dotenv

load_dotenv()

from llama_index.core.agent.workflow import AgentWorkflow, FunctionAgent
from llama_index.llms.anthropic import Anthropic

llm = Anthropic(
    model="claude-haiku-4-5-20251001",
    max_tokens=200,
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

agent = FunctionAgent(
    name="assistant",
    description="A helpful assistant that responds to the given input.",
    system_prompt="You are a helpful assistant that responds to the given input.",
    llm=llm,
)

workflow = AgentWorkflow(agents=[agent], root_agent="assistant")


if __name__ == "__main__":
    import asyncio

    async def main() -> None:
        result = await workflow.run(user_msg="Say hello in one sentence.")
        print(result)

    asyncio.run(main())
