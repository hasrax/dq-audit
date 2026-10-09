"""Strict-JSON LLM calls: strip markdown fences, parse, retry once.

Shared by every Layer 2/3 prompt that expects a JSON object back (the
auditor, the schema documentation generator), so the parse-and-retry
policy is one citable mechanism rather than a copy per caller.
"""
from __future__ import annotations

import json
import re

from app.llm.client import LLMClient

_FENCE_RE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)
RETRY_NUDGE = "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON object, no other text."


def extract_json(raw: str) -> dict:
    """Strip markdown fences / surrounding prose and parse the JSON object."""
    text = _FENCE_RE.sub("", raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def complete_json(client: LLMClient, system: str, user: str, **kwargs) -> dict | None:
    """One completion parsed as a JSON object, retrying once on a parse
    failure; None if both replies are unparseable (callers degrade
    gracefully rather than aborting a whole-database run)."""
    for prompt in (user, user + RETRY_NUDGE):
        try:
            parsed = extract_json(client.complete(system, prompt, **kwargs))
        except (json.JSONDecodeError, ValueError):
            continue
        return parsed if isinstance(parsed, dict) else None
    return None
