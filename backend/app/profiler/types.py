"""Per-column type inference from string-typed values.

Columns are positional and untitled (see app.io.loader), so type
inference works purely on value syntax: int, decimal (ERP money
columns render as 2 or 4 decimal places), date/datetime, or varchar.
"""
from __future__ import annotations

import pandas as pd

TYPE_INT = "int"
TYPE_DECIMAL = "decimal"
TYPE_DATE = "date"
TYPE_VARCHAR = "varchar"
TYPE_UNKNOWN = "unknown"  # column has no non-null values to type

_INT_RE = r"-?\d+"
_DECIMAL_RE = r"-?\d+\.\d+"
_DATE_RE = r"\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}:\d{2}(\.\d+)?)?"


def infer_column_type(series: pd.Series) -> str:
    """Infer the type of a single column from its non-null values."""
    values = series.dropna().astype(str).str.strip()
    if values.empty:
        return TYPE_UNKNOWN
    if values.str.fullmatch(_DATE_RE).all():
        return TYPE_DATE
    if values.str.fullmatch(_DECIMAL_RE).all():
        return TYPE_DECIMAL
    if values.str.fullmatch(_INT_RE).all():
        return TYPE_INT
    return TYPE_VARCHAR


def infer_types(df: pd.DataFrame) -> dict[str, str]:
    """Infer types for every column in a DataFrame."""
    return {col: infer_column_type(df[col]) for col in df.columns}
