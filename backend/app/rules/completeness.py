"""Completeness rule: null_pct > threshold on any column.

Severity scales with null_pct, and is bumped up a level when the column
plays an FK role (a child_column in a discovered relationship) -- a
missing foreign key is a worse problem than a missing free-text remark.
A PK-role bump isn't meaningful: a column can only be a PK candidate if
it has zero nulls, so completeness never fires on one.
"""
from __future__ import annotations

import pandas as pd

from app.rules.registry import record_rule
from app.rules.describe import share_text

SEVERITY_ORDER = ["low", "medium", "high"]
RULE_ID = "completeness_null_pct"
MAX_SAMPLE_ROWS = 5


def _base_severity(null_pct: float) -> str:
    if null_pct > 50:
        return "high"
    if null_pct > 20:
        return "medium"
    return "low"


def _bump(severity: str) -> str:
    idx = SEVERITY_ORDER.index(severity)
    return SEVERITY_ORDER[min(idx + 1, len(SEVERITY_ORDER) - 1)]


def check_completeness(
    table_name: str,
    df: pd.DataFrame,
    profile: dict,
    fk_columns: set[str] | None = None,
    threshold_pct: float = 5.0,
    rules: list[dict] | None = None,
) -> list[dict]:
    fk_columns = fk_columns or set()
    issues = []

    for col, col_stats in profile["columns"].items():
        null_pct = col_stats["null_pct"]
        fires = null_pct > threshold_pct
        key = record_rule(
            rules, RULE_ID, "completeness", table_name, col,
            parameters={"max_null_pct": threshold_pct, "fk_role": col in fk_columns},
            evidence={"null_pct": null_pct, "null_count": col_stats["null_count"],
                      "row_count": profile["row_count"]},
            violation_count=col_stats["null_count"] if fires else 0,
        )
        if not fires:
            continue

        severity = _base_severity(null_pct)
        if col in fk_columns:
            severity = _bump(severity)

        sample_rows = df.index[df[col].isna()][:MAX_SAMPLE_ROWS].tolist()
        issues.append(
            {
                "table_name": table_name,
                "column_name": col,
                "dimension": "completeness",
                "source": "rule",
                "error_type": "type1",
                "severity": severity,
                "description": (
                    f"{col} is empty in {col_stats['null_count']:,} of {profile['row_count']:,} rows "
                    f"({share_text(col_stats['null_count'], profile['row_count'])})."
                ),
                "recommendation": "Find out why this is left blank. If it matters to the business, make it a required field when data is entered.",
                "fixable": "no",
                "status": "flagged",
                "row_reference": {
                    "count": col_stats["null_count"],
                    "sample_rows": sample_rows,
                },
                "rule_id": RULE_ID,
                "rule_key": key,
            }
        )
    return issues
