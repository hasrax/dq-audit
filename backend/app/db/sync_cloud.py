"""Copy the local issue repository to a hosted database, with PII masked.

The hosted demo must not hold personal data, so values are masked on the
way out (deterministic MASK_xxxx tokens, the same ones the LLM sees):
  - stored sample rows (issues.row_reference.samples): every text-typed
    column, plus the client's privacy mask list (phone-as-int, IDs, GPS);
  - profile min/max of those same columns;
  - any run of 9+ digits in free text (descriptions, rule evidence, notes,
    schema docs) -- older audits may predate the privacy overrides.
User accounts, app settings and chat transcripts are NOT copied; create
the hosted admin separately. Row ids are preserved so every link
(rule_ref, previous_issue_id, projections) still resolves.

Usage (reads CLOUD_DATABASE_URL from backend/.env):
    python -m app.db.sync_cloud [--replace] [--dry-run] [--discard-study-results]

Never copied: local-only workspaces (Companies D-G), test/injection data,
user accounts, chat and study sessions. --replace refuses to run once
real study participants have started on the hosted database.
"""
from __future__ import annotations

import os
import re
import sys

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.aliases import base_client
from app.config import load_settings
from app.db import repository
from app.privacy.pseudonymize import load_overrides, mask_value

# FK-safe order (parents first; issues are inserted by id so a recurring
# issue's previous_issue_id always exists already).
TABLES = [
    "workspaces", "uploads", "profiles", "relationships", "rules", "issues", "issue_projections",
    "dq_scores", "schema_doc_tables", "schema_doc_columns", "org_notes",
    "rule_suppressions", "llm_prompt_log",
]
VALUE_TYPES = {"int", "decimal", "date"}
LONG_DIGITS = re.compile(r"(?<![\d.])\d{9,}(?![\d.])")


def scrub(obj):
    """Mask long digit runs anywhere inside text / JSON-like structures."""
    if isinstance(obj, str):
        return LONG_DIGITS.sub(lambda m: mask_value(m.group(0)), obj)
    if isinstance(obj, bool) or obj is None:
        return obj
    if isinstance(obj, int):
        return mask_value(str(obj)) if len(str(abs(obj))) >= 9 else obj
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()}
    return obj


class Masker:
    """Decides which (upload, table, column) values are personal data."""

    def __init__(self, local: psycopg.Connection):
        self.types = {
            (u, t, c): ty for u, t, c, ty in local.execute(
                "SELECT upload_id, table_name, column_name, data_type FROM profiles").fetchall()
        }
        self.clients = dict(local.execute("SELECT id, source_client FROM uploads").fetchall())
        # copies (study / replay) use their parent company's privacy list
        self.parents = dict(local.execute(
            "SELECT slug, config_from FROM workspaces WHERE config_from IS NOT NULL").fetchall())
        self._overrides: dict[str, dict] = {}
        # Never hosted: local-only workspaces (Companies D-G) and test (injection) data.
        self.excluded_clients = {r[0] for r in local.execute(
            "SELECT slug FROM workspaces WHERE local_only OR kind = 'test'").fetchall()}
        self.excluded_uploads = {u for u, c in self.clients.items() if c in self.excluded_clients}

    def _override_mask(self, upload_id: int, table: str) -> set[str]:
        client = self.clients.get(upload_id)
        client = base_client(self.parents.get(client, client))
        if client not in self._overrides:
            self._overrides[client] = load_overrides(client)
        return self._overrides[client].get(table, {}).get("mask", set())

    def is_personal(self, upload_id: int, table: str, column: str) -> bool:
        if column in self._override_mask(upload_id, table):
            return True
        return self.types.get((upload_id, table, column), "varchar") not in VALUE_TYPES

    def value(self, upload_id: int, table: str, column: str, value):
        if value is None:
            return None
        return mask_value(str(value)) if self.is_personal(upload_id, table, column) else scrub(value)


