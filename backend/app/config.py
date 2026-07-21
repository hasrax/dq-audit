"""Configuration loaded from backend/.env (never hard-coded, never committed).

One place reads the environment so the rest of the code takes typed
settings. The LLM section is deliberately a plain (base_url, model, key)
triple -- the "one OpenAI-compatible client" decision -- so switching
providers (Groq / Ollama / OpenRouter / NIM) is a .env edit, not a code
change.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


@dataclass(frozen=True)
class Settings:
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    db_host: str
    db_port: str
    db_name: str
    db_user: str
    db_password: str

    @property
    def db_url(self) -> str:
        """psycopg-style URL; password is percent-encoded (it may contain @ etc.)."""
        return (
            f"postgresql://{self.db_user}:{quote(self.db_password, safe='')}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


def load_settings(env_path: Path | None = None) -> Settings:
    load_dotenv(env_path or ENV_PATH)
    return Settings(
        llm_base_url=os.environ.get("LLM_BASE_URL", ""),
        llm_model=os.environ.get("LLM_MODEL", ""),
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        db_host=os.environ.get("DB_HOST", "localhost"),
        db_port=os.environ.get("DB_PORT", "5432"),
        db_name=os.environ.get("DB_NAME", "dq_audit"),
        db_user=os.environ.get("DB_USER", "postgres"),
        db_password=os.environ.get("DB_PASSWORD", ""),
    )
