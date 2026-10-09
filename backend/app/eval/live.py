"""Live evaluation metrics for the Evaluation page (and Chapter 5).

Everything is computed from the repository as it stands, so the numbers
update as reviewers triage -- human decisions are the ground truth:
  precision            accepted / (accepted + rejected), per detector
  AI rejection rate    rejected AI findings / decided AI findings
                       (the hallucination indicator), tracked per audit
                       alongside how many feedback examples the AI saw
  complementarity      locations found only by rules / only by AI / both
  schema-doc accuracy  share of AI drafts verified without edits, by the
                       AI's own confidence
  projection error     projected vs actual affected rows, once settled
Recall needs the separate spot-check of unflagged rows (eval scripts).
"""
from __future__ import annotations

import statistics
from collections import defaultdict

import psycopg
from psycopg.rows import dict_row

from app.aliases import REPLAY_SUFFIX, base_client

MIN_DECISIONS = 20   # below this, a precision figure is shown as provisional


def scope_issues(conn: psycopg.Connection, upload_id: int | None = None,
                 source_client: str | None = None) -> list[dict]:
    """Issues of one audit, one company (all its audits), or everything."""
    sql = "SELECT i.* FROM issues i JOIN uploads u ON u.id = i.upload_id"
    params: tuple = ()
    if upload_id is not None:
        sql, params = sql + " WHERE i.upload_id = %s", (upload_id,)
    elif source_client is not None:
        sql, params = sql + " WHERE u.source_client = %s", (source_client,)
    else:   # all companies = real workspaces only (no study / test / replay copies)
        sql += " WHERE u.source_client IN (SELECT slug FROM workspaces WHERE kind = 'real')"
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql + " ORDER BY i.id", params).fetchall()


def _precision(acc: int, rej: int) -> float | None:
    return round(acc / (acc + rej), 4) if acc + rej else None


def precision_by_dimension(issues: list[dict]) -> list[dict]:
    """[{dimension, source, accepted, rejected, considered, precision}]"""
    counts: dict[tuple, dict] = defaultdict(lambda: {"accepted": 0, "rejected": 0, "considered": 0, "flagged": 0})
    for i in issues:
        dim = "validity" if i["dimension"] == "accuracy" else i["dimension"]
        counts[(dim, i["source"])][i.get("status", "flagged")] += 1
    out = []
    for (dim, source), c in sorted(counts.items()):
        out.append({"dimension": dim, "source": source, **c,
                    "precision": _precision(c["accepted"], c["rejected"])})
    return out


def ai_rejection_by_audit(conn: psycopg.Connection, source_client: str) -> list[dict]:
    """Per audit of a company: AI findings, decisions, rejection rate, and the
    number of feedback examples + notes the AI was given (prompt log)."""
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "SELECT u.id AS upload_id, COALESCE(u.data_as_of, u.upload_date::date) AS as_of, "
            "count(i.id) FILTER (WHERE i.source = 'llm') AS ai_findings, "
            "count(i.id) FILTER (WHERE i.source = 'llm' AND i.status = 'accepted') AS accepted, "
            "count(i.id) FILTER (WHERE i.source = 'llm' AND i.status = 'rejected') AS rejected, "
            "(SELECT COALESCE(sum(cardinality(l.accepted_issue_ids) + cardinality(l.rejected_issue_ids)), 0) "
            " FROM llm_prompt_log l WHERE l.upload_id = u.id) AS feedback_examples, "
            "(SELECT COALESCE(max(cardinality(l.note_ids)), 0) FROM llm_prompt_log l WHERE l.upload_id = u.id) AS notes "
            "FROM uploads u LEFT JOIN issues i ON i.upload_id = u.id "
            "WHERE u.source_client = %s GROUP BY u.id ORDER BY u.id",
            (source_client,),
        ).fetchall()
    for r in rows:
        decided = r["accepted"] + r["rejected"]
        r["rejection_rate"] = round(r["rejected"] / decided, 4) if decided else None
    return [r for r in rows if r["ai_findings"]]


def schema_doc_accuracy(conn: psycopg.Connection, source_client: str | None = None) -> list[dict]:
    """Verified AI-drafted column docs, and how many were confirmed unedited,
    grouped by the confidence the AI gave."""
    where, params = "WHERE confidence = 'VERIFIED' AND llm_description IS NOT NULL", ()
    if source_client:
        where, params = where + " AND source_client = %s", (base_client(source_client),)
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            f"SELECT llm_confidence, count(*) AS verified, "
            f"count(*) FILTER (WHERE edited_before_verify = FALSE) AS unedited "
            f"FROM schema_doc_columns {where} GROUP BY llm_confidence "
            f"ORDER BY CASE llm_confidence WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END",
            params,
        ).fetchall()
    for r in rows:
        r["unedited_pct"] = round(100 * r["unedited"] / r["verified"], 1) if r["verified"] else None
    return rows


def projection_error(conn: psycopg.Connection, source_client: str | None = None) -> dict:
    """Settled projections: count, median and mean absolute % error, MAE."""
    # Flat histories (slope 0, e.g. re-auditing an unchanged export) are
    # trivially "predicted" and would flatter the error, so only changing
    # issues count.
    sql = ("SELECT p.projected_value, p.actual_value FROM issue_projections p "
           "JOIN uploads u ON u.id = p.upload_id WHERE p.actual_value IS NOT NULL AND p.slope <> 0")
    params: tuple = ()
    if source_client:
        base = base_client(source_client)
        sql, params = sql + " AND u.source_client IN (%s, %s)", (base, base + REPLAY_SUFFIX)
    else:   # the replay is the documented source of projections; study / test copies never count
        sql += " AND u.source_client IN (SELECT slug FROM workspaces WHERE kind IN ('real', 'replay'))"
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return {"n": 0}
    abs_err = [abs(float(p) - a) for p, a in rows]
    pct = [abs(float(p) - a) / a * 100 for p, a in rows if a]
    return {
        "n": len(rows),
        "mae_rows": round(statistics.mean(abs_err), 1),
        "median_ape": round(statistics.median(pct), 1) if pct else None,
        "mean_ape": round(statistics.mean(pct), 1) if pct else None,
    }
