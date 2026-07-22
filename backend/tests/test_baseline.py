import numpy as np
import pandas as pd

from app.eval import baseline


def _rng(seed=42):
    return np.random.default_rng(seed)


def test_subset_is_reproducible_and_reindexed():
    df = pd.DataFrame({"col_1": [str(i) for i in range(100)]})
    a = baseline.subset(df, 10, _rng(1))
    b = baseline.subset(df, 10, _rng(1))
    assert list(a["col_1"]) == list(b["col_1"])
    assert list(a.index) == list(range(10))


def test_make_non_null_fills_gaps():
    df = pd.DataFrame({"c": ["1", None, "3", None]})
    out = baseline.make_non_null(df, ["c"])
    assert out["c"].isna().sum() == 0


def test_make_non_negative_removes_negatives():
    df = pd.DataFrame({"c": ["-5.00", "3.00", "-1.50"]})
    out = baseline.make_non_negative(df, ["c"])
    assert (pd.to_numeric(out["c"]) >= 0).all()


def test_dedupe_key_removes_duplicate_keys():
    df = pd.DataFrame({"pk": ["1", "1", "2", "3", "3"]})
    out = baseline.dedupe_key(df, "pk")
    assert out["pk"].is_unique


def test_clamp_dates_pulls_out_of_window_into_range():
    df = pd.DataFrame({"d": ["1900-01-01 00:00:00.000", "2099-01-01 00:00:00.000"]})
    out = baseline.clamp_dates(df, ["d"])
    parsed = pd.to_datetime(out["d"])
    assert (parsed >= baseline.VALID_DATE_LO).all()
    assert (parsed <= baseline.VALID_DATE_HI).all()


def test_order_dates_fixes_inversions():
    df = pd.DataFrame({"lo": ["2024-03-01 00:00:00.000"], "hi": ["2024-01-01 00:00:00.000"]})
    out = baseline.order_dates(df, "lo", "hi")
    assert pd.to_datetime(out["lo"]).iloc[0] <= pd.to_datetime(out["hi"]).iloc[0]


def test_drop_orphans_keeps_only_valid_fk():
    df = pd.DataFrame({"fk": ["1", "2", "999"]})
    out = baseline.drop_orphans(df, "fk", {"1", "2"})
    assert set(out["fk"]) == {"1", "2"}
