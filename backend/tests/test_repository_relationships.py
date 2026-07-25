"""Relationship review repository functions (real DB; skips when unavailable)."""
import pytest

from app.db import repository

TEST_CLIENT = "__pytest_rel__"


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


def _seed(conn):
    uid = repository.create_upload(conn, "run", TEST_CLIENT, 10)
    repository.save_relationships(conn, uid, [
        {"child_table": "GRNDtl", "child_column": "col_1", "parent_table": "GRNHED",
         "parent_column": "col_1", "overlap_pct": 100.0},
        {"child_table": "GRNDtl", "child_column": "col_7", "parent_table": "GRNHED",
         "parent_column": "col_1", "overlap_pct": 82.0},
    ])
    return uid


def test_fetch_relationships_orders_by_overlap_desc(conn):
    uid = _seed(conn)
    rels = repository.fetch_relationships(conn, uid)
    assert [r["overlap_pct"] for r in rels] == [100.0, 82.0]
    assert all(r["confirmed"] is False for r in rels)  # default unconfirmed


def test_confirm_and_unconfirm_a_relationship(conn):
    uid = _seed(conn)
    rel = repository.fetch_relationships(conn, uid)[0]

    repository.set_relationship_confirmed(conn, rel["id"], True)
    assert repository.fetch_relationships(conn, uid)[0]["confirmed"] is True

    repository.set_relationship_confirmed(conn, rel["id"], False)
    assert repository.fetch_relationships(conn, uid)[0]["confirmed"] is False
