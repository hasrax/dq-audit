from pathlib import Path

import pandas as pd

from app.io.loader import load_csv
from app.profiler.stats import compute_stats
from app.profiler.types import infer_types
from app.rules.ordering import check_ordering, mine_orderings

HEMAS_DIR = Path(__file__).resolve().parents[2] / "data" / "hemas"


def _profiled(path):
    df = load_csv(path)
    types = infer_types(df)
    stats = compute_stats(df, types)
    distinct_counts = {c: s["distinct_count"] for c, s in stats.items()}
    return df, types, distinct_counts


def test_grndtl_real_ordering_relationship_found():
    df, types, distinct_counts = _profiled(HEMAS_DIR / "GRNDtl.csv")
    rels = mine_orderings(df, types, distinct_counts=distinct_counts)
    match = [r for r in rels if r["column_lo"] == "col_8" and r["column_hi"] == "col_3"]
    assert len(match) == 1
    assert match[0]["violation_count"] == 187


def test_low_cardinality_columns_excluded_from_mining():
    # GRNHED has a near-constant column (col_14, 1 distinct value) that
    # would otherwise "order" trivially against other columns.
    df, types, distinct_counts = _profiled(HEMAS_DIR / "GRNHED.csv")
    rels = mine_orderings(df, types, distinct_counts=distinct_counts)
    assert not any(r["column_lo"] == "col_14" or r["column_hi"] == "col_14" for r in rels)


def test_exclude_columns_removes_pk_and_fk_role_columns():
    df, types, distinct_counts = _profiled(HEMAS_DIR / "InvoiceDetails.csv")
    without_exclusion = mine_orderings(df, types, distinct_counts=distinct_counts)
    with_exclusion = mine_orderings(
        df, types, distinct_counts=distinct_counts, exclude_columns={"col_1", "col_3", "col_4", "col_7", "col_9", "col_14"}
    )
    assert len(with_exclusion) < len(without_exclusion)
    assert all(r["column_lo"] not in {"col_1", "col_3"} and r["column_hi"] not in {"col_1", "col_3"} for r in with_exclusion)


def test_perfect_conformance_with_no_violations_is_not_reported():
    df = pd.DataFrame({"col_1": list(range(1, 51)), "col_2": list(range(51, 101))})
    types = infer_types(df)
    rels = mine_orderings(df, types, min_valid_pairs=10)
    assert rels == []


def test_low_conformance_pair_is_not_reported():
    # col_1 <= col_2 only ~50% of the time -- no dominant order to speak of.
    df = pd.DataFrame({"col_1": [str(i) for i in range(50)], "col_2": [str(49 - i) for i in range(50)]})
    types = infer_types(df)
    rels = mine_orderings(df, types, min_valid_pairs=10)
    assert rels == []


def test_check_ordering_produces_consistency_issues():
    df, types, distinct_counts = _profiled(HEMAS_DIR / "GRNDtl.csv")
    fk_columns = {"col_1", "col_7", "col_10", "col_11"}  # discovered FK-role columns
    issues = check_ordering("GRNDtl", df, types, distinct_counts=distinct_counts, exclude_columns=fk_columns)
    assert len(issues) == 1
    assert issues[0]["dimension"] == "consistency"
    assert issues[0]["severity"] == "high"
    assert issues[0]["row_reference"]["count"] == 187
