from datetime import datetime
from pathlib import Path

import pandas as pd

from app.io.loader import load_csv
from app.profiler.profile import profile_table
from app.rules.timeliness import check_timeliness

HEMAS_DIR = Path(__file__).resolve().parents[2] / "data" / "hemas"


def test_grnhed_frozen_systemdate_column_flagged_stale():
    # col_16 is constant at 2019-04-19 across all 2989 rows -- a frozen field.
    df = load_csv(HEMAS_DIR / "GRNHED.csv")
    profile = profile_table(df, "GRNHED")
    issues = check_timeliness("GRNHED", df, profile)
    col_16_issues = [i for i in issues if i["column_name"] == "col_16"]
    assert len(col_16_issues) == 1
    assert col_16_issues[0]["rule_id"] == "timeliness_stale_column"
    assert col_16_issues[0]["row_reference"]["count"] == 2989


def test_grnhed_1900_sentinel_time_column_flagged_stale():
    # col_12 (EntryTime) is anchored at 1900-01-01, a classic sentinel-date pattern.
    df = load_csv(HEMAS_DIR / "GRNHED.csv")
    profile = profile_table(df, "GRNHED")
    issues = check_timeliness("GRNHED", df, profile)
    assert any(i["column_name"] == "col_12" and i["rule_id"] == "timeliness_stale_column" for i in issues)


def test_grnhed_billdate_columns_are_not_flagged():
    # col_3/col_4 range up to ~2026-05, well within the fresh window as of today.
    df = load_csv(HEMAS_DIR / "GRNHED.csv")
    profile = profile_table(df, "GRNHED")
    issues = check_timeliness("GRNHED", df, profile)
    assert not any(i["column_name"] in ("col_3", "col_4") for i in issues)


def test_future_date_is_flagged_high_severity():
    reference = datetime(2026, 1, 1)
    df = pd.DataFrame(
        {"col_1": ["2025-12-01 00:00:00.000"] * 9 + ["2027-06-15 00:00:00.000"]}
    )
    profile = profile_table(df, "T")
    issues = check_timeliness("T", df, profile, reference_date=reference)
    future_issues = [i for i in issues if i["rule_id"] == "timeliness_future_date"]
    assert len(future_issues) == 1
    assert future_issues[0]["severity"] == "high"
    assert future_issues[0]["row_reference"]["count"] == 1


def test_recent_dates_relative_to_a_fixed_reference_are_not_flagged():
    reference = datetime(2026, 1, 1)
    df = pd.DataFrame({"col_1": ["2025-06-15 00:00:00.000"] * 10})
    profile = profile_table(df, "T")
    assert check_timeliness("T", df, profile, reference_date=reference) == []
