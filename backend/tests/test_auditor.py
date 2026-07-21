from app.llm.auditor import _extract_json, _normalize_issue, audit_table
from app.llm.prompt import build_user_payload


class FakeClient:
    """Stand-in LLMClient that returns queued canned replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def complete(self, system, user, **kwargs):
        self.calls += 1
        return self.replies.pop(0)


PROFILE = {
    "table_name": "InvoiceDetails",
    "row_count": 3,
    "columns": {
        "col_1": {"type": "int", "null_pct": 0.0, "distinct_count": 3, "min": 1, "max": 3, "mean": 2.0, "top_values": {"1": 1}},
        "col_2": {"type": "decimal", "null_pct": 0.0, "distinct_count": 3, "min": 5.0, "max": 900.0, "mean": 305.0, "top_values": {"5.0": 1}},
    },
}
SAMPLE = {"table_name": "InvoiceDetails", "sampled_rows": [{"row_index": 2, "values": {"col_1": "3", "col_2": "900.0"}}]}


def test_extract_json_handles_markdown_fences():
    raw = '```json\n{"issues": []}\n```'
    assert _extract_json(raw) == {"issues": []}


def test_extract_json_handles_surrounding_prose():
    raw = 'Here is the result:\n{"issues": [{"severity": "high"}]}\nHope that helps!'
    assert _extract_json(raw)["issues"][0]["severity"] == "high"


def test_normalize_issue_maps_to_ontology_tuple():
    raw = {
        "column": "col_2", "row_ref": 2, "dimension": "Accuracy",
        "severity": "critical", "explanation": "Price is 100x the typical value.",
        "recommendation": "Check for a decimal error.", "fixable": "yes",
    }
    issue = _normalize_issue(raw, "InvoiceDetails")
    assert issue["source"] == "llm"
    assert issue["error_type"] == "type2"
    assert issue["severity"] == "high"          # critical -> high
    assert issue["dimension"] == "accuracy"     # lowercased
    assert issue["fixable"] == "yes"
    assert issue["row_reference"]["sample_rows"] == [2]
    assert issue["rule_id"] is None


def test_unknown_dimension_falls_back_to_consistency():
    issue = _normalize_issue({"dimension": "weirdness", "severity": "low"}, "T")
    assert issue["dimension"] == "consistency"


def test_audit_table_parses_clean_reply():
    client = FakeClient(['{"issues": [{"column": "col_2", "row_ref": 2, "dimension": "validity", "severity": "high", "explanation": "e", "recommendation": "r", "fixable": "no"}]}'])
    issues = audit_table(PROFILE, SAMPLE, [], client=client)
    assert len(issues) == 1
    assert issues[0]["column_name"] == "col_2"
    assert client.calls == 1


def test_audit_table_retries_once_on_bad_json_then_succeeds():
    client = FakeClient(["not json at all", '{"issues": []}'])
    issues = audit_table(PROFILE, SAMPLE, [], client=client)
    assert issues == []
    assert client.calls == 2  # retried


def test_audit_table_gives_up_gracefully_after_retry():
    client = FakeClient(["garbage", "still garbage"])
    issues = audit_table(PROFILE, SAMPLE, [], client=client)
    assert issues == []
    assert client.calls == 2


def test_build_user_payload_only_includes_this_tables_relationships():
    rels = [
        {"child_table": "InvoiceDetails", "child_column": "col_1", "parent_table": "InvoiceHeader", "parent_column": "col_1", "overlap_pct": 100.0},
        {"child_table": "SomethingElse", "child_column": "col_9", "parent_table": "X", "parent_column": "col_1", "overlap_pct": 99.0},
    ]
    payload = build_user_payload(PROFILE, SAMPLE, rels)
    assert "InvoiceHeader" in payload
    assert "SomethingElse" not in payload
