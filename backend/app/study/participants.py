"""Study mode: unmoderated remote usability test (RQ5).

A participant is only a code (P00 = pilot, P01-P15). Codes are created by
an admin, single-use once finished, resumable while in progress. Each of
the five tasks records the participant's own verdict (done / couldn't)
and a system check from what the app observed (study_events), so
self-reported and verified completion can both be reported. Decisions go
to study_decisions -- never to the real issues table -- and each
participant sees only their own decisions.
"""
from __future__ import annotations

import json
import re

import psycopg
from psycopg.rows import dict_row

PILOT_CODE = "P00"
CODE_RE = re.compile(r"^P\d{2}$")

TASKS = {
    1: ("Find the most serious issue",
        "Open the most serious (high-severity) issue and read its explanation. Then answer below: "
        "in one sentence, what is wrong?"),
    2: ("Decide on it",
        "Accept or reject that issue and write a short note explaining why."),
    3: ("Ask about it",
        "Ask the chat one question about that issue."),
    4: ("Export a table's issues",
        "Filter the issues to one table and download them as Excel."),
    5: ("Get the report",
        "Download the PDF report."),
}
N_TASKS = len(TASKS)

# What the app must have observed for a task to count as system-verified.
VERIFY = {
    1: ("detail_opened", lambda d: d.get("severity") == "high"),
    2: ("decision_saved", lambda d: d.get("decision") in ("accepted", "rejected") and bool(d.get("note"))),
    3: ("chat_sent", lambda d: True),
    4: ("excel_downloaded", lambda d: bool(d.get("table"))),
    5: ("pdf_downloaded", lambda d: True),
}


def normalise_code(raw: str) -> str:
    return raw.strip().upper()


def parse_user_agent(ua: str | None) -> tuple[str, str]:
    """(device_type, browser name) from a User-Agent; no other detail kept."""
    ua = ua or ""
    if "iPad" in ua or ("Android" in ua and "Mobile" not in ua) or "Tablet" in ua:
        device = "tablet"
    elif "Mobi" in ua or "iPhone" in ua or "Android" in ua:
        device = "mobile"
    else:
        device = "desktop"
    for token, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
                        ("Chrome/", "Chrome"), ("Safari/", "Safari")):
        if token in ua:
            return device, name
    return device, "Other"


# --- codes (admin) ------------------------------------------------------------

def create_codes(conn: psycopg.Connection, codes: list[str]) -> int:
    made = 0
    for code in (normalise_code(c) for c in codes):
        if not CODE_RE.match(code):
            raise ValueError(f"Codes look like P00-P99 (got {code!r}).")
        made += conn.execute("INSERT INTO study_participants (code) VALUES (%s) ON CONFLICT DO NOTHING",
                             (code,)).rowcount
    conn.commit()
    return made


def get(conn: psycopg.Connection, code: str) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute("SELECT * FROM study_participants WHERE code = %s", (normalise_code(code),)).fetchone()


def check_code(conn: psycopg.Connection, raw: str) -> tuple[bool, str]:
    """(allowed, message). Finished codes cannot start again."""
    p = get(conn, raw)
    if p is None:
        return False, "We don't recognise this code. Please check it and try again."
    if p["status"] == "finished":
        return False, "This code has already been used. Thank you for taking part."
    return True, ""


def overview(conn: psycopg.Connection) -> list[dict]:
    """Every code with its status, times, device and task counts."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT p.*, count(t.id) FILTER (WHERE t.self_status = 'done') AS tasks_done_self, "
            "count(t.id) FILTER (WHERE t.system_verified) AS tasks_verified "
            "FROM study_participants p LEFT JOIN study_task_events t ON t.code = p.code "
            "GROUP BY p.code ORDER BY p.code"
        ).fetchall()


def reset(conn: psycopg.Connection, code: str) -> None:
    """Wipe a code's progress (used for the pilot P00) so it can run again."""
    code = normalise_code(code)
    for table in ("study_task_events", "study_events", "study_decisions"):
        conn.execute(f"DELETE FROM {table} WHERE code = %s", (code,))
    conn.execute("DELETE FROM chat_threads WHERE study_code = %s", (code,))
    conn.execute("UPDATE study_participants SET consent_at = NULL, started_at = NULL, finished_at = NULL, "
                 "status = 'not_started', device_type = NULL, browser = NULL WHERE code = %s", (code,))
    conn.commit()


# --- the participant's session ------------------------------------------------

