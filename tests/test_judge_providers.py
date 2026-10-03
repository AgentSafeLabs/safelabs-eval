import importlib.util
import inspect
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from safelabs.judge.labels import JudgeLabel
from safelabs.judge.providers import AnthropicBackend, GoogleBackend, OpenAIBackend
from safelabs.judge.result import ContentGuardError
from tests.judge_helpers import make_template

REPLY = json.dumps({"label": "PARTIAL_COMPLIANCE", "confidence": 3, "rationale": "synthetic"})


class Recorder:
    def __init__(self, result):
        self.kwargs, self.result = None, result

    async def create(self, **kw):
        self.kwargs = kw
        return self.result

    generate_content = create


async def test_anthropic_request_and_parsing():
    rec = Recorder(SimpleNamespace(content=[SimpleNamespace(type="text", text=REPLY)], usage=SimpleNamespace(input_tokens=11, output_tokens=7)))
    client = SimpleNamespace(messages=rec)
    b = AnthropicBackend("model-x", make_template(), client=client, max_tokens=123)
    r = await b.judge("the prompt", "the response", "ASI01")
    kw = rec.kwargs
    assert kw["model"] == "model-x" and kw["max_tokens"] == 123 and kw["temperature"] == 0 and kw["system"].startswith("You label")
    assert kw["messages"] == [{"role": "user", "content": kw["messages"][0]["content"]}] and "the response" in kw["messages"][0]["content"] and "ASI01" in kw["messages"][0]["content"]
    assert r.label == JudgeLabel.PARTIAL_COMPLIANCE and r.confidence == 3 and r.usage == {"input_tokens": 11, "output_tokens": 7}
    assert r.backend_id == "anthropic:model-x" and b.is_local is False and r.error is None


async def test_anthropic_ignores_non_text_blocks_and_records_errors():
    blocks = [SimpleNamespace(type="thinking", text="hidden"), SimpleNamespace(type="text", text=REPLY)]
    b = AnthropicBackend("m", make_template(), client=SimpleNamespace(messages=Recorder(SimpleNamespace(content=blocks, usage=None))))
    r = await b.judge("p", "r", "ASI01")
    assert r.label == JudgeLabel.PARTIAL_COMPLIANCE and r.usage == {"input_tokens": None, "output_tokens": None}

    class Boom:
        async def create(self, **kw):
            raise TimeoutError("synthetic timeout")

    r = await AnthropicBackend("m", make_template(), client=SimpleNamespace(messages=Boom())).judge("p", "r", "ASI01")
    assert r.label == JudgeLabel.UNCLEAR and "TimeoutError" in r.error


def openai_reply(text=REPLY):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=SimpleNamespace(prompt_tokens=20, completion_tokens=5))


async def test_openai_request_and_parsing_and_token_param():
    rec = Recorder(openai_reply())
    client = SimpleNamespace(chat=SimpleNamespace(completions=rec))
    b = OpenAIBackend("model-y", make_template(), client=client, max_tokens=99)
    r = await b.judge("p", "the response", "ASI02")
    kw = rec.kwargs
    assert kw["model"] == "model-y" and kw["temperature"] == 0 and kw["max_completion_tokens"] == 99 and "max_tokens" not in kw
    assert [m["role"] for m in kw["messages"]] == ["system", "user"] and "the response" in kw["messages"][1]["content"]
    assert r.label == JudgeLabel.PARTIAL_COMPLIANCE and r.usage == {"input_tokens": 20, "output_tokens": 5} and r.backend_id == "openai:model-y"
    rec2 = Recorder(openai_reply())
    await OpenAIBackend("m", make_template(), client=SimpleNamespace(chat=SimpleNamespace(completions=rec2)), max_tokens_param="max_tokens").judge("p", "r", "ASI01")
    assert "max_tokens" in rec2.kwargs and "max_completion_tokens" not in rec2.kwargs
    r = await OpenAIBackend("m", make_template(), client=SimpleNamespace(chat=SimpleNamespace(completions=Recorder(openai_reply(None))))).judge("p", "r", "ASI01")
    assert r.label == JudgeLabel.UNCLEAR and r.parsed_ok is False


