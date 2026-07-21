"""Thin wrapper over one OpenAI-compatible client.

Not a provider-abstraction layer -- just enough to point the standard
OpenAI SDK at whatever base_url/model/key are in .env and get a text
completion back. Any OpenAI-compatible endpoint (Groq, Ollama,
OpenRouter, NIM) works through this same object.
"""
from __future__ import annotations

from openai import OpenAI

from app.config import Settings, load_settings


class LLMClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
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
        resp = self._client.chat.completions.create(
            model=self.settings.llm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""
