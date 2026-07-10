"""
examples/autogen_example.py — minimal real ag2 (autogen) setup for adapter
verification.

Builds the smallest possible agent pair: one ConversableAgent under test
(backed by a real Claude Haiku call) and one UserProxyAgent that sends it
a single message and stops (no auto-reply, no code execution).

Usage:
    python examples/autogen_example.py
"""
import os

from dotenv import load_dotenv

load_dotenv()

from autogen import ConversableAgent, UserProxyAgent

llm_config = {
    "config_list": [
        {
            "model": "claude-haiku-4-5-20251001",
            "api_key": os.getenv("ANTHROPIC_API_KEY"),
            "api_type": "anthropic",
        }
    ],
    "max_tokens": 200,
}

agent = ConversableAgent(
    name="assistant",
    system_message="You are a helpful assistant that responds to the given input.",
    llm_config=llm_config,
    human_input_mode="NEVER",
)

user_proxy = UserProxyAgent(
    name="user",
    human_input_mode="NEVER",
    max_consecutive_auto_reply=0,
    code_execution_config=False,
)


if __name__ == "__main__":
    result = user_proxy.initiate_chat(agent, message="Say hello in one sentence.", max_turns=1)
    print(result.chat_history[-1]["content"])
