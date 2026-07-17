from pathlib import Path

import pandas as pd

from app.io.loader import load_csv
from app.profiler.keys import choose_primary_key, find_pk_candidates

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "hemas"


def test_grnhed_header_table_has_pk_candidates():
    df = load_csv(DATA_DIR / "GRNHED.csv")
    candidates = find_pk_candidates(df)
    assert candidates == ["col_1", "col_5"]
    assert choose_primary_key(df, candidates) == "col_1"


def test_grndtl_detail_table_has_no_single_column_pk():
    df = load_csv(DATA_DIR / "GRNDtl.csv")
    assert find_pk_candidates(df) == []
    assert choose_primary_key(df) is None


def test_invoicedetails_detail_table_has_no_single_column_pk():
    df = load_csv(DATA_DIR / "InvoiceDetails.csv")
    assert find_pk_candidates(df) == []


def test_duplicate_values_disqualify_a_column():
    df = pd.DataFrame({"col_1": ["1", "1", "2"], "col_2": ["a", "b", "c"]})
    assert find_pk_candidates(df) == ["col_2"]


def test_nulls_disqualify_a_column():
    df = pd.DataFrame({"col_1": ["1", None, "3"], "col_2": ["a", "b", "c"]})
    assert find_pk_candidates(df) == ["col_2"]


def test_empty_dataframe_has_no_candidates():
    df = pd.DataFrame({"col_1": pd.Series([], dtype=object)})
    assert find_pk_candidates(df) == []
    assert choose_primary_key(df) is None
