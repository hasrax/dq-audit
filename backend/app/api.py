"""FastAPI layer over the shared service functions.

The business logic lives in the app package (repository, engine, LLM
auditor, scoring); both the Streamlit dashboard and this HTTP API are
thin presentation layers over it. The API exposes the audit repository
for programmatic / integration use and satisfies the stated stack; the
dashboard uses the same service functions directly for a single-user
local deployment.

Run:
    uvicorn app.api:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.db import repository

app = FastAPI(title="DQ Audit API", version="1.0.0")


class TriageDecision(BaseModel):
    status: str
    note: str | None = None


def _connect():
    return repository.connect()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/uploads")
def list_uploads() -> dict:
    conn = _connect()
    try:
        return {"uploads": repository.list_uploads(conn)}
    finally:
        conn.close()


@app.get("/uploads/{upload_id}/issues")
def get_issues(
    upload_id: int,
    source: str | None = None,
    dimension: str | None = None,
    severity: str | None = None,
    status: str | None = None,
) -> dict:
    conn = _connect()
    try:
        issues = repository.fetch_issues(conn, upload_id, source, dimension, severity, status)
        return {"upload_id": upload_id, "count": len(issues), "issues": issues}
    finally:
        conn.close()


@app.get("/uploads/{upload_id}/score")
def get_score(upload_id: int) -> dict:
    conn = _connect()
    try:
        return repository.score_upload(conn, upload_id)
    finally:
        conn.close()


@app.post("/uploads/{upload_id}/issues/{issue_id}/triage")
def triage_issue(upload_id: int, issue_id: int, decision: TriageDecision) -> dict:
    conn = _connect()
    try:
        repository.update_issue_status(conn, issue_id, decision.status, decision.note)
        return {"ok": True, "issue_id": issue_id, "status": decision.status}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()
