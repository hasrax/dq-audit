from pathlib import Path

import pandas as pd

from app.io.loader import list_data_files, load_csv
from app.profiler.relationships import find_relationships
from app.profiler.types import infer_types

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "hemas"


def _load_hemas_tables():
    tables, types_by_table = {}, {}
    for f in list_data_files(DATA_DIR):
        df = load_csv(f)
        tables[f.stem] = df
        types_by_table[f.stem] = infer_types(df)
    return tables, types_by_table


def test_grndtl_references_grnhed_by_grn_number():
    tables, types_by_table = _load_hemas_tables()
    rels = find_relationships(tables, types_by_table)
    match = [
        r
        for r in rels
        if r["child_table"] == "GRNDtl"
        and r["child_column"] == "col_1"
        and r["parent_table"] == "GRNHED"
        and r["parent_column"] == "col_1"
    ]
    assert match and match[0]["overlap_pct"] == 100.0


def test_invoicedetails_references_invoiceheader_by_invoice_id():
    tables, types_by_table = _load_hemas_tables()
    rels = find_relationships(tables, types_by_table)
    match = [
        r
        for r in rels
        if r["child_table"] == "InvoiceDetails"
        and r["child_column"] == "col_1"
        and r["parent_table"] == "InvoiceHeader"
        and r["parent_column"] == "col_1"
    ]
    assert match and match[0]["overlap_pct"] == 100.0


def test_no_same_table_relationships_by_default():
    tables, types_by_table = _load_hemas_tables()
    rels = find_relationships(tables, types_by_table)
    assert all(r["child_table"] != r["parent_table"] for r in rels)


def test_full_overlap_detected_on_synthetic_tables():
    tables = {
        "child": pd.DataFrame({"col_1": [str(i) for i in range(1, 31)]}),
        "parent": pd.DataFrame({"col_1": [str(i) for i in range(1, 101)]}),
    }
    types_by_table = {name: infer_types(df) for name, df in tables.items()}
    rels = find_relationships(tables, types_by_table, min_distinct=2)
    assert {
        "child_table": "child",
        "child_column": "col_1",
        "parent_table": "parent",
        "parent_column": "col_1",
        "overlap_pct": 100.0,
    } in rels


def test_partial_overlap_below_threshold_is_excluded():
    # "child" only half-overlaps "parent" in the child -> parent direction,
    # so that specific pairing must not appear (the reverse direction is a
    # separate, genuinely full-overlap candidate and is expected to appear).
    tables = {
        "child": pd.DataFrame({"col_1": [str(i) for i in range(1, 31)]}),
        "parent": pd.DataFrame({"col_1": [str(i) for i in range(16, 31)]}),
    }
    types_by_table = {name: infer_types(df) for name, df in tables.items()}
    rels = find_relationships(tables, types_by_table, min_distinct=2)
    assert not any(r["child_table"] == "child" and r["parent_table"] == "parent" for r in rels)


def test_min_distinct_filters_low_cardinality_flag_columns():
    tables = {
        "child": pd.DataFrame({"flag": ["1", "2"] * 15}),  # only 2 distinct values
        "parent": pd.DataFrame({"col_1": [str(i) for i in range(1, 101)]}),
    }
    types_by_table = {name: infer_types(df) for name, df in tables.items()}
    rels = find_relationships(tables, types_by_table, min_distinct=20)
    assert rels == []


def test_mismatched_types_are_not_compared():
    tables = {
        "child": pd.DataFrame({"col_1": [f"CODE{i}" for i in range(1, 31)]}),
        "parent": pd.DataFrame({"col_1": [str(i) for i in range(1, 101)]}),
    }
    types_by_table = {name: infer_types(df) for name, df in tables.items()}
    rels = find_relationships(tables, types_by_table, min_distinct=2)
    assert rels == []
