"""Chat clarification (Layer 3): plain-language Q&A about an audit.

Users ask about one issue or about their imported data in general. Each
question is answered ONLY from context assembled here:
  - a compact profile summary of the relevant tables,
  - verified schema documentation,
  - the selected issue (and its rule's what/why), if any, with up to 5 of
    its stored example rows -- masked,
  - a summary of the current audit's issues,
  - the last 6 chat turns (the API is stateless, so history is resent).

Privacy (same principle as Layer 2: profile + small masked samples).
Text columns never send min/max values (they could be names); columns in
the client's privacy mask list send no values at all. The only rows sent
are the selected issue's stored examples (at most 5), with every text or
privacy-listed value replaced by its MASK_xxxx pseudonym (equal values
keep equal pseudonyms, so rows can still be compared) and long digit runs
masked. Added after a usability-study participant asked whether duplicate
invoices differ in their other columns and the chat could not see them
(10 Oct 2026). The model may not write SQL or code to run on the data --
a function-catalogue approach is future work.
"""
from __future__ import annotations

import dataclasses
import os
from collections import Counter

import psycopg
from psycopg.rows import dict_row

from app.aliases import base_client, client_label
from app.reports.wording import show
from app.db import repository
from app.config import load_settings
from app.llm.client import LLMClient
from app.privacy.pseudonymize import load_overrides, mask_value
from app.schema_docs.store import display_aliases, get_schema_context
from app.db.sync_cloud import scrub

HISTORY_TURNS = 6
MAX_ISSUES_LISTED = 15
VALUE_TYPES = ("int", "decimal", "date")

SYSTEM_PROMPT = """\
You are a friendly data quality assistant helping non-technical staff at a \
Sri Lankan small or medium business (accountants, storekeepers, data-entry \
operators, managers) understand an audit of their business data. Work out \
the kind of business from the tables and columns; do not assume an industry.

Answer only from the profile, schema documentation, and audit results \
provided. If a question needs data that is not included, say so clearly \
instead of guessing. Never invent numbers. Do not write SQL or code.

Write in plain business English, no database jargon. Keep every answer \
short: at most about 120 words, at most 4 bullet points, no markdown \
headings (use **bold** labels instead). When you show example rows, use one \
small markdown table (at most 5 rows). End every answer with one last line \
in italics that starts with "Based on:" and names what you used, for example \
"*Based on: issue #123 and 5 example rows*" or "*Based on: the audit summary \
and the profile of InvoiceDetails*". Column names like col_3 may have a friendly name in \
brackets; use the friendly name when there is one. Some values appear as \
MASK_xxxx because they were hidden for privacy; never guess them, but \
two identical MASK values do mean the same original value, so you can say \
whether rows match. When EXAMPLE ROWS are given for an issue, use them to \
answer questions about those rows (for example whether duplicates differ in \
other columns), and say that they are only examples, not every affected row.
"""


def _profile_summary(conn: psycopg.Connection, upload_id: int, tables: list[str],
                     source_client: str, aliases: dict) -> str:
    masked = {t: v.get("mask", set()) for t, v in load_overrides(source_client).items()}
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "SELECT table_name, column_name, data_type, null_pct, distinct_count, min_val, max_val "
            "FROM profiles WHERE upload_id = %s AND table_name = ANY(%s) ORDER BY table_name, id",
            (upload_id, tables),
        ).fetchall()
    lines, current = [], None
    for r in rows:
        if r["table_name"] != current:
            current = r["table_name"]
            lines.append(f"Table {current}:")
        name = aliases.get(current, {}).get(r["column_name"])
        label = f"{r['column_name']} ({name})" if name else r["column_name"]
        line = f"  {label}: {r['data_type']}, {float(r['null_pct'] or 0):g}% blank, {r['distinct_count']} distinct"
        if r["data_type"] in VALUE_TYPES and r["column_name"] not in masked.get(current, set()):
            line += f", range {r['min_val']} .. {r['max_val']}"
        lines.append(line)
    return "\n".join(lines)


def _issue_line(i: dict, aliases: dict) -> str:
    col = i.get("column_name")
    if col:
        names = [aliases.get(i["table_name"], {}).get(c.strip()) or c.strip() for c in col.split(",")]
        col = ", ".join(names)
    where = i["table_name"] + (f".{col}" if col else "")
    rows = i.get("affected_row_count")
    trend = f", {i['trend_status']}" if i.get("trend_status") else ""
    return (f"#{i['id']} {where} — {i['dimension']}, {i['severity']} severity, "
            f"{'rule' if i['source'] == 'rule' else 'AI'}-detected, "
            f"{rows if rows is not None else '?'} rows, status {i['status']}{trend}")


