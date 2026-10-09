"""Thin wrapper over one OpenAI-compatible client.

Not a provider-abstraction layer -- just enough to point the standard
OpenAI SDK at whatever base_url/model/key are in .env and get a text
completion back. Any OpenAI-compatible endpoint (Groq, Ollama,
OpenRouter, NIM) works through this same object.
"""
from __future__ import annotations

import time

from openai import APIStatusError, OpenAI, RateLimitError

from app.config import Settings, load_settings

# Free/on-demand tiers cap tokens-per-minute; when several table audits stack up
# the endpoint returns 429 (rate limit) or 413 (request too large for the TPM
# window). Waiting a beat and retrying clears it.
_RATE_LIMIT_STATUSES = {413, 429}
_MAX_RETRIES = 4
_BACKOFF_SECONDS = 20


class LLMClient:
    def __init__(self, settings: Settings | None = None):
        if settings is None:
            from app.llm.selection import selected_settings

            settings = selected_settings(load_settings())  # admin-chosen .env option
        self.settings = settings
        self._client = OpenAI(
            base_url=self.settings.llm_base_url,
            api_key=self.settings.llm_api_key or "not-needed",  # local endpoints ignore the key
        )

    def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> str:
        """Single chat completion; returns the raw assistant text."""
        return self.chat(system, [{"role": "user", "content": user}], temperature, max_tokens)

    def chat(
        self,
        system: str,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> str:
        """Multi-turn completion. The API is stateless, so callers resend the
        conversation history they want the model to see.

        Retries with a fixed backoff on rate-limit / request-too-large-for-TPM
        errors so a batch of table audits survives a tight per-minute quota.
        """
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._client.chat.completions.create(
                    model=self.settings.llm_model,
                    messages=[{"role": "system", "content": system}, *messages],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return resp.choices[0].message.content or ""
            except (RateLimitError, APIStatusError) as exc:
                status = getattr(exc, "status_code", None)
                if status in _RATE_LIMIT_STATUSES and attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_SECONDS)
                    continue
                raise
        return ""
