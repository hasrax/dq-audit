"""Company workspaces: user-named folders that group an organisation's imports.

A workspace's slug is the source_client key used everywhere else, so a
workspace is purely an organising + naming layer: users create
"Sunrise Traders" and its audits are stored under "sunrise-traders".
Clients that already have uploads are adopted automatically, named from
config/client_labels.json (e.g. "Company A") so real names never show.
"""
from __future__ import annotations

import re

import psycopg
from psycopg.rows import dict_row

from app import aliases


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:48] or "company"


def sync_from_uploads(conn: psycopg.Connection) -> int:
    """Create a workspace for any client that has uploads but no workspace."""
    labels = aliases.load_client_labels()
    missing = [r[0] for r in conn.execute(
        "SELECT DISTINCT source_client FROM uploads u WHERE NOT EXISTS "
        "(SELECT 1 FROM workspaces w WHERE w.slug = u.source_client) ORDER BY 1").fetchall()]
    for slug in missing:
        name = labels.get(slug, slug)
        if conn.execute("SELECT 1 FROM workspaces WHERE name = %s", (name,)).fetchone():
            name = f"{name} ({slug})"
        replay = slug.endswith(aliases.REPLAY_SUFFIX)
        conn.execute("INSERT INTO workspaces (slug, name, kind, config_from) VALUES (%s, %s, %s, %s)",
                     (slug, name, "replay" if replay else "real",
                      slug[: -len(aliases.REPLAY_SUFFIX)] if replay else None))
    conn.commit()
    return len(missing)


KINDS = ("real", "study", "test", "replay")
KIND_LABEL = {"study": "study copy", "test": "test data", "replay": "replay"}
# Real data used only to demonstrate the system, never as evaluation evidence: Company G's
# monthly packs were built for the viva on 18 Oct 2026, after the data cut-off.
DEMO_ONLY = ("demo-company",)


def list_workspaces(conn: psycopg.Connection, kinds: tuple[str, ...] = KINDS) -> list[dict]:
    """Workspaces (of the given kinds) with audit counts, most recently
    active first. Only kind 'real' counts in real-data views and metrics."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT w.*, count(u.id) AS audit_count, max(u.id) AS last_upload_id, "
            "max(COALESCE(u.data_as_of, u.upload_date::date)) AS last_as_of "
            "FROM workspaces w LEFT JOIN uploads u ON u.source_client = w.slug "
            "WHERE w.kind = ANY(%s) GROUP BY w.id ORDER BY max(u.id) DESC NULLS LAST, w.name",
            (list(kinds),),
        ).fetchall()


def get(conn: psycopg.Connection, slug: str) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute("SELECT * FROM workspaces WHERE slug = %s", (slug,)).fetchone()


def study_workspace(conn: psycopg.Connection) -> str | None:
    row = conn.execute("SELECT slug FROM workspaces WHERE kind = 'study' ORDER BY id LIMIT 1").fetchone()
    return row[0] if row else None


def set_local_only(conn: psycopg.Connection, slug: str, local_only: bool) -> None:
    """Local-only workspaces (e.g. Companies D-G) are never copied to the
    hosted database."""
    conn.execute("UPDATE workspaces SET local_only = %s WHERE slug = %s", (local_only, slug))
    conn.commit()


def create_workspace(conn: psycopg.Connection, name: str, created_by: str | None = None,
                     description: str | None = None, kind: str = "real", local_only: bool = False) -> str:
    """Create a workspace with any display name; returns its (unique) slug."""
    name = " ".join(name.split())
    if not name:
        raise ValueError("Please enter a company name.")
    if conn.execute("SELECT 1 FROM workspaces WHERE lower(name) = lower(%s)", (name,)).fetchone():
        raise ValueError(f"A company called “{name}” already exists.")
    base = slug = slugify(name)
    n = 2
    while conn.execute("SELECT 1 FROM workspaces WHERE slug = %s", (slug,)).fetchone():
        slug, n = f"{base}-{n}", n + 1
    conn.execute("INSERT INTO workspaces (slug, name, description, created_by, kind, local_only) "
                 "VALUES (%s, %s, %s, %s, %s, %s)", (slug, name, description, created_by, kind, local_only))
    conn.commit()
    return slug


def rename_workspace(conn: psycopg.Connection, slug: str, name: str) -> None:
    name = " ".join(name.split())
    if not name:
        raise ValueError("Please enter a company name.")
    if conn.execute("SELECT 1 FROM workspaces WHERE lower(name) = lower(%s) AND slug <> %s",
                    (name, slug)).fetchone():
        raise ValueError(f"A company called “{name}” already exists.")
    conn.execute("UPDATE workspaces SET name = %s WHERE slug = %s", (name, slug))
    conn.commit()


def refresh_names(conn: psycopg.Connection) -> dict[str, str]:
    """Load workspace names into the label lookup used across the app."""
    names = dict(conn.execute("SELECT slug, name FROM workspaces").fetchall())
    aliases.set_workspace_names(names)
    aliases.set_config_parents(dict(conn.execute(
        "SELECT slug, config_from FROM workspaces WHERE config_from IS NOT NULL").fetchall()))
    return names
