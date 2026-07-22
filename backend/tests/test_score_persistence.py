"""DQ score persistence + trend (real DB; skips when unavailable)."""
import pytest

from app.db import repository

TEST_CLIENT = "__pytest_score__"


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


def _seed(conn, issues):
    uid = repository.create_upload(conn, "run", TEST_CLIENT, 100)
    if issues:
        repository.save_issues(conn, uid, issues)
    return uid


def _issue(dimension, severity="high", status="flagged"):
    return {"table_name": "T", "column_name": "c", "dimension": dimension, "source": "rule",
            "error_type": "type1", "severity": severity, "description": "d", "recommendation": "r",
            "fixable": "no", "status": status, "row_reference": {"count": 1}, "rule_id": "x"}


def test_score_upload_persists_a_single_row(conn):
    uid = _seed(conn, [_issue("completeness", "high")])
    score = repository.score_upload(conn, uid)
    assert score["overall_score"] < 100.0
    n = conn.execute("SELECT count(*) FROM dq_scores WHERE upload_id = %s", (uid,)).fetchone()[0]
    assert n == 1


def test_rescoring_replaces_previous_snapshot(conn):
    uid = _seed(conn, [_issue("completeness", "high")])
    repository.score_upload(conn, uid)
    repository.score_upload(conn, uid)  # second time must not add a row
    n = conn.execute("SELECT count(*) FROM dq_scores WHERE upload_id = %s", (uid,)).fetchone()[0]
    assert n == 1


def test_rejecting_an_issue_raises_the_score(conn):
    uid = _seed(conn, [_issue("cross_table", "high")])
    before = repository.score_upload(conn, uid)["overall_score"]

    issue_id = repository.fetch_issues(conn, uid)[0]["id"]
    repository.update_issue_status(conn, issue_id, "rejected")
    after = repository.score_upload(conn, uid)["overall_score"]

    assert after > before  # excluded rejected issue -> quality looks better


def test_fetch_score_trend_returns_scored_uploads_in_order(conn):
    uid1 = _seed(conn, [_issue("validity", "high")])
    repository.score_upload(conn, uid1)
    uid2 = _seed(conn, [])
    repository.score_upload(conn, uid2)

    trend = repository.fetch_score_trend(conn, TEST_CLIENT)
    ids = [r["upload_id"] for r in trend]
    assert uid1 in ids and uid2 in ids
    assert ids == sorted(ids)  # ordered by date/id
