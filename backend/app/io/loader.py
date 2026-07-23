"""Table loading for SeefaERP exports (CSV/Excel, headerless or headered)."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

DATA_SUFFIXES = (".csv", ".xlsx", ".xls")

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
    """All CSV/Excel data files in a client directory, minus legacy tables."""
    directory = Path(directory)
    files = [f for f in directory.iterdir()
             if f.suffix.lower() in DATA_SUFFIXES and not is_excluded(f.stem)]
    return sorted(files)


def load_csv(path: Path | str) -> pd.DataFrame:
    """Load a headerless SeefaERP CSV export with positional columns.

    Kept for callers that specifically want the always-headerless
    behaviour; general ingestion should use load_table.
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


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _has_header(raw: pd.DataFrame, sample_rows: int = 25) -> bool:
    """Heuristic: does row 0 look like a header rather than data?

    A header row is fully populated, all-non-numeric, and has distinct
    labels, while the data below it contains at least one numeric cell.
    SeefaERP exports all begin with a numeric id column, so they are
    correctly classified as headerless (row 0 has a number).
    """
    if len(raw) < 2:
        return False
    first = [raw.iat[0, j] for j in range(raw.shape[1])]
    if any(v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "" for v in first):
        return False
    if any(_is_number(str(v)) for v in first):
        return False
    if len({str(v) for v in first}) != len(first):
        return False
    body = raw.iloc[1 : sample_rows + 1]
    return any(
        _is_number(str(body.iat[i, j]))
        for i in range(len(body))
        for j in range(body.shape[1])
        if pd.notna(body.iat[i, j])
    )


def _clean_headers(names: list[str]) -> list[str]:
    """Strip, fill blanks, and de-duplicate header labels."""
    seen: dict[str, int] = {}
    out = []
    for i, name in enumerate(names):
        label = str(name).strip() or f"col_{i + 1}"
        if label in seen:
            seen[label] += 1
            label = f"{label}_{seen[label]}"
        else:
            seen[label] = 0
        out.append(label)
    return out


def _read_raw(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".xlsx", ".xls"):
        raw = pd.read_excel(path, header=None, dtype=str)
        return raw.map(
            lambda v: None if (pd.isna(v) or str(v).strip() in ("", "NULL", "null")) else v
        )
    return pd.read_csv(
        path, header=None, encoding="utf-8-sig", dtype=str,
        keep_default_na=False, na_values=NULL_TOKENS,
    )


def load_table(path: Path | str) -> pd.DataFrame:
    """Load a CSV or Excel export, detecting a header row if present.

    Headered files keep their real column names (which double as display
    aliases); headerless SeefaERP exports fall back to positional
    col_1..col_n. Everything is string-typed so the profiler decides
    types, not pandas.
    """
    path = Path(path)
    raw = _read_raw(path)
    if _has_header(raw):
        names = _clean_headers([raw.iat[0, j] for j in range(raw.shape[1])])
        df = raw.iloc[1:].reset_index(drop=True)
        df.columns = names
    else:
        df = raw.copy()
        df.columns = [f"col_{i + 1}" for i in range(df.shape[1])]
    return df
