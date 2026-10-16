from pathlib import Path

import pandas as pd

from app.io.loader import list_data_files, load_table

COMPANY_A_DIR = Path(__file__).resolve().parents[2] / "data" / "hemas"


def test_real_seeaerp_files_stay_headerless_positional():
    # every ERP export begins with a numeric id, so header detection
    # must NOT fire -- columns stay col_1..col_n.
    df = load_table(COMPANY_A_DIR / "GRNHED.csv")
    assert list(df.columns) == [f"col_{i + 1}" for i in range(16)]
    assert len(df) == 2989


def test_headered_csv_uses_its_real_names(tmp_path):
    p = tmp_path / "orders.csv"
    p.write_text(
        "OrderId,Customer,Amount\n1,Acme,100.00\n2,Beta,200.00\n", encoding="utf-8"
    )
    df = load_table(p)
    assert list(df.columns) == ["OrderId", "Customer", "Amount"]
    assert len(df) == 2
    assert df.iloc[0]["Customer"] == "Acme"


def test_duplicate_header_labels_fall_back_to_positional(tmp_path):
    # a first row with repeated labels is ambiguous, so the conservative
    # heuristic treats it as headerless rather than risk misreading data.
    p = tmp_path / "dup.csv"
    p.write_text("Name,Name,Value\na,b,1\nc,d,2\n", encoding="utf-8")
    df = load_table(p)
    assert list(df.columns) == ["col_1", "col_2", "col_3"]


def test_excel_file_loads(tmp_path):
    p = tmp_path / "book.xlsx"
    pd.DataFrame({"Id": [1, 2], "City": ["Colombo", "Kandy"]}).to_excel(p, index=False, header=True)
    df = load_table(p)
    assert list(df.columns) == ["Id", "City"]
    assert df.iloc[1]["City"] == "Kandy"


def test_null_tokens_become_missing_in_headered_file(tmp_path):
    p = tmp_path / "n.csv"
    p.write_text("A,B\n1,NULL\n2,x\n", encoding="utf-8")
    df = load_table(p)
    assert df["B"].isna().sum() == 1


def test_list_data_files_includes_excel(tmp_path):
    (tmp_path / "a.csv").write_text("1,2\n", encoding="utf-8")
    (tmp_path / "b.xlsx").write_bytes(b"")
    (tmp_path / "Temp_old.csv").write_text("1\n", encoding="utf-8")
    names = {f.name for f in list_data_files(tmp_path)}
    assert names == {"a.csv", "b.xlsx"}  # legacy Temp_* excluded
