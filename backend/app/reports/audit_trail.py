"""Audit trail: who decided what, and when (read-only, from data already stored).

Each audit stores its own copy of a recurring issue (linked by previous_issue_id),
and each copy keeps the decision made on it: status, reviewer, time and note. So
an issue's history is the chain of its copies, oldest first. A decision changed
later on the same copy replaces the earlier one (only the latest is stored).
"""
from __future__ import annotations

import csv
import io

import psycopg
from psycopg.rows import dict_row

from app.reports.wording import DATE_FORMAT

DECISION_LABEL = {"accepted": "Accepted", "rejected": "Rejected", "considered": "Unsure", "flagged": "No decision yet"}
LOG_FIELDS = ["issue", "audit", "table", "column", "decision", "reviewer", "decided_at", "note"]


def issue_trail(conn: psycopg.Connection, issue_id: int) -> list[dict]:
    """The issue in every audit that found it, oldest first:
    [{issue_id, upload_id, as_of, affected_row_count, status, reviewed_by, reviewed_at, user_note}]."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "WITH RECURSIVE chain AS ("
            "  SELECT id, previous_issue_id, 0 AS depth FROM issues WHERE id = %s"
            "  UNION ALL"
            "  SELECT i.id, i.previous_issue_id, c.depth + 1 FROM issues i JOIN chain c ON i.id = c.previous_issue_id"
            ") SELECT i.id AS issue_id, i.upload_id, i.affected_row_count, i.status, i.reviewed_by, i.reviewed_at, "
            "  i.user_note, COALESCE(u.data_as_of, u.upload_date::date) AS as_of "
            "FROM chain c JOIN issues i ON i.id = c.id JOIN uploads u ON u.id = i.upload_id ORDER BY c.depth DESC",
            (issue_id,),
        ).fetchall()


def decision_log(conn: psycopg.Connection, upload_id: int) -> list[dict]:
    """Every decided issue of one audit, latest decision first (for the CSV log)."""
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "SELECT id, upload_id, table_name, column_name, status, reviewed_by, reviewed_at, user_note "
            "FROM issues WHERE upload_id = %s AND status <> 'flagged' ORDER BY reviewed_at DESC NULLS LAST, id",
            (upload_id,),
        ).fetchall()
    return [{"issue": r["id"], "audit": r["upload_id"], "table": r["table_name"], "column": r["column_name"] or "",
             "decision": DECISION_LABEL.get(r["status"], r["status"]), "reviewer": r["reviewed_by"] or "",
             "decided_at": f"{r['reviewed_at']:{DATE_FORMAT} %H:%M}" if r["reviewed_at"] else "",
             "note": r["user_note"] or ""} for r in rows]


def decision_log_csv(rows: list[dict], display_column=None) -> str:
    """CSV text; display_column(table, column) -> shown name, if given."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=LOG_FIELDS)
    w.writeheader()
    for r in rows:
        w.writerow({**r, "column": display_column(r["table"], r["column"]) if display_column and r["column"]
                    else r["column"]})
    return buf.getvalue()
