from pathlib import Path

import pandas as pd

from app.io.loader import load_csv
from app.profiler.profile import profile_table
from app.profiler.sampling import build_stratified_sample

COMPANY_A_DIR = Path(__file__).resolve().parents[2] / "data" / "hemas"


def _grnhed():
    df = load_csv(COMPANY_A_DIR / "GRNHED.csv")
    return df, profile_table(df, "GRNHED")


def test_flagged_rows_are_always_included_and_labelled():
    df, profile = _grnhed()
    sample = build_stratified_sample(df, profile, flagged_rows={5, 100, 200})
    by_index = {r["row_index"]: r for r in sample["sampled_rows"]}
    for idx in (5, 100, 200):
        assert idx in by_index
        assert "flagged" in by_index[idx]["reasons"]


def test_sample_is_capped_at_max_rows():
    df, profile = _grnhed()
    sample = build_stratified_sample(df, profile, flagged_rows=set(range(50)), max_rows=15)
    assert len(sample["sampled_rows"]) == 15


def test_sample_is_deterministic_for_a_fixed_seed():
    df, profile = _grnhed()
    a = build_stratified_sample(df, profile, seed=7)
    b = build_stratified_sample(df, profile, seed=7)
    assert [r["row_index"] for r in a["sampled_rows"]] == [r["row_index"] for r in b["sampled_rows"]]
    assert a["seed"] == 7


def test_different_seed_changes_random_fill():
    df, profile = _grnhed()
    a = build_stratified_sample(df, profile, seed=1)
    b = build_stratified_sample(df, profile, seed=2)
    assert [r["row_index"] for r in a["sampled_rows"]] != [r["row_index"] for r in b["sampled_rows"]]


def test_edge_rows_capture_column_min_and_max():
    df, profile = _grnhed()
    # col_1 is the PK: min=1 -> row of value "1", max=2989 -> row of value "2989"
    sample = build_stratified_sample(df, profile, max_rows=40)
    reasons = {r["row_index"]: r["reasons"] for r in sample["sampled_rows"]}
    assert any("edge:col_1:min" in rs for rs in reasons.values())
    assert any("edge:col_1:max" in rs for rs in reasons.values())


def test_random_fill_used_when_flagged_and_edge_are_few():
    df, profile = _grnhed()
    sample = build_stratified_sample(df, profile, flagged_rows={0}, max_rows=20)
    assert len(sample["sampled_rows"]) == 20
    assert any("random" in r["reasons"] for r in sample["sampled_rows"])


def test_out_of_range_flagged_indices_are_ignored():
    df, profile = _grnhed()
    sample = build_stratified_sample(df, profile, flagged_rows={999999})
    assert all(r["row_index"] != 999999 for r in sample["sampled_rows"])


def test_missing_values_serialize_as_none():
    df = pd.DataFrame({"col_1": ["a", None, "c"], "col_2": ["1", "2", "3"]})
    profile = profile_table(df, "T")
    sample = build_stratified_sample(df, profile, flagged_rows={1}, max_rows=1)
    row = sample["sampled_rows"][0]
    assert row["values"]["col_1"] is None
