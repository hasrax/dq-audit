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


def test_mostly_blank_fields_go_to_check():
    rules = [{"id": n, "template": "completeness_null_pct", "evidence": {"null_pct": pct}}
             for n, pct in ((1, 100.0), (2, 99.9), (3, 75.0), (4, 50.0), (5, 49.9))]
    assert grouping.unused_rule_ids(rules) == {1, 2}
    blank = grouping.mostly_blank_rule_ids(rules)
    assert blank == {3, 4}
    assert grouping.action_category(_i(1, "T", "completeness_null_pct", ref=3), set(), blank) == "check"
    assert grouping.action_category(_i(2, "T", "completeness_null_pct", ref=5), set(), blank) == "can_wait"


def test_value_order_findings_are_marked():
    assert grouping.is_value_order(_i(1, "T", "consistency_ordering_violation"))
    assert not grouping.is_value_order(_i(2, "T", "cross_table_fk_orphan"))
    assert not grouping.is_value_order(_i(3, "T", "consistency_ordering_violation", source="llm"))


def test_link_groups():
    assert grouping.link_group("No Of Items", "GRN Number") == ("careful", "item counts are small numbers")
    assert grouping.link_group("Purchased Quantity", "Receipt Computer Code")[0] == "careful"
    assert grouping.link_group("Item Order", "GRN Number") == ("careful", "line order is 1, 2, 3…")
    assert grouping.link_group("Order No", "Order No")[0] == "same"            # an identifier, not a count
    assert grouping.link_group("GRN Number", "GRN Number")[0] == "same"
    assert grouping.link_group("col_1", "col_1")[0] == "different"            # unknown names decide nothing
    assert grouping.link_group("Supplier", "Supplier Code")[0] == "different"


def test_link_disagrees_and_rule_uses():
    assert grouping.link_disagrees({"overlap_pct": 96.3, "orphan_count": 269501, "row_count": 272519})
    assert not grouping.link_disagrees({"overlap_pct": 96.3, "orphan_count": 60, "row_count": 20608})
    assert not grouping.link_disagrees({"overlap_pct": 80, "orphan_count": 9, "row_count": 10})
    rules = [{"template": "cross_table_fk_orphan", "table_name": "A", "column_name": "c",
              "parameters": {"parent_table": "B", "parent_column": "id"}}] * 2
    assert grouping.rule_uses(rules) == {("A", "c", "B", "id"): 2}
