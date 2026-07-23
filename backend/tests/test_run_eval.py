"""End-to-end injection eval, rules-only (offline, no API).

Exercises the whole harness on the real Hemas extract and asserts the
Type 1 recall target. Type 2 recall is intentionally low here because
those classes are the LLM's job (verified separately in the full run).
"""
from pathlib import Path

import pytest

from app.eval.run_eval import build_scenario, run

HEMAS_DIR = Path(__file__).resolve().parents[2] / "data" / "hemas"

pytestmark = pytest.mark.skipif(
    not (HEMAS_DIR / "GRNHED.csv").exists(), reason="Hemas data not present"
)


def test_scenario_injects_all_six_dimensions():
    _, labels = build_scenario(HEMAS_DIR, seed=20260722)
    dims = {lab.dimension for lab in labels}
    assert {"completeness", "validity", "consistency", "uniqueness", "timeliness", "cross_table"} <= dims


def test_scenario_is_reproducible_under_fixed_seed():
    _, a = build_scenario(HEMAS_DIR, seed=123)
    _, b = build_scenario(HEMAS_DIR, seed=123)
    assert [(x.table, x.row, x.column, x.error_class) for x in a] == \
           [(x.table, x.row, x.column, x.error_class) for x in b]


def test_rules_meet_type1_recall_target():
    report = run(HEMAS_DIR, with_llm=False, seed=20260722)
    m = report["metrics"]
    # locked target: rules >= 0.90 recall on Type 1 injections
    assert m["recall_by_type"]["type1"] >= 0.90
    assert m["by_source"]["rule"]["recall"] >= 0.90


def test_every_type1_dimension_is_detected_by_rules():
    report = run(HEMAS_DIR, with_llm=False, seed=20260722)
    rbd = report["metrics"]["recall_by_dimension"]
    for dim in ("completeness", "consistency", "cross_table", "timeliness", "uniqueness"):
        assert rbd[dim] == 1.0, f"{dim} not fully detected: {rbd[dim]}"
