from pathlib import Path

from app.io.loader import is_excluded, list_data_files, load_csv

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "hemas"


def test_list_data_files_finds_all_hemas_csvs():
    names = {f.name for f in list_data_files(DATA_DIR)}
    assert names == {
        "GRNDtl.csv",
        "GRNHED.csv",
        "InvoiceDetails.csv",
        "InvoiceHeader.csv",
        "SupplierMaster.csv",
    }


def test_load_csv_strips_bom_and_assigns_positional_columns():
    df = load_csv(DATA_DIR / "GRNHED.csv")
    assert list(df.columns) == [f"col_{i + 1}" for i in range(16)]
    assert len(df) == 2989
    assert df.iloc[0, 0] == "3"


def test_null_literal_and_empty_string_become_missing():
    df = load_csv(DATA_DIR / "GRNHED.csv")
    assert df.isna().any().any()


def test_quoted_commas_do_not_split_fields():
    df = load_csv(DATA_DIR / "SupplierMaster.csv")
    assert len(df) == 3
    assert list(df.columns) == [f"col_{i + 1}" for i in range(19)]


def test_is_excluded_matches_legacy_patterns():
    assert is_excluded("Temp_Foo")
    assert is_excluded("Customer_OLD")
    assert is_excluded("Z_Archive")
    assert is_excluded("Report_Backup_2023")
    assert not is_excluded("GRNHED")
