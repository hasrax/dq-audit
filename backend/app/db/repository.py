"""PostgreSQL persistence for the issue repository.

One connection helper plus insert functions for each artifact an audit
run produces. Issues from both sources (rule / llm) share one table and
one insert path, so Layer 3 triage and the complementarity analysis
query them uniformly.
"""
from __future__ import annotations

import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.config import Settings, load_settings
from app.scoring.score import compute_dq_score

VALID_STATUSES = ("flagged", "accepted", "rejected", "considered")

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect(settings: Settings | None = None) -> psycopg.Connection:
    settings = settings or load_settings()
    return psycopg.connect(settings.db_url)


def apply_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def create_upload(conn: psycopg.Connection, filename: str, source_client: str, row_count: int) -> int:
    row = conn.execute(
        "INSERT INTO uploads (filename, source_client, row_count) VALUES (%s, %s, %s) RETURNING id",
        (filename, source_client, row_count),
    ).fetchone()
    conn.commit()
    return row[0]


def save_profiles(conn: psycopg.Connection, upload_id: int, profiles: dict[str, dict]) -> int:
    rows = []
    for table_name, profile in profiles.items():
        for col, stats in profile["columns"].items():
            rows.append((
                upload_id, table_name, col, stats["type"],
                stats["null_count"], stats["null_pct"], stats["distinct_count"],
                _as_text(stats["min"]), _as_text(stats["max"]), _as_number(stats["mean"]),
            ))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO profiles (upload_id, table_name, column_name, data_type, "
            "null_count, null_pct, distinct_count, min_val, max_val, mean_val) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
    conn.commit()
    return len(rows)


def save_relationships(conn: psycopg.Connection, upload_id: int, relationships: list[dict]) -> int:
    rows = [
        (upload_id, r["child_table"], r["child_column"], r["parent_table"],
         r["parent_column"], r["overlap_pct"])
        for r in relationships
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO relationships (upload_id, child_table, child_column, "
            "parent_table, parent_column, overlap_pct) VALUES (%s, %s, %s, %s, %s, %s)",
            rows,
        )
    conn.commit()
    return len(rows)


def save_issues(conn: psycopg.Connection, upload_id: int, issues: list[dict]) -> int:
    rows = [
        (
            upload_id, i["table_name"], i.get("column_name"), i["dimension"],
            i["source"], i["error_type"], i["severity"], i.get("description"),
            i.get("recommendation"), i.get("fixable"), i.get("status", "flagged"),
            json.dumps(i.get("row_reference")), i.get("rule_id"),
        )
        for i in issues
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO issues (upload_id, table_name, column_name, dimension, "
            "source, error_type, severity, description, recommendation, fixable, "
            "status, row_reference, rule_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
    conn.commit()
    return len(rows)


def list_uploads(conn: psycopg.Connection) -> list[dict]:
    """All audit runs, newest first, with a live triage-progress count."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT u.id, u.filename, u.source_client, u.upload_date, u.row_count, "
            "u.status, "
            "count(i.id) AS issue_count, "
            "count(i.id) FILTER (WHERE i.status = 'flagged') AS pending_count "
            "FROM uploads u LEFT JOIN issues i ON i.upload_id = u.id "
            "GROUP BY u.id ORDER BY u.id DESC"
        ).fetchall()


def fetch_issues(
    conn: psycopg.Connection,
    upload_id: int,
    source: str | None = None,
    dimension: str | None = None,
    severity: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """Issues for an upload, optionally filtered; high severity first."""
    clauses = ["upload_id = %s"]
    params: list = [upload_id]
    for column, value in (("source", source), ("dimension", dimension),
                          ("severity", severity), ("status", status)):
        if value is not None:
            clauses.append(f"{column} = %s")
            params.append(value)

    sql = (
        "SELECT * FROM issues WHERE " + " AND ".join(clauses) +
        " ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id"
    )
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, params).fetchall()


def update_issue_status(
    conn: psycopg.Connection,
    issue_id: int,
    status: str,
    user_note: str | None = None,
) -> None:
    """Record a triage decision, stamping reviewed_at (throughput evidence)."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    conn.execute(
        "UPDATE issues SET status = %s, user_note = %s, "
        "reviewed_at = CASE WHEN %s = 'flagged' THEN NULL ELSE now() END "
        "WHERE id = %s",
        (status, user_note, status, issue_id),
    )
    conn.commit()


def triage_counts(conn: psycopg.Connection, upload_id: int) -> dict[str, int]:
    """Issue counts by triage status for one upload."""
    rows = conn.execute(
        "SELECT status, count(*) FROM issues WHERE upload_id = %s GROUP BY status",
        (upload_id,),
    ).fetchall()
    return {status: count for status, count in rows}


def save_dq_score(conn: psycopg.Connection, upload_id: int, score: dict) -> None:
    """Store an upload's DQ score, replacing any previous snapshot.

    Latest-wins: the score changes as triage rejects false positives, so
    dq_scores always holds the current score per upload rather than a
    history of stale snapshots.
    """
    conn.execute("DELETE FROM dq_scores WHERE upload_id = %s", (upload_id,))
    conn.execute(
        "INSERT INTO dq_scores (upload_id, overall_score, completeness_score, "
        "validity_score, consistency_score, uniqueness_score, timeliness_score, "
        "cross_table_score) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            upload_id, score["overall_score"], score["completeness_score"],
            score["validity_score"], score["consistency_score"],
            score["uniqueness_score"], score["timeliness_score"],
            score["cross_table_score"],
        ),
    )
    conn.commit()


def score_upload(conn: psycopg.Connection, upload_id: int) -> dict:
    """Recompute an upload's DQ score from its current issues and persist it."""
    score = compute_dq_score(fetch_issues(conn, upload_id))
    save_dq_score(conn, upload_id, score)
    return score


def fetch_score_trend(conn: psycopg.Connection, source_client: str) -> list[dict]:
    """Stored DQ scores for a client's uploads over time (for the trend view)."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT u.id AS upload_id, u.upload_date, u.source_client, "
            "s.overall_score, s.completeness_score, s.validity_score, "
            "s.consistency_score, s.uniqueness_score, s.timeliness_score, "
            "s.cross_table_score "
            "FROM uploads u JOIN dq_scores s ON s.upload_id = u.id "
            "WHERE u.source_client = %s ORDER BY u.upload_date, u.id",
            (source_client,),
        ).fetchall()


def _as_text(value) -> str | None:
    return None if value is None else str(value)


def _as_number(value):
    return value if isinstance(value, (int, float)) else None
