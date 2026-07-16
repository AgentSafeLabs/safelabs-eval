"""
safelabs-eval
=============
OWASP Agentic Security Initiative (ASI) Top 10 — red-teaming and evaluation
framework for AI agents.

Quick start
-----------
    from safelabs.prompts import load_library
    from safelabs.agents import HttpAdapter
    from safelabs.scoring import Scorer

    agent   = HttpAdapter(base_url="http://localhost:8000/chat")
    library = load_library()
    scorer  = Scorer()

    prompts = library.by_category("ASI01")
    for entry in prompts:
        response = await agent.execute(entry.prompt)
        result   = await scorer.score("prompt_injection", entry.prompt, response.output)
        print(result.verdict, result.confidence)

GitHub: https://github.com/AgentSafeLabs/safelabs-eval
"""

__version__ = "0.2.2"
__author__  = "Waqar Javed"
__license__ = "Apache-2.0"

from safelabs.runner import EvalRecord, EvalResult, run_eval  # noqa: E402

__all__ = ["EvalRecord", "EvalResult", "run_eval"]
