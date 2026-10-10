"""Build the study workspace: a copy of one real audit's results.

Participants work on "Study – <company>", a copy of one real audit
(profile, relationships, rules, issues with explanations, score). There
is exactly one study workspace: building a new copy removes the old one.
No source files are needed, so it loads fast on a small hosted server.
Every issue starts as "to review" with no reviewer notes, and the copy
reuses the parent company's column names, privacy masks and verified
schema docs through workspaces.config_from. Rebuilding replaces the
previous copy (and with it any participant decisions on it).

Study-dataset curation (agreed 10 Oct 2026): pairwise ordering rules
("X is normally not more than Y") are left out of the copy. With real column names
most of them read as nonsense (e.g. "Price <= InvoiceNo"), which would
measure detection noise instead of usability. This is a fixed,
mechanical filter -- no issue-by-issue judgement -- and the ordering
noise itself is reported as a transferability finding (T6). The DQ
score is recomputed from the issues that were kept.

Usage:
    python -m app.study.setup --from-upload=<upload id> [--name="Study – Company D"]
"""
from __future__ import annotations

import sys

import psycopg
from psycopg.types.json import Jsonb

from app.aliases import client_label
from app.db import repository
from app.workspaces import refresh_names, slugify
_SKIP = {"id", "upload_id"}
STUDY_SKIP_TEMPLATES = ("consistency_ordering_violation",)


def _columns(conn: psycopg.Connection, table: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position",
        (table,)).fetchall() if r[0] not in _SKIP]


def _copy(conn: psycopg.Connection, table: str, src: int, dst: int, override: dict | None = None,
          where: str = "", params: tuple = ()) -> dict[int, int]:
    """Copy one upload's rows of `table` to another upload; returns {old id: new id}."""
    cols = _columns(conn, table)
    override = override or {}
    select = ", ".join(override.get(c, c) for c in cols)
    rows = conn.execute(f"SELECT id, {select} FROM {table} WHERE upload_id = %s {where} ORDER BY id",
                        (src, *params)).fetchall()
    ids: dict[int, int] = {}
    for row in rows:
        new = conn.execute(
            f"INSERT INTO {table} (upload_id, {', '.join(cols)}) VALUES (%s, {', '.join(['%s'] * len(cols))}) "
            "RETURNING id", (dst, *(Jsonb(v) if isinstance(v, (dict, list)) else v for v in row[1:]))).fetchone()[0]
        ids[row[0]] = new
    return ids


def build_study_workspace(conn: psycopg.Connection, from_upload: int, name: str | None = None,
                          slug: str | None = None, replace_others: bool = True) -> int:
    src = conn.execute("SELECT source_client, row_count, data_as_of, upload_date FROM uploads WHERE id = %s",
                       (from_upload,)).fetchone()
    if src is None:
        raise SystemExit(f"upload #{from_upload} does not exist")
    parent, row_count, as_of, uploaded = src
    refresh_names(conn)
    name = name or f"Study – {client_label(parent)}"
    slug = slug or "study-" + slugify(client_label(parent))
    if replace_others:   # one study at a time: retire any other study copy
        old = [r[0] for r in conn.execute("SELECT slug FROM workspaces WHERE kind = 'study' AND slug <> %s",
                                          (slug,)).fetchall()]
        for o in old:
            conn.execute("DELETE FROM uploads WHERE source_client = %s", (o,))
            conn.execute("DELETE FROM workspaces WHERE slug = %s", (o,))
    conn.execute(
        "INSERT INTO workspaces (slug, name, kind, config_from) VALUES (%s, %s, 'study', %s) "
        "ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name, kind = 'study', config_from = EXCLUDED.config_from",
        (slug, name, parent))
    conn.execute("DELETE FROM uploads WHERE source_client = %s", (slug,))
    dst = conn.execute(
        "INSERT INTO uploads (filename, source_client, row_count, data_as_of) VALUES (%s, %s, %s, %s) RETURNING id",
        (f"Study copy of audit #{from_upload}", slug, row_count, as_of or uploaded.date())).fetchone()[0]

    for table in ("profiles", "relationships"):
        _copy(conn, table, from_upload, dst)
    skip = (list(STUDY_SKIP_TEMPLATES),)
    rule_ids = _copy(conn, "rules", from_upload, dst, {"source_client": f"'{slug}'"},
                     where="AND template <> ALL(%s)", params=skip)
    # Fresh, unreviewed issues: participants start from a clean state.
    issue_ids = _copy(conn, "issues", from_upload, dst, {
        "status": "'flagged'", "user_note": "NULL", "reviewed_at": "NULL", "reviewed_by": "NULL",
        "previous_issue_id": "NULL", "occurrence_count": "1", "rule_ref": "NULL",
    }, where="AND (rule_id IS NULL OR rule_id <> ALL(%s))", params=skip)
    # re-link each copied issue to its copied rule (by issue id, so ids can't collide)
    for old_issue, old_rule in conn.execute(
            "SELECT id, rule_ref FROM issues WHERE upload_id = %s AND rule_ref IS NOT NULL", (from_upload,)).fetchall():
        if old_rule in rule_ids and old_issue in issue_ids:
            conn.execute("UPDATE issues SET rule_ref = %s WHERE id = %s", (rule_ids[old_rule], issue_ids[old_issue]))
    repository.score_upload(conn, dst)   # the score matches the issues participants see
    conn.commit()
    return dst


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    opts = dict(a[2:].split("=", 1) for a in argv if a.startswith("--") and "=" in a)
    if "from-upload" not in opts:
        print(__doc__)
        raise SystemExit(1)
    conn = repository.connect()
    repository.apply_schema(conn)
    dst = build_study_workspace(conn, int(opts["from-upload"]), opts.get("name"))
    n = conn.execute("SELECT count(*) FROM issues WHERE upload_id = %s", (dst,)).fetchone()[0]
    name = conn.execute("SELECT w.name FROM workspaces w JOIN uploads u ON u.source_client = w.slug "
                        "WHERE u.id = %s", (dst,)).fetchone()[0]
    print(f"study workspace '{name}' rebuilt as upload #{dst} with {n} issues")


if __name__ == "__main__":
    main()
