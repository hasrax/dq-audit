import pytest

from app.scoring.score import DEFAULT_K, compute_dq_score


def _issue(dimension, severity="high", status="flagged"):
    return {"dimension": dimension, "severity": severity, "status": status}


def test_no_issues_scores_perfect_100():
    score = compute_dq_score([])
    assert score["overall_score"] == 100.0
    assert score["completeness_score"] == 100.0
    assert score["cross_table_score"] == 100.0


def test_rejected_issues_are_excluded():
    issues = [_issue("cross_table", "high", status="rejected") for _ in range(5)]
    assert compute_dq_score(issues)["overall_score"] == 100.0


def test_accepted_and_flagged_issues_both_count():
    flagged = compute_dq_score([_issue("validity", "high", "flagged")])
    accepted = compute_dq_score([_issue("validity", "high", "accepted")])
    assert flagged == accepted
    assert flagged["validity_score"] < 100.0


def test_accuracy_folds_into_validity():
    acc = compute_dq_score([_issue("accuracy", "high")])
    val = compute_dq_score([_issue("validity", "high")])
    assert acc["validity_score"] == val["validity_score"] < 100.0


def test_sub_score_matches_the_hyperbolic_formula():
    # one high-severity issue -> penalty 3 -> s = 10/13
    score = compute_dq_score([_issue("completeness", "high")])
    expected_sub = round(100 * DEFAULT_K / (DEFAULT_K + 3), 2)
    assert score["completeness_score"] == expected_sub


def test_higher_severity_lowers_score_more():
    high = compute_dq_score([_issue("validity", "high")])["validity_score"]
    low = compute_dq_score([_issue("validity", "low")])["validity_score"]
    assert high < low < 100.0


def test_weighting_makes_consistency_hurt_more_than_timeliness():
    # same single high issue, different dimension -> heavier-weighted
    # dimension pulls the overall score down further
    consistency = compute_dq_score([_issue("consistency", "high")])["overall_score"]
    timeliness = compute_dq_score([_issue("timeliness", "high")])["overall_score"]
    assert consistency < timeliness < 100.0


def test_more_issues_in_a_dimension_lower_its_sub_score():
    one = compute_dq_score([_issue("uniqueness", "high")])["uniqueness_score"]
    many = compute_dq_score([_issue("uniqueness", "high") for _ in range(5)])["uniqueness_score"]
    assert many < one
