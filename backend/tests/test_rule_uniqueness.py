from pathlib import Path

import pandas as pd

from app.io.loader import load_csv
from app.profiler.profile import profile_table
from app.rules.uniqueness import check_uniqueness

HEMAS_DIR = Path(__file__).resolve().parents[2] / "data" / "hemas"
USSAWATTE_DIR = Path(__file__).resolve().parents[2] / "data" / "ussawatte"


def test_grnhed_has_no_full_row_duplicates():
    df = load_csv(HEMAS_DIR / "GRNHED.csv")
    profile = profile_table(df, "GRNHED")
    issues = check_uniqueness("GRNHED", df, profile)
    assert not any(i["rule_id"] == "uniqueness_full_row_duplicate" for i in issues)


def test_grnhed_entrytime_near_key_collisions_are_flagged():
    # col_12 (EntryTime, a 1900-01-01-sentinel-date + real time-of-day column)
    # has genuine second-level timestamp collisions across different GRNs --
    # a real near-key duplicate, not a test artifact.
    df = load_csv(HEMAS_DIR / "GRNHED.csv")
    profile = profile_table(df, "GRNHED")
    issues = check_uniqueness("GRNHED", df, profile)
    col_12_issues = [i for i in issues if i["column_name"] == "col_12"]
    assert len(col_12_issues) == 1
    assert col_12_issues[0]["row_reference"]["count"] == 272


def test_whynotbilling_near_key_column_flags_real_duplicates():
    df = load_csv(USSAWATTE_DIR / "WhyNotBilling.csv")
    profile = profile_table(df, "WhyNotBilling")
    issues = check_uniqueness("WhyNotBilling", df, profile)
    col_1_issues = [i for i in issues if i["column_name"] == "col_1"]
    assert len(col_1_issues) == 1
    assert col_1_issues[0]["row_reference"]["count"] == 104
    assert col_1_issues[0]["severity"] == "high"
    assert col_1_issues[0]["dimension"] == "uniqueness"
    assert col_1_issues[0]["rule_id"] == "uniqueness_near_key_duplicate"


def test_full_row_duplicates_are_detected():
    df = pd.DataFrame({"col_1": ["1", "1", "2"], "col_2": ["a", "a", "b"]})
    profile = profile_table(df, "T")
    issues = check_uniqueness("T", df, profile)
    row_dup_issues = [i for i in issues if i["rule_id"] == "uniqueness_full_row_duplicate"]
    assert len(row_dup_issues) == 1
    assert row_dup_issues[0]["column_name"] is None
    assert row_dup_issues[0]["row_reference"]["count"] == 2


def test_perfectly_unique_column_is_not_flagged():
    df = pd.DataFrame({"col_1": [str(i) for i in range(100)]})
    profile = profile_table(df, "T")
    assert check_uniqueness("T", df, profile) == []


def test_low_cardinality_column_is_not_treated_as_near_key():
    # a status-code style column: lots of repeats, low distinct ratio.
    # Needs a second, varying column so rows aren't literal full-row dups.
    df = pd.DataFrame(
        {"col_1": ["1", "2", "3"] * 40, "col_2": [str(i) for i in range(120)]}
    )
    profile = profile_table(df, "T")
    assert check_uniqueness("T", df, profile) == []