def _audit_summary(issues: list[dict], aliases: dict) -> str:
    by_dim = Counter(i["dimension"] for i in issues)
    by_sev = Counter(i["severity"] for i in issues)
    by_src = Counter(i["source"] for i in issues)
    top = sorted(issues, key=lambda i: ({"high": 0, "medium": 1}.get(i["severity"], 2),
                                        -(i.get("affected_row_count") or 0)))[:MAX_ISSUES_LISTED]
    return "\n".join([
        f"{len(issues)} issues: by dimension {dict(by_dim)}; by severity {dict(by_sev)}; "
        f"rule-detected {by_src.get('rule', 0)}, AI-detected {by_src.get('llm', 0)}.",
        f"Most important {len(top)}:",
        *[f"  {_issue_line(i, aliases)}" for i in top],
    ])


def _issue_detail(conn: psycopg.Connection, issue: dict, aliases: dict) -> str:
    lines = [
        f"SELECTED ISSUE #{issue['id']}:",
        _issue_line(issue, aliases),
        f"What was found: {show(issue.get('description'), issue['table_name'], aliases)}",
        f"Recommendation: {show(issue.get('recommendation'), issue['table_name'], aliases)}",
        f"Fixable: {issue.get('fixable') or 'unknown'}",
    ]
    if issue.get("user_note"):
        lines.append(f"Reviewer note: {issue['user_note']}")
    if issue.get("rule_ref"):
        rule = repository.fetch_rule(conn, issue["rule_ref"])
        if rule:
            lines += [f"Rule — what it checks: {show(rule['what_text'], issue['table_name'], aliases)}",
                      f"Rule — why it exists: {show(rule['why_text'], issue['table_name'], aliases)}"]
    lines += _example_rows(conn, issue, aliases)
    return "\n".join(lines)


MAX_EXAMPLE_ROWS = 5


def _example_rows(conn: psycopg.Connection, issue: dict, aliases: dict) -> list[str]:
    """The issue's stored example rows, masked (see the privacy note above)."""
    samples = ((issue.get("row_reference") or {}).get("samples") or [])[:MAX_EXAMPLE_ROWS]
    if not samples:
        return []
    table = issue["table_name"]
    types = dict(conn.execute(
        "SELECT column_name, data_type FROM profiles WHERE upload_id = %s AND table_name = %s",
        (issue["upload_id"], table)).fetchall())
    private = load_overrides(base_client(issue.get("source_client"))).get(table, {}).get("mask", set())
    names = aliases.get(table, {})
    out = [f"EXAMPLE ROWS for issue #{issue['id']} ({len(samples)} of "
           f"{(issue.get('row_reference') or {}).get('count') or len(samples)} affected rows; masked):"]
    for s in samples:
        cells = []
        for col, v in (s.get("values") or {}).items():
            if v is None:
                shown = "(empty)"
            elif str(v).startswith("MASK_"):
                shown = str(v)
            elif col in private or types.get(col, "varchar") not in VALUE_TYPES:
                shown = mask_value(str(v))
            else:
                shown = scrub(str(v))
            label = f"{names[col]} ({col})" if col in names else col
            cells.append(f"{label} = {shown}")
        out.append(f"- Row {int(s.get('row', 0)) + 1}: " + "; ".join(cells))
    return out


def build_context(conn: psycopg.Connection, upload: dict, issue: dict | list[dict] | None = None) -> str:
    """Context for one question. `issue` may be one issue or several (a
    thread can discuss a group of issues together)."""
    client = upload["source_client"]
    aliases = display_aliases(conn, client)
    issues = repository.fetch_issues(conn, upload["id"])
    focus = [issue] if isinstance(issue, dict) else list(issue or [])
    if focus:
        rels = repository.fetch_relationships(conn, upload["id"])
        focus_tables = {i["table_name"] for i in focus}
        tables = focus_tables | {
            r["parent_table"] for r in rels if r["child_table"] in focus_tables and r["confirmed"]
        }
    else:
        tables = {r[0] for r in conn.execute(
            "SELECT DISTINCT table_name FROM profiles WHERE upload_id = %s", (upload["id"],)).fetchall()}
    tables = sorted(tables)

    parts = [
        f"ORGANISATION: {client_label(client)} · audit #{upload['id']} · {upload['row_count']:,} rows audited",
        "DATA PROFILE (statistics only):\n" + _profile_summary(conn, upload["id"], tables, base_client(client), aliases),
    ]
    docs = [d for d in (get_schema_context(client, t, conn=conn) for t in tables) if d]
    if docs:
        parts.append("\n\n".join(docs))
    parts.append("AUDIT RESULTS:\n" + _audit_summary(issues, aliases))
    for i in focus:
        parts.append(_issue_detail(conn, i, aliases))
    return "\n\n".join(parts)


