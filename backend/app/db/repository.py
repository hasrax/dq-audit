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

from app.config import Settings, load_settings

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


def _as_text(value) -> str | None:
    return None if value is None else str(value)


def _as_number(value):
    return value if isinstance(value, (int, float)) else None
