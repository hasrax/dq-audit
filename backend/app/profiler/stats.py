"""Per-column statistics: nulls, distinct count, min/max, mean, top-k."""
from __future__ import annotations

import pandas as pd

from app.profiler.types import TYPE_DATE, TYPE_DECIMAL, TYPE_INT


def compute_column_stats(series: pd.Series, col_type: str, top_k: int = 5) -> dict:
    """Compute descriptive statistics for one column.

    min/max/mean are computed on the type-appropriate representation
    (numeric for int/decimal, datetime for date) rather than raw string
    sort order, so e.g. "9" doesn't outrank "10".
    """
    total = len(series)
    non_null = series.dropna()
    null_count = total - len(non_null)
    null_pct = round((null_count / total * 100), 2) if total else 0.0

    min_val = max_val = mean_val = None
    if not non_null.empty:
        if col_type in (TYPE_INT, TYPE_DECIMAL):
            numeric = pd.to_numeric(non_null, errors="coerce")
            min_val = numeric.min().item()
            max_val = numeric.max().item()
            mean_val = round(numeric.mean().item(), 4)
        elif col_type == TYPE_DATE:
            dates = pd.to_datetime(non_null, errors="coerce")
            min_val = dates.min().isoformat()
            max_val = dates.max().isoformat()
        else:
            min_val = non_null.min()
            max_val = non_null.max()

    top_values = {
        str(value): int(count)
        for value, count in non_null.value_counts().head(top_k).items()
    }

    return {
        "null_count": int(null_count),
        "null_pct": null_pct,
        "distinct_count": int(non_null.nunique()),
        "min": min_val,
        "max": max_val,
        "mean": mean_val,
        "top_values": top_values,
    }


def compute_stats(df: pd.DataFrame, types: dict[str, str], top_k: int = 5) -> dict[str, dict]:
    """Compute statistics for every column in a DataFrame."""
    return {col: compute_column_stats(df[col], types[col], top_k) for col in df.columns}
