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

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def connect(settings: Settings | None = None) -> psycopg.Connection:
    settings = settings or load_settings()
    return psycopg.connect(settings.db_url)


def apply_schema(conn: psycopg.Connection) -> list[str]:
    """Apply any numbered migrations (NNN_name.sql) not yet recorded.

    Each file runs once, in filename order, and is recorded in
    schema_migrations. 001 is idempotent, so databases created before
    migrations existed adopt it safely. Returns the versions applied now.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version TEXT PRIMARY KEY, applied_at TIMESTAMP NOT NULL DEFAULT now())"
    )
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    applied = []
    for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        if path.stem in done:
            continue
        conn.execute(path.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
        applied.append(path.stem)
    conn.commit()
    return applied


def create_upload(
    conn: psycopg.Connection, filename: str, source_client: str, row_count: int, data_as_of=None,
) -> int:
    row = conn.execute(
        "INSERT INTO uploads (filename, source_client, row_count, data_as_of) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (filename, source_client, row_count, data_as_of),
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


def save_rules(
    conn: psycopg.Connection, upload_id: int, source_client: str,
    rules: list[dict], aliases: dict | None = None,
) -> dict[str, int]:
    """Persist an upload's rule instances with their plain-language
    what/why text; returns {rule_key: rules.id} for linking issues."""
    from app.rules.describe import describe_rule

    ids: dict[str, int] = {}
    with conn.cursor() as cur:
        for r in rules:
            what, why = describe_rule(r, aliases)
            row = cur.execute(
                "INSERT INTO rules (upload_id, source_client, rule_key, template, category, "
                "table_name, column_name, parameters, what_text, why_text, evidence, violation_count, enabled) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (upload_id, source_client, r["rule_key"], r["template"], r["category"],
                 r["table_name"], r["column_name"], json.dumps(r["parameters"], default=str),
                 what, why, json.dumps(r["evidence"], default=str), r["violation_count"],
                 r.get("enabled", True)),
            ).fetchone()
            ids[r["rule_key"]] = row[0]
    conn.commit()
    return ids


def save_issues(
    conn: psycopg.Connection, upload_id: int, issues: list[dict],
    rule_ids: dict[str, int] | None = None,
) -> int:
    rule_ids = rule_ids or {}
    rows = [
        (
            upload_id, i["table_name"], i.get("column_name"), i["dimension"],
            i["source"], i["error_type"], i["severity"], i.get("description"),
            i.get("recommendation"), i.get("fixable"), i.get("status", "flagged"),
            json.dumps(i.get("row_reference")), i.get("rule_id"),
            rule_ids.get(i.get("rule_key")),
        )
        for i in issues
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO issues (upload_id, table_name, column_name, dimension, "
            "source, error_type, severity, description, recommendation, fixable, "
            "status, row_reference, rule_id, rule_ref) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
    conn.commit()
    return len(rows)


def fetch_rules(
    conn: psycopg.Connection, upload_id: int,
    table_name: str | None = None, category: str | None = None,
    violated_only: bool = False,
) -> list[dict]:
    """An upload's rules, most violations first."""
    clauses, params = ["upload_id = %s"], [upload_id]
    for column, value in (("table_name", table_name), ("category", category)):
        if value is not None:
            clauses.append(f"{column} = %s")
            params.append(value)
    if violated_only:
        clauses.append("violation_count > 0")
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT * FROM rules WHERE " + " AND ".join(clauses) +
            " ORDER BY violation_count DESC, table_name, category, id",
            params,
        ).fetchall()


def active_suppressions(conn: psycopg.Connection, source_client: str) -> dict[str, dict]:
    """Rules an admin marked "not applicable to us" for a client: {rule_key: row}."""
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "SELECT * FROM rule_suppressions WHERE source_client = %s AND active", (source_client,)
        ).fetchall()
    return {r["rule_key"]: r for r in rows}


def set_rule_suppressed(conn: psycopg.Connection, source_client: str, rule_key: str, suppressed: bool,
                        by: str | None = None, reason: str | None = None) -> None:
    conn.execute(
        "INSERT INTO rule_suppressions (source_client, rule_key, reason, disabled_by, active) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (source_client, rule_key) DO UPDATE SET "
        "active = EXCLUDED.active, reason = COALESCE(EXCLUDED.reason, rule_suppressions.reason), "
        "disabled_by = EXCLUDED.disabled_by, disabled_at = now()",
        (source_client, rule_key, reason, by, suppressed),
    )
    conn.commit()


