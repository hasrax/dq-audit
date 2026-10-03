"""Consistency rule, part 1: pairwise ordering mining.

No column names means we can't look for "BillDate" and "DueDate"
directly. Instead: for every pair of same-kind columns (both date, or
both numeric) in a table, test whether one is <= the other for almost
all rows. If that holds for >=98% of rows but not all of them, it's a
discovered ordering relationship (this generalizes both "BillDate <=
DueDate" and "PaidAmount <= BillAmount" under one mechanism), and the
rare violating rows are the consistency issue -- the same value-overlap-
style discovery methodology used for Layer 0's FK mining, applied to
row order instead of value membership.

Violations are rare by construction (<=2% of valid rows, or the pair
wouldn't have qualified), so severity is fixed at "high" rather than
scaled -- there's no meaningful low/medium tier to distinguish.

PK/FK-role columns must be excluded from the candidate pool (pass their
names via exclude_columns). Confirmed on real data: without exclusion,
InvoiceDetails.col_3 (a quantity column) "orders below" col_1 (its FK
reference to InvoiceHeader) 99.99% of the time -- not a business rule,
just a small number almost always being smaller than a large ID. This
is the same dense-ID-range problem documented in relationships.py,
showing up in a new place.
"""
from __future__ import annotations

import pandas as pd

from app.rules.registry import record_rule

RULE_ID = "consistency_ordering_violation"
MAX_SAMPLE_ROWS = 5
MIN_CONFORMANCE = 0.98
MIN_VALID_PAIRS = 30
MIN_DISTINCT = 20

NUMERIC_TYPES = ("int", "decimal")


def _parse(series: pd.Series, is_date: bool) -> pd.Series:
    return pd.to_datetime(series, errors="coerce") if is_date else pd.to_numeric(series, errors="coerce")


def mine_orderings(
    df: pd.DataFrame,
    types: dict[str, str],
    distinct_counts: dict[str, int] | None = None,
    exclude_columns: set[str] | None = None,
    min_conformance: float = MIN_CONFORMANCE,
    min_valid_pairs: int = MIN_VALID_PAIRS,
    min_distinct: int = MIN_DISTINCT,
    include_clean: bool = False,
) -> list[dict]:
    """Discover (lo, hi) column pairs where lo <= hi holds for most rows.

    include_clean also returns pairs that hold for every row (zero
    violations) -- they are checked rules too, just ones that passed.

    distinct_counts (from the Layer 0 profile) filters out low-
    cardinality status/flag columns: two small integer codes with only
    a handful of distinct values will often "order" against each other
    by pure chance, the same coincidental-smallness problem as the
    dense-ID-range issue exclude_columns handles. Confirmed on real
    data: without this filter, InvoiceHeader.col_22/24/28/32 (2-19
    distinct values) produced 11 near-zero-support "orderings" (as few
    as 1 violating row) against unrelated columns.
    """
    exclude_columns = exclude_columns or set()
    distinct_counts = distinct_counts or {}

    def _eligible(col: str) -> bool:
        return col not in exclude_columns and distinct_counts.get(col, min_distinct) >= min_distinct

    date_cols = [c for c, t in types.items() if t == "date" and _eligible(c)]
    numeric_cols = [c for c, t in types.items() if t in NUMERIC_TYPES and _eligible(c)]

    discovered = []
    for group, is_date in ((date_cols, True), (numeric_cols, False)):
        parsed = {c: _parse(df[c], is_date) for c in group}
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                sa, sb = parsed[a], parsed[b]
                valid = sa.notna() & sb.notna()
                n = int(valid.sum())
                if n < min_valid_pairs:
                    continue

                a_le_b = (sa[valid] <= sb[valid]).mean()
                b_le_a = (sb[valid] <= sa[valid]).mean()
                lo, hi, conformance = (a, b, a_le_b) if a_le_b >= b_le_a else (b, a, b_le_a)
                if conformance < min_conformance or (conformance >= 1.0 and not include_clean):
                    continue

                s_lo, s_hi = parsed[lo], parsed[hi]
                violation_mask = valid & (s_lo > s_hi)
                violation_count = int(violation_mask.sum())
                if violation_count == 0 and not include_clean:
                    continue

                discovered.append(
                    {
                        "is_date": is_date,
                        "column_lo": lo,
                        "column_hi": hi,
                        "conformance": round(conformance, 6),
                        "valid_pairs": n,
                        "violation_count": violation_count,
                        "sample_rows": df.index[violation_mask][:MAX_SAMPLE_ROWS].tolist(),
                    }
                )
    return discovered


def check_ordering(
    table_name: str,
    df: pd.DataFrame,
    types: dict[str, str],
    distinct_counts: dict[str, int] | None = None,
    exclude_columns: set[str] | None = None,
    rules: list[dict] | None = None,
) -> list[dict]:
    issues = []
    mined = mine_orderings(df, types, distinct_counts=distinct_counts,
                           exclude_columns=exclude_columns, include_clean=rules is not None)
    for rel in mined:
        key = record_rule(
            rules, RULE_ID, "consistency", table_name, f"{rel['column_lo']},{rel['column_hi']}",
            parameters={"is_date": rel["is_date"], "min_conformance": MIN_CONFORMANCE},
            evidence={"conformance_pct": round(rel["conformance"] * 100, 4),
                      "valid_pairs": rel["valid_pairs"]},
            violation_count=rel["violation_count"],
        )
        if rel["violation_count"] == 0:
            continue
        issues.append(
            {
                "table_name": table_name,
                "column_name": f"{rel['column_lo']},{rel['column_hi']}",
                "dimension": "consistency",
                "source": "rule",
                "error_type": "type1",
                "severity": "high",
                "description": (
                    f"{rel['column_lo']} is usually <= {rel['column_hi']} "
                    f"({rel['conformance'] * 100:.4f}% of {rel['valid_pairs']} rows), "
                    f"but {rel['violation_count']} rows break that order."
                ),
                "recommendation": f"Check rows where {rel['column_lo']} > {rel['column_hi']} for swapped or mis-entered values.",
                "fixable": "no",
                "status": "flagged",
                "row_reference": {
                    "count": rel["violation_count"],
                    "sample_rows": rel["sample_rows"],
                },
                "rule_id": RULE_ID,
                "rule_key": key,
            }
        )
    return issues
