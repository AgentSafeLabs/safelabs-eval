"""safelabs/judge/providers.py: real provider backends. Optional extras; the SDKs are imported only
inside ``_make_client`` (never at package import), and only when no ``client`` was injected.
Tests inject a mock client. The SDK reads its own API key from the environment; this module does
not read keys. All requests use temperature 0.

Installed-SDK API (checked against .venv site-packages, 2026-10-03):
  anthropic 0.104.1: AsyncAnthropic (_client.py:534); AsyncMessages.create(model, max_tokens, system,
    messages, temperature) (resources/messages/messages.py:1557); reply .content blocks with .text
    (types/message.py:30, text_block.py:12); .usage.input_tokens / .output_tokens (types/usage.py:26,29).
  openai 2.44.0: AsyncOpenAI (_client.py:701); AsyncCompletions.create(model, messages, temperature,
    max_completion_tokens) (resources/chat/completions/completions.py:1623, 261, 281); reply
    .choices[0].message.content (types/chat/chat_completion.py:199, 56); .usage.prompt_tokens /
    .completion_tokens (types/completion_usage.py:50, 47).
  google-genai 2.23.0: Client (client.py:257), .aio (client.py:399) with .models (client.py:161);
    AsyncModels.generate_content(model, contents, config) (models.py:8289) where config may be a dict
    (GenerateContentConfigOrDict) with system_instruction, temperature, max_output_tokens
    (types.py:6460, 6467, 6496); reply .text (types.py:8697); .usage_metadata.prompt_token_count /
    .candidates_token_count (types.py:8612, 8468, 8460).
"""

from __future__ import annotations

from urllib.parse import urlparse

from safelabs.judge.backends import BaseJudgeBackend
from safelabs.judge.template import JudgeTemplate

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


class AnthropicBackend(BaseJudgeBackend):
    """Extra: ``pip install safelabs-eval[judge-anthropic]``. External provider (is_local False)."""

    is_local = False

    def __init__(self, model: str, template: JudgeTemplate, *, client=None, max_tokens: int = 300) -> None:
        super().__init__(f"anthropic:{model}", template)
        self.model, self.max_tokens, self._client = model, max_tokens, client

    def _make_client(self):
        import anthropic  # optional extra, imported on first real use only
        return anthropic.AsyncAnthropic()

    async def _complete(self, system, user, item):
        if self._client is None:
            self._client = self._make_client()
        resp = await self._client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=system, temperature=0,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(getattr(b, "text", "") or "" for b in resp.content if getattr(b, "type", "text") == "text")
        u = getattr(resp, "usage", None)
        return text, {"input_tokens": getattr(u, "input_tokens", None), "output_tokens": getattr(u, "output_tokens", None)}


class OpenAIBackend(BaseJudgeBackend):
    """Extra: ``pip install safelabs-eval[judge-openai]``. With ``base_url`` pointing at this machine
    (localhost, 127.0.0.1, ::1) the backend counts as local, for an OpenAI-compatible local server;
    otherwise it is external."""

    def __init__(self, model: str, template: JudgeTemplate, *, client=None, base_url: str | None = None,
                 max_tokens: int = 300, max_tokens_param: str = "max_completion_tokens") -> None:
        host = (urlparse(base_url).hostname or "") if base_url else ""
        self.is_local = bool(base_url) and host in _LOCAL_HOSTS
        super().__init__(f"{'local-openai-compat' if self.is_local else 'openai'}:{model}", template)
        self.model, self.max_tokens, self.max_tokens_param = model, max_tokens, max_tokens_param
        self.base_url, self._client = base_url, client

    def _make_client(self):
        import openai  # optional extra, imported on first real use only
        return openai.AsyncOpenAI(base_url=self.base_url) if self.base_url else openai.AsyncOpenAI()

    async def _complete(self, system, user, item):
        if self._client is None:
            self._client = self._make_client()
        resp = await self._client.chat.completions.create(
            model=self.model, temperature=0, **{self.max_tokens_param: self.max_tokens},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        text = resp.choices[0].message.content or ""
        u = getattr(resp, "usage", None)
        return text, {"input_tokens": getattr(u, "prompt_tokens", None), "output_tokens": getattr(u, "completion_tokens", None)}


class GoogleBackend(BaseJudgeBackend):
    """Extra: ``pip install safelabs-eval[judge-google]``. External provider (is_local False)."""

    is_local = False

    def __init__(self, model: str, template: JudgeTemplate, *, client=None, max_tokens: int = 300) -> None:
        super().__init__(f"google:{model}", template)
        self.model, self.max_tokens, self._client = model, max_tokens, client

    def _make_client(self):
        from google import genai  # optional extra, imported on first real use only
        return genai.Client()

    async def _complete(self, system, user, item):
        if self._client is None:
            self._client = self._make_client()
        resp = await self._client.aio.models.generate_content(
            model=self.model, contents=user,
            config={"system_instruction": system, "temperature": 0, "max_output_tokens": self.max_tokens},
        )
        u = getattr(resp, "usage_metadata", None)
        return resp.text or "", {"input_tokens": getattr(u, "prompt_token_count", None), "output_tokens": getattr(u, "candidates_token_count", None)}
