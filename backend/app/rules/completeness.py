"""Completeness rule: null_pct > threshold on any column.

Severity scales with null_pct, and is bumped up a level when the column
plays an FK role (a child_column in a discovered relationship) -- a
missing foreign key is a worse problem than a missing free-text remark.
A PK-role bump isn't meaningful: a column can only be a PK candidate if
it has zero nulls, so completeness never fires on one.
"""
from __future__ import annotations

import pandas as pd

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
) -> list[dict]:
    fk_columns = fk_columns or set()
    issues = []

    for col, col_stats in profile["columns"].items():
        null_pct = col_stats["null_pct"]
        if null_pct <= threshold_pct:
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
                    f"{col} is {null_pct}% null "
                    f"({col_stats['null_count']} of {profile['row_count']} rows)."
                ),
                "recommendation": "Investigate why values are missing; consider making this field mandatory at entry if it is business-critical.",
                "fixable": "no",
                "status": "flagged",
                "row_reference": {
                    "count": col_stats["null_count"],
                    "sample_rows": sample_rows,
                },
                "rule_id": RULE_ID,
            }
        )
    return issues
