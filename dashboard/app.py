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

import html
import sys
from pathlib import Path

# Make the backend package importable without installing it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pandas as pd
import streamlit as st

from app.db import repository

DIMENSIONS = ["completeness", "validity", "consistency", "uniqueness", "timeliness", "cross_table", "accuracy"]
STATUSES = ["flagged", "accepted", "rejected", "considered"]
STATUS_LABEL = {"flagged": "⚪ Flagged", "accepted": "✅ Accepted", "rejected": "❌ Rejected", "considered": "🤔 Considered"}
SEVERITY_COLOR = {"high": "#d92d20", "medium": "#dd6b20", "low": "#667085"}
SOURCE_STYLE = {"rule": ("⚙️ RULE · Type 1", "#2563eb"), "llm": ("🤖 LLM · Type 2", "#7c3aed")}
ALL = "(all)"

CSS = """
<style>
.issue-card {
  border: 1px solid rgba(128,128,128,0.25);
  border-left: 6px solid var(--sev, #667085);
  border-radius: 10px;
  padding: 14px 18px 12px 18px;
  margin: 2px 0 6px 0;
  background: var(--tint, rgba(128,128,128,0.03));
}
.issue-head { display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; }
.issue-title { font-size: 1.06rem; font-weight: 700; letter-spacing:0.2px; }
.issue-desc { margin-top: 8px; font-size: 0.96rem; line-height: 1.45; }
.issue-rec  { margin-top: 6px; font-size: 0.88rem; opacity: 0.75; font-style: italic; }
.issue-meta { margin-top: 8px; font-size: 0.78rem; opacity: 0.65; }
.pills { display:flex; gap:6px; flex-wrap:wrap; }
.pill {
  display:inline-block; padding: 2px 10px; border-radius: 999px;
  font-size: 0.72rem; font-weight: 700; color: #fff; white-space: nowrap;
}
.pill-outline {
  background: transparent; color: var(--fg, inherit);
  border: 1px solid rgba(128,128,128,0.45); font-weight: 600;
}
.hero { text-align:center; padding: 8px 0 6px 0; }
.hero-num { font-size: 4.2rem; font-weight: 800; line-height: 1; }
.hero-max { font-size: 1.4rem; font-weight: 600; opacity: 0.5; }
.hero-label { font-size: 0.9rem; opacity: 0.7; letter-spacing: 0.4px; text-transform: uppercase; }
.meter { margin: 10px 0; }
.meter-top { display:flex; justify-content:space-between; font-size:0.9rem; margin-bottom:4px; }
.meter-label { font-weight:600; }
.meter-val { font-weight:800; }
.meter-track { height:12px; border-radius:999px; background:rgba(128,128,128,0.18); overflow:hidden; }
.meter-fill { height:100%; border-radius:999px; }
</style>
"""

SCORE_GOOD, SCORE_WARN = 80, 50
TREND_HUE = "#3b82f6"
DIMENSION_ROWS = [
    ("Completeness", "completeness_score"),
    ("Validity", "validity_score"),
    ("Consistency", "consistency_score"),
    ("Uniqueness", "uniqueness_score"),
    ("Timeliness", "timeliness_score"),
    ("Cross-table", "cross_table_score"),
]


def _status_color(value: float) -> str:
    if value >= SCORE_GOOD:
        return "#16a34a"
    if value >= SCORE_WARN:
        return "#d97706"
    return "#dc2626"


@st.cache_resource
def get_conn():
    return repository.connect()


def _filter(label: str, options: list[str], default: str = ALL):
    choices = [ALL, *options]
    return st.sidebar.selectbox(label, choices, index=choices.index(default))


def _none_if_all(value: str) -> str | None:
    return None if value == ALL else value


def _pill(text: str, color: str) -> str:
    return f'<span class="pill" style="background:{color}">{html.escape(text)}</span>'


def _outline_pill(text: str) -> str:
    return f'<span class="pill pill-outline">{html.escape(text)}</span>'


