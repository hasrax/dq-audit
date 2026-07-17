"""Primary key candidate discovery: columns that are unique + non-null."""
from __future__ import annotations

import pandas as pd


def find_pk_candidates(df: pd.DataFrame) -> list[str]:
    """Columns where every value is present and every value is distinct.

    Detail/line tables (e.g. GRNDtl) typically have none -- their real
    key is composite (header ref + line no), which single-column
    discovery does not attempt. Very small tables can produce spurious
    candidates (any non-null column looks "unique" when there are only
    a handful of rows).
    """
    n = len(df)
    if n == 0:
        return []
    return [
        col
        for col in df.columns
        if df[col].isna().sum() == 0 and df[col].nunique() == n
    ]


def choose_primary_key(df: pd.DataFrame, candidates: list[str] | None = None) -> str | None:
    """Pick one PK candidate, preferring the leftmost column (export
    convention places identity/PK columns first)."""
    candidates = candidates if candidates is not None else find_pk_candidates(df)
    if not candidates:
        return None
    order = list(df.columns)
    return min(candidates, key=order.index)
