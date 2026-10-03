"""Uniqueness rule: full-row duplicates + duplicate values in near-key columns.

Checking "duplicate PK values" against profile['primary_key'] would be
vacuous: find_pk_candidates only names a column PK if it is *already*
100% unique, so it can never have duplicates by construction. The real
target is columns that look like they were meant to be a unique
identifier (distinct/non-null ratio close to 1) but aren't quite --
that's exactly the WhyNotBilling.col_1 case found on real data: 45,545
distinct values across 45,597 rows, 52 values duplicated.
"""
from __future__ import annotations

import pandas as pd

from app.rules.registry import record_rule

FULL_ROW_RULE_ID = "uniqueness_full_row_duplicate"
NEAR_KEY_RULE_ID = "uniqueness_near_key_duplicate"
MAX_SAMPLE_ROWS = 5
NEAR_UNIQUE_THRESHOLD = 0.90


def check_uniqueness(
    table_name: str,
    df: pd.DataFrame,
    profile: dict,
    near_unique_threshold: float = NEAR_UNIQUE_THRESHOLD,
    rules: list[dict] | None = None,
) -> list[dict]:
    issues = []
    row_count = profile["row_count"]

    dup_mask = df.duplicated(keep=False)
    dup_count = int(dup_mask.sum())
    full_key = record_rule(
        rules, FULL_ROW_RULE_ID, "uniqueness", table_name, None,
        parameters={}, evidence={"row_count": row_count, "duplicate_rows": dup_count},
        violation_count=dup_count,
    )
    if dup_count > 0:
        sample_rows = df.index[dup_mask][:MAX_SAMPLE_ROWS].tolist()
        issues.append(
            {
                "table_name": table_name,
                "column_name": None,
                "dimension": "uniqueness",
                "source": "rule",
                "error_type": "type1",
                "severity": "high",
                "description": f"{dup_count} rows are exact full-row duplicates of another row.",
                "recommendation": "Remove or investigate duplicate rows -- likely a repeated import or double entry.",
                "fixable": "no",
                "status": "flagged",
                "row_reference": {"count": dup_count, "sample_rows": sample_rows},
                "rule_id": FULL_ROW_RULE_ID,
                "rule_key": full_key,
            }
        )

    for col, stats in profile["columns"].items():
        non_null_count = row_count - stats["null_count"]
        if non_null_count == 0:
            continue
        ratio = stats["distinct_count"] / non_null_count
        if ratio < near_unique_threshold or non_null_count < 2:
            continue
        evidence = {"distinct_count": stats["distinct_count"], "non_null_count": non_null_count,
                    "distinct_pct": round(ratio * 100, 2)}
        if ratio >= 1.0:  # already unique: the rule holds, nothing to flag
            record_rule(rules, NEAR_KEY_RULE_ID, "uniqueness", table_name, col,
                        parameters={"min_distinct_ratio": near_unique_threshold},
                        evidence=evidence, violation_count=0)
            continue

        value_counts = df[col].dropna().value_counts()
        dup_values = value_counts[value_counts > 1]
        dup_row_count = int(dup_values.sum())
        sample_rows = df.index[df[col].isin(dup_values.index)][:MAX_SAMPLE_ROWS].tolist()
        key = record_rule(rules, NEAR_KEY_RULE_ID, "uniqueness", table_name, col,
                          parameters={"min_distinct_ratio": near_unique_threshold},
                          evidence={**evidence, "duplicated_values": len(dup_values)},
                          violation_count=dup_row_count)

        issues.append(
            {
                "table_name": table_name,
                "column_name": col,
                "dimension": "uniqueness",
                "source": "rule",
                "error_type": "type1",
                "severity": "high",
                "description": (
                    f"{col} looks like a unique identifier ({stats['distinct_count']} distinct "
                    f"of {non_null_count} non-null values, {ratio * 100:.2f}%) but has "
                    f"{len(dup_values)} duplicated values covering {dup_row_count} rows."
                ),
                "recommendation": "Investigate whether these are true duplicate records or a reused/recycled identifier.",
                "fixable": "no",
                "status": "flagged",
                "row_reference": {"count": dup_row_count, "sample_rows": sample_rows},
                "rule_id": NEAR_KEY_RULE_ID,
                "rule_key": key,
            }
        )
    return issues
