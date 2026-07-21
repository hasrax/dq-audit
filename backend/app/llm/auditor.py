"""Layer 2 LLM auditor: profile + pseudonymized sample -> Type 2 issues.

Only the profile JSON, the discovered relationships, and a small
pseudonymized sample ever reach the model (selective-invocation
principle). Every issue comes back normalized into the same ontology
tuple the rules engine emits, tagged source=llm / error_type=type2, so
Layer 3 triage and the complementarity analysis treat both sources
uniformly.
"""
from __future__ import annotations

import json
import re

from app.llm.client import LLMClient
from app.llm.prompt import SYSTEM_PROMPT, build_user_payload

_FENCE_RE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)
_SEVERITY_MAP = {
    "critical": "high", "high": "high", "major": "high",
    "medium": "medium", "moderate": "medium",
    "low": "low", "minor": "low", "info": "low",
}
_ALLOWED_DIMENSIONS = {"completeness", "validity", "consistency", "uniqueness", "timeliness", "cross_table", "accuracy"}
_RETRY_NUDGE = "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON object, no other text."


def _extract_json(raw: str) -> dict:
    """Strip markdown fences / surrounding prose and parse the JSON object."""
    text = _FENCE_RE.sub("", raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def _normalize_issue(raw_issue: dict, table_name: str) -> dict:
    severity = _SEVERITY_MAP.get(str(raw_issue.get("severity", "")).lower(), "medium")
    dimension = str(raw_issue.get("dimension", "consistency")).lower().replace(" ", "_")
    if dimension not in _ALLOWED_DIMENSIONS:
        dimension = "consistency"
    fixable = "yes" if str(raw_issue.get("fixable", "")).lower() in ("yes", "true", "1") else "no"
    row_ref = raw_issue.get("row_ref")

    return {
        "table_name": table_name,
        "column_name": raw_issue.get("column"),
        "dimension": dimension,
        "source": "llm",
        "error_type": "type2",
        "severity": severity,
        "description": str(raw_issue.get("explanation", "")).strip(),
        "recommendation": str(raw_issue.get("recommendation", "")).strip(),
        "fixable": fixable,
        "status": "flagged",
        "row_reference": {"count": 1, "sample_rows": [row_ref] if row_ref is not None else []},
        "rule_id": None,
    }


def audit_table(
    profile: dict,
    sample: dict,
    relationships: list[dict],
    client: LLMClient | None = None,
) -> list[dict]:
    """Run the LLM auditor on one table's profile + pseudonymized sample.

    Retries once if the model's first reply isn't parseable JSON, then
    gives up and returns [] rather than raising -- a single unparseable
    table must not abort a whole-database audit.
    """
    client = client or LLMClient()
    user = build_user_payload(profile, sample, relationships)

    raw = client.complete(SYSTEM_PROMPT, user)
    try:
        parsed = _extract_json(raw)
    except (json.JSONDecodeError, ValueError):
        raw = client.complete(SYSTEM_PROMPT, user + _RETRY_NUDGE)
        try:
            parsed = _extract_json(raw)
        except (json.JSONDecodeError, ValueError):
            return []

    issues = parsed.get("issues", []) if isinstance(parsed, dict) else []
    return [_normalize_issue(i, profile["table_name"]) for i in issues if isinstance(i, dict)]
