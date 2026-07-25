from app.eval.complementarity import real_complementarity


def _issue(table, dimension, column, source, status="flagged"):
    return {"table_name": table, "dimension": dimension, "column_name": column,
            "source": source, "status": status}


def test_rule_only_and_llm_only_are_separated():
    issues = [
        _issue("T", "completeness", "c1", "rule"),
        _issue("T", "validity", "c2", "llm"),
    ]
    r = real_complementarity(issues)
    assert r["overall"]["rule_only"] == 1
    assert r["overall"]["llm_only"] == 1
    assert r["overall"]["both"] == 0
    assert r["n_locations"] == 2


def test_same_location_from_both_sources_counts_as_both():
    issues = [
        _issue("T", "validity", "c2", "rule"),
        _issue("T", "accuracy", "c2", "llm"),  # accuracy folds to validity -> same location
    ]
    r = real_complementarity(issues)
    assert r["overall"]["both"] == 1
    assert r["overall"]["rule_only"] == 0
    assert r["by_dimension"]["validity"]["both"] == 1


def test_rejected_issues_are_excluded():
    issues = [_issue("T", "completeness", "c1", "rule", status="rejected")]
    r = real_complementarity(issues)
    assert r["n_locations"] == 0


def test_percentages_sum_to_100():
    issues = [
        _issue("T", "completeness", "c1", "rule"),
        _issue("T", "validity", "c2", "rule"),
        _issue("T", "uniqueness", "c3", "llm"),
    ]
    o = real_complementarity(issues)["overall"]
    assert round(o["rule_only_pct"] + o["llm_only_pct"] + o["both_pct"], 1) == 100.0


def test_by_dimension_breakdown():
    issues = [
        _issue("T", "completeness", "c1", "rule"),
        _issue("T", "completeness", "c2", "rule"),
        _issue("T", "validity", "c3", "llm"),
    ]
    bd = real_complementarity(issues)["by_dimension"]
    assert bd["completeness"]["rule_only"] == 2
    assert bd["validity"]["llm_only"] == 1
