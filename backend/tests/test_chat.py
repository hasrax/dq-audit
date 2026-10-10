from app.chat import assistant


class FakeClient:
    def __init__(self):
        self.calls = []

    def chat(self, system, messages, **kwargs):
        self.calls.append((system, messages, kwargs))
        return "  answer  "


def test_answer_resends_last_six_turns_and_forbids_guessing(monkeypatch):
    monkeypatch.setattr(assistant, "build_context", lambda conn, upload, issue=None: "CTX")
    history = [{"role": "user" if n % 2 == 0 else "assistant", "content": f"t{n}"} for n in range(10)]
    client = FakeClient()

    reply = assistant.answer(None, {"id": 1}, "why?", history, client=client)

    system, messages, kwargs = client.calls[0]
    assert reply == "answer"
    assert "Never invent numbers" in system and "Do not write SQL or code" in system
    assert messages[0]["content"].endswith("CTX")
    assert [m["content"] for m in messages[2:-1]] == [f"t{n}" for n in range(4, 10)]
    assert messages[-1] == {"role": "user", "content": "why?"}
    assert kwargs["temperature"] <= 0.2


def test_issue_line_uses_friendly_column_names():
    issue = {"id": 7, "table_name": "GRNHED", "column_name": "col_3,col_4", "dimension": "consistency",
             "severity": "high", "source": "rule", "affected_row_count": 12, "status": "flagged",
             "trend_status": "worsening"}
    line = assistant._issue_line(issue, {"GRNHED": {"col_3": "Bill Date"}})
    assert "GRNHED.Bill Date, col_4" in line and "12 rows" in line and "worsening" in line


class _TypesConn:
    """Stand-in connection: answers the profile-types query only."""
    def execute(self, sql, params=None):
        class R:
            def fetchall(self_inner):
                return [("InvoiceNo", "int"), ("ReturnDate", "date"), ("UserID", "varchar"), ("Phone", "int")]
        return R()


def test_example_rows_are_masked_but_comparable(monkeypatch):
    from app.chat import assistant
    monkeypatch.setattr(assistant, "load_overrides", lambda client: {"T": {"mask": {"Phone"}}})
    issue = {"id": 7, "upload_id": 1, "table_name": "T", "source_client": "x",
             "row_reference": {"count": 2, "samples": [
                 {"row": 0, "values": {"InvoiceNo": "5", "ReturnDate": "2024-01-01", "UserID": "ann", "Phone": "0771234567"}},
                 {"row": 1, "values": {"InvoiceNo": "5", "ReturnDate": None, "UserID": "ann", "Phone": None}}]}}
    lines = assistant._example_rows(_TypesConn(), issue, {})
    text = "\n".join(lines)
    assert "ann" not in text and "0771234567" not in text            # text + privacy-listed columns masked
    assert "InvoiceNo = 5" in text and "ReturnDate = 2024-01-01" in text and "(empty)" in text
    user_tokens = [l.split("UserID = ")[1].split(";")[0] for l in lines[1:]]
    assert user_tokens[0] == user_tokens[1] and user_tokens[0].startswith("MASK_")   # equal values stay equal
    assert assistant._example_rows(_TypesConn(), {**issue, "row_reference": {"count": 1}}, {}) == []
