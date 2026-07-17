from pathlib import Path

import pandas as pd

from app.io.loader import load_csv
from app.profiler.types import (
    TYPE_DATE,
    TYPE_DECIMAL,
    TYPE_INT,
    TYPE_UNKNOWN,
    TYPE_VARCHAR,
    infer_column_type,
    infer_types,
)

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "hemas"


def test_infer_types_on_grnhed():
    df = load_csv(DATA_DIR / "GRNHED.csv")
    types = infer_types(df)
    # col_1: GRN number, e.g. "3"
    assert types["col_1"] == TYPE_INT
    # col_3: BillDate, e.g. "2023-05-25 00:00:00.000"
    assert types["col_3"] == TYPE_DATE
    # col_5: InvoiceNo, alphanumeric e.g. "31405GRN000004"
    assert types["col_5"] == TYPE_VARCHAR
    # col_7: BillAmount, e.g. "744731.05"
    assert types["col_7"] == TYPE_DECIMAL


def test_negative_decimal_still_classified_as_decimal():
    s = pd.Series(["1.50", "-2.00", "0.00"])
    assert infer_column_type(s) == TYPE_DECIMAL


def test_all_null_column_is_unknown():
    s = pd.Series([None, None, None])
    assert infer_column_type(s) == TYPE_UNKNOWN


def test_mixed_alnum_column_is_varchar():
    s = pd.Series(["HN000004", "HN000005", None])
    assert infer_column_type(s) == TYPE_VARCHAR


def test_int_with_nulls_ignores_nulls():
    s = pd.Series(["1", "2", None, "3"])
    assert infer_column_type(s) == TYPE_INT