# Chat-only backup models (LLM_CHAT_FALLBACK = "name|base_url|model|KEY_ENV;name|..."),
# tried in order -- one quick attempt each -- only when the main model still fails
# after its own retries. Audits, schema docs and reports never fall back, so
# evaluation numbers stay on one model.
FALLBACK_NOTE = "\n\n_(Answered by the backup AI model because the main one was unavailable.)_"


def fallback_clients() -> list[LLMClient]:
    clients = []
    for chunk in filter(None, (c.strip() for c in os.environ.get("LLM_CHAT_FALLBACK", "").split(";"))):
        parts = [p.strip() for p in chunk.split("|")] + [""] * 4
        _name, base_url, model, key_env = parts[:4]
        if base_url and model:
            settings = dataclasses.replace(load_settings(), llm_base_url=base_url, llm_model=model,
                                           llm_api_key=os.environ.get(key_env, "") if key_env else "")
            clients.append(LLMClient(settings, max_retries=1))
    return clients


def answer(conn: psycopg.Connection, upload: dict, question: str, history: list[dict],
           issue: dict | list[dict] | None = None, client: LLMClient | None = None) -> str:
    """One answer. history = prior turns [{role, content}], oldest first.
    If the main model fails (after its retries) and a backup is configured,
    the backup answers and the reply ends with FALLBACK_NOTE."""
    explicit = client is not None
    client = client or LLMClient()
    context = build_context(conn, upload, issue)
    messages = [{"role": "user", "content": f"CONTEXT FOR THIS CONVERSATION:\n{context}"},
                {"role": "assistant", "content": "Understood. I will answer only from this context."}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history[-HISTORY_TURNS:]]
    messages.append({"role": "user", "content": question})
    try:
        return client.chat(SYSTEM_PROMPT, messages, temperature=0.1, max_tokens=700).strip()
    except Exception:
        if explicit:
            raise
        for backup in fallback_clients():
            try:
                reply = backup.chat(SYSTEM_PROMPT, messages, temperature=0.1, max_tokens=700).strip()
            except Exception:  # noqa: BLE001 -- busy free model: try the next one
                continue
            if reply:
                return reply + FALLBACK_NOTE
        raise


REPORT_PROMPT = """\
You write data quality reports for non-technical staff and managers at a \
Sri Lankan small or medium business (work out the kind of business from the \
context; do not assume an industry). Write ONLY from the context provided \
(profile statistics, schema documentation, audit results, selected issues \
and the conversation). Never invent numbers; if something the reader asked \
for is not in the context, say so in one sentence.

Format the report in Markdown: start with a "# " title, use "## " section \
headings, short paragraphs, "- " bullet points, **bold** for key facts, and \
at most one simple pipe table when it helps. Plain business English, no \
database jargon. Finish with a short "## Recommended next steps" section.
"""

REPORT_SUGGESTIONS = [
    "A one-page summary of this audit for the managing director",
    "A report on the high-severity issues with a clear action plan",
    "A report for the data-entry team: what to check and how to avoid these errors",
]


def write_report(conn: psycopg.Connection, upload: dict, instruction: str,
                 issues: list[dict] | None = None, history: list[dict] | None = None,
                 client: LLMClient | None = None) -> str:
    """An AI-written custom report (Markdown) following the user's instruction."""
    client = client or LLMClient()
    context = build_context(conn, upload, issues or [])
    convo = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in (history or [])[-HISTORY_TURNS:])
    user = (f"CONTEXT:\n{context}\n\n"
            + (f"CONVERSATION SO FAR:\n{convo}\n\n" if convo else "")
            + f"WRITE THIS REPORT: {instruction.strip()}")
    return client.chat(REPORT_PROMPT, [{"role": "user", "content": user}],
                       temperature=0.2, max_tokens=2200).strip()


def save_turn(conn: psycopg.Connection, upload_id: int, role: str, content: str,
              issue_id: int | None = None, username: str | None = None) -> None:
    conn.execute(
        "INSERT INTO chat_messages (upload_id, issue_id, username, role, content) VALUES (%s, %s, %s, %s, %s)",
        (upload_id, issue_id, username, role, content),
    )
    conn.commit()


def fetch_turns(conn: psycopg.Connection, upload_id: int, issue_id: int | None = None,
                username: str | None = None) -> list[dict]:
    """A conversation thread: one upload + one issue (or the general thread)."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT role, content, created_at FROM chat_messages WHERE upload_id = %s "
            "AND issue_id IS NOT DISTINCT FROM %s AND username IS NOT DISTINCT FROM %s ORDER BY id",
            (upload_id, issue_id, username),
        ).fetchall()
