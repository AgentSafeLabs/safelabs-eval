"""
examples/openai_agents_example.py — minimal real OpenAI Agents SDK setup
for adapter verification.

Builds the smallest possible setup: a single Agent backed by a real
Claude Haiku call via the SDK's bundled LiteLLM extension (openai-agents
is OpenAI-native by default; ANTHROPIC_API_KEY is the real key present
in .env, so routing through LiteLLM is used instead of OpenAI's own API).

Usage:
    python examples/openai_agents_example.py
"""
import os

from dotenv import load_dotenv

load_dotenv()

from agents import Agent, ModelSettings, Runner
from agents.extensions.models.litellm_model import LitellmModel

model = LitellmModel(
    model="anthropic/claude-haiku-4-5-20251001",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

agent = Agent(
    name="assistant",
    instructions="You are a helpful assistant that responds to the given input.",
    model=model,
    model_settings=ModelSettings(max_tokens=200),
)


if __name__ == "__main__":
    import asyncio

    async def main() -> None:
        result = await Runner.run(agent, "Say hello in one sentence.")
        print(result.final_output)

    asyncio.run(main())
