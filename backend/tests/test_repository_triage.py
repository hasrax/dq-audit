"""Read/update repository functions used by the Layer 3 triage UI."""
import pytest

from app.db import repository

TEST_CLIENT = "__pytest_triage__"


@pytest.fixture
def seeded(conn_or_skip):
    conn = conn_or_skip
    uid = repository.create_upload(conn, "hemas", TEST_CLIENT, 100)
    issues = [
        {"table_name": "T", "column_name": "c1", "dimension": "completeness", "source": "rule",
         "error_type": "type1", "severity": "high", "description": "d", "recommendation": "r",
         "fixable": "no", "status": "flagged", "row_reference": {"count": 5}, "rule_id": "x"},
        {"table_name": "T", "column_name": "c2", "dimension": "validity", "source": "rule",
         "error_type": "type1", "severity": "low", "description": "d", "recommendation": "r",
         "fixable": "no", "status": "flagged", "row_reference": {"count": 1}, "rule_id": "y"},
        {"table_name": "T", "column_name": "c3", "dimension": "accuracy", "source": "llm",
         "error_type": "type2", "severity": "medium", "description": "d", "recommendation": "r",
         "fixable": "yes", "status": "flagged", "row_reference": {"count": 1}, "rule_id": None},
    ]
    repository.save_issues(conn, uid, issues)
    return conn, uid


@pytest.fixture
def conn_or_skip():
    try:
        c = repository.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available: {exc}")
    repository.apply_schema(c)
    yield c
    c.execute("DELETE FROM uploads WHERE source_client = %s", (TEST_CLIENT,))
    c.commit()
    c.close()


def test_list_uploads_reports_pending_count(seeded):
    conn, uid = seeded
    row = next(u for u in repository.list_uploads(conn) if u["id"] == uid)
    assert row["issue_count"] == 3
    assert row["pending_count"] == 3


def test_fetch_issues_orders_high_severity_first(seeded):
    conn, uid = seeded
    issues = repository.fetch_issues(conn, uid)
    assert [i["severity"] for i in issues] == ["high", "medium", "low"]


def test_fetch_issues_filters_by_source(seeded):
    conn, uid = seeded
    llm = repository.fetch_issues(conn, uid, source="llm")
    assert len(llm) == 1 and llm[0]["source"] == "llm"


def test_update_issue_status_records_decision_and_timestamp(seeded):
    conn, uid = seeded
    target = repository.fetch_issues(conn, uid)[0]
    repository.update_issue_status(conn, target["id"], "accepted", user_note="confirmed real")

    updated = next(i for i in repository.fetch_issues(conn, uid) if i["id"] == target["id"])
    assert updated["status"] == "accepted"
    assert updated["user_note"] == "confirmed real"
    assert updated["reviewed_at"] is not None
    assert repository.triage_counts(conn, uid)["accepted"] == 1


def test_reverting_to_flagged_clears_reviewed_at(seeded):
    conn, uid = seeded
    target = repository.fetch_issues(conn, uid)[0]
    repository.update_issue_status(conn, target["id"], "rejected")
    repository.update_issue_status(conn, target["id"], "flagged")
    updated = next(i for i in repository.fetch_issues(conn, uid) if i["id"] == target["id"])
    assert updated["reviewed_at"] is None


def test_invalid_status_is_rejected(seeded):
    conn, uid = seeded
    target = repository.fetch_issues(conn, uid)[0]
    with pytest.raises(ValueError):
        repository.update_issue_status(conn, target["id"], "maybe")


def test_bulk_set_statuses(seeded):
    conn, uid = seeded
    ids = [i["id"] for i in repository.fetch_issues(conn, uid)]
    n = repository.set_issue_statuses(conn, ids, "accepted")
    assert n == len(ids)
    assert repository.triage_counts(conn, uid).get("accepted") == len(ids)

    # bulk reset back to flagged clears reviewed_at
    repository.set_issue_statuses(conn, ids, "flagged")
    assert all(i["reviewed_at"] is None for i in repository.fetch_issues(conn, uid))


def test_bulk_empty_list_is_noop(seeded):
    conn, uid = seeded
    assert repository.set_issue_statuses(conn, [], "accepted") == 0


def test_bulk_invalid_status_rejected(seeded):
    conn, uid = seeded
    with pytest.raises(ValueError):
        repository.set_issue_statuses(conn, [1], "nope")
