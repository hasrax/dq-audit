"""CSV loading for headerless SeefaERP exports."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# SQL Server's textual export writes NULL as the literal string "NULL".
# Empty fields are also missing. Other pandas default NA tokens (e.g.
# "NA") are deliberately NOT treated as missing, since those can be
# legitimate SME data (e.g. a supplier code or remark of "NA").
NULL_TOKENS = ["NULL", "null", ""]

_EXCLUDE_PATTERNS = [
    re.compile(r"^Temp_", re.IGNORECASE),
    re.compile(r"_OLD", re.IGNORECASE),
    re.compile(r"Old$", re.IGNORECASE),
    re.compile(r"^Z_", re.IGNORECASE),
    re.compile(r"xx$", re.IGNORECASE),
    re.compile(r"_Deleted", re.IGNORECASE),
    re.compile(r"^BK_", re.IGNORECASE),
    re.compile(r"_Backup_", re.IGNORECASE),
]


def is_excluded(table_name: str) -> bool:
    """True if a table name matches a legacy/backup naming pattern."""
    return any(p.search(table_name) for p in _EXCLUDE_PATTERNS)


def list_data_files(directory: Path | str) -> list[Path]:
    """All CSV files in a client data directory, minus legacy tables."""
    directory = Path(directory)
    return [f for f in sorted(directory.glob("*.csv")) if not is_excluded(f.stem)]


def load_csv(path: Path | str) -> pd.DataFrame:
    """Load a headerless SeefaERP CSV export.

    Columns are positional (col_1..col_n) since these exports carry no
    header row. Everything is read as string dtype so the profiler's own
    type-inference step (not pandas') decides column types.
    """
    path = Path(path)
    df = pd.read_csv(
        path,
        header=None,
        encoding="utf-8-sig",
        dtype=str,
        keep_default_na=False,
        na_values=NULL_TOKENS,
    )
    df.columns = [f"col_{i + 1}" for i in range(df.shape[1])]
    return df
