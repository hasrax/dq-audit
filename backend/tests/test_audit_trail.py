"""Audit trail: an issue's decisions across audits, and the decision log."""
import pytest

from app.db import repository
from app.reports import audit_trail

TEST_CLIENT = "__pytest_audit_trail__"


def _issue(**kw):
    return {"table_name": "T", "column_name": "c1", "dimension": "completeness", "source": "rule",
            "error_type": "type1", "severity": "high", "description": "d", "recommendation": "r",
            "fixable": "no", "status": "flagged", "row_reference": {"count": 5}, "rule_id": "x", **kw}


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


def test_trail_follows_the_issue_across_audits(conn):
    first = repository.create_upload(conn, "a", TEST_CLIENT, 10)
    repository.save_issues(conn, first, [_issue()])
    old = repository.fetch_issues(conn, first)[0]
    repository.update_issue_status(conn, old["id"], "accepted", "real", reviewed_by="mhg")
    second = repository.create_upload(conn, "b", TEST_CLIENT, 10)
    repository.save_issues(conn, second, [_issue()])
    new = repository.fetch_issues(conn, second)[0]
    conn.execute("UPDATE issues SET previous_issue_id = %s WHERE id = %s", (old["id"], new["id"]))
    trail = audit_trail.issue_trail(conn, new["id"])
    assert [t["upload_id"] for t in trail] == [first, second]
    assert (trail[0]["status"], trail[0]["reviewed_by"], trail[0]["user_note"]) == ("accepted", "mhg", "real")
    assert trail[1]["status"] == "flagged"
    log = audit_trail.decision_log(conn, first)
    assert len(log) == 1 and log[0]["decision"] == "Accepted" and log[0]["reviewer"] == "mhg"
    assert audit_trail.decision_log(conn, second) == []
    text = audit_trail.decision_log_csv(log, lambda t, c: "Name")
    assert text.splitlines()[0] == ",".join(audit_trail.LOG_FIELDS) and ",Name," in text