def transform(table: str, row: dict, m: Masker) -> dict | None:
    row = dict(row)
    if row.get("upload_id") in m.excluded_uploads or row.get("source_client") in m.excluded_clients \
            or (table == "workspaces" and row["slug"] in m.excluded_clients):
        return None
    if table == "profiles":
        if m.is_personal(row["upload_id"], row["table_name"], row["column_name"]):
            row["min_val"] = mask_value(row["min_val"]) if row["min_val"] is not None else None
            row["max_val"] = mask_value(row["max_val"]) if row["max_val"] is not None else None
    elif table == "issues":
        ref = row.get("row_reference") or {}
        if ref.get("samples"):
            ref = dict(ref)
            ref["samples"] = [
                {**s, "values": {c: m.value(row["upload_id"], row["table_name"], c, v)
                                 for c, v in s["values"].items()}}
                for s in ref["samples"]
            ]
        row["row_reference"] = ref or None
        for f in ("description", "recommendation", "user_note"):
            row[f] = scrub(row[f])
    elif table == "rules":
        for f in ("what_text", "why_text", "evidence"):
            row[f] = scrub(row[f])
    elif table in ("schema_doc_columns", "schema_doc_tables", "org_notes"):
        row = {k: (scrub(v) if isinstance(v, (str, list, dict)) else v) for k, v in row.items()}
    elif table == "uploads":
        row["filename"] = scrub(row["filename"])
    return row


def _jsonb_columns(conn: psycopg.Connection, table: str) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s AND data_type = 'jsonb'",
        (table,)).fetchall()}


def copy_table(local, cloud, table: str, m: Masker, dry_run: bool) -> int:
    with local.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
    rows = [r for r in (transform(table, r, m) for r in rows) if r is not None]
    if not rows or dry_run:
        return len(rows)
    jsonb = _jsonb_columns(cloud, table)
    cols = list(rows[0])
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})")
    values = [
        tuple(Jsonb(r[c]) if c in jsonb and r[c] is not None else r[c] for c in cols)
        for r in rows
    ]
    with cloud.cursor() as cur:
        cur.executemany(sql, values)
        cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"(SELECT COALESCE(MAX(id), 1) FROM {table}))")
    return len(rows)


def sync(local_url: str, cloud_url: str, replace: bool = False, dry_run: bool = False,
         discard_study: bool = False) -> dict[str, int]:
    local = psycopg.connect(local_url)
    cloud = psycopg.connect(cloud_url)
    try:
        repository.apply_schema(local)
        repository.apply_schema(cloud)
        existing = cloud.execute("SELECT count(*) FROM uploads").fetchone()[0]
        if existing and not replace and not dry_run:
            raise SystemExit(f"cloud database already has {existing} uploads; re-run with --replace to overwrite")
        started = cloud.execute(
            "SELECT count(*) FROM study_participants WHERE status <> 'not_started' AND code <> 'P00'").fetchone()[0]
        if replace and started and not discard_study and not dry_run:
            raise SystemExit(f"{started} study participant(s) have already started on the hosted database; "
                             "replacing would delete their results. Download them first (Study page), then "
                             "re-run with --discard-study-results if you really mean it.")
        if replace and not dry_run:
            cloud.execute("TRUNCATE " + ", ".join(TABLES) + ", chat_messages RESTART IDENTITY CASCADE")
        masker = Masker(local)
        counts = {t: copy_table(local, cloud, t, masker, dry_run) for t in TABLES}
        if dry_run:
            cloud.rollback()
        else:
            cloud.commit()
        return counts
    finally:
        local.close()
        cloud.close()


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    settings = load_settings()
    cloud_url = os.environ.get("CLOUD_DATABASE_URL", "").strip()
    if not cloud_url:
        raise SystemExit("set CLOUD_DATABASE_URL in backend/.env first")
    if "sslmode=" not in cloud_url:
        cloud_url += ("&" if "?" in cloud_url else "?") + "sslmode=require"
    dry = "--dry-run" in argv
    counts = sync(settings.db_url, cloud_url, replace="--replace" in argv, dry_run=dry,
                  discard_study="--discard-study-results" in argv)
    verb = "would copy" if dry else "copied"
    for table, n in counts.items():
        print(f"{verb} {n:>6} rows  {table}")


if __name__ == "__main__":
    main()