def fetch_issue(conn: psycopg.Connection, issue_id: int) -> dict | None:
    """One issue with its upload's client and data date (for the detail view)."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT i.*, u.source_client, COALESCE(u.data_as_of, u.upload_date::date) AS as_of "
            "FROM issues i JOIN uploads u ON u.id = i.upload_id WHERE i.id = %s",
            (issue_id,),
        ).fetchone()


def fetch_rule(conn: psycopg.Connection, rule_id: int) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute("SELECT * FROM rules WHERE id = %s", (rule_id,)).fetchone()


def list_uploads(conn: psycopg.Connection, source_client: str | None = None) -> list[dict]:
    """Audit runs (optionally one client's), newest first, with a live
    triage-progress count."""
    where = "WHERE u.source_client = %s " if source_client else ""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT u.id, u.filename, u.source_client, u.upload_date, u.data_as_of, u.row_count, "
            "u.status, "
            "count(i.id) AS issue_count, "
            "count(i.id) FILTER (WHERE i.status = 'flagged') AS pending_count "
            "FROM uploads u LEFT JOIN issues i ON i.upload_id = u.id " + where +
            "GROUP BY u.id ORDER BY u.id DESC",
            (source_client,) if source_client else (),
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
    reviewed_by: str | None = None,
) -> None:
    """Record a triage decision, stamping reviewed_at (throughput evidence)
    and who made it."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    conn.execute(
        "UPDATE issues SET status = %s, user_note = %s, "
        "reviewed_at = CASE WHEN %s = 'flagged' THEN NULL ELSE now() END, "
        "reviewed_by = CASE WHEN %s = 'flagged' THEN NULL ELSE %s END "
        "WHERE id = %s",
        (status, user_note, status, status, reviewed_by, issue_id),
    )
    conn.commit()
    _rescore_for([issue_id], conn)


def _rescore_for(issue_ids: list[int], conn: psycopg.Connection) -> None:
    """Rejected issues do not count towards the DQ score, so a decision
    changes it: re-store the score now, so every page shows the same value."""
    uploads = conn.execute("SELECT DISTINCT upload_id FROM issues WHERE id = ANY(%s)", (issue_ids,)).fetchall()
    for (upload_id,) in uploads:
        score_upload(conn, upload_id)


def set_issue_statuses(conn: psycopg.Connection, issue_ids: list[int], status: str,
                       reviewed_by: str | None = None) -> int:
    """Bulk triage: set many issues to one status in a single statement."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    ids = [int(i) for i in issue_ids]
    if not ids:
        return 0
    conn.execute(
        "UPDATE issues SET status = %s, "
        "reviewed_at = CASE WHEN %s = 'flagged' THEN NULL ELSE now() END, "
        "reviewed_by = CASE WHEN %s = 'flagged' THEN NULL ELSE %s END "
        "WHERE id = ANY(%s)",
        (status, status, status, reviewed_by, ids),
    )
    conn.commit()
    _rescore_for(ids, conn)
    return len(ids)


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


def fetch_relationships(conn: psycopg.Connection, upload_id: int) -> list[dict]:
    """Discovered cross-table relationships for an upload, strongest first."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT id, child_table, child_column, parent_table, parent_column, "
            "overlap_pct, confirmed FROM relationships WHERE upload_id = %s "
            "ORDER BY overlap_pct DESC, id",
            (upload_id,),
        ).fetchall()


def set_relationship_confirmed(conn: psycopg.Connection, relationship_id: int, confirmed: bool) -> None:
    """Record a human's confirm/unconfirm decision on a discovered FK."""
    conn.execute(
        "UPDATE relationships SET confirmed = %s WHERE id = %s",
        (confirmed, relationship_id),
    )
    conn.commit()


def fetch_score_trend(conn: psycopg.Connection, source_client: str) -> list[dict]:
    """Stored DQ scores for a client's uploads over time (for the trend view)."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT u.id AS upload_id, u.upload_date, u.source_client, "
            "COALESCE(u.data_as_of, u.upload_date::date) AS as_of, "
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
