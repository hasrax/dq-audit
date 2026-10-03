"""PostgreSQL persistence for schema documentation + the Layer 2 hook.

Docs live per client (not per upload), so verification work carries over
to later audits. Each row keeps the LLM's original draft beside the
current text; verifying records whether the reviewer changed anything
first, which yields "% confirmed without edits, by confidence level".
"""
from __future__ import annotations

import json

import psycopg
from psycopg.rows import dict_row

CONTEXT_HEADER = "VERIFIED SCHEMA DOCUMENTATION (confirmed by the business; use it to read the columns):"


def documented_tables(conn: psycopg.Connection, source_client: str) -> set[str]:
    rows = conn.execute(
        "SELECT table_name FROM schema_doc_tables WHERE source_client = %s", (source_client,)
    ).fetchall()
    return {r[0] for r in rows}


def save_table_doc(conn: psycopg.Connection, source_client: str, table_name: str, doc: dict, model: str | None = None) -> None:
    """Upsert an LLM draft. Verified tables keep their text and verified
    columns are never overwritten; the stored original draft is refreshed."""
    conn.execute(
        "INSERT INTO schema_doc_tables (source_client, table_name, table_description, table_module, "
        "llm_table_description, llm_table_module, llm_raw, missing_columns, hallucinated_columns, model) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (source_client, table_name) DO UPDATE SET "
        "table_description = CASE WHEN schema_doc_tables.verified_at IS NULL "
        "  THEN EXCLUDED.table_description ELSE schema_doc_tables.table_description END, "
        "table_module = CASE WHEN schema_doc_tables.verified_at IS NULL "
        "  THEN EXCLUDED.table_module ELSE schema_doc_tables.table_module END, "
        "llm_table_description = EXCLUDED.llm_table_description, "
        "llm_table_module = EXCLUDED.llm_table_module, llm_raw = EXCLUDED.llm_raw, "
        "missing_columns = EXCLUDED.missing_columns, "
        "hallucinated_columns = EXCLUDED.hallucinated_columns, "
        "model = EXCLUDED.model, generated_at = now()",
        (
            source_client, table_name, doc["table_description"], doc["table_module"],
            doc["table_description"], doc["table_module"], json.dumps(doc.get("llm_raw")),
            json.dumps(doc["missing"]), json.dumps(doc["hallucinated"]), model,
        ),
    )
    rows = []
    for col, c in doc["columns"].items():
        from_llm = c["from_llm"]
        rows.append((
            source_client, table_name, col, c["display_name"], c["description"],
            c["expected_domain"], json.dumps(c["business_rules"]), c["confidence"],
            c["display_name"] if from_llm else None,
            c["description"] if from_llm else None,
            c["expected_domain"] if from_llm else None,
            json.dumps(c["business_rules"]) if from_llm else None,
            c["confidence"],
        ))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO schema_doc_columns (source_client, table_name, column_name, display_name, "
            "description, expected_domain, business_rules, confidence, llm_display_name, "
            "llm_description, llm_expected_domain, llm_business_rules, llm_confidence) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (source_client, table_name, column_name) DO UPDATE SET "
            "display_name = EXCLUDED.display_name, description = EXCLUDED.description, "
            "expected_domain = EXCLUDED.expected_domain, business_rules = EXCLUDED.business_rules, "
            "confidence = EXCLUDED.confidence, llm_display_name = EXCLUDED.llm_display_name, "
            "llm_description = EXCLUDED.llm_description, "
            "llm_expected_domain = EXCLUDED.llm_expected_domain, "
            "llm_business_rules = EXCLUDED.llm_business_rules, "
            "llm_confidence = EXCLUDED.llm_confidence, generated_at = now() "
            "WHERE schema_doc_columns.confidence <> 'VERIFIED'",
            rows,
        )
    conn.commit()


