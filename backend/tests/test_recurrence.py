import pytest

from app.db import repository
from app.monitoring import recurrence

TEST_CLIENT = "__pytest_recur__"


def test_trend_status_uses_five_percent_band():
    assert recurrence.trend_status(100, 106) == "worsening"
    assert recurrence.trend_status(100, 94) == "improving"
    assert recurrence.trend_status(100, 104) == "stable"
    assert recurrence.trend_status(0, 3) == "worsening"


def test_projection_needs_three_points_and_extends_the_line():
    assert recurrence.project_next([10, 20]) is None
    slope, nxt = recurrence.project_next([10, 20, 30])
    assert slope == pytest.approx(10) and nxt == pytest.approx(40)
    assert recurrence.project_next([30, 20, 10, 0])[1] == 0.0      # never below zero


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


def _issue(count, column="col_1", rule="completeness_null_pct"):
    return {"table_name": "T", "column_name": column, "dimension": "completeness", "source": "rule",
            "error_type": "type1", "severity": "low", "description": "d", "rule_id": rule,
            "row_reference": {"count": count}}


def _audit(conn, issues):
    uid = repository.create_upload(conn, "f", TEST_CLIENT, 10)
    repository.save_issues(conn, uid, issues)
    return uid, recurrence.link_recurring(conn, uid)


def test_link_recurring_tracks_history_projection_and_not_seen(conn):
    _audit(conn, [_issue(10), _issue(5, column="col_9")])
    _audit(conn, [_issue(20)])
    uid3, counts = _audit(conn, [_issue(30), _issue(1, column="col_2")])

    assert counts["recurring"] == 1 and counts["new"] == 1
    current = {i["column_name"]: i for i in repository.fetch_issues(conn, uid3)}
    assert current["col_1"]["trend_status"] == "worsening"
    assert current["col_1"]["occurrence_count"] == 3
    assert [h["affected_row_count"] for h in recurrence.issue_history(conn, current["col_1"]["id"])] == [10, 20, 30]

    proj = recurrence.projections_for_upload(conn, uid3)[current["col_1"]["id"]]
    assert float(proj["projected_value"]) == pytest.approx(40)

    # next audit settles the projection with the actual value
    uid4, _ = _audit(conn, [_issue(37)])
    settled = conn.execute("SELECT actual_value FROM issue_projections WHERE issue_id = %s",
                           (current["col_1"]["id"],)).fetchone()
    assert settled[0] == 37
    assert [i["column_name"] for i in recurrence.not_seen_issues(conn, uid4)] == ["col_2"]
