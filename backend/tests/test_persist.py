"""End-to-end persistence test: tiny CSVs -> run_rules -> DB (real DB, no API)."""
from pathlib import Path

import pytest

from app.db import repository
from app.db.persist import persist_audit

TEST_CLIENT = "__pytest_persist__"


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


def _make_client_dir(tmp_path: Path) -> Path:
    data = tmp_path / TEST_CLIENT
    data.mkdir()
    # a column that is 100% null -> guarantees at least one completeness issue
    (data / "T.csv").write_text(
        "\n".join(f"{i},{i * 10},NULL" for i in range(1, 41)) + "\n", encoding="utf-8"
    )
    return data


def test_persist_audit_writes_upload_profiles_and_issues(conn, tmp_path):
    data = _make_client_dir(tmp_path)
    result = persist_audit(data, source_client=TEST_CLIENT)

    assert result["upload_id"] > 0
    assert result["profiles"] == 3          # three columns profiled
    assert result["issues"] >= 1            # the all-null column fires completeness

    n = conn.execute(
        "SELECT count(*) FROM issues WHERE upload_id = %s", (result["upload_id"],)
    ).fetchone()[0]
    assert n == result["issues"]


def test_persisted_llm_issues_are_stored_alongside_rule_issues(conn, tmp_path):
    data = _make_client_dir(tmp_path)
    llm_issues = [{
        "table_name": "T", "column_name": "col_2", "dimension": "accuracy", "source": "llm",
        "error_type": "type2", "severity": "medium", "description": "d", "recommendation": "r",
        "fixable": "yes", "status": "flagged", "row_reference": {"count": 1, "sample_rows": [3]}, "rule_id": None,
    }]
    result = persist_audit(data, source_client=TEST_CLIENT, llm_issues=llm_issues)

    by_source = dict(
        conn.execute(
            "SELECT source, count(*) FROM issues WHERE upload_id = %s GROUP BY source",
            (result["upload_id"],),
        ).fetchall()
    )
    assert by_source.get("llm") == 1
    assert by_source.get("rule", 0) >= 1