def fetch_doc_tables(conn: psycopg.Connection, source_client: str) -> list[dict]:
    """Documented tables for a client with per-table verification counts."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT t.*, count(c.id) AS column_count, "
            "count(c.id) FILTER (WHERE c.confidence = 'VERIFIED') AS verified_count, "
            "count(c.id) FILTER (WHERE c.confidence = 'LOW') AS low_count "
            "FROM schema_doc_tables t LEFT JOIN schema_doc_columns c "
            "ON c.source_client = t.source_client AND c.table_name = t.table_name "
            "WHERE t.source_client = %s GROUP BY t.id ORDER BY t.table_name",
            (source_client,),
        ).fetchall()


_CONFIDENCE_ORDER = "CASE confidence WHEN 'LOW' THEN 0 WHEN 'MEDIUM' THEN 1 WHEN 'HIGH' THEN 2 ELSE 3 END"


def fetch_doc_columns(conn: psycopg.Connection, source_client: str, table_name: str) -> list[dict]:
    """A table's column docs, least certain first (LOW -> VERIFIED), then
    in column order (col_2 before col_10)."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            f"SELECT * FROM schema_doc_columns WHERE source_client = %s AND table_name = %s "
            f"ORDER BY {_CONFIDENCE_ORDER}, length(column_name), column_name",
            (source_client, table_name),
        ).fetchall()


def update_column(
    conn: psycopg.Connection, column_id: int, display_name: str | None,
    description: str | None, expected_domain: str | None, business_rules: list[str],
) -> None:
    conn.execute(
        "UPDATE schema_doc_columns SET display_name = %s, description = %s, "
        "expected_domain = %s, business_rules = %s WHERE id = %s",
        (display_name or None, description or None, expected_domain or None,
         json.dumps(business_rules), column_id),
    )
    conn.commit()


