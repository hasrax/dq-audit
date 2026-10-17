from pathlib import Path

import pandas as pd

from app.io.loader import load_csv
from app.profiler.profile import profile_table
from app.rules.validity import check_validity

COMPANY_A_DIR = Path(__file__).resolve().parents[2] / "data" / "hemas"
COMPANY_B_DIR = Path(__file__).resolve().parents[2] / "data" / "ussawatte"


def test_no_negatives_in_grnhed_fires_no_issues():
    df = load_csv(COMPANY_A_DIR / "GRNHED.csv")
    profile = profile_table(df, "GRNHED")
    issues = check_validity("GRNHED", df, profile)
    assert issues == []


def test_dis_invoiceheader_negative_column_fires_medium_severity():
    df = load_csv(COMPANY_B_DIR / "Dis_InvoiceHeader.csv")
    profile = profile_table(df, "Dis_InvoiceHeader")
    issues = check_validity("Dis_InvoiceHeader", df, profile)
    col_12_issues = [i for i in issues if i["column_name"] == "col_12"]
    assert len(col_12_issues) == 1
    assert col_12_issues[0]["row_reference"]["count"] == 3098
    assert col_12_issues[0]["severity"] == "medium"
    assert col_12_issues[0]["dimension"] == "validity"


def test_rare_negative_fires_high_severity():
    df = pd.DataFrame({"col_1": ["10"] * 99 + ["-5"]})  # 1% negative
    profile = profile_table(df, "T")
    issues = check_validity("T", df, profile)
    assert issues[0]["severity"] == "high"
    assert issues[0]["row_reference"]["count"] == 1


def test_common_negative_fires_low_severity():
    df = pd.DataFrame({"col_1": ["-10"] * 60 + ["10"] * 40})  # 60% negative
    profile = profile_table(df, "T")
    issues = check_validity("T", df, profile)
    assert issues[0]["severity"] == "low"


def test_varchar_and_date_columns_are_never_checked():
    df = pd.DataFrame(
        {
            "col_1": ["abc", "def"],
            "col_2": ["2023-05-25 00:00:00.000", "2023-05-26 00:00:00.000"],
        }
    )
    profile = profile_table(df, "T")
    assert check_validity("T", df, profile) == []
