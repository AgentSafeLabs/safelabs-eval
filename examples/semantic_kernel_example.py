"""
examples/semantic_kernel_example.py — minimal real Semantic Kernel setup
for adapter verification.

Builds the smallest possible SK agent: a single ChatCompletionAgent with
no plugins, backed by a real Claude Haiku call via the built-in Anthropic
chat-completion connector. A max_tokens=200 cap is applied through the
agent's default execution settings to keep the real API call cheap.

Usage:
    python examples/semantic_kernel_example.py
"""
import os

from dotenv import load_dotenv

load_dotenv()

from semantic_kernel.agents import ChatCompletionAgent
from semantic_kernel.connectors.ai.anthropic import (
    AnthropicChatCompletion,
    AnthropicChatPromptExecutionSettings,
)
from semantic_kernel.functions import KernelArguments

agent = ChatCompletionAgent(
    service=AnthropicChatCompletion(
        ai_model_id="claude-haiku-4-5-20251001",
        api_key=os.getenv("ANTHROPIC_API_KEY"),
    ),
    name="assistant",
    instructions="You are a helpful assistant that responds to the given input.",
    arguments=KernelArguments(
        settings=AnthropicChatPromptExecutionSettings(max_tokens=200),
    ),
)


if __name__ == "__main__":
    import asyncio

    async def main() -> None:
        result = await agent.get_response(messages="Say hello in one sentence.")
        print(result.message.content)

    asyncio.run(main())