def verify_column(conn: psycopg.Connection, column_id: int, verified_by: str | None = None) -> None:
    """Mark VERIFIED, recording whether the reviewer edited the draft first.

    Only the substantive fields count as an edit (description, expected
    domain, rules); renaming the display label does not.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute("SELECT * FROM schema_doc_columns WHERE id = %s", (column_id,)).fetchone()
    if row is None or row["confidence"] == "VERIFIED":
        return
    edited = (
        _norm(row["description"]) != _norm(row["llm_description"])
        or _norm(row["expected_domain"]) != _norm(row["llm_expected_domain"])
        or _norm_rules(row["business_rules"]) != _norm_rules(row["llm_business_rules"])
    )
    conn.execute(
        "UPDATE schema_doc_columns SET confidence = 'VERIFIED', verified_at = now(), "
        "verified_by = %s, edited_before_verify = %s WHERE id = %s",
        (verified_by, edited, column_id),
    )
    conn.commit()


def update_table(conn: psycopg.Connection, source_client: str, table_name: str,
                 description: str | None, module: str | None) -> None:
    conn.execute(
        "UPDATE schema_doc_tables SET table_description = %s, table_module = %s "
        "WHERE source_client = %s AND table_name = %s",
        (description or None, module, source_client, table_name),
    )
    conn.commit()


def verify_table(conn: psycopg.Connection, source_client: str, table_name: str,
                 verified_by: str | None = None) -> int:
    """Verify the table description and every still-unverified column."""
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM schema_doc_columns WHERE source_client = %s AND table_name = %s "
        "AND confidence <> 'VERIFIED'", (source_client, table_name),
    ).fetchall()]
    for column_id in ids:
        verify_column(conn, column_id, verified_by)
    conn.execute(
        "UPDATE schema_doc_tables SET verified_at = now(), verified_by = %s "
        "WHERE source_client = %s AND table_name = %s AND verified_at IS NULL",
        (verified_by, source_client, table_name),
    )
    conn.commit()
    return len(ids)


def doc_stats(conn: psycopg.Connection, source_client: str) -> dict:
    row = conn.execute(
        "SELECT (SELECT count(*) FROM schema_doc_tables WHERE source_client = %s), "
        "(SELECT count(*) FROM schema_doc_tables WHERE source_client = %s AND verified_at IS NOT NULL), "
        "(SELECT count(*) FROM schema_doc_columns WHERE source_client = %s), "
        "(SELECT count(*) FROM schema_doc_columns WHERE source_client = %s AND confidence = 'VERIFIED')",
        (source_client,) * 4,
    ).fetchone()
    return {"tables": row[0], "tables_verified": row[1], "columns": row[2], "columns_verified": row[3]}


def doc_aliases(conn: psycopg.Connection, source_client: str) -> dict[str, dict[str, str]]:
    """Verified display names as an alias overlay {table: {col: name}}."""
    rows = conn.execute(
        "SELECT table_name, column_name, display_name FROM schema_doc_columns "
        "WHERE source_client = %s AND confidence = 'VERIFIED' AND display_name IS NOT NULL",
        (source_client,),
    ).fetchall()
    out: dict[str, dict[str, str]] = {}
    for table, col, name in rows:
        out.setdefault(table, {})[col] = name
    return out


def display_aliases(conn: psycopg.Connection, source_client: str) -> dict[str, dict[str, str]]:
    """Column display names: config/aliases overlaid with verified schema-doc names."""
    from app.aliases import load_aliases

    merged = {t: dict(m) for t, m in load_aliases(source_client).items() if isinstance(m, dict)}
    for table, names in doc_aliases(conn, source_client).items():
        merged.setdefault(table, {}).update(names)
    return merged


def build_schema_context(table_name: str, table_doc: dict | None, columns: list[dict]) -> str:
    """Compact text block from verified docs only; "" if nothing verified."""
    verified = [c for c in columns if c["confidence"] == "VERIFIED"]
    table_ok = table_doc is not None and table_doc.get("verified_at") is not None
    if not verified and not table_ok:
        return ""

    lines = [CONTEXT_HEADER]
    head = f"Table {table_name}"
    if table_ok:
        head += f" ({table_doc.get('table_module') or 'Other'})"
        if table_doc.get("table_description"):
            head += f": {table_doc['table_description']}"
    lines.append(head)
    for c in sorted(verified, key=lambda c: (len(c["column_name"]), c["column_name"])):
        line = f"- {c['column_name']}"
        if c.get("display_name"):
            line += f' "{c["display_name"]}"'
        line += f": {c.get('description') or ''}".rstrip()
        if c.get("expected_domain"):
            line += f" Expected: {c['expected_domain']}."
        rules = c.get("business_rules") or []
        if rules:
            line += " Rules: " + "; ".join(rules) + "."
        lines.append(line)
    return "\n".join(lines)


def get_schema_context(source_client: str | None, table_name: str, conn: psycopg.Connection | None = None) -> str:
    """Verified schema docs for one table as a prompt block.

    Returns "" when there are no verified docs or the repository is
    unreachable, so Layer 2 behaves exactly as it did before docs existed.
    """
    if not source_client:
        return ""
    own_conn = conn is None
    try:
        if own_conn:
            from app.db import repository
            conn = repository.connect()
        with conn.cursor(row_factory=dict_row) as cur:
            table_doc = cur.execute(
                "SELECT * FROM schema_doc_tables WHERE source_client = %s AND table_name = %s",
                (source_client, table_name),
            ).fetchone()
        columns = fetch_doc_columns(conn, source_client, table_name)
        return build_schema_context(table_name, table_doc, columns)
    except psycopg.Error:
        if not own_conn and conn is not None:
            conn.rollback()  # leave the caller's connection usable
        return ""
    finally:
        if own_conn and conn is not None:
            conn.close()


def _norm(text) -> str:
    return " ".join(str(text or "").split())


def _norm_rules(rules) -> list[str]:
    return [_norm(r) for r in (rules or []) if _norm(r)]
