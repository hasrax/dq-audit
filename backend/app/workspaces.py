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
        conn.execute("INSERT INTO workspaces (slug, name) VALUES (%s, %s)", (slug, name))
    conn.commit()
    return len(missing)


def list_workspaces(conn: psycopg.Connection) -> list[dict]:
    """Workspaces with audit counts, most recently active first."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT w.*, count(u.id) AS audit_count, max(u.id) AS last_upload_id, "
            "max(COALESCE(u.data_as_of, u.upload_date::date)) AS last_as_of "
            "FROM workspaces w LEFT JOIN uploads u ON u.source_client = w.slug "
            "GROUP BY w.id ORDER BY max(u.id) DESC NULLS LAST, w.name"
        ).fetchall()


def create_workspace(conn: psycopg.Connection, name: str, created_by: str | None = None,
                     description: str | None = None) -> str:
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
    conn.execute("INSERT INTO workspaces (slug, name, description, created_by) VALUES (%s, %s, %s, %s)",
                 (slug, name, description, created_by))
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
    return names
