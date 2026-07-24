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
import tempfile
from collections import Counter
from pathlib import Path

# Make the backend package importable without installing it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pandas as pd
import streamlit as st

from app.aliases import display_columns, load_aliases
from app.db import repository
from app.db.persist import persist_audit
from app.eval.triage_metrics import compute_triage_metrics
from app.export import issues_to_csv, score_to_csv
from app.llm.run_audit import audit_directory
from app.scoring.score import DEFAULT_K, DIMENSION_ALIASES, SEVERITY_POINTS

# Temporary working name — change these two lines to rebrand the whole app.
APP_NAME = "ClarityDQ"
APP_TAGLINE = "Explainable Data Quality Audit for SeefaERP"

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
.topbar {
  display:flex; align-items:baseline; gap:14px; flex-wrap:wrap;
  padding:2px 0 12px 0; margin-bottom:14px;
  border-bottom:2px solid rgba(37,99,235,0.35);
}
.topbar .brand { font-size:1.7rem; font-weight:800; letter-spacing:0.3px; }
.topbar .brand .mark { color:#2563eb; }
.topbar .tagline { font-size:0.9rem; opacity:0.6; }
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
    st.set_page_config(page_title=APP_NAME, page_icon="🔍", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    conn = get_conn()

    st.markdown(
        f'<div class="topbar"><span class="brand">🔍 <span class="mark">{APP_NAME}</span></span>'
        f'<span class="tagline">{APP_TAGLINE}</span></div>',
        unsafe_allow_html=True,
    )

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

    tab_upload, tab_triage, tab_scores = st.tabs(["⬆️ New audit", "🧾 Triage", "📊 Scores & trend"])

    with tab_upload:
        _render_upload()

    with tab_triage:
        issues = repository.fetch_issues(
            conn, uid,
            source=_none_if_all(f_source),
            dimension=_none_if_all(f_dimension),
            severity=_none_if_all(f_severity),
            status=_none_if_all(f_status),
        )
        aliases = load_aliases(upload["source_client"])
        head_l, head_r = st.columns([3, 1])
        head_l.caption(f"Showing {len(issues)} issue(s)")
        if issues:
            head_r.download_button(
                "⬇️ Export CSV",
                data=issues_to_csv(issues, aliases),
                file_name=f"issues_upload_{uid}.csv",
                mime="text/csv",
                width="stretch",
                help="Downloads the currently filtered issues (with triage status).",
            )
        for issue in issues:
            _render_issue_card(conn, issue, aliases)

    with tab_scores:
        _render_scores(conn, upload)


def _process_upload(files, client: str, run_llm: bool) -> dict:
    """Write uploaded CSVs to a temp dir and run the full audit pipeline."""
    tmp = Path(tempfile.mkdtemp(prefix="dq_upload_"))
    for f in files:
        (tmp / f.name).write_bytes(f.getvalue())

    llm_issues = None
    if run_llm:
        audited = audit_directory(tmp)
        llm_issues = [issue for table_issues in audited.values() for issue in table_issues]
    return persist_audit(tmp, source_client=client, llm_issues=llm_issues)


def _render_upload() -> None:
    st.markdown("#### Upload a SeefaERP CSV export and run an audit")
    st.caption(
        "Files are headerless CSV exports. Upload related tables together "
        "(e.g. a header + its detail table) so cross-table checks can run."
    )
    files = st.file_uploader("CSV file(s)", type=["csv"], accept_multiple_files=True)
    client = st.text_input("Client / source name", placeholder="e.g. hemas_2026_q3")
    run_llm = st.checkbox(
        "Also run AI contextual analysis (LLM) — slower; sends pseudonymized samples to the cloud model",
        value=False,
    )

    if st.button("▶️ Run audit", type="primary", disabled=not (files and client.strip())):
        with st.spinner("Profiling, running rules" + (", AI analysis" if run_llm else "") + ", scoring…"):
            try:
                result = _process_upload(files, client.strip(), run_llm)
            except Exception as exc:  # noqa: BLE001 -- surface any failure to the user
                st.error(f"Audit failed: {exc}")
                return
        st.success(
            f"Audit complete — upload #{result['upload_id']}: "
            f"{result['issues']} issues "
            f"({result['rule_issues']} rule + {result['llm_issues']} LLM) · "
            f"DQ score {result['overall_score']}/100"
        )
        st.info("Open the **Triage** or **Scores** tab and select this upload from the sidebar.")


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

    st.download_button(
        "⬇️ Export score summary (CSV)",
        data=score_to_csv(score),
        file_name=f"dq_score_upload_{upload['id']}.csv",
        mime="text/csv",
    )

    _render_dimension_breakdown(conn, upload, score)

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

    _render_triage_metrics(conn, upload)


def _pct_or_dash(value) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def _render_triage_metrics(conn, upload: dict) -> None:
    st.markdown("#### Triage-derived metrics (E3)")
    tm = compute_triage_metrics(repository.fetch_issues(conn, upload["id"]))
    if tm["reviewed"] == 0:
        st.caption("No issues triaged yet — accept/reject/consider issues in the Triage tab to populate these.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Empirical precision", _pct_or_dash(tm["empirical_precision"]))
    c2.metric("Rule precision", _pct_or_dash(tm["by_source"]["rule"]["empirical_precision"]))
    c3.metric("LLM hallucination", _pct_or_dash(tm["hallucination_rate"]))
    c4.metric("Ambiguity", _pct_or_dash(tm["ambiguity_rate"]))
    st.caption(
        f"{tm['reviewed']} of {tm['total']} issues reviewed. "
        "Precision counts only accept-vs-reject decisions (target ≥ 80%)."
    )


def _render_dimension_breakdown(conn, upload: dict, score: dict) -> None:
    st.markdown("#### By dimension — click to see what drives each score")
    aliases = load_aliases(upload["source_client"])
    # Only non-rejected issues count toward the score, so only they are shown.
    scored_issues = [i for i in repository.fetch_issues(conn, upload["id"]) if i.get("status") != "rejected"]

    by_dim: dict[str, list] = {}
    for issue in scored_issues:
        dim = DIMENSION_ALIASES.get(issue["dimension"], issue["dimension"])
        by_dim.setdefault(dim, []).append(issue)

    for label, key in sorted(DIMENSION_ROWS, key=lambda d: score[d[1]]):
        dim = key.removesuffix("_score")
        sub = score[key]
        dim_issues = by_dim.get(dim, [])
        badge = "🟢" if sub >= SCORE_GOOD else ("🟠" if sub >= SCORE_WARN else "🔴")

        with st.expander(f"{badge}  {label} — {sub:.0f}/100  ·  {len(dim_issues)} issue(s)"):
            st.markdown(_meter(label, sub), unsafe_allow_html=True)
            sev = Counter(i["severity"] for i in dim_issues)
            penalty = sum(SEVERITY_POINTS.get(i["severity"], 1) for i in dim_issues)
            st.caption(
                f"🔴 {sev.get('high', 0)} high · 🟠 {sev.get('medium', 0)} medium · "
                f"⚪ {sev.get('low', 0)} low  →  penalty {penalty} pts  →  "
                f"score = 100 × {DEFAULT_K:g}/({DEFAULT_K:g}+{penalty}) = {sub:.0f}"
            )
            if dim_issues:
                st.dataframe(
                    [
                        {
                            "Location": i["table_name"] + (
                                "." + display_columns(aliases, i["table_name"], i["column_name"])
                                if i["column_name"] else ""
                            ),
                            "Severity": i["severity"],
                            "Source": i["source"],
                            "Fixable": i.get("fixable") or "",
                            "Status": i.get("status"),
                            "What was found": (i.get("description") or "")[:200],
                        }
                        for i in sorted(dim_issues, key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x["severity"], 3))
                    ],
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.caption("No issues in this dimension — it scores a perfect 100.")


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


def _render_issue_card(conn, issue: dict, aliases: dict | None = None) -> None:
    severity = issue["severity"]
    sev_color = SEVERITY_COLOR.get(severity, "#667085")
    src_label, src_color = SOURCE_STYLE.get(issue["source"], (issue["source"].upper(), "#667085"))
    col_display = display_columns(aliases or {}, issue["table_name"], issue["column_name"])
    location = issue["table_name"] + (f".{col_display}" if col_display else "")

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
    if b1.button("✅ Accept", key=f"acc_{issue['id']}", width="stretch"):
        _decide(conn, issue["id"], "accepted")
    if b2.button("❌ Reject", key=f"rej_{issue['id']}", width="stretch"):
        _decide(conn, issue["id"], "rejected")
    if b3.button("🤔 Consider", key=f"con_{issue['id']}", width="stretch"):
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
