import json

from app.scoring.score import (
    DIMENSION_WEIGHTS,
    compute_dq_score,
    load_weights,
    score_contributions,
)


def _issue(dimension, severity="high"):
    return {"dimension": dimension, "severity": severity, "status": "flagged"}


def test_load_weights_falls_back_to_defaults_when_no_file(tmp_path):
    w = load_weights(tmp_path / "missing.json")
    assert w == DIMENSION_WEIGHTS


def test_config_overrides_and_missing_dims_keep_defaults(tmp_path):
    path = tmp_path / "w.json"
    path.write_text(json.dumps({"completeness": 0.5}), encoding="utf-8")
    w = load_weights(path)
    assert w["completeness"] == 0.5
    assert w["consistency"] == DIMENSION_WEIGHTS["consistency"]  # untouched


def test_shipped_weights_file_sums_to_one():
    w = load_weights()  # config/score_weights.json
    assert round(sum(w.values()), 6) == 1.0


def test_custom_weights_change_the_overall_score():
    issues = [_issue("timeliness", "high")]
    default = compute_dq_score(issues)["overall_score"]
    heavier = compute_dq_score(issues, weights={**DIMENSION_WEIGHTS, "timeliness": 0.9})["overall_score"]
    assert heavier < default  # weighting the hurt dimension more lowers the score


def test_contributions_sum_to_overall():
    issues = [_issue("completeness"), _issue("cross_table"), _issue("validity", "medium")]
    score = compute_dq_score(issues)
    contribs = score_contributions(score)
    assert round(sum(contribs.values()), 2) == score["overall_score"]
