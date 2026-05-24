"""
examples/quickstart.py — minimal red-teaming example.

Usage:
    python examples/quickstart.py
    TARGET_URL=http://my-agent/chat python examples/quickstart.py
"""
import asyncio, os
from safelabs.agents import HttpAdapter
from safelabs.prompts import get_prompts_for_category
from safelabs.scoring import Scorer
from safelabs.scoring.models import VerdictLevel

TARGET_URL = os.getenv("TARGET_URL", "http://localhost:8000/chat")

async def main():
    print(f"\nsafelabs-eval Quick Start | Target: {TARGET_URL}")
    print("─" * 50)
    agent  = HttpAdapter(base_url=TARGET_URL)
    scorer = Scorer()
    passed = failed = vulnerable = 0
    for entry in get_prompts_for_category("ASI01"):
        print(f"\n[{entry.id}] {entry.severity.upper()}")
        response = await agent.execute(entry.prompt)
        if response.error: print(f"  ERROR: {response.error}"); continue
        result = await scorer.score("prompt_injection", entry.prompt, response.output)
        print(f"  Verdict: {result.verdict.value.upper()}  ({result.confidence:.0%})")
        if result.verdict == VerdictLevel.PASS: passed += 1
        elif result.verdict == VerdictLevel.VULNERABLE:
            vulnerable += 1; print(f"  Fix: {result.remediation_hint}")
        else: failed += 1
    print(f"\n{'─'*50}\nResults: {passed} PASS | {failed} FAIL | {vulnerable} VULNERABLE")

if __name__ == "__main__":
    asyncio.run(main())
