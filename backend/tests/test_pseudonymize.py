from pathlib import Path

import pandas as pd

from app.io.loader import load_csv
from app.profiler.profile import profile_table
from app.profiler.sampling import build_stratified_sample
from app.privacy.pseudonymize import (
    classify_pii_columns,
    mask_value,
    pseudonymize_profile,
    pseudonymize_sample,
)

USSAWATTE_DIR = Path(__file__).resolve().parents[2] / "data" / "ussawatte"
HEMAS_DIR = Path(__file__).resolve().parents[2] / "data" / "hemas"


def _customermaster():
    df = load_csv(USSAWATTE_DIR / "CustomerMaster.csv")
    return df, profile_table(df, "CustomerMaster")


def test_free_text_columns_masked_and_numeric_dates_kept():
    df, profile = _customermaster()
    d = classify_pii_columns(df, profile)
    # names / address / remark
    assert d["col_2"] is True
    assert d["col_3"] is True
    assert d["col_8"] is True
    # id (int), a category, a date -> kept
    assert d["col_1"] is False
    assert d["col_7"] is False  # date
    assert d["col_12"] is False  # constant "A" category


def test_numeric_columns_are_never_auto_masked():
    # phone-as-int (col_4) is NOT caught by the heuristic -- documented
    # override territory, proving the numeric-is-audit-data rule.
    df, profile = _customermaster()
    d = classify_pii_columns(df, profile)
    assert d["col_4"] is False


def test_mask_override_protects_columns_the_heuristic_misses():
    df, profile = _customermaster()
    d = classify_pii_columns(df, profile, mask_override={"col_4", "col_21"})
    assert d["col_4"] is True  # phone
    assert d["col_21"] is True  # geo-coords


def test_keep_override_unmasks_a_column():
    df, profile = _customermaster()
    d = classify_pii_columns(df, profile, keep_override={"col_2"})
    assert d["col_2"] is False


def test_mask_override_takes_precedence_over_keep_override():
    df, profile = _customermaster()
    d = classify_pii_columns(
        df, profile, mask_override={"col_1"}, keep_override={"col_1"}
    )
    assert d["col_1"] is True


def test_mask_value_is_deterministic_and_salted():
    assert mask_value("Sunil Stores") == mask_value("Sunil Stores")
    assert mask_value("Sunil Stores").startswith("MASK_")
    assert mask_value("Sunil Stores", salt="a") != mask_value("Sunil Stores", salt="b")


def test_pseudonymize_sample_masks_values_and_records_columns():
    df, profile = _customermaster()
    pii = classify_pii_columns(df, profile)
    sample = build_stratified_sample(df, profile, flagged_rows={0, 1, 2})
    masked = pseudonymize_sample(sample, pii)

    assert "col_2" in masked["masked_columns"]
    for row in masked["sampled_rows"]:
        if row["values"]["col_2"] is not None:
            assert row["values"]["col_2"].startswith("MASK_")
        # a kept numeric column passes through unchanged
        assert masked["sampled_rows"][0]["values"]["col_1"] == sample["sampled_rows"][0]["values"]["col_1"]


def test_pseudonymization_preserves_repeated_identity_for_near_duplicates():
    # Two rows share the same shop name -> must map to the same token so
    # the LLM can still detect the duplicate.
    df = pd.DataFrame(
        {
            "col_1": ["1", "2", "3"],
            "col_2": ["Sunil Stores here", "Sunil Stores here", "Other Shop name"],
        }
    )
    profile = profile_table(df, "T")
    pii = classify_pii_columns(df, profile)
    sample = build_stratified_sample(df, profile, flagged_rows={0, 1, 2})
    masked = pseudonymize_sample(sample, pii)
    by_index = {r["row_index"]: r["values"]["col_2"] for r in masked["sampled_rows"]}
    assert by_index[0].startswith("MASK_")
    assert by_index[0] == by_index[1]
    assert by_index[0] != by_index[2]


def test_pseudonymize_profile_masks_stats_of_pii_columns_only():
    df = pd.DataFrame(
        {
            "col_1": ["Acme Distributors Ltd A", "Acme Distributors Ltd B", "Zebra Traders C"],
            "col_2": ["100", "200", "300"],
        }
    )
    profile = profile_table(df, "T")
    pii = classify_pii_columns(df, profile)
    safe = pseudonymize_profile(profile, pii)

    # PII column: min/max and top_values keys must be masked
    c1 = safe["columns"]["col_1"]
    assert c1["min"].startswith("MASK_")
    assert c1["max"].startswith("MASK_")
    assert all(k.startswith("MASK_") for k in c1["top_values"])
    assert not any("Acme" in k for k in c1["top_values"])
    # numeric column: statistics pass through untouched (the LLM needs them)
    assert safe["columns"]["col_2"]["min"] == profile["columns"]["col_2"]["min"]
    assert safe["columns"]["col_2"]["max"] == profile["columns"]["col_2"]["max"]


def test_profile_and_sample_masking_share_the_same_token():
    df = pd.DataFrame({"col_1": ["Acme Distributors Ltd X"] * 3, "col_2": ["1", "2", "3"]})
    profile = profile_table(df, "T")
    pii = classify_pii_columns(df, profile)
    safe_profile = pseudonymize_profile(profile, pii)
    sample = build_stratified_sample(df, profile, flagged_rows={0}, max_rows=1)
    masked_sample = pseudonymize_sample(sample, pii)
    # same underlying value -> same token in both profile stats and sample
    assert safe_profile["columns"]["col_1"]["min"] == masked_sample["sampled_rows"][0]["values"]["col_1"]


def test_null_values_stay_null_after_masking():
    df = pd.DataFrame({"col_1": ["Alpha Beta", None], "col_2": ["1", "2"]})
    profile = profile_table(df, "T")
    pii = classify_pii_columns(df, profile)
    sample = build_stratified_sample(df, profile, flagged_rows={0, 1})
    masked = pseudonymize_sample(sample, pii)
    row1 = next(r for r in masked["sampled_rows"] if r["row_index"] == 1)
    assert row1["values"]["col_1"] is None
