import numpy as np
import pandas as pd

from app.eval import injection as inj


def _rng(seed=42):
    return np.random.default_rng(seed)


def _amounts(n=50):
    return pd.DataFrame({"col_1": [str(i) for i in range(n)], "col_2": [f"{100.0 + i:.2f}" for i in range(n)]})


def test_inject_nulls_creates_exactly_count_labels_and_nulls():
    df = _amounts()
    labels = inj.inject_nulls(df, "T", "col_2", 5, _rng())
    assert len(labels) == 5
    for lab in labels:
        assert lab.error_type == "type1" and lab.dimension == "completeness"
        assert pd.isna(df.iat[lab.row, df.columns.get_loc("col_2")])


def test_inject_negatives_makes_values_negative():
    df = _amounts()
    labels = inj.inject_negatives(df, "T", "col_2", 4, _rng())
    assert len(labels) == 4
    for lab in labels:
        assert float(df.iat[lab.row, df.columns.get_loc("col_2")]) < 0
        assert lab.error_class == "negative"


def test_inject_duplicate_pk_produces_a_repeated_value():
    df = _amounts()
    before_unique = df["col_1"].nunique()
    labels = inj.inject_duplicate_pk(df, "T", "col_1", 3, _rng())
    assert len(labels) == 3
    assert df["col_1"].nunique() < before_unique


def test_inject_future_dates_sets_a_future_value():
    df = pd.DataFrame({"col_1": ["2024-01-01 00:00:00.000"] * 20})
    labels = inj.inject_future_dates(df, "T", "col_1", 3, _rng())
    assert len(labels) == 3
    for lab in labels:
        assert pd.to_datetime(df.iat[lab.row, 0]) > pd.Timestamp.now()
        assert lab.dimension == "timeliness"


def test_inject_date_inversion_makes_lo_after_hi():
    df = pd.DataFrame({
        "lo": ["2024-01-01 00:00:00.000"] * 20,
        "hi": ["2024-02-01 00:00:00.000"] * 20,
    })
    labels = inj.inject_date_inversion(df, "T", "lo", "hi", 3, _rng())
    for lab in labels:
        lo = pd.to_datetime(df.iat[lab.row, 0])
        hi = pd.to_datetime(df.iat[lab.row, 1])
        assert lo > hi
        assert lab.dimension == "consistency"


def test_inject_fk_orphans_sets_values_outside_parent():
    child = pd.DataFrame({"fk": [str(i) for i in range(1, 31)]})
    parent_values = {str(i) for i in range(1, 31)}
    labels = inj.inject_fk_orphans(child, "child", "fk", parent_values, 4, _rng())
    assert len(labels) == 4
    for lab in labels:
        assert child.iat[lab.row, 0] not in parent_values
        assert lab.dimension == "cross_table"


def test_inject_quantity_magnitude_scales_up():
    df = pd.DataFrame({"qty": [str(i) for i in range(1, 41)]})
    labels = inj.inject_quantity_magnitude(df, "T", "qty", 3, _rng(), factor=100)
    for lab in labels:
        assert lab.error_type == "type2"
        assert lab.error_class == "quantity_x100"


def test_inject_near_duplicate_appends_rows():
    df = _amounts(20)
    n0 = len(df)
    labels = inj.inject_near_duplicate(df, "T", 3, _rng())
    assert len(df) == n0 + 3
    assert all(lab.error_class == "near_duplicate" for lab in labels)


def test_injection_is_reproducible_under_fixed_seed():
    a = inj.inject_negatives(_amounts(), "T", "col_2", 5, _rng(7))
    b = inj.inject_negatives(_amounts(), "T", "col_2", 5, _rng(7))
    assert [x.row for x in a] == [x.row for x in b]


def test_different_seeds_pick_different_rows():
    a = inj.inject_nulls(_amounts(), "T", "col_2", 5, _rng(1))
    b = inj.inject_nulls(_amounts(), "T", "col_2", 5, _rng(2))
    assert [x.row for x in a] != [x.row for x in b]


def test_version_tag_present():
    assert inj.INJECTION_VERSION == "1.0.0"
