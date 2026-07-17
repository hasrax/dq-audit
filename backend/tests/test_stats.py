from pathlib import Path

import pandas as pd
import pytest

from app.io.loader import load_csv
from app.profiler.stats import compute_column_stats, compute_stats
from app.profiler.types import TYPE_DATE, TYPE_DECIMAL, TYPE_INT, TYPE_VARCHAR, infer_types

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "hemas"


def test_stats_on_grnhed_pk_like_column():
    df = load_csv(DATA_DIR / "GRNHED.csv")
    stats = compute_column_stats(df["col_1"], TYPE_INT)
    assert stats["null_count"] == 0
    assert stats["null_pct"] == 0.0
    assert stats["distinct_count"] == 2989 == len(df)


def test_stats_on_fully_null_column():
    df = load_csv(DATA_DIR / "GRNHED.csv")
    stats = compute_column_stats(df["col_9"], "unknown")
    assert stats["null_count"] == len(df)
    assert stats["null_pct"] == 100.0
    assert stats["distinct_count"] == 0
    assert stats["min"] is None
    assert stats["max"] is None
    assert stats["mean"] is None


def test_numeric_min_max_mean_use_numeric_order_not_string_order():
    s = pd.Series(["9", "10", "2"])
    stats = compute_column_stats(s, TYPE_INT)
    assert stats["min"] == 2
    assert stats["max"] == 10
    assert stats["mean"] == pytest.approx(7.0)


def test_decimal_stats_on_real_amount_column():
    df = load_csv(DATA_DIR / "GRNHED.csv")
    stats = compute_column_stats(df["col_7"], TYPE_DECIMAL)
    assert stats["min"] == 0.0
    assert stats["max"] == 18717909.33


def test_date_min_max_are_isoformat_strings():
    s = pd.Series(["2023-05-25 00:00:00.000", "1900-01-01 04:58:11.000"])
    stats = compute_column_stats(s, TYPE_DATE)
    assert stats["min"].startswith("1900-01-01")
    assert stats["max"].startswith("2023-05-25")


def test_varchar_top_values_counts():
    s = pd.Series(["A", "A", "B", None])
    stats = compute_column_stats(s, TYPE_VARCHAR)
    assert stats["top_values"] == {"A": 2, "B": 1}
    assert stats["null_count"] == 1


def test_compute_stats_covers_all_columns():
    df = load_csv(DATA_DIR / "GRNHED.csv")
    types = infer_types(df)
    stats = compute_stats(df, types)
    assert set(stats.keys()) == set(df.columns)