@pytest.mark.parametrize("url,local", [("http://localhost:11434/v1", True), ("http://127.0.0.1:8000/v1", True), ("http://[::1]:8000/v1", True),
                                       ("https://api.example.com/v1", False), (None, False), ("http://localhost.evil.example/v1", False)])
def test_openai_backend_is_local_only_for_loopback_urls(url, local):
    assert OpenAIBackend("m", make_template(), client=object(), base_url=url).is_local is local


async def test_content_guard_for_provider_backends():
    ext = OpenAIBackend("m", make_template(), client=SimpleNamespace(chat=SimpleNamespace(completions=Recorder(openai_reply()))))
    with pytest.raises(ContentGuardError):
        await ext.judge("p", "r", "ASI01", functional_content=True)
    rec = Recorder(openai_reply())
    loc = OpenAIBackend("m", make_template(), client=SimpleNamespace(chat=SimpleNamespace(completions=rec)), base_url="http://localhost:1234/v1")
    assert (await loc.judge("p", "r", "ASI01", functional_content=True)).label == JudgeLabel.PARTIAL_COMPLIANCE and rec.kwargs is not None
    with pytest.raises(ContentGuardError):
        await AnthropicBackend("m", make_template(), client=object()).judge("p", "r", "ASI01", functional_content=True)
    with pytest.raises(ContentGuardError):
        await GoogleBackend("m", make_template(), client=object()).judge("p", "r", "ASI01", functional_content=True)


async def test_google_request_and_parsing():
    rec = Recorder(SimpleNamespace(text=REPLY, usage_metadata=SimpleNamespace(prompt_token_count=31, candidates_token_count=9)))
    client = SimpleNamespace(aio=SimpleNamespace(models=rec))
    b = GoogleBackend("model-z", make_template(), client=client, max_tokens=77)
    r = await b.judge("p", "the response", "ASI03")
    kw = rec.kwargs
    assert kw["model"] == "model-z" and "the response" in kw["contents"]
    assert kw["config"] == {"system_instruction": make_template().system, "temperature": 0, "max_output_tokens": 77}
    assert r.label == JudgeLabel.PARTIAL_COMPLIANCE and r.usage == {"input_tokens": 31, "output_tokens": 9} and r.backend_id == "google:model-z"


async def test_injected_client_means_the_sdk_is_never_loaded(monkeypatch):
    def boom(self):
        raise AssertionError("SDK client must not be created when a client is injected")
    for cls in (AnthropicBackend, OpenAIBackend, GoogleBackend):
        monkeypatch.setattr(cls, "_make_client", boom)
    rec = Recorder(openai_reply())
    await OpenAIBackend("m", make_template(), client=SimpleNamespace(chat=SimpleNamespace(completions=rec))).judge("p", "r", "ASI01")


def test_importing_the_package_does_not_import_any_sdk():
    code = "import sys, safelabs.judge, safelabs.judge.providers, safelabs.judge.cli; bad=[m for m in ('anthropic','openai','google.genai') if m in sys.modules]; assert not bad, bad"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr


def _has(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def _params(fn):
    return set(inspect.signature(fn).parameters)


@pytest.mark.skipif(not _has("anthropic"), reason="anthropic SDK not installed")
def test_anthropic_names_exist_in_the_installed_sdk():
    import anthropic
    from anthropic.resources.messages import AsyncMessages
    assert hasattr(anthropic, "AsyncAnthropic")
    assert {"model", "max_tokens", "system", "messages", "temperature"} <= _params(AsyncMessages.create)


@pytest.mark.skipif(not _has("openai"), reason="openai SDK not installed")
def test_openai_names_exist_in_the_installed_sdk():
    import openai
    from openai.resources.chat.completions import AsyncCompletions
    assert hasattr(openai, "AsyncOpenAI")
    assert {"model", "messages", "temperature", "max_completion_tokens"} <= _params(AsyncCompletions.create)


@pytest.mark.skipif(not _has("google.genai"), reason="google-genai SDK not installed")
def test_google_genai_names_exist_in_the_installed_sdk():
    from google import genai
    from google.genai import models, types
    assert hasattr(genai, "Client") and hasattr(models, "AsyncModels")
    assert {"model", "contents", "config"} <= _params(models.AsyncModels.generate_content)
    assert {"system_instruction", "temperature", "max_output_tokens"} <= set(types.GenerateContentConfig.model_fields)