def give_consent(conn: psycopg.Connection, code: str, device_type: str, browser: str) -> None:
    conn.execute(
        "UPDATE study_participants SET consent_at = COALESCE(consent_at, now()), "
        "started_at = COALESCE(started_at, now()), status = 'in_progress', device_type = %s, browser = %s "
        "WHERE code = %s AND status <> 'finished'",
        (device_type, browser, code),
    )
    conn.execute("INSERT INTO study_task_events (code, task_no) VALUES (%s, 1) ON CONFLICT DO NOTHING", (code,))
    conn.commit()


def tasks(conn: psycopg.Connection, code: str) -> dict[int, dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute("SELECT * FROM study_task_events WHERE code = %s ORDER BY task_no", (code,)).fetchall()
    return {r["task_no"]: r for r in rows}


def current_task(conn: psycopg.Connection, code: str) -> int | None:
    """The first unfinished task (None when all five are done)."""
    done = {n for n, r in tasks(conn, code).items() if r["ended_at"] is not None}
    return next((n for n in range(1, N_TASKS + 1) if n not in done), None)


def log_event(conn: psycopg.Connection, code: str, event: str, detail: dict | None = None) -> None:
    conn.execute("INSERT INTO study_events (code, event, detail) VALUES (%s, %s, %s)",
                 (code, event, json.dumps(detail or {}, default=str)))
    conn.commit()


def is_verified(conn: psycopg.Connection, code: str, task_no: int) -> bool:
    event, ok = VERIFY[task_no]
    rows = conn.execute("SELECT detail FROM study_events WHERE code = %s AND event = %s",
                        (code, event)).fetchall()
    return any(ok(r[0] or {}) for r in rows)


def end_task(conn: psycopg.Connection, code: str, task_no: int, self_status: str,
             answer: str | None = None) -> None:
    """Close a task with the participant's verdict and the system check,
    then open the next one (or finish the session after task 5)."""
    if self_status not in ("done", "couldnt"):
        raise ValueError(self_status)
    conn.execute(
        "UPDATE study_task_events SET ended_at = now(), self_status = %s, system_verified = %s, "
        "answer_text = COALESCE(%s, answer_text) WHERE code = %s AND task_no = %s AND ended_at IS NULL",
        (self_status, is_verified(conn, code, task_no), (answer or "").strip() or None, code, task_no),
    )
    if task_no < N_TASKS:
        conn.execute("INSERT INTO study_task_events (code, task_no) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                     (code, task_no + 1))
    else:
        conn.execute("UPDATE study_participants SET finished_at = now(), status = 'finished' WHERE code = %s",
                     (code,))
    conn.commit()


# --- decisions (never touch issues) -------------------------------------------

def decisions(conn: psycopg.Connection, code: str) -> dict[int, dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute("SELECT * FROM study_decisions WHERE code = %s", (code,)).fetchall()
    return {r["issue_id"]: r for r in rows}


def save_decision(conn: psycopg.Connection, code: str, issue_id: int, decision: str, note: str | None) -> None:
    conn.execute(
        "INSERT INTO study_decisions (code, issue_id, decision, note) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (code, issue_id) DO UPDATE SET decision = EXCLUDED.decision, note = EXCLUDED.note, "
        "created_at = now()",
        (code, issue_id, decision, (note or "").strip() or None),
    )
    conn.commit()
    log_event(conn, code, "decision_saved", {"issue_id": issue_id, "decision": decision,
                                             "note": bool((note or "").strip())})


def overlay(issues: list[dict], own: dict[int, dict], code: str) -> list[dict]:
    """The participant's view: each issue starts as 'to review'; only their
    own decisions are shown."""
    out = []
    for i in issues:
        d = own.get(i["id"])
        out.append({**i, "status": d["decision"] if d else "flagged", "user_note": d["note"] if d else None,
                    "reviewed_by": code if d else None, "reviewed_at": d["created_at"] if d else None})
    return out


def results_rows(conn: psycopg.Connection) -> list[dict]:
    """One row per code and task, for the admin's CSV download."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT p.code, p.status, p.consent_at, p.started_at, p.finished_at, p.device_type, p.browser, "
            "t.task_no, t.started_at AS task_started, t.ended_at AS task_ended, "
            "EXTRACT(EPOCH FROM (t.ended_at - t.started_at)) AS task_seconds, t.self_status, "
            "t.system_verified, t.answer_text "
            "FROM study_participants p LEFT JOIN study_task_events t ON t.code = p.code "
            "ORDER BY p.code, t.task_no"
        ).fetchall()
