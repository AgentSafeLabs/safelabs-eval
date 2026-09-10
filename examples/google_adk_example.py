"""
examples/google_adk_example.py — minimal real Google ADK setup for adapter
verification.

Builds the smallest possible ADK agent: a single LlmAgent with no tools,
backed by a real Claude Haiku call via the ADK's LiteLlm model wrapper
(ADK is Gemini-native by default; ANTHROPIC_API_KEY is the real key
present in .env, so routing through LiteLlm is used instead of the Google
GenAI API).

Usage:
    python examples/google_adk_example.py
"""
import os

from dotenv import load_dotenv

load_dotenv()

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm

agent = Agent(
    name="assistant",
    model=LiteLlm(
        model="anthropic/claude-haiku-4-5-20251001",
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        max_tokens=200,
    ),
    instruction="You are a helpful assistant that responds to the given input.",
)


if __name__ == "__main__":
    import asyncio

    from google.adk.runners import InMemoryRunner
    from google.genai import types

    async def main() -> None:
        runner  = InMemoryRunner(agent=agent, app_name="safelabs_eval")
        session = await runner.session_service.create_session(
            app_name=runner.app_name, user_id="safelabs"
        )
        message = types.Content(
            role="user", parts=[types.Part(text="Say hello in one sentence.")]
        )
        async for event in runner.run_async(
            user_id="safelabs", session_id=session.id, new_message=message
        ):
            if event.is_final_response() and event.content:
                print("".join(p.text for p in event.content.parts if p.text))

    asyncio.run(main())
