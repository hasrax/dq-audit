"""Build a cleaned baseline extract from a real client table (E1).

Takes a subset of a real Hemas/Ussawatte table and removes the specific
error conditions we are about to inject on the target columns, so that
after injection a detected error in those columns is attributable to a
known injection. Recall is already exact against the injected labels;
this cleaning is what makes precision interpretable.

Threat to validity (documented for Chapter 4): cleaning targets the
injected classes on the injected columns only -- the real base may still
contain unlabelled pre-existing errors elsewhere, so precision is
reported as "within the injected scope".
"""
from __future__ import annotations

import numpy as np
import pandas as pd

VALID_DATE_LO = pd.Timestamp.now() - pd.Timedelta(days=600)
VALID_DATE_HI = pd.Timestamp.now() - pd.Timedelta(days=30)


def subset(df: pd.DataFrame, n: int, rng: np.random.Generator) -> pd.DataFrame:
    """A reproducible n-row sample with positional index reset to 0..n-1."""
    n = min(n, len(df))
    idx = np.sort(rng.choice(len(df), size=n, replace=False))
    return df.iloc[idx].reset_index(drop=True)


def make_non_null(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for col in columns:
        filler = df[col].dropna()
        if not filler.empty:
            df[col] = df[col].fillna(filler.iloc[0])
    return df


def make_non_negative(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for col in columns:
        numeric = pd.to_numeric(df[col], errors="coerce")
        df[col] = numeric.abs().map(lambda v: f"{v:.2f}" if pd.notna(v) else None)
    return df


def dedupe_key(df: pd.DataFrame, column: str) -> pd.DataFrame:
    return df.drop_duplicates(subset=[column], keep="first").reset_index(drop=True)


def clamp_dates(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    mid = (VALID_DATE_LO + (VALID_DATE_HI - VALID_DATE_LO) / 2).strftime("%Y-%m-%d %H:%M:%S.000")
    for col in columns:
        parsed = pd.to_datetime(df[col], errors="coerce")
        out_of_window = (parsed < VALID_DATE_LO) | (parsed > VALID_DATE_HI)
        df.loc[out_of_window, col] = mid
    return df


def order_dates(df: pd.DataFrame, lo_col: str, hi_col: str) -> pd.DataFrame:
    """Ensure lo_col <= hi_col by pulling any inverted lo back to hi."""
    lo = pd.to_datetime(df[lo_col], errors="coerce")
    hi = pd.to_datetime(df[hi_col], errors="coerce")
    inverted = lo.notna() & hi.notna() & (lo > hi)
    df.loc[inverted, lo_col] = df.loc[inverted, hi_col]
    return df


def drop_orphans(df: pd.DataFrame, fk_col: str, parent_values: set) -> pd.DataFrame:
    keep = df[fk_col].isna() | df[fk_col].isin(parent_values)
    return df[keep].reset_index(drop=True)
