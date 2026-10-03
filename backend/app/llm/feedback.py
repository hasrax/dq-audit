"""Feedback-based prompt customisation (in-context learning, no training).

Before each Layer 2 table audit, the organisation's own history is added
to the prompt:
  - active organisation notes ("We are VAT-exempt, so zero tax is normal");
  - recent LLM issues the reviewers ACCEPTED (confirmed real issues);
  - recent LLM issues they REJECTED (not problems -- don't repeat them).
Same-table examples come first, then the most recently reviewed. The
whole prompt is kept under ~6K tokens by trimming the oldest examples
first. With no notes and no reviewed examples the block is empty and the
prompt is exactly what it was before this feature.
"""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from app.aliases import REPLAY_SUFFIX, base_client

MAX_EXAMPLES = 8
PROMPT_TOKEN_BUDGET = 6000
CHARS_PER_TOKEN = 4
DESCRIPTION_CHARS = 160


def estimate_tokens(*texts: str) -> int:
    return sum(len(t) for t in texts) // CHARS_PER_TOKEN


def _clients(source_client: str) -> list[str]:
    """An organisation's decisions count across its real and replay uploads."""
    base = base_client(source_client)
    return [base, base + REPLAY_SUFFIX]


def fetch_examples(conn: psycopg.Connection, source_client: str, table_name: str,
                   status: str, limit: int = MAX_EXAMPLES) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT i.id, i.table_name, i.column_name, i.description, i.user_note "
            "FROM issues i JOIN uploads u ON u.id = i.upload_id "
            "WHERE u.source_client = ANY(%s) AND i.source = 'llm' AND i.status = %s "
            "ORDER BY (i.table_name = %s) DESC, i.reviewed_at DESC NULLS LAST, i.id DESC LIMIT %s",
            (_clients(source_client), status, table_name, limit),
        ).fetchall()


def fetch_active_notes(conn: psycopg.Connection, source_client: str) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT id, note FROM org_notes WHERE source_client = %s AND active ORDER BY id",
            (base_client(source_client),),
        ).fetchall()


def _example_line(e: dict) -> str:
    where = e["table_name"] + (f".{e['column_name']}" if e.get("column_name") else "")
    text = " ".join((e.get("description") or "").split())[:DESCRIPTION_CHARS]
    line = f"- {where}: {text}"
    if e.get("user_note"):
        line += f' (reviewer: "{" ".join(e["user_note"].split())[:120]}")'
    return line


def build_org_context(notes: list[dict], accepted: list[dict], rejected: list[dict]) -> str:
    parts = []
    if notes:
        parts.append("ORGANISATION NOTES (facts about this business; respect them):\n"
                     + "\n".join(f"- {n['note']}" for n in notes))
    if accepted:
        parts.append("CONFIRMED REAL ISSUES FOR THIS ORGANISATION (reviewers accepted these; "
                     "similar findings are likely genuine):\n" + "\n".join(_example_line(e) for e in accepted))
    if rejected:
        parts.append("PREVIOUSLY REJECTED AS NOT PROBLEMS (do not report similar findings unless "
                     "the evidence is clearly different):\n" + "\n".join(_example_line(e) for e in rejected))
    return "\n\n".join(parts)


def fit_to_budget(base_tokens: int, notes: list[dict], accepted: list[dict], rejected: list[dict],
                  budget: int = PROMPT_TOKEN_BUDGET) -> tuple[str, list[dict], list[dict], int]:
    """Drop the oldest examples (alternating, rejected first) until the
    prompt fits; notes are kept. Returns (text, accepted, rejected, trimmed)."""
    accepted, rejected = list(accepted), list(rejected)
    trimmed = 0
    text = build_org_context(notes, accepted, rejected)
    while estimate_tokens(text) + base_tokens > budget and (accepted or rejected):
        (rejected if len(rejected) >= len(accepted) and rejected else accepted).pop()
        trimmed += 1
        text = build_org_context(notes, accepted, rejected)
    return text, accepted, rejected, trimmed


def feedback_context(conn: psycopg.Connection | None, source_client: str | None, table_name: str,
                     base_tokens: int) -> tuple[str, dict | None]:
    """Org-context block for one table audit plus its log entry; ("", None)
    when there is nothing to add or the repository is unreachable."""
    if conn is None or not source_client:
        return "", None
    try:
        notes = fetch_active_notes(conn, source_client)
        accepted = fetch_examples(conn, source_client, table_name, "accepted")
        rejected = fetch_examples(conn, source_client, table_name, "rejected")
    except psycopg.Error:
        conn.rollback()
        return "", None
    text, accepted, rejected, trimmed = fit_to_budget(base_tokens, notes, accepted, rejected)
    log = {
        "source_client": source_client,
        "table_name": table_name,
        "accepted_issue_ids": [e["id"] for e in accepted],
        "rejected_issue_ids": [e["id"] for e in rejected],
        "note_ids": [n["id"] for n in notes],
        "est_prompt_tokens": base_tokens + estimate_tokens(text),
        "trimmed_examples": trimmed,
    }
    return text, log


# --- organisation notes (admin) ---------------------------------------------

def list_notes(conn: psycopg.Connection, source_client: str) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT * FROM org_notes WHERE source_client = %s ORDER BY active DESC, id DESC",
            (base_client(source_client),),
        ).fetchall()


def add_note(conn: psycopg.Connection, source_client: str, note: str, created_by: str | None = None) -> int:
    row = conn.execute(
        "INSERT INTO org_notes (source_client, note, created_by) VALUES (%s, %s, %s) RETURNING id",
        (base_client(source_client), note.strip(), created_by),
    ).fetchone()
    conn.commit()
    return row[0]


def set_note_active(conn: psycopg.Connection, note_id: int, active: bool) -> None:
    conn.execute("UPDATE org_notes SET active = %s WHERE id = %s", (active, note_id))
    conn.commit()


def save_prompt_log(conn: psycopg.Connection, upload_id: int, entries: list[dict]) -> int:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO llm_prompt_log (upload_id, source_client, table_name, accepted_issue_ids, "
            "rejected_issue_ids, note_ids, schema_context, est_prompt_tokens, trimmed_examples) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [(upload_id, e["source_client"], e["table_name"], e["accepted_issue_ids"],
              e["rejected_issue_ids"], e["note_ids"], e.get("schema_context", False),
              e["est_prompt_tokens"], e["trimmed_examples"]) for e in entries],
        )
    conn.commit()
    return len(entries)


def fetch_prompt_log(conn: psycopg.Connection, upload_id: int) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT * FROM llm_prompt_log WHERE upload_id = %s ORDER BY table_name", (upload_id,)
        ).fetchall()


def feedback_stats(conn: psycopg.Connection, source_client: str) -> dict:
    row = conn.execute(
        "SELECT count(*) FILTER (WHERE i.status = 'accepted'), count(*) FILTER (WHERE i.status = 'rejected') "
        "FROM issues i JOIN uploads u ON u.id = i.upload_id "
        "WHERE u.source_client = ANY(%s) AND i.source = 'llm'",
        (_clients(source_client),),
    ).fetchone()
    return {"accepted": row[0], "rejected": row[1]}
