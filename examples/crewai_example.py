"""
examples/crewai_example.py — minimal real CrewAI setup for adapter verification.

Builds the smallest possible Crew (one Agent, one Task) that accepts a
prompt via `inputs={"input": ...}` and returns it through CrewOutput.raw.
Uses Claude Haiku with a low max_tokens cap to keep the real API call cheap.

Usage:
    python examples/crewai_example.py
"""
import os

from dotenv import load_dotenv

load_dotenv()

from crewai import LLM, Agent, Crew, Task

llm = LLM(
    model="claude-haiku-4-5-20251001",
    max_tokens=200,
)

agent = Agent(
    role="helpful assistant",
    goal="Respond helpfully and safely to the given input",
    backstory="A generic assistant that responds to whatever input it is given.",
    llm=llm,
)

task = Task(
    description="{input}",
    agent=agent,
    expected_output="A direct text response to the input.",
)

crew = Crew(agents=[agent], tasks=[task])


if __name__ == "__main__":
    result = crew.kickoff(inputs={"input": "Say hello in one sentence."})
    print(result.raw)
