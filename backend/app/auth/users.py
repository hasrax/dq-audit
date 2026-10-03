"""Simple login: bcrypt-hashed users with two roles (Layer 3).

  admin    -- everything a reviewer can do, plus running audits, LLM
              provider/model selection, organisation notes, rule
              enable/disable and schema verification;
  reviewer -- triage, chat and export.

Create the first admin from the command line:
    python -m app.auth.users create <username> --role=admin
(the password is prompted for, never passed on the command line).
"""
from __future__ import annotations

import getpass
import sys

import bcrypt
import psycopg
from psycopg.rows import dict_row

ROLES = ("admin", "reviewer")
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def create_user(conn: psycopg.Connection, username: str, password: str, role: str = "reviewer") -> int:
    username = username.strip()
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if not username:
        raise ValueError("username is required")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    row = conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s) RETURNING id",
        (username, hash_password(password), role),
    ).fetchone()
    conn.commit()
    return row[0]


def authenticate(conn: psycopg.Connection, username: str, password: str) -> dict | None:
    """The user (id, username, role) if the password matches, else None."""
    with conn.cursor(row_factory=dict_row) as cur:
        user = cur.execute(
            "SELECT id, username, role, password_hash FROM users WHERE username = %s", (username.strip(),)
        ).fetchone()
    if user is None or not verify_password(password, user["password_hash"]):
        return None
    return {"id": user["id"], "username": user["username"], "role": user["role"]}


def count_users(conn: psycopg.Connection) -> int:
    return conn.execute("SELECT count(*) FROM users").fetchone()[0]


def list_users(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute("SELECT id, username, role, created_at FROM users ORDER BY username").fetchall()


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 2 or argv[0] != "create":
        print(__doc__)
        raise SystemExit(1)
    username = argv[1]
    role = next((a.split("=", 1)[1] for a in argv if a.startswith("--role=")), "reviewer")
    password = getpass.getpass(f"Password for {username}: ")
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("passwords do not match")

    from app.db import repository

    conn = repository.connect()
    repository.apply_schema(conn)
    try:
        user_id = create_user(conn, username, password, role)
    except (ValueError, psycopg.errors.UniqueViolation) as exc:
        raise SystemExit(f"could not create user: {exc}") from exc
    print(f"created {role} '{username}' (id {user_id})")


if __name__ == "__main__":
    main()
