"""Consistency rule, part 2: arithmetic triple mining (a * b ~= c).

Discovers column triples where one numeric column is the product of two
others for almost all rows -- generalizing "Total = Qty * Price"
without needing column names. The rare rows that break the discovered
identity are the consistency issue (e.g. a mis-keyed quantity or a
total that wasn't recomputed after an edit).

Unlike ordering.py, this rule deliberately does NOT exclude PK/FK-role
columns. Ordering (a <= b) is a weak constraint with many coincidental
matches, so dense-ID columns had to be filtered out. Arithmetic
(a * b ~= c within tolerance, for >=95% of thousands of rows) is a very
strong constraint that essentially never holds by chance -- and the
quantity column we most need is exactly the one that looks FK-ish
(small integers overlapping a dense ID range). Confirmed on real data:
Hemas InvoiceDetails col_3 (qty) x col_6 (price) = col_11 (total) holds
for 99.996% of rows, but col_3 had been flagged an FK-role column, so
excluding it -- as ordering does -- would hide the single most
important consistency identity in the dataset.

A light min_distinct drops constant columns, and a tolerance model
(absolute floor + relative band) absorbs money rounding. The critical
guard, though, is measuring conformance over *informative* rows only
(where the product or c is non-zero). Without it, near-all-zero columns
produce a flood of degenerate "0 x 0 = 0" matches -- confirmed on real
data, where Hemas InvoiceDetails has two mostly-zero columns (col_9:
259,575/272,519 zeros; col_14: 269,461 zeros) that generated 16
spurious triples (anything x col_9 ~= col_14) alongside the one real
identity. Scored over all rows those junk triples reach ~0.95
conformance purely from the shared zeros; scored over informative rows
they collapse below 0.09, while the real qty x price = total identity
stays at 0.9999.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RULE_ID = "consistency_arithmetic_violation"
MAX_SAMPLE_ROWS = 5
MIN_CONFORMANCE = 0.95
MIN_INFORMATIVE_ROWS = 30
MIN_DISTINCT = 2
TOL_ABS = 0.05
TOL_REL = 0.01

NUMERIC_TYPES = ("int", "decimal")


def mine_arithmetic(
    df: pd.DataFrame,
    types: dict[str, str],
    distinct_counts: dict[str, int] | None = None,
    min_conformance: float = MIN_CONFORMANCE,
    min_informative_rows: int = MIN_INFORMATIVE_ROWS,
    min_distinct: int = MIN_DISTINCT,
    tol_abs: float = TOL_ABS,
    tol_rel: float = TOL_REL,
) -> list[dict]:
    """Discover (a, b, c) triples where a * b ~= c for most rows.

    Conformance is measured over *informative* rows only -- those where
    the product or c is non-zero -- so the mass of trivial 0 ~= 0 rows
    in zero-heavy columns can't inflate the score. This is what cleanly
    separates the real identity from the degenerate matches: on real
    Hemas data col_3 x col_6 = col_11 scores 0.9999 informative
    conformance, while the zero-inflated junk (col_3 x col_14 = col_9
    etc.) collapses to <0.09.
    """
    distinct_counts = distinct_counts or {}
    cols = [
        c
        for c, t in types.items()
        if t in NUMERIC_TYPES and distinct_counts.get(c, min_distinct) >= min_distinct
    ]
    parsed = {c: pd.to_numeric(df[c], errors="coerce") for c in cols}

    discovered = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            a, b = cols[i], cols[j]
            product = parsed[a] * parsed[b]
            ab_valid = parsed[a].notna() & parsed[b].notna()
            for c in cols:
                if c in (a, b):
                    continue
                sc = parsed[c]
                # Informative rows: all values present, and the identity
                # asserts something (product or c is non-zero, not 0 ~= 0).
                informative = ab_valid & sc.notna() & ((sc != 0) | (product != 0))
                n = int(informative.sum())
                if n < min_informative_rows:
                    continue

                diff = (product - sc).abs()
                tol = np.maximum(tol_abs, sc.abs() * tol_rel)
                conformance = int((informative & (diff <= tol)).sum()) / n
                if conformance < min_conformance:
                    continue

                violation_mask = informative & (diff > tol)
                violation_count = int(violation_mask.sum())
                if violation_count == 0:
                    continue

                discovered.append(
                    {
                        "factor_a": a,
                        "factor_b": b,
                        "product_c": c,
                        "conformance": round(conformance, 6),
                        "informative_rows": n,
                        "violation_count": violation_count,
                        "sample_rows": df.index[violation_mask][:MAX_SAMPLE_ROWS].tolist(),
                    }
                )
    return discovered


def check_arithmetic(
    table_name: str,
    df: pd.DataFrame,
    types: dict[str, str],
    distinct_counts: dict[str, int] | None = None,
) -> list[dict]:
    issues = []
    for rel in mine_arithmetic(df, types, distinct_counts=distinct_counts):
        issues.append(
            {
                "table_name": table_name,
                "column_name": f"{rel['factor_a']},{rel['factor_b']},{rel['product_c']}",
                "dimension": "consistency",
                "source": "rule",
                "error_type": "type1",
                "severity": "high",
                "description": (
                    f"{rel['product_c']} usually equals {rel['factor_a']} x {rel['factor_b']} "
                    f"({rel['conformance'] * 100:.4f}% of {rel['informative_rows']} rows), "
                    f"but {rel['violation_count']} rows break that identity."
                ),
                "recommendation": (
                    f"Check rows where {rel['product_c']} != {rel['factor_a']} x {rel['factor_b']} "
                    f"for a mis-entered quantity, price, or total."
                ),
                "fixable": "no",
                "status": "flagged",
                "row_reference": {
                    "count": rel["violation_count"],
                    "sample_rows": rel["sample_rows"],
                },
                "rule_id": RULE_ID,
            }
        )
    return issues
