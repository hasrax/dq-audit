"""Recurring-issue detection and trend projection (Layer 4 monitoring).

After an upload's issues are saved, each one is matched against the most
recent earlier upload for the same client. A match is the same table +
column + dimension + source (+ rule template for rule issues). Matched
issues inherit an occurrence count and a trend status from the change in
affected rows:
    > +5%  worsening      < -5%  improving      otherwise  stable
Unmatched issues are new. Previous issues with no successor are "not
seen in this audit" -- listed, never deleted.

With >= 3 historical points an issue also gets a straight-line
projection (numpy.polyfit, degree 1) of its affected rows at the next
audit. x is the audit sequence (0, 1, 2, ...) rather than the calendar,
because audits arrive at irregular intervals. Each projection is stored
and its actual value filled in when the next audit lands, so prediction
error is measurable.

Usage (recompute history for every existing upload, oldest first):
    python -m app.monitoring.recurrence --backfill
"""
from __future__ import annotations

import sys
from collections import defaultdict

import numpy as np
import psycopg
from psycopg.rows import dict_row

TREND_THRESHOLD = 0.05
MIN_PROJECTION_POINTS = 3


def match_key(issue: dict) -> tuple:
    rule_template = issue.get("rule_id") if issue["source"] == "rule" else None
    return (issue["table_name"], issue.get("column_name"), issue["dimension"], issue["source"], rule_template)


def trend_status(previous: int, current: int, threshold: float = TREND_THRESHOLD) -> str:
    if previous == 0:
        return "worsening" if current > 0 else "stable"
    change = (current - previous) / previous
    if change > threshold:
        return "worsening"
    if change < -threshold:
        return "improving"
    return "stable"


def project_next(counts: list[int]) -> tuple[float, float] | None:
    """Linear fit over audit sequence; returns (slope, projected next value)."""
    if len(counts) < MIN_PROJECTION_POINTS:
        return None
    x = np.arange(len(counts))
    slope, intercept = np.polyfit(x, np.asarray(counts, dtype=float), 1)
    return float(slope), max(0.0, float(slope * len(counts) + intercept))


def _affected(issue: dict) -> int:
    ref = issue.get("row_reference") or {}
    count = ref.get("count")
    return int(count) if count is not None else 1


def previous_upload_id(conn: psycopg.Connection, upload_id: int) -> int | None:
    row = conn.execute(
        "SELECT p.id FROM uploads u JOIN uploads p ON p.source_client = u.source_client AND p.id < u.id "
        "WHERE u.id = %s ORDER BY p.id DESC LIMIT 1",
        (upload_id,),
    ).fetchone()
    return row[0] if row else None


def _issues(conn: psycopg.Connection, upload_id: int) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute("SELECT * FROM issues WHERE upload_id = %s ORDER BY id", (upload_id,)).fetchall()


def link_recurring(conn: psycopg.Connection, upload_id: int) -> dict:
    """Match an upload's issues to the previous upload, set trend fields,
    store projections, and settle the previous upload's projections."""
    current = _issues(conn, upload_id)
    prev_id = previous_upload_id(conn, upload_id)
    previous = _issues(conn, prev_id) if prev_id else []

    pool: dict[tuple, list[dict]] = defaultdict(list)
    for p in previous:
        pool[match_key(p)].append(p)

    counts = {"new": 0, "worsening": 0, "improving": 0, "stable": 0}
    matched: list[tuple[dict, dict, int]] = []
    with conn.cursor() as cur:
        for issue in current:
            affected = _affected(issue)
            candidates = pool.get(match_key(issue))
            prev = candidates.pop(0) if candidates else None
            if prev is None:
                status, occurrence, prev_ref = "new", 1, None
            else:
                prev_affected = prev["affected_row_count"] if prev["affected_row_count"] is not None else _affected(prev)
                status = trend_status(prev_affected, affected)
                occurrence, prev_ref = prev["occurrence_count"] + 1, prev["id"]
                matched.append((issue, prev, affected))
            counts[status] += 1
            cur.execute(
                "UPDATE issues SET affected_row_count = %s, previous_issue_id = %s, "
                "occurrence_count = %s, trend_status = %s WHERE id = %s",
                (affected, prev_ref, occurrence, status, issue["id"]),
            )

        cur.execute("DELETE FROM issue_projections WHERE upload_id = %s", (upload_id,))
        for issue, prev, affected in matched:
            # settle the projection the previous audit made for this issue
            cur.execute(
                "UPDATE issue_projections SET actual_value = %s, actual_issue_id = %s, evaluated_at = now() "
                "WHERE issue_id = %s",
                (affected, issue["id"], prev["id"]),
            )
            history = issue_history(conn, issue["id"])
            fit = project_next([h["affected_row_count"] for h in history])
            if fit:
                cur.execute(
                    "INSERT INTO issue_projections (issue_id, upload_id, n_points, slope, projected_value) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (issue["id"], upload_id, len(history), round(fit[0], 4), round(fit[1], 2)),
                )
    conn.commit()
    counts["recurring"] = len(matched)
    counts["not_seen"] = sum(len(v) for v in pool.values())
    counts["total"] = len(current)
    return counts


