import json
from datetime import datetime
from pathlib import Path

from app.rules.engine import run_rules, summarize

COMPANY_A_DIR = Path(__file__).resolve().parents[2] / "data" / "hemas"
COMPANY_B_DIR = Path(__file__).resolve().parents[2] / "data" / "ussawatte"

# Fixed reference date so timeliness results are deterministic across runs.
REF = datetime(2026, 7, 18)


def test_issues_carry_row_sample_snapshots():
    result = run_rules(COMPANY_A_DIR, reference_date=REF)
    with_samples = [i for i in result["issues"] if (i["row_reference"] or {}).get("samples")]
    assert with_samples, "expected at least one issue with captured row samples"
    sample = with_samples[0]["row_reference"]["samples"][0]
    assert "row" in sample and isinstance(sample["values"], dict)
    # values are JSON-safe (str or None)
    assert all(v is None or isinstance(v, str) for v in sample["values"].values())


def test_run_rules_on_hemas_produces_issues_across_dimensions():
    result = run_rules(COMPANY_A_DIR, reference_date=REF)
    issues = result["issues"]
    assert len(issues) > 0
    dimensions = {i["dimension"] for i in issues}
    # Every issue carries the full ontology tuple.
    for i in issues:
        assert set(["dimension", "source", "error_type", "severity", "fixable", "status"]).issubset(i)
        assert i["source"] == "rule"
        assert i["error_type"] == "type1"
        assert i["id"] > 0
        assert i["source_client"] == "hemas"
    # At least the dimensions we know fire on this data.
    assert {"completeness", "consistency", "cross_table"}.issubset(dimensions)


def test_hemas_arithmetic_identity_violation_present():
    result = run_rules(COMPANY_A_DIR, reference_date=REF)
    arithmetic = [i for i in result["issues"] if i["rule_id"] == "consistency_arithmetic_violation"]
    assert any(i["table_name"] == "InvoiceDetails" and i["row_reference"]["count"] == 10 for i in arithmetic)


def test_run_rules_on_ussawatte_produces_issues():
    result = run_rules(COMPANY_B_DIR, reference_date=REF)
    assert result["source_client"] == "ussawatte"
    assert len(result["issues"]) > 0
    # WhyNotBilling near-key duplicates should surface.
    assert any(
        i["table_name"] == "WhyNotBilling" and i["dimension"] == "uniqueness"
        for i in result["issues"]
    )


def test_issue_ids_are_unique_and_sequential():
    result = run_rules(COMPANY_A_DIR, reference_date=REF)
    ids = [i["id"] for i in result["issues"]]
    assert ids == list(range(1, len(ids) + 1))


def test_result_is_json_serializable():
    result = run_rules(COMPANY_A_DIR, reference_date=REF)
    json.dumps(result, default=str)


def test_summarize_counts_match_issue_list():
    result = run_rules(COMPANY_A_DIR, reference_date=REF)
    summary = summarize(result)
    assert summary["total_issues"] == len(result["issues"])
    assert sum(summary["by_dimension"].values()) == len(result["issues"])
    assert sum(summary["by_severity"].values()) == len(result["issues"])
