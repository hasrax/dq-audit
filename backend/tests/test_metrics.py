from app.eval.injection import Injection
from app.eval.matching import label_matches_issue
from app.eval.metrics import evaluate


def _issue(table, column, dimension, source):
    return {"table_name": table, "column_name": column, "dimension": dimension, "source": source}


def test_label_matches_on_table_column_dimension():
    lab = Injection("T", 3, "col_2", "negative", "type1", "validity")
    assert label_matches_issue(lab, _issue("T", "col_2", "validity", "rule"))
    assert not label_matches_issue(lab, _issue("T", "col_9", "validity", "rule"))
    assert not label_matches_issue(lab, _issue("Other", "col_2", "validity", "rule"))


def test_accuracy_issue_matches_validity_label():
    lab = Injection("T", 1, "col_2", "price_outlier", "type2", "validity")
    assert label_matches_issue(lab, _issue("T", "col_2", "accuracy", "llm"))


def test_consistency_column_pair_matches_either_member():
    lab = Injection("T", 0, "col_3", "date_inversion", "type1", "consistency")
    assert label_matches_issue(lab, _issue("T", "col_3,col_4", "consistency", "rule"))


def test_combined_recall_and_missed():
    labels = [
        Injection("T", 0, "c1", "null", "type1", "completeness"),
        Injection("T", 1, "c2", "negative", "type1", "validity"),
        Injection("T", 2, "c3", "future_date", "type1", "timeliness"),  # undetected
    ]
    issues = [
        _issue("T", "c1", "completeness", "rule"),
        _issue("T", "c2", "validity", "rule"),
    ]
    m = evaluate(labels, issues)
    assert m["combined_recall"] == round(2 / 3, 4)
    assert m["complementarity"]["missed"] == 1
    assert m["recall_by_dimension"]["timeliness"] == 0.0


def test_complementarity_splits_rule_llm_both():
    labels = [
        Injection("T", 0, "c1", "null", "type1", "completeness"),      # rule only
        Injection("T", 1, "c2", "price_outlier", "type2", "validity"),  # llm only
        Injection("T", 2, "c3", "negative", "type1", "validity"),       # both
    ]
    issues = [
        _issue("T", "c1", "completeness", "rule"),
        _issue("T", "c2", "validity", "llm"),
        _issue("T", "c3", "validity", "rule"),
        _issue("T", "c3", "accuracy", "llm"),
    ]
    comp = evaluate(labels, issues)["complementarity"]
    assert comp["rule_only"] == 1
    assert comp["llm_only"] == 1
    assert comp["both"] == 1


def test_per_source_recall_targets_the_right_type():
    labels = [
        Injection("T", 0, "c1", "null", "type1", "completeness"),
        Injection("T", 1, "c2", "price_outlier", "type2", "validity"),
    ]
    issues = [
        _issue("T", "c1", "completeness", "rule"),   # catches the type1
        _issue("T", "c2", "validity", "llm"),         # catches the type2
    ]
    m = evaluate(labels, issues)
    assert m["by_source"]["rule"]["recall"] == 1.0   # rules on type1
    assert m["by_source"]["llm"]["recall"] == 1.0    # llm on type2


def test_in_scope_precision_penalises_false_positives():
    labels = [Injection("T", 0, "c1", "null", "type1", "completeness")]
    issues = [
        _issue("T", "c1", "completeness", "rule"),   # TP
        _issue("T", "c9", "completeness", "rule"),   # FP, same in-scope dimension
    ]
    prec = evaluate(labels, issues)["by_source"]["rule"]["precision"]
    assert prec == 0.5
