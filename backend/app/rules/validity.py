"""Validity rule: negative values in numeric columns.

Applied uniformly to every int/decimal column -- there is no column
name to tell us "this one is a Price/Qty/Amount field". Severity is
inverse to how common the negative values are: a handful of negatives
in an otherwise all-positive column looks like an outlier/error (high);
negatives making up a large share of the column look more like an
intentionally signed field (a discount or return-adjustment column,
as seen for real in Dis_InvoiceHeader) than a data error (low). Either
way it's flagged -- human triage makes the final call, and that
accept/reject signal is itself evaluation data.
"""
from __future__ import annotations

import pandas as pd

from app.rules.registry import record_rule

RULE_ID = "validity_negative_value"
MAX_SAMPLE_ROWS = 5
NUMERIC_TYPES = ("int", "decimal")


def _severity(negative_pct: float) -> str:
    if negative_pct <= 1:
        return "high"
    if negative_pct <= 20:
        return "medium"
    return "low"


def check_validity(table_name: str, df: pd.DataFrame, profile: dict, rules: list[dict] | None = None) -> list[dict]:
    issues = []

    for col, col_stats in profile["columns"].items():
        if col_stats["type"] not in NUMERIC_TYPES:
            continue
        if col_stats["min"] is None or col_stats["min"] >= 0:
            record_rule(
                rules, RULE_ID, "validity", table_name, col,
                parameters={"min_allowed": 0},
                evidence={"type": col_stats["type"], "min": col_stats["min"], "negative_count": 0},
                violation_count=0,
            )
            continue

        numeric = pd.to_numeric(df[col], errors="coerce")
        negative_mask = numeric < 0
        negative_count = int(negative_mask.sum())
        negative_pct = round(negative_count / profile["row_count"] * 100, 2)
        sample_rows = df.index[negative_mask][:MAX_SAMPLE_ROWS].tolist()
        key = record_rule(
            rules, RULE_ID, "validity", table_name, col,
            parameters={"min_allowed": 0},
            evidence={"type": col_stats["type"], "min": col_stats["min"],
                      "negative_count": negative_count, "negative_pct": negative_pct},
            violation_count=negative_count,
        )

        issues.append(
            {
                "table_name": table_name,
                "column_name": col,
                "dimension": "validity",
                "source": "rule",
                "error_type": "type1",
                "severity": _severity(negative_pct),
                "description": (
                    f"{col} has {negative_count} negative values "
                    f"({negative_pct}% of {profile['row_count']} rows); "
                    f"min={col_stats['min']}."
                ),
                "recommendation": "Confirm whether negative values are intentional (e.g. a discount or return adjustment) or data-entry errors.",
                "fixable": "no",
                "status": "flagged",
                "row_reference": {
                    "count": negative_count,
                    "sample_rows": sample_rows,
                },
                "rule_id": RULE_ID,
                "rule_key": key,
            }
        )
    return issues
