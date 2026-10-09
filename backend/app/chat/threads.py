"""Chat threads: a user's conversations per company, with attached issues."""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

TITLE_CHARS = 60


def list_threads(conn: psycopg.Connection, source_client: str, username: str | None) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT t.*, (SELECT count(*) FROM chat_messages m WHERE m.thread_id = t.id) AS message_count "
            "FROM chat_threads t WHERE t.source_client = %s AND t.username IS NOT DISTINCT FROM %s "
            "AND NOT t.archived ORDER BY t.updated_at DESC, t.id DESC",
            (source_client, username),
        ).fetchall()


def create_thread(conn: psycopg.Connection, source_client: str, username: str | None,
                  title: str = "New chat", issue_ids: list[int] | None = None,
                  study_code: str | None = None) -> int:
    """study_code flags a usability-study participant's thread (kept apart
    from real conversations)."""
    tid = conn.execute(
        "INSERT INTO chat_threads (source_client, username, title, study_code) VALUES (%s, %s, %s, %s) RETURNING id",
        (source_client, username, title[:TITLE_CHARS], study_code),
    ).fetchone()[0]
    for issue_id in issue_ids or []:
        attach_issue(conn, tid, issue_id)
    conn.commit()
    return tid


def attach_issue(conn: psycopg.Connection, thread_id: int, issue_id: int) -> None:
    conn.execute("INSERT INTO chat_thread_issues (thread_id, issue_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                 (thread_id, issue_id))
    conn.commit()


def detach_issue(conn: psycopg.Connection, thread_id: int, issue_id: int) -> None:
    conn.execute("DELETE FROM chat_thread_issues WHERE thread_id = %s AND issue_id = %s", (thread_id, issue_id))
    conn.commit()


def thread_issues(conn: psycopg.Connection, thread_id: int) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT i.* FROM chat_thread_issues t JOIN issues i ON i.id = t.issue_id "
            "WHERE t.thread_id = %s ORDER BY i.id", (thread_id,),
        ).fetchall()


def messages(conn: psycopg.Connection, thread_id: int) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT role, content, created_at FROM chat_messages WHERE thread_id = %s ORDER BY id", (thread_id,),
        ).fetchall()


def add_message(conn: psycopg.Connection, thread_id: int, upload_id: int, role: str, content: str,
                username: str | None = None) -> None:
    conn.execute(
        "INSERT INTO chat_messages (thread_id, upload_id, username, role, content) VALUES (%s, %s, %s, %s, %s)",
        (thread_id, upload_id, username, role, content),
    )
    # first question names an untitled thread
    if role == "user":
        conn.execute("UPDATE chat_threads SET title = %s WHERE id = %s AND title = 'New chat'",
                     (" ".join(content.split())[:TITLE_CHARS], thread_id))
    conn.execute("UPDATE chat_threads SET updated_at = now() WHERE id = %s", (thread_id,))
    conn.commit()


def rename_thread(conn: psycopg.Connection, thread_id: int, title: str) -> None:
    conn.execute("UPDATE chat_threads SET title = %s WHERE id = %s", (title.strip()[:TITLE_CHARS] or "Chat", thread_id))
    conn.commit()


def archive_thread(conn: psycopg.Connection, thread_id: int) -> None:
    conn.execute("UPDATE chat_threads SET archived = TRUE WHERE id = %s", (thread_id,))
    conn.commit()
