"""Seeded, versioned synthetic error injection (E1).

ONE injection script, a fixed version tag, and a single seeded RNG --
reproducibility is a certain viva question, so every run of a given
(version, seed, config) reproduces the same corruptions and the same
ground-truth labels.

Each injector mutates a copy of the target table in place and returns
the ground truth it created: one Injection per corrupted cell/row,
recording (table, row, column, error_class, error_type, dimension) so
detections can later be matched against it.

Row indices in labels are POSITIONAL (0-based) into the extract passed
in, matching how the profiler/rules reference rows.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

INJECTION_VERSION = "1.0.0"


@dataclass(frozen=True)
class Injection:
    table: str
    row: int
    column: str | None
    error_class: str
    error_type: str  # type1 | type2
    dimension: str

    def as_dict(self) -> dict:
        return asdict(self)


def _pick_rows(df: pd.DataFrame, count: int, rng: np.random.Generator, eligible: pd.Series | None = None) -> list[int]:
    """Choose `count` distinct positional row indices, optionally restricted."""
    pool = np.arange(len(df)) if eligible is None else np.where(eligible.to_numpy())[0]
    if len(pool) == 0:
        return []
    count = min(count, len(pool))
    return sorted(int(i) for i in rng.choice(pool, size=count, replace=False))


# --- Type 1 (deterministic-rule targets) -----------------------------------

def inject_nulls(df, table, column, count, rng) -> list[Injection]:
    rows = _pick_rows(df, count, rng, eligible=df[column].notna())
    for r in rows:
        df.iat[r, df.columns.get_loc(column)] = None
    return [Injection(table, r, column, "null", "type1", "completeness") for r in rows]


def inject_negatives(df, table, column, count, rng) -> list[Injection]:
    numeric = pd.to_numeric(df[column], errors="coerce")
    rows = _pick_rows(df, count, rng, eligible=numeric > 0)
    loc = df.columns.get_loc(column)
    for r in rows:
        df.iat[r, loc] = str(-abs(float(numeric.iloc[r])))
    return [Injection(table, r, column, "negative", "type1", "validity") for r in rows]


def inject_duplicate_pk(df, table, column, count, rng) -> list[Injection]:
    """Overwrite some PK values with an existing value -> uniqueness break."""
    rows = _pick_rows(df, count, rng)
    loc = df.columns.get_loc(column)
    labels = []
    for r in rows:
        donor = int(rng.integers(0, len(df)))
        if donor == r:
            donor = (donor + 1) % len(df)
        df.iat[r, loc] = df.iat[donor, loc]
        labels.append(Injection(table, r, column, "duplicate_pk", "type1", "uniqueness"))
    return labels


def inject_future_dates(df, table, column, count, rng, days_ahead: int = 400) -> list[Injection]:
    rows = _pick_rows(df, count, rng, eligible=pd.to_datetime(df[column], errors="coerce").notna())
    loc = df.columns.get_loc(column)
    future = (pd.Timestamp.now() + pd.Timedelta(days=days_ahead)).strftime("%Y-%m-%d %H:%M:%S.000")
    for r in rows:
        df.iat[r, loc] = future
    return [Injection(table, r, column, "future_date", "type1", "timeliness") for r in rows]


def inject_date_inversion(df, table, lo_col, hi_col, count, rng) -> list[Injection]:
    """Make lo_col later than hi_col -> ordering/consistency break."""
    both = pd.to_datetime(df[lo_col], errors="coerce").notna() & pd.to_datetime(df[hi_col], errors="coerce").notna()
    rows = _pick_rows(df, count, rng, eligible=both)
    lo_loc, hi_loc = df.columns.get_loc(lo_col), df.columns.get_loc(hi_col)
    for r in rows:
        # swap so lo > hi, then push lo further out
        hi_val = pd.to_datetime(df.iat[r, hi_loc])
        df.iat[r, lo_loc] = (hi_val + pd.Timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S.000")
    return [Injection(table, r, lo_col, "date_inversion", "type1", "consistency") for r in rows]


def inject_fk_orphans(child_df, table, fk_col, parent_values, count, rng) -> list[Injection]:
    """Set FK to a value absent from the parent key set -> cross-table break."""
    rows = _pick_rows(child_df, count, rng)
    loc = child_df.columns.get_loc(fk_col)
    orphan_base = max((int(v) for v in parent_values if str(v).lstrip("-").isdigit()), default=0) + 10_000
    for i, r in enumerate(rows):
        child_df.iat[r, loc] = str(orphan_base + i)
    return [Injection(table, r, fk_col, "fk_orphan", "type1", "cross_table") for r in rows]


# --- Type 2 (contextual / LLM targets) -------------------------------------

def inject_quantity_magnitude(df, table, column, count, rng, factor: int = 100) -> list[Injection]:
    rows = _pick_rows(df, count, rng, eligible=pd.to_numeric(df[column], errors="coerce") > 0)
    loc = df.columns.get_loc(column)
    for r in rows:
        df.iat[r, loc] = str(int(float(df.iat[r, loc]) * factor))
    return [Injection(table, r, column, f"quantity_x{factor}", "type2", "validity") for r in rows]


def inject_price_outlier(df, table, column, count, rng, factor: float = 50.0) -> list[Injection]:
    numeric = pd.to_numeric(df[column], errors="coerce")
    rows = _pick_rows(df, count, rng, eligible=numeric > 0)
    loc = df.columns.get_loc(column)
    for r in rows:
        df.iat[r, loc] = f"{float(df.iat[r, loc]) * factor:.2f}"
    return [Injection(table, r, column, "price_outlier", "type2", "validity") for r in rows]


def inject_near_duplicate(df, table, count, rng) -> list[Injection]:
    """Append near-duplicate rows (copy of an existing row) -> Type 2."""
    donors = _pick_rows(df, count, rng)
    labels = []
    for donor in donors:
        new_row = df.iloc[donor].copy()
        df.loc[len(df)] = new_row.to_numpy()
        labels.append(Injection(table, len(df) - 1, None, "near_duplicate", "type2", "uniqueness"))
    return labels
