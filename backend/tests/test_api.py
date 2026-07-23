import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.db import repository

client = TestClient(app)


def test_health_needs_no_database():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.fixture
def db_or_skip():
    try:
        c = repository.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available: {exc}")
    c.close()


def _first_upload_id():
    conn = repository.connect()
    try:
        uploads = repository.list_uploads(conn)
        return uploads[0]["id"] if uploads else None
    finally:
        conn.close()


def test_list_uploads(db_or_skip):
    r = client.get("/uploads")
    assert r.status_code == 200
    assert "uploads" in r.json()


def test_get_issues_and_score(db_or_skip):
    uid = _first_upload_id()
    if uid is None:
        pytest.skip("no uploads persisted")
    issues = client.get(f"/uploads/{uid}/issues")
    assert issues.status_code == 200
    assert issues.json()["upload_id"] == uid

    score = client.get(f"/uploads/{uid}/score")
    assert score.status_code == 200
    assert "overall_score" in score.json()


def test_issue_filter_by_source(db_or_skip):
    uid = _first_upload_id()
    if uid is None:
        pytest.skip("no uploads persisted")
    r = client.get(f"/uploads/{uid}/issues", params={"source": "rule"})
    assert r.status_code == 200
    assert all(i["source"] == "rule" for i in r.json()["issues"])


def test_triage_updates_then_revert(db_or_skip):
    uid = _first_upload_id()
    if uid is None:
        pytest.skip("no uploads persisted")
    conn = repository.connect()
    flagged = repository.fetch_issues(conn, uid, status="flagged")
    conn.close()
    if not flagged:
        pytest.skip("no flagged issue to triage")
    issue_id = flagged[0]["id"]

    r = client.post(
        f"/uploads/{uid}/issues/{issue_id}/triage",
        json={"status": "accepted", "note": "api test"},
    )
    assert r.status_code == 200 and r.json()["ok"] is True

    conn = repository.connect()
    updated = next(i for i in repository.fetch_issues(conn, uid) if i["id"] == issue_id)
    assert updated["status"] == "accepted"
    repository.update_issue_status(conn, issue_id, "flagged")  # revert
    conn.close()


def test_invalid_triage_status_returns_400(db_or_skip):
    uid = _first_upload_id()
    if uid is None:
        pytest.skip("no uploads persisted")
    conn = repository.connect()
    issues = repository.fetch_issues(conn, uid)
    conn.close()
    if not issues:
        pytest.skip("no issues")
    r = client.post(
        f"/uploads/{uid}/issues/{issues[0]['id']}/triage",
        json={"status": "maybe"},
    )
    assert r.status_code == 400
