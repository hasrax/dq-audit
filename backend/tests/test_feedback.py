import pytest

from app.db import repository
from app.llm import feedback

TEST_CLIENT = "__pytest_feedback__"


def _ex(i, table="T"):
    return {"id": i, "table_name": table, "column_name": "col_1", "description": "x" * 200, "user_note": None}


def test_empty_feedback_adds_nothing():
    assert feedback.build_org_context([], [], []) == ""


def test_context_labels_accepted_and_rejected():
    text = feedback.build_org_context([{"id": 1, "note": "We are VAT-exempt."}],
                                      [_ex(1)], [{**_ex(2), "user_note": "promo pricing"}])
    assert "We are VAT-exempt." in text
    assert "CONFIRMED REAL ISSUES" in text
    assert "do not report similar findings unless the evidence is clearly different" in text
    assert 'reviewer: "promo pricing"' in text


def test_budget_trims_examples_but_keeps_notes():
    notes = [{"id": 1, "note": "keep me"}]
    text, acc, rej, trimmed = feedback.fit_to_budget(5950, notes, [_ex(i) for i in range(8)], [_ex(i) for i in range(8)])
    assert trimmed > 0 and len(acc) + len(rej) == 16 - trimmed
    assert "keep me" in text
    assert feedback.estimate_tokens(text) + 5950 <= feedback.PROMPT_TOKEN_BUDGET or not (acc or rej)


@pytest.fixture
def conn():
    try:
        c = repository.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available: {exc}")
    repository.apply_schema(c)
    yield c
    c.execute("DELETE FROM uploads WHERE source_client = %s", (TEST_CLIENT,))
    c.execute("DELETE FROM org_notes WHERE source_client = %s", (TEST_CLIENT,))
    c.commit()
    c.close()


def test_feedback_context_uses_llm_decisions_and_active_notes(conn):
    uid = repository.create_upload(conn, "f", TEST_CLIENT, 1)
    base = {"column_name": "col_2", "dimension": "accuracy", "error_type": "type2", "severity": "low"}
    repository.save_issues(conn, uid, [
        {**base, "table_name": "T", "source": "llm", "description": "real one"},
        {**base, "table_name": "T", "source": "llm", "description": "false alarm"},
        {**base, "table_name": "T", "source": "rule", "error_type": "type1", "description": "rule issue"},
    ])
    ids = {i["description"]: i["id"] for i in repository.fetch_issues(conn, uid)}
    repository.update_issue_status(conn, ids["real one"], "accepted")
    repository.update_issue_status(conn, ids["false alarm"], "rejected", "seasonal discount")
    repository.update_issue_status(conn, ids["rule issue"], "accepted")
    keep = feedback.add_note(conn, TEST_CLIENT, "Zero tax is normal.")
    off = feedback.add_note(conn, TEST_CLIENT, "Old note.")
    feedback.set_note_active(conn, off, False)

    text, log = feedback.feedback_context(conn, TEST_CLIENT, "T", base_tokens=100)
    assert "real one" in text and "false alarm" in text and "seasonal discount" in text
    assert "rule issue" not in text and "Old note." not in text
    assert log["accepted_issue_ids"] == [ids["real one"]] and log["note_ids"] == [keep]

    feedback.save_prompt_log(conn, uid, [{**log, "schema_context": False}])
    assert feedback.fetch_prompt_log(conn, uid)[0]["rejected_issue_ids"] == [ids["false alarm"]]