def issue_history(conn: psycopg.Connection, issue_id: int) -> list[dict]:
    """An issue's occurrences oldest -> newest: [{issue_id, upload_id, as_of, affected_row_count}]."""
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "WITH RECURSIVE chain AS ("
            "  SELECT id, previous_issue_id, upload_id, affected_row_count, 0 AS depth FROM issues WHERE id = %s"
            "  UNION ALL"
            "  SELECT i.id, i.previous_issue_id, i.upload_id, i.affected_row_count, c.depth + 1"
            "  FROM issues i JOIN chain c ON i.id = c.previous_issue_id"
            ") SELECT c.id AS issue_id, c.upload_id, c.affected_row_count, "
            "  COALESCE(u.data_as_of, u.upload_date::date) AS as_of "
            "FROM chain c JOIN uploads u ON u.id = c.upload_id ORDER BY c.depth DESC",
            (issue_id,),
        ).fetchall()
    return rows


def histories_for_upload(conn: psycopg.Connection, upload_id: int) -> dict[int, list[dict]]:
    """History chains for every issue of an upload in one query: {issue_id: [...]}."""
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "WITH RECURSIVE chain AS ("
            "  SELECT id AS root, id, previous_issue_id, upload_id, affected_row_count, 0 AS depth"
            "  FROM issues WHERE upload_id = %s AND previous_issue_id IS NOT NULL"
            "  UNION ALL"
            "  SELECT c.root, i.id, i.previous_issue_id, i.upload_id, i.affected_row_count, c.depth + 1"
            "  FROM issues i JOIN chain c ON i.id = c.previous_issue_id"
            ") SELECT c.root, c.affected_row_count, COALESCE(u.data_as_of, u.upload_date::date) AS as_of "
            "FROM chain c JOIN uploads u ON u.id = c.upload_id ORDER BY c.root, c.depth DESC",
            (upload_id,),
        ).fetchall()
    out: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        out[r["root"]].append(r)
    return dict(out)


def projections_for_upload(conn: psycopg.Connection, upload_id: int) -> dict[int, dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute("SELECT * FROM issue_projections WHERE upload_id = %s", (upload_id,)).fetchall()
    return {r["issue_id"]: r for r in rows}


def not_seen_issues(conn: psycopg.Connection, upload_id: int) -> list[dict]:
    """Issues from the previous upload that did not recur in this one."""
    prev_id = previous_upload_id(conn, upload_id)
    if prev_id is None:
        return []
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT p.* FROM issues p WHERE p.upload_id = %s AND NOT EXISTS "
            "(SELECT 1 FROM issues c WHERE c.upload_id = %s AND c.previous_issue_id = p.id) "
            "ORDER BY p.affected_row_count DESC NULLS LAST, p.id",
            (prev_id, upload_id),
        ).fetchall()


def recurrence_summary(conn: psycopg.Connection, upload_id: int) -> dict:
    row = conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE trend_status = 'new'), "
        "count(*) FILTER (WHERE trend_status IN ('worsening','improving','stable')), "
        "count(*) FILTER (WHERE trend_status = 'worsening'), "
        "count(*) FILTER (WHERE trend_status = 'improving') "
        "FROM issues WHERE upload_id = %s",
        (upload_id,),
    ).fetchone()
    return {
        "total": row[0], "new": row[1], "recurring": row[2], "worsening": row[3], "improving": row[4],
        "not_seen": len(not_seen_issues(conn, upload_id)),
        "has_previous": previous_upload_id(conn, upload_id) is not None,
    }


def backfill(conn: psycopg.Connection) -> int:
    """Recompute recurrence for every upload, oldest first."""
    ids = [r[0] for r in conn.execute("SELECT id FROM uploads ORDER BY id").fetchall()]
    for upload_id in ids:
        link_recurring(conn, upload_id)
    return len(ids)


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if "--backfill" not in argv:
        print(__doc__)
        raise SystemExit(1)
    from app.db import repository

    conn = repository.connect()
    repository.apply_schema(conn)
    print(f"recurrence recomputed for {backfill(conn)} uploads")


if __name__ == "__main__":
    main()
