"""Cross-table referential integrity rule: FK orphans.

Reuses Layer 0's relationships.json directly rather than rediscovering
anything -- for each candidate (child_table.child_col -> parent_table.
parent_col), find child rows whose value is absent from the parent's
value set.

Severity is inverse to the row-level orphan rate, same rationale as
validity.py: a handful of orphans against an otherwise-solid FK are
almost certainly real broken references -- the "invisible in
single-file checks" errors this whole project exists to surface (high).
A very high orphan rate more likely means the "relationship" itself is
spurious (a low-cardinality flag column that happened to value-overlap
a dense ID range at discovery time -- confirmed on real data: GRNDtl's
col_11 showed 97.3% *distinct-value* overlap with GRNHED at discovery,
but is 93% orphaned at the *row* level, because its dominant value (0)
sits outside GRNHED's PK range entirely). That distinction matters for
a human reviewing relationships.confirmed, so the description says so
explicitly rather than silently dropping the issue.
"""
from __future__ import annotations

import pandas as pd

from app.rules.registry import record_rule

RULE_ID = "cross_table_fk_orphan"
MAX_SAMPLE_ROWS = 5


def _severity(orphan_pct: float) -> str:
    if orphan_pct <= 5:
        return "high"
    if orphan_pct <= 50:
        return "medium"
    return "low"


def check_cross_table(
    tables: dict[str, pd.DataFrame],
    relationships: list[dict],
    rules: list[dict] | None = None,
) -> list[dict]:
    issues = []

    for rel in relationships:
        child_df = tables[rel["child_table"]]
        parent_df = tables[rel["parent_table"]]
        child_col, parent_col = rel["child_column"], rel["parent_column"]

        parent_values = set(parent_df[parent_col].dropna())
        child_series = child_df[child_col]
        orphan_mask = child_series.notna() & ~child_series.isin(parent_values)
        orphan_count = int(orphan_mask.sum())
        key = record_rule(
            rules, RULE_ID, "cross_table", rel["child_table"], child_col,
            parameters={"parent_table": rel["parent_table"], "parent_column": parent_col},
            evidence={"overlap_pct": rel["overlap_pct"], "orphan_count": orphan_count,
                      "row_count": len(child_df)},
            violation_count=orphan_count,
            key_detail=f"{rel['parent_table']}.{parent_col}",
        )
        if orphan_count == 0:
            continue

        orphan_pct = round(orphan_count / len(child_df) * 100, 2)
        sample_rows = child_df.index[orphan_mask][:MAX_SAMPLE_ROWS].tolist()
        spurious_note = (
            " This high an orphan rate may mean the discovered relationship itself "
            "is spurious rather than a real broken FK -- worth confirming before trusting it."
            if orphan_pct > 50
            else ""
        )

        issues.append(
            {
                "table_name": rel["child_table"],
                "column_name": child_col,
                "dimension": "cross_table",
                "source": "rule",
                "error_type": "type1",
                "severity": _severity(orphan_pct),
                "description": (
                    f"{rel['child_table']}.{child_col} has {orphan_count} values "
                    f"({orphan_pct}%) not found in {rel['parent_table']}.{parent_col}."
                    f"{spurious_note}"
                ),
                "recommendation": "Correct the reference or investigate the source of the orphaned rows.",
                "fixable": "no",
                "status": "flagged",
                "row_reference": {"count": orphan_count, "sample_rows": sample_rows},
                "rule_id": RULE_ID,
                "rule_key": key,
            }
        )
    return issues
