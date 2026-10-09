"""Admin-selectable LLM provider/model, from options defined in .env.

Still ONE OpenAI-compatible client: an option is just a (base_url, model,
key) triple. Options are listed in .env as

    LLM_OPTIONS=Groq Llama 3.3 70B|https://api.groq.com/openai/v1|llama-3.3-70b-versatile|LLM_API_KEY;Ollama Qwen 2.5 7B|http://localhost:11434/v1|qwen2.5:7b|

-- name | base_url | model | NAME of the env var holding the key (blank
for local endpoints). Keys themselves never appear in the option string
or the database. Without LLM_OPTIONS the single LLM_BASE_URL/LLM_MODEL
pair is the only option. The admin's choice is stored in app_settings;
pseudonymization is applied whichever option is active.
"""
from __future__ import annotations

import dataclasses
import os
from urllib.parse import urlparse

from app.config import Settings, load_settings

SETTING_KEY = "llm_option"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def llm_options(settings: Settings | None = None) -> list[dict]:
    settings = settings or load_settings()
    raw = os.environ.get("LLM_OPTIONS", "").strip()
    options = []
    for chunk in filter(None, (c.strip() for c in raw.split(";"))):
        parts = [p.strip() for p in chunk.split("|")] + [""] * 4
        name, base_url, model, key_env = parts[:4]
        if name and base_url and model:
            options.append({"name": name, "base_url": base_url, "model": model, "key_env": key_env})
    if not options:
        options.append({"name": f"Default ({settings.llm_model or 'not set'})",
                        "base_url": settings.llm_base_url, "model": settings.llm_model, "key_env": "LLM_API_KEY"})
    return options


def is_local(option: dict) -> bool:
    return (urlparse(option["base_url"]).hostname or "") in LOCAL_HOSTS


def get_selected_name(conn) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = %s", (SETTING_KEY,)).fetchone()
    return row[0] if row else None


def set_selected(conn, name: str, updated_by: str | None = None) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value, updated_by) VALUES (%s, %s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, "
        "updated_at = now()",
        (SETTING_KEY, name, updated_by),
    )
    conn.commit()


def active_option(conn=None, settings: Settings | None = None) -> dict:
    options = llm_options(settings)
    name = None
    try:
        if conn is None:
            from app.db import repository

            with repository.connect(settings) as own:
                name = get_selected_name(own)
        else:
            name = get_selected_name(conn)
    except Exception:  # noqa: BLE001 -- no DB / no table yet: fall back to the first option
        name = None
    return next((o for o in options if o["name"] == name), options[0])


def selected_settings(settings: Settings | None = None) -> Settings:
    """Settings with the LLM fields replaced by the admin-selected option."""
    settings = settings or load_settings()
    option = active_option(settings=settings)
    key = os.environ.get(option["key_env"], "") if option["key_env"] else ""
    return dataclasses.replace(settings, llm_base_url=option["base_url"],
                               llm_model=option["model"], llm_api_key=key)
