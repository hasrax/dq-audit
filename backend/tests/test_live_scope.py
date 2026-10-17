"""Evaluation page uses the thesis definitions; triage decisions re-store the DQ score."""
import pytest

from app.db import repository
from app.eval import live

TEST_CLIENT = "__pytest_live_scope__"


def _issue(col, source, severity="high", dimension="completeness"):
    return {"table_name": "T", "column_name": col, "dimension": dimension, "source": source,
            "error_type": "type1" if source == "rule" else "type2", "severity": severity, "description": "d",
            "recommendation": "r", "fixable": "no", "status": "flagged", "row_reference": {"count": 5},
            "rule_id": "x" if source == "rule" else None}


@pytest.fixture
def conn():
    try:
        c = repository.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available: {exc}")
    repository.apply_schema(c)
    yield c
    c.execute("DELETE FROM uploads WHERE source_client = %s", (TEST_CLIENT,))
    c.commit()
    c.close()


def test_company_scope_is_the_latest_audit(conn):
    old = repository.create_upload(conn, "old", TEST_CLIENT, 100)
    repository.save_issues(conn, old, [_issue("c1", "rule"), _issue("c9", "llm")])
    new = repository.create_upload(conn, "new", TEST_CLIENT, 100)
    repository.save_issues(conn, new, [_issue("c1", "rule")])
    assert live.latest_upload_ids(conn, TEST_CLIENT) == [new]
    assert {i["upload_id"] for i in live.scope_issues(conn, source_client=TEST_CLIENT)} == {new}
    assert {i["upload_id"] for i in live.scope_issues(conn, upload_id=old)} == {old}


def test_complementarity_counts_each_audit_separately(conn):
    a = repository.create_upload(conn, "a", TEST_CLIENT, 100)
    repository.save_issues(conn, a, [_issue("c1", "rule"), _issue("c1", "llm"), _issue("c2", "llm")])
    b = repository.create_upload(conn, "b", TEST_CLIENT, 100)
    repository.save_issues(conn, b, [_issue("c1", "rule")])   # same location name, other audit: not merged
    comp = live.complementarity(conn, [a, b])
    assert comp["n_locations"] == 3
    assert (comp["overall"]["both"], comp["overall"]["llm_only"], comp["overall"]["rule_only"]) == (1, 1, 1)


def test_rejected_issue_leaves_complementarity(conn):
    uid = repository.create_upload(conn, "a", TEST_CLIENT, 100)
    repository.save_issues(conn, uid, [_issue("c1", "rule"), _issue("c2", "llm")])
    ai = next(i for i in repository.fetch_issues(conn, uid) if i["source"] == "llm")
    repository.update_issue_status(conn, ai["id"], "rejected", reviewed_by="pytest")
    assert live.complementarity(conn, [uid])["n_locations"] == 1


def test_decision_restores_the_score(conn):
    uid = repository.create_upload(conn, "a", TEST_CLIENT, 100)
    repository.save_issues(conn, uid, [_issue("c1", "rule"), _issue("c2", "rule", "low", "validity")])
    before = repository.score_upload(conn, uid)["overall_score"]
    high = next(i for i in repository.fetch_issues(conn, uid) if i["severity"] == "high")
    repository.set_issue_statuses(conn, [high["id"]], "rejected", reviewed_by="pytest")
    stored = conn.execute("SELECT overall_score FROM dq_scores WHERE upload_id = %s", (uid,)).fetchall()
    assert len(stored) == 1 and float(stored[0][0]) > before   # rejected issues no longer count
    repository.update_issue_status(conn, high["id"], "flagged")
    assert float(conn.execute("SELECT overall_score FROM dq_scores WHERE upload_id = %s",
                              (uid,)).fetchone()[0]) == pytest.approx(before)
