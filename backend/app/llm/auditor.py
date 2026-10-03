"""Layer 2 LLM auditor: profile + pseudonymized sample -> Type 2 issues.

Only the profile JSON, the discovered relationships, and a small
pseudonymized sample ever reach the model (selective-invocation
principle). Every issue comes back normalized into the same ontology
tuple the rules engine emits, tagged source=llm / error_type=type2, so
Layer 3 triage and the complementarity analysis treat both sources
uniformly.
"""
from __future__ import annotations

from app.llm.client import LLMClient
from app.llm.prompt import SYSTEM_PROMPT, build_user_payload
from app.llm.structured import complete_json
from app.llm.structured import extract_json as _extract_json  # noqa: F401 -- re-exported for tests

_SEVERITY_MAP = {
    "critical": "high", "high": "high", "major": "high",
    "medium": "medium", "moderate": "medium",
    "low": "low", "minor": "low", "info": "low",
}
_ALLOWED_DIMENSIONS = {"completeness", "validity", "consistency", "uniqueness", "timeliness", "cross_table", "accuracy"}


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
    schema_context: str = "",
) -> list[dict]:
    """Run the LLM auditor on one table's profile + pseudonymized sample.

    schema_context is the human-verified schema documentation for this
    table (see app.schema_docs.store.get_schema_context); it is prepended
    to the payload so the model reads columns by their verified meaning.
    Empty -> the prompt is exactly as before docs existed.

    Retries once if the model's first reply isn't parseable JSON, then
    gives up and returns [] rather than raising -- a single unparseable
    table must not abort a whole-database audit.
    """
    client = client or LLMClient()
    user = build_user_payload(profile, sample, relationships)
    if schema_context:
        user = schema_context + "\n\n" + user

    parsed = complete_json(client, SYSTEM_PROMPT, user)
    issues = parsed.get("issues", []) if parsed else []
    return [_normalize_issue(i, profile["table_name"]) for i in issues if isinstance(i, dict)]
