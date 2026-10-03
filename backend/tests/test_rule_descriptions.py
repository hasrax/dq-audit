import pandas as pd

from app.rules.completeness import check_completeness
from app.rules.cross_table import check_cross_table
from app.rules.describe import describe_rule
from app.rules.ordering import check_ordering


def _rule(template, category, table, column, parameters, evidence):
    return {"template": template, "category": category, "table_name": table, "column_name": column,
            "parameters": parameters, "evidence": evidence, "violation_count": 0}


def test_cross_table_text_cites_overlap_and_uses_aliases():
    rule = _rule("cross_table_fk_orphan", "cross_table", "GRNDtl", "col_1",
                 {"parent_table": "GRNHED", "parent_column": "col_1"},
                 {"overlap_pct": 95.0, "orphan_count": 0, "row_count": 10})
    what, why = describe_rule(rule, {"GRNDtl": {"col_1": "GRN Number"}})
    assert what == "Checks that every GRN Number (col_1) in GRNDtl exists in GRNHED · col_1."
    assert "95% of the distinct GRN Number (col_1) values match" in why


def test_every_template_produces_text():
    rules = [
        _rule("completeness_null_pct", "completeness", "T", "col_1", {"max_null_pct": 5.0, "fk_role": True},
              {"null_pct": 12.5, "null_count": 5, "row_count": 40}),
        _rule("validity_negative_value", "validity", "T", "col_2", {"min_allowed": 0},
              {"type": "decimal", "min": -3, "negative_count": 2}),
        _rule("uniqueness_full_row_duplicate", "uniqueness", "T", None, {}, {"row_count": 40, "duplicate_rows": 0}),
        _rule("uniqueness_near_key_duplicate", "uniqueness", "T", "col_1", {"min_distinct_ratio": 0.9},
              {"distinct_pct": 99.5, "non_null_count": 400, "distinct_count": 398}),
        _rule("timeliness_future_date", "timeliness", "T", "col_3", {"max_days_ahead": 30},
              {"max_date": "2026-01-01", "reference_date": "2026-10-03"}),
        _rule("timeliness_stale_column", "timeliness", "T", "col_3", {"max_age_years": 2},
              {"max_date": "2019-04-19", "reference_date": "2026-10-03"}),
        _rule("consistency_ordering_violation", "consistency", "T", "col_3,col_4", {"is_date": True},
              {"conformance_pct": 99.2, "valid_pairs": 1000}),
        _rule("consistency_arithmetic_violation", "consistency", "T", "col_3,col_6,col_11", {},
              {"conformance_pct": 99.99, "informative_rows": 5000}),
    ]
    for r in rules:
        what, why = describe_rule(r)
        assert what.startswith("Checks") and why and "{" not in what + why
    assert "is on or before col_4" in describe_rule(rules[6])[0]
    assert "links to another table" in describe_rule(rules[0])[1]


def test_checks_record_clean_rules_and_tag_issues():
    df = pd.DataFrame({"col_1": ["1", "2", None, None], "col_2": ["a", "b", "c", "d"]})
    profile = {"row_count": 4, "columns": {
        "col_1": {"null_pct": 50.0, "null_count": 2},
        "col_2": {"null_pct": 0.0, "null_count": 0},
    }}
    rules: list[dict] = []
    issues = check_completeness("T", df, profile, rules=rules)
    assert [r["violation_count"] for r in rules] == [2, 0]      # clean column still recorded
    assert issues[0]["rule_key"] == rules[0]["rule_key"]


def test_cross_table_keys_distinguish_parents():
    child = pd.DataFrame({"c": ["1", "2"]})
    tables = {"C": child, "P1": pd.DataFrame({"k": ["1", "2"]}), "P2": pd.DataFrame({"k": ["1"]})}
    rels = [{"child_table": "C", "child_column": "c", "parent_table": p, "parent_column": "k", "overlap_pct": 100}
            for p in ("P1", "P2")]
    rules: list[dict] = []
    issues = check_cross_table(tables, rels, rules=rules)
    assert len({r["rule_key"] for r in rules}) == 2
    assert [r["violation_count"] for r in rules] == [0, 1]
    assert issues[0]["rule_key"] == rules[1]["rule_key"]


def test_ordering_without_collector_is_unchanged():
    df = pd.DataFrame({"a": [str(i) for i in range(40)], "b": [str(i + 1) for i in range(40)]})
    types = {"a": "int", "b": "int"}
    assert check_ordering("T", df, types, distinct_counts={"a": 40, "b": 40}) == []   # perfect pair: no issue
    rules: list[dict] = []
    check_ordering("T", df, types, distinct_counts={"a": 40, "b": 40}, rules=rules)
    assert len(rules) == 1 and rules[0]["violation_count"] == 0                     # but recorded as a passed rule
