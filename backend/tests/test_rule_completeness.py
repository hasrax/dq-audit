from pathlib import Path

import pandas as pd

from app.io.loader import load_csv
from app.profiler.profile import profile_table
from app.rules.completeness import check_completeness

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "hemas"


def test_fully_null_column_fires_high_severity():
    df = load_csv(DATA_DIR / "GRNHED.csv")
    profile = profile_table(df, "GRNHED")
    issues = check_completeness("GRNHED", df, profile)
    col_9_issues = [i for i in issues if i["column_name"] == "col_9"]
    assert len(col_9_issues) == 1
    assert col_9_issues[0]["severity"] == "high"
    assert col_9_issues[0]["row_reference"]["count"] == 2989
    assert col_9_issues[0]["dimension"] == "completeness"
    assert col_9_issues[0]["source"] == "rule"
    assert col_9_issues[0]["error_type"] == "type1"


def test_column_below_threshold_does_not_fire():
    df = load_csv(DATA_DIR / "GRNHED.csv")
    profile = profile_table(df, "GRNHED")
    issues = check_completeness("GRNHED", df, profile)
    # col_1 (the PK, 0% null) must never fire
    assert not any(i["column_name"] == "col_1" for i in issues)


def test_fk_role_bumps_severity_by_one_level():
    df = pd.DataFrame({"col_1": [str(i) for i in range(100)]})
    df.loc[0:10, "col_1"] = None  # 11% null -> base severity "low"
    profile = profile_table(df, "T")

    plain_issues = check_completeness("T", df, profile)
    fk_issues = check_completeness("T", df, profile, fk_columns={"col_1"})

    assert plain_issues[0]["severity"] == "low"
    assert fk_issues[0]["severity"] == "medium"


def test_sample_rows_are_capped_and_point_to_actual_nulls():
    df = pd.DataFrame({"col_1": [None] * 20 + ["x"] * 80})
    profile = profile_table(df, "T")
    issues = check_completeness("T", df, profile)
    sample = issues[0]["row_reference"]["sample_rows"]
    assert len(sample) == 5
    assert all(pd.isna(df.loc[r, "col_1"]) for r in sample)
