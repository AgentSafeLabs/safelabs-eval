"""Tests for the agent adapter layer."""
from __future__ import annotations
import pytest
from safelabs.agents.base import AgentAdapter
from safelabs.agents.schemas import AgentResponse

class _SlowAdapter(AgentAdapter):
    @property
    def adapter_type(self): return "slow"
    async def _execute(self, prompt):
        import asyncio; await asyncio.sleep(999); return AgentResponse(output="never")

class _ErrorAdapter(AgentAdapter):
    @property
    def adapter_type(self): return "error"
    async def _execute(self, prompt): raise RuntimeError("Connection refused")

class _EchoAdapter(AgentAdapter):
    @property
    def adapter_type(self): return "echo"
    async def _execute(self, prompt): return AgentResponse(output=f"echo: {prompt}", latency_ms=1.0)

@pytest.mark.asyncio
async def test_timeout_returns_error_response():
    r = await _SlowAdapter(timeout=0.01).execute("test")
    assert r.error and "timed out" in r.error.lower() and r.output == ""

@pytest.mark.asyncio
async def test_exception_returns_error_response():
    r = await _ErrorAdapter().execute("test")
    assert r.error and "Connection refused" in r.error

@pytest.mark.asyncio
async def test_successful_execution():
    r = await _EchoAdapter().execute("hello")
    assert r.output == "echo: hello" and r.error is None and r.succeeded

def test_agent_response_raw_field():
    r = AgentResponse(output="ok", raw={"response": "ok", "tokens": 10})
    assert r.raw == {"response": "ok", "tokens": 10}

def test_agent_response_succeeded_false_on_empty():
    assert not AgentResponse(output="").succeeded

def test_agent_response_succeeded_true():
    assert AgentResponse(output="hello", latency_ms=10.0).succeeded