def main() -> None:
    st.set_page_config(page_title="DQ Audit — Triage", page_icon="🔍", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
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
    f_status = _filter("Status", STATUSES, default="flagged")

    counts = repository.triage_counts(conn, uid)
    total = upload["issue_count"]
    reviewed = sum(counts.get(s, 0) for s in ("accepted", "rejected", "considered"))

    _render_header(upload, counts, total, reviewed)

    st.sidebar.markdown("### Triage progress")
    s1, s2 = st.sidebar.columns(2)
    s1.metric("✅ Accepted", counts.get("accepted", 0))
    s2.metric("❌ Rejected", counts.get("rejected", 0))
    s3, s4 = st.sidebar.columns(2)
    s3.metric("🤔 Considered", counts.get("considered", 0))
    s4.metric("⚪ Pending", counts.get("flagged", 0))

    tab_triage, tab_scores = st.tabs(["🧾 Triage", "📊 Scores & trend"])

    with tab_triage:
        issues = repository.fetch_issues(
            conn, uid,
            source=_none_if_all(f_source),
            dimension=_none_if_all(f_dimension),
            severity=_none_if_all(f_severity),
            status=_none_if_all(f_status),
        )
        st.caption(f"Showing {len(issues)} issue(s)")
        for issue in issues:
            _render_issue_card(conn, issue)

    with tab_scores:
        _render_scores(conn, upload)


def _render_scores(conn, upload: dict) -> None:
    # Recompute live so the score reflects current triage (rejected issues
    # are excluded); this also refreshes the stored value for the trend.
    score = repository.score_upload(conn, upload["id"])
    overall = score["overall_score"]
    color = _status_color(overall)

    st.markdown(
        f'<div class="hero"><div class="hero-num" style="color:{color}">'
        f'{overall:.0f}<span class="hero-max">/100</span></div>'
        f'<div class="hero-label">Overall Data Quality Score</div></div>',
        unsafe_allow_html=True,
    )
    st.caption("Excludes issues rejected in triage. Recomputes as you review.")

    st.markdown("#### By dimension — worst first")
    meters = "".join(
        _meter(label, score[key])
        for label, key in sorted(DIMENSION_ROWS, key=lambda d: score[d[1]])
    )
    st.markdown(meters, unsafe_allow_html=True)

    st.markdown("#### Trend across uploads")
    trend = repository.fetch_score_trend(conn, upload["source_client"])
    if len(trend) >= 2:
        df = pd.DataFrame(
            [{"upload": f"#{r['upload_id']}", "DQ score": float(r["overall_score"])} for r in trend]
        ).set_index("upload")
        st.line_chart(df, y="DQ score", color=TREND_HUE)
    else:
        st.caption(
            f"Only one scored upload for **{upload['source_client']}** so far — "
            "the trend line appears once another upload for this client is audited "
            "(e.g. a later export, or re-auditing after fixes)."
        )


def _meter(label: str, value: float) -> str:
    color = _status_color(value)
    return (
        f'<div class="meter"><div class="meter-top">'
        f'<span class="meter-label">{html.escape(label)}</span>'
        f'<span class="meter-val" style="color:{color}">{value:.0f}</span></div>'
        f'<div class="meter-track"><div class="meter-fill" '
        f'style="width:{max(0, min(100, value))}%; background:{color}"></div></div></div>'
    )


def _render_header(upload: dict, counts: dict, total: int, reviewed: int) -> None:
    st.caption(f"Upload #{upload['id']} — **{upload['source_client']}** · {total} issues")
    if total:
        st.progress(reviewed / total, text=f"{reviewed} of {total} reviewed")


def _render_issue_card(conn, issue: dict) -> None:
    severity = issue["severity"]
    sev_color = SEVERITY_COLOR.get(severity, "#667085")
    src_label, src_color = SOURCE_STYLE.get(issue["source"], (issue["source"].upper(), "#667085"))
    location = issue["table_name"] + (f".{issue['column_name']}" if issue["column_name"] else "")

    pills = _pill(severity.upper(), sev_color) + _pill(src_label, src_color) + _outline_pill(issue["dimension"])
    if issue["status"] != "flagged":
        pills += _outline_pill(STATUS_LABEL.get(issue["status"], issue["status"]))

    ref = issue.get("row_reference") or {}
    meta_bits = []
    if ref.get("count") is not None:
        meta_bits.append(f"Affected rows: <b>{ref['count']}</b>")
    if ref.get("sample_rows"):
        meta_bits.append("e.g. rows " + ", ".join(str(r) for r in ref["sample_rows"][:5]))
    if issue.get("fixable"):
        meta_bits.append(f"Fixable: <b>{html.escape(issue['fixable'])}</b>")

    rec_html = f'<div class="issue-rec">💡 {html.escape(issue["recommendation"])}</div>' if issue.get("recommendation") else ""
    tint = f"rgba({int(sev_color[1:3],16)},{int(sev_color[3:5],16)},{int(sev_color[5:7],16)},0.05)"

    st.markdown(
        f'''<div class="issue-card" style="--sev:{sev_color}; --tint:{tint}">
  <div class="issue-head">
    <span class="issue-title">{html.escape(location)}</span>
    <span class="pills">{pills}</span>
  </div>
  <div class="issue-desc">{html.escape(issue["description"] or "")}</div>
  {rec_html}
  <div class="issue-meta">{" · ".join(meta_bits)}</div>
</div>''',
        unsafe_allow_html=True,
    )

    b1, b2, b3, spacer = st.columns([1, 1, 1, 5])
    if b1.button("✅ Accept", key=f"acc_{issue['id']}", use_container_width=True):
        _decide(conn, issue["id"], "accepted")
    if b2.button("❌ Reject", key=f"rej_{issue['id']}", use_container_width=True):
        _decide(conn, issue["id"], "rejected")
    if b3.button("🤔 Consider", key=f"con_{issue['id']}", use_container_width=True):
        _decide(conn, issue["id"], "considered")

    with st.expander("📝 Add a note"):
        note = st.text_area(
            "Note", value=issue.get("user_note") or "", key=f"note_{issue['id']}",
            label_visibility="collapsed", placeholder="Optional context for this decision…",
        )
        if st.button("Save note", key=f"savenote_{issue['id']}"):
            repository.update_issue_status(conn, issue["id"], issue["status"], note or None)
            st.rerun()


def _decide(conn, issue_id: int, status: str) -> None:
    # preserve any note already typed for this issue
    note = st.session_state.get(f"note_{issue_id}") or None
    repository.update_issue_status(conn, issue_id, status, note)
    st.rerun()


if __name__ == "__main__":
    main()
