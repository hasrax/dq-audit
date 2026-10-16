from pathlib import Path

import pandas as pd

from app.io.loader import load_csv
from app.profiler.stats import compute_stats
from app.profiler.types import infer_types
from app.rules.arithmetic import check_arithmetic, mine_arithmetic

COMPANY_A_DIR = Path(__file__).resolve().parents[2] / "data" / "hemas"
COMPANY_B_DIR = Path(__file__).resolve().parents[2] / "data" / "ussawatte"


def _profiled(path):
    df = load_csv(path)
    types = infer_types(df)
    stats = compute_stats(df, types)
    distinct_counts = {c: s["distinct_count"] for c, s in stats.items()}
    return df, types, distinct_counts


def test_hemas_qty_price_total_identity_found_with_real_violations():
    df, types, distinct_counts = _profiled(COMPANY_A_DIR / "InvoiceDetails.csv")
    found = mine_arithmetic(df, types, distinct_counts=distinct_counts)
    real = [
        r
        for r in found
        if {r["factor_a"], r["factor_b"]} == {"col_3", "col_6"} and r["product_c"] == "col_11"
    ]
    assert len(real) == 1
    assert real[0]["conformance"] > 0.999
    assert real[0]["violation_count"] == 10


def test_zero_inflated_columns_do_not_produce_spurious_identities():
    # col_9 and col_14 are mostly-zero; without informative-row scoring
    # they produce 0*0=0 "identities". None should survive.
    df, types, distinct_counts = _profiled(COMPANY_A_DIR / "InvoiceDetails.csv")
    found = mine_arithmetic(df, types, distinct_counts=distinct_counts)
    assert all("col_9" not in (r["factor_a"], r["factor_b"], r["product_c"]) or r["conformance"] > 0.99 for r in found)
    assert all("col_14" not in (r["factor_a"], r["factor_b"], r["product_c"]) for r in found)


def test_perfectly_clean_identity_produces_no_issue():
    # Company B col_7 x col_12 = col_13 holds for 100% of rows -> no violation,
    # so the rule correctly emits nothing (there is no data quality problem).
    df, types, distinct_counts = _profiled(COMPANY_B_DIR / "Dis_InvoiceDetails.csv")
    issues = check_arithmetic("Dis_InvoiceDetails", df, types, distinct_counts=distinct_counts)
    assert issues == []


def test_injected_violation_into_clean_identity_is_caught():
    qty = list(range(1, 201))
    price = [10.0] * 200
    total = [q * 10.0 for q in qty]
    total[5] = 999.99  # one corrupted total
    df = pd.DataFrame(
        {"col_1": [str(x) for x in qty], "col_2": [f"{p:.2f}" for p in price], "col_3": [f"{t:.2f}" for t in total]}
    )
    types = infer_types(df)
    issues = check_arithmetic("T", df, types)
    assert len(issues) == 1
    assert issues[0]["dimension"] == "consistency"
    assert issues[0]["severity"] == "high"
    assert issues[0]["row_reference"]["count"] == 1
    assert 5 in issues[0]["row_reference"]["sample_rows"]


def test_constant_columns_are_dropped_by_min_distinct():
    df = pd.DataFrame(
        {
            "col_1": ["0"] * 100,
            "col_2": [str(i) for i in range(100)],
            "col_3": ["0"] * 100,
        }
    )
    types = infer_types(df)
    distinct_counts = {"col_1": 1, "col_2": 100, "col_3": 1}
    assert mine_arithmetic(df, types, distinct_counts=distinct_counts) == []
