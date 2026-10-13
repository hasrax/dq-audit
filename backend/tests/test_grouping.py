from app.reports import grouping


DIMS = {"completeness_null_pct": "completeness", "cross_table_fk_orphan": "cross_table",
        "consistency_ordering_violation": "consistency", "validity_negative_value": "validity"}


def _i(id_, table, rule, sev="high", source="rule", ref=None, rows=1):
    return {"id": id_, "table_name": table, "column_name": f"c{id_}", "rule_id": rule, "severity": sev,
            "source": source, "rule_ref": ref, "affected_row_count": rows,
            "dimension": DIMS.get(rule, "accuracy")}


def test_unused_fields_and_attention():
    rules = [{"id": 1, "template": "completeness_null_pct", "evidence": {"null_pct": 100.0}},
             {"id": 2, "template": "completeness_null_pct", "evidence": {"null_pct": 40.0}}]
    unused = grouping.unused_rule_ids(rules)
    assert unused == {1}
    issues = [_i(1, "T", "completeness_null_pct", ref=1), _i(2, "T", "completeness_null_pct", ref=2),
              _i(3, "T", None, sev="medium", source="llm")]
    assert grouping.unused_fields(issues, unused) == {"T": ["c1"]}
    assert [i["id"] for i in grouping.needs_attention(issues, unused)] == [2]          # high, not unused
    assert [i["id"] for i in grouping.needs_attention(issues[2:], unused)] == [3]      # no high -> medium


def test_group_keeps_order_and_never_groups_ai():
    issues = [_i(1, "T", "consistency_ordering_violation", rows=5), _i(2, "U", "validity_negative_value"),
              _i(3, "T", "consistency_ordering_violation", sev="medium", rows=7),
              _i(4, "T", None, source="llm"), _i(5, "T", None, source="llm")]
    items = grouping.group(issues)
    assert [x["kind"] for x in items] == ["group", "issue", "issue", "issue"]
    g = items[0]
    assert g["title"] == "2 pairs of values are in an unexpected order" and g["rows"] == 12
    assert g["severity"] == "high" and [m["id"] for m in g["issues"]] == [1, 3]


def test_priority_prefers_weighty_dimensions_and_bigger_problems():
    missing = _i(1, "T", "completeness_null_pct", rows=450_000)    # huge, but only missing values
    broken = _i(2, "T", "cross_table_fk_orphan", rows=100)
    small_broken = _i(3, "U", "cross_table_fk_orphan", sev="low", rows=1)
    order = grouping.by_priority(grouping.group([missing, small_broken, broken]))
    assert [x["issue"]["id"] for x in order] == [2, 1, 3]       # severity, then type weight, then rows
    assert grouping.type_label("accuracy") == grouping.type_label("validity") == "Wrong values"


def test_action_categories():
    unused = {9}
    assert grouping.action_category(_i(1, "T", "cross_table_fk_orphan"), unused) == "fix_now"
    assert grouping.action_category(_i(2, "T", "completeness_null_pct"), unused) == "can_wait"
    assert grouping.action_category(_i(3, "T", "completeness_null_pct", ref=9), unused) == "not_used"
    assert grouping.action_category(_i(4, "T", None, source="llm"), unused) == "check"
    assert grouping.action_category(_i(5, "T", "validity_negative_value", sev="medium"), unused) == "can_wait"
