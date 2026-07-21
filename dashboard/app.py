"""Layer 3 - Human triage UI (Streamlit).

Reads issues from the PostgreSQL repository and lets a reviewer mark each
one accepted / rejected / considered. Every decision is written straight
back to the DB with a timestamp -- these decisions are the project's
ground-truth labels (accepted = true issue, rejected = false positive /
LLM hallucination, considered = ambiguous), so the UI doubles as the
evaluation instrument.

Run:
    streamlit run dashboard/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the backend package importable without installing it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import streamlit as st

from app.db import repository

DIMENSIONS = ["completeness", "validity", "consistency", "uniqueness", "timeliness", "cross_table", "accuracy"]
SEVERITY_BADGE = {"high": "🔴 High", "medium": "🟠 Medium", "low": "🟡 Low"}
SOURCE_BADGE = {"rule": "⚙️ Rule (Type 1)", "llm": "🤖 LLM (Type 2)"}
STATUS_BADGE = {"flagged": "⚪ Flagged", "accepted": "✅ Accepted", "rejected": "❌ Rejected", "considered": "🤔 Considered"}
ALL = "(all)"


@st.cache_resource
def get_conn():
    return repository.connect()


def _filter(label: str, options: list[str], default: str = ALL):
    choices = [ALL, *options]
    return st.sidebar.selectbox(label, choices, index=choices.index(default))


def _none_if_all(value: str) -> str | None:
    return None if value == ALL else value


def main() -> None:
    st.set_page_config(page_title="DQ Audit — Triage", page_icon="🔍", layout="wide")
    conn = get_conn()

    st.title("🔍 Data Quality Audit — Triage")

    uploads = repository.list_uploads(conn)
    if not uploads:
        st.info("No audit runs found yet. Run `python -m app.db.persist <data_dir>` first.")
        return

    upload_labels = {
        f"#{u['id']} · {u['source_client']} · {u['pending_count']}/{u['issue_count']} pending": u
        for u in uploads
    }
    chosen = st.sidebar.selectbox("Audit run", list(upload_labels))
    upload = upload_labels[chosen]
    uid = upload["id"]

    st.sidebar.markdown("### Filters")
    f_source = _filter("Source", ["rule", "llm"])
    f_dimension = _filter("Dimension", DIMENSIONS)
    f_severity = _filter("Severity", ["high", "medium", "low"])
    f_status = _filter("Status", ["flagged", "accepted", "rejected", "considered"], default="flagged")

    counts = repository.triage_counts(conn, uid)
    st.sidebar.markdown("### Triage progress")
    c1, c2 = st.sidebar.columns(2)
    c1.metric("Accepted", counts.get("accepted", 0))
    c2.metric("Rejected", counts.get("rejected", 0))
    c3, c4 = st.sidebar.columns(2)
    c3.metric("Considered", counts.get("considered", 0))
    c4.metric("Pending", counts.get("flagged", 0))

    issues = repository.fetch_issues(
        conn, uid,
        source=_none_if_all(f_source),
        dimension=_none_if_all(f_dimension),
        severity=_none_if_all(f_severity),
        status=_none_if_all(f_status),
    )

    st.caption(
        f"Upload #{uid} — {upload['source_client']} · "
        f"{upload['issue_count']} issues total · showing {len(issues)}"
    )

    for issue in issues:
        _render_issue_card(conn, issue)


def _render_issue_card(conn, issue: dict) -> None:
    with st.container(border=True):
        head = st.columns([3, 2, 2, 2])
        location = issue["table_name"] + (f".{issue['column_name']}" if issue["column_name"] else "")
        head[0].markdown(f"**{location}**")
        head[1].markdown(SEVERITY_BADGE.get(issue["severity"], issue["severity"]))
        head[2].markdown(SOURCE_BADGE.get(issue["source"], issue["source"]))
        head[3].markdown(STATUS_BADGE.get(issue["status"], issue["status"]))

        st.markdown(f"**{issue['dimension']}** — {issue['description']}")
        if issue.get("recommendation"):
            st.markdown(f"💡 _{issue['recommendation']}_")

        ref = issue.get("row_reference") or {}
        meta = []
        if ref.get("count") is not None:
            meta.append(f"Affected rows: **{ref['count']}**")
        if ref.get("sample_rows"):
            meta.append("e.g. " + ", ".join(str(r) for r in ref["sample_rows"][:5]))
        if issue.get("fixable"):
            meta.append(f"Fixable: **{issue['fixable']}**")
        if meta:
            st.caption(" · ".join(meta))

        note = st.text_input("Note (optional)", value=issue.get("user_note") or "", key=f"note_{issue['id']}")
        b1, b2, b3, _ = st.columns([1, 1, 1, 4])
        if b1.button("✅ Accept", key=f"acc_{issue['id']}", use_container_width=True):
            repository.update_issue_status(conn, issue["id"], "accepted", note or None)
            st.rerun()
        if b2.button("❌ Reject", key=f"rej_{issue['id']}", use_container_width=True):
            repository.update_issue_status(conn, issue["id"], "rejected", note or None)
            st.rerun()
        if b3.button("🤔 Consider", key=f"con_{issue['id']}", use_container_width=True):
            repository.update_issue_status(conn, issue["id"], "considered", note or None)
            st.rerun()


if __name__ == "__main__":
    main()
