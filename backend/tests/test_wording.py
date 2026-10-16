"""Display-time wording: stored issue / rule texts in plain English."""
import pytest

from app.reports.wording import plain, show

CASES = [
    ("col_5 is empty in 20,608 of 20,608 rows (100%).", "col_5 is empty in every row (20,608 of 20,608)."),
    ("col_6 is empty in 2,988 of 2,989 rows (99.97%).", "col_6 is empty in 2,988 of 2,989 rows (over 99.9%)."),
    ("Remarks is 99.27% null (79669 of 80258 rows).", "Remarks is empty in 79,669 of 80,258 rows (99.3%)."),
    ("TotalDiscount is usually <= InvoiceNo (99.9982% of 456121 rows), but 8 rows break that order.",
     "TotalDiscount is usually not more than InvoiceNo (over 99.9% of 456,121 rows), but in 8 rows it does not."),
    ("col_11 usually equals col_3 x col_6 (99.9963% of 191714 rows), but 7 rows break that identity.",
     "col_11 usually equals col_3 × col_6 (over 99.9% of 191,714 rows), but in 7 rows it does not."),
    ("col_12 is below zero in 3,098 of 42,367 rows (7.31%); the lowest value is -79000.0.",
     "col_12 is below zero in 3,098 of 42,367 rows (7.3%); the lowest value is -79,000."),
    ("The latest col_16 is 2019-04-19, more than 2 years ago.", "The latest col_16 is 19 Apr 2019, more than 2 years ago."),
    ("col_12 looks like a unique identifier (1842 distinct of 1890 non-null values, 97.46%)",
     "col_12 looks like a unique identifier (1,842 distinct of 1,890 filled values, 97.5%)"),
    ("Check rows where col_11 != col_3 x col_6 -- a mis-entered total.",
     "Check rows where col_11 is not col_3 × col_6 — a mis-entered total."),
    ("in 7 of 456,121 rows (<0.1%)", "in 7 of 456,121 rows (<0.1%)"),
    ("60 rows in GRNDtl (0.29%) have a value", "60 rows in GRNDtl (0.3%) have a value"),
    ("true in 100.00% of 272,108 rows", "true in 100% of 272,108 rows"),
    ("issue #1234 in audit #298, data from 2026", "issue #1234 in audit #298, data from 2026"),
]


@pytest.mark.parametrize("stored, shown", CASES)
def test_plain(stored, shown):
    assert plain(stored) == shown


def test_show_names_columns_first():
    aliases = {"GRNHED": {"col_5": "Remarks"}}
    assert show("col_5 is empty in 20,608 of 20,608 rows (100%).", "GRNHED", aliases) == \
        "Remarks is empty in every row (20,608 of 20,608)."


def test_empty_text():
    assert plain(None) == "" and plain("") == ""
