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
