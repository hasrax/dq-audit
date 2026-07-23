from app.eval.cross_client import compare_fingerprints, fingerprint


def _rule(dimension, rule_id):
    return {"source": "rule", "dimension": dimension, "rule_id": rule_id}


def test_fingerprint_collects_rule_types_and_dimensions():
    issues = [
        _rule("completeness", "completeness_null_pct"),
        _rule("validity", "validity_negative_value"),
        {"source": "llm", "dimension": "accuracy", "rule_id": None},  # ignored
    ]
    fp = fingerprint(issues, label="hemas", dq_score=30.4)
    assert fp["dimensions"] == ["completeness", "validity"]
    assert fp["rule_types"] == ["completeness_null_pct", "validity_negative_value"]
    assert fp["rule_issue_count"] == 2
    assert fp["dq_score"] == 30.4


def test_compare_reports_overlap_and_client_specifics():
    a = fingerprint(
        [_rule("completeness", "completeness_null_pct"),
         _rule("validity", "validity_negative_value"),
         _rule("cross_table", "cross_table_fk_orphan")],
        label="hemas", dq_score=30.4,
    )
    b = fingerprint(
        [_rule("completeness", "completeness_null_pct"),
         _rule("validity", "validity_negative_value"),
         _rule("uniqueness", "uniqueness_full_row_duplicate")],
        label="ussawatte", dq_score=44.06,
    )
    cmp = compare_fingerprints(a, b)
    assert cmp["rule_types_both"] == ["completeness_null_pct", "validity_negative_value"]
    assert cmp["rule_types_only_a"] == ["cross_table_fk_orphan"]
    assert cmp["rule_types_only_b"] == ["uniqueness_full_row_duplicate"]
    # 2 shared of 4 total rule types -> 50%
    assert cmp["rule_type_overlap_pct"] == 50.0
    assert cmp["dq_scores"] == {"hemas": 30.4, "ussawatte": 44.06}


def test_full_overlap_is_100_pct():
    fp = fingerprint([_rule("completeness", "completeness_null_pct")], "a")
    cmp = compare_fingerprints(fp, fingerprint([_rule("completeness", "completeness_null_pct")], "b"))
    assert cmp["rule_type_overlap_pct"] == 100.0
    assert cmp["dimension_overlap_pct"] == 100.0
