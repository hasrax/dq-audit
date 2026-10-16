from pathlib import Path

import pandas as pd

from app.io.loader import load_csv
from app.rules.cross_table import check_cross_table

COMPANY_A_DIR = Path(__file__).resolve().parents[2] / "data" / "hemas"


def _load_grn_tables():
    return {
        "GRNDtl": load_csv(COMPANY_A_DIR / "GRNDtl.csv"),
        "GRNHED": load_csv(COMPANY_A_DIR / "GRNHED.csv"),
    }


def test_true_fk_with_perfect_integrity_fires_nothing():
    tables = _load_grn_tables()
    relationships = [
        {
            "child_table": "GRNDtl",
            "child_column": "col_1",
            "parent_table": "GRNHED",
            "parent_column": "col_1",
            "overlap_pct": 100.0,
        }
    ]
    assert check_cross_table(tables, relationships) == []


def test_spurious_relationship_shows_high_orphan_rate_and_low_severity():
    # col_11 looked like a 97.3% distinct-overlap FK candidate at discovery
    # time, but its dominant value (0) sits outside GRNHED's PK range --
    # a real example of a spurious relationship candidate.
    tables = _load_grn_tables()
    relationships = [
        {
            "child_table": "GRNDtl",
            "child_column": "col_11",
            "parent_table": "GRNHED",
            "parent_column": "col_1",
            "overlap_pct": 97.3,
        }
    ]
    issues = check_cross_table(tables, relationships)
    assert len(issues) == 1
    assert issues[0]["row_reference"]["count"] == 19205
    assert issues[0]["severity"] == "low"
    assert "may not be real" in issues[0]["description"]   # plain-English spurious-link note


def test_small_orphan_rate_fires_high_severity():
    tables = {
        "child": pd.DataFrame({"col_1": [str(i) for i in range(1, 101)]}),
        "parent": pd.DataFrame({"col_1": [str(i) for i in range(1, 99)]}),  # 99, 100 missing
    }
    relationships = [
        {
            "child_table": "child",
            "child_column": "col_1",
            "parent_table": "parent",
            "parent_column": "col_1",
            "overlap_pct": 98.0,
        }
    ]
    issues = check_cross_table(tables, relationships)
    assert len(issues) == 1
    assert issues[0]["row_reference"]["count"] == 2
    assert issues[0]["severity"] == "high"


def test_no_relationships_means_no_issues():
    tables = _load_grn_tables()
    assert check_cross_table(tables, []) == []
