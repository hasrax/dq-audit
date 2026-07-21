import json

import pytest

from app.db import repository

TEST_CLIENT = "__pytest__"


@pytest.fixture
def conn():
    try:
        c = repository.connect()
    except Exception as exc:  # noqa: BLE001 -- any connection failure -> skip
        pytest.skip(f"PostgreSQL not available: {exc}")
    repository.apply_schema(c)
    yield c
    # tidy up anything this test run created
    c.execute("DELETE FROM uploads WHERE source_client = %s", (TEST_CLIENT,))
    c.commit()
    c.close()


def test_apply_schema_is_idempotent(conn):
    repository.apply_schema(conn)  # second call must not error
    repository.apply_schema(conn)


def test_create_upload_returns_id(conn):
    uid = repository.create_upload(conn, "hemas", TEST_CLIENT, 1000)
    assert isinstance(uid, int)


def test_save_profiles_and_read_back(conn):
    uid = repository.create_upload(conn, "hemas", TEST_CLIENT, 10)
    profiles = {
        "T": {
            "table_name": "T",
            "row_count": 10,
            "columns": {
                "col_1": {"type": "int", "null_count": 0, "null_pct": 0.0, "distinct_count": 10, "min": 1, "max": 10, "mean": 5.5, "top_values": {}},
            },
        }
    }
    n = repository.save_profiles(conn, uid, profiles)
    assert n == 1
    count = conn.execute("SELECT count(*) FROM profiles WHERE upload_id = %s", (uid,)).fetchone()[0]
    assert count == 1


def test_save_issues_stores_both_sources_with_jsonb_row_reference(conn):
    uid = repository.create_upload(conn, "hemas", TEST_CLIENT, 10)
    issues = [
        {"table_name": "T", "column_name": "col_9", "dimension": "completeness", "source": "rule",
         "error_type": "type1", "severity": "high", "description": "d", "recommendation": "r",
         "fixable": "no", "status": "flagged", "row_reference": {"count": 10, "sample_rows": [0, 1]}, "rule_id": "completeness_null_pct"},
        {"table_name": "T", "column_name": "col_2", "dimension": "validity", "source": "llm",
         "error_type": "type2", "severity": "medium", "description": "d", "recommendation": "r",
         "fixable": "yes", "status": "flagged", "row_reference": {"count": 1, "sample_rows": [5]}, "rule_id": None},
    ]
    n = repository.save_issues(conn, uid, issues)
    assert n == 2

    by_source = dict(
        conn.execute(
            "SELECT source, count(*) FROM issues WHERE upload_id = %s GROUP BY source", (uid,)
        ).fetchall()
    )
    assert by_source == {"rule": 1, "llm": 1}

    ref = conn.execute(
        "SELECT row_reference FROM issues WHERE upload_id = %s AND source = 'rule'", (uid,)
    ).fetchone()[0]
    assert ref["count"] == 10  # JSONB round-trips to a dict


def test_invalid_dimension_is_rejected_by_check_constraint(conn):
    uid = repository.create_upload(conn, "hemas", TEST_CLIENT, 10)
    bad = [{"table_name": "T", "column_name": None, "dimension": "nonsense", "source": "rule",
            "error_type": "type1", "severity": "high", "row_reference": {}}]
    with pytest.raises(Exception):
        repository.save_issues(conn, uid, bad)
    conn.rollback()
