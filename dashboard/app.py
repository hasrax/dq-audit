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
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

# Make the backend package importable without installing it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
# dashboard/theme.py. Appended, not prepended: first on the path, this
# folder's app.py would shadow the backend's `app` package.
sys.path.append(str(Path(__file__).resolve().parent))

import pandas as pd
import plotly.graph_objects as go
import psycopg
from psycopg.rows import dict_row
import streamlit as st

import theme as ui

from app.aliases import client_label, display_columns, load_client_labels
from app.db import repository
from app.db.persist import persist_audit
import json

from app.eval.complementarity import real_complementarity
from app.eval.figures import figure_png_bytes
from app.eval.triage_metrics import compute_triage_metrics
from app.export import issues_to_csv, issues_to_xlsx, score_to_csv
from app.llm.run_audit import audit_directory
from app.scoring.score import load_weights, score_contributions
from app.scoring.score import DEFAULT_K, DIMENSION_ALIASES, SEVERITY_POINTS
from app.schema_docs import store as docs_store
from app.schema_docs.generate import document_directory
from app.schema_docs.prompt import MODULES
from app.rules.describe import CATEGORY_LABEL
from app.monitoring import recurrence
from app.llm import feedback
from app.chat import assistant
from app.chat import threads as chat_threads
from app.reports.audit_report import build_markdown as standard_report_md
from app.reports.render import to_docx, to_pdf
from app.eval import live as live_eval
from app.auth import users as auth
from app.llm import selection as llm_selection
from app.aliases import base_client
from app import workspaces as ws
from app.study import participants as sp
from app.study.setup import build_study_workspace

# Temporary working name — change these two lines to rebrand the whole app.
APP_NAME = "ClarityDQ"
APP_TAGLINE = "Explainable Data Quality Audit for SME ERP data"

ABOUT_MD = f"""
**{APP_NAME}** is an explainable data quality audit tool for SME ERP
exports. It reads CSV/Excel files (no database access) and flags issues in
plain language.

**Two-error taxonomy — how issues are found:**
- ⚙️ **Rules (Type 1)** — deterministic checks over *every row*:
  completeness, validity, consistency, uniqueness, timeliness, and
  cross-table referential integrity.
- 🤖 **LLM (Type 2)** — an AI auditor on a small *pseudonymized* sample
  catches *contextual* problems rules can't express (implausible discounts,
  ×100 quantities, price outliers, near-duplicates).

The two are **complementary**, not redundant — see the complementarity
figure on the Scores tab.

**DQ score** is severity-weighted (not a flat average), so a critical
cross-table break outweighs a handful of trivial nulls.

**Triage** — mark each issue accept / reject / consider. Those decisions
become the ground truth that measures the tool's own precision.
"""

DIMENSIONS = ["completeness", "validity", "consistency", "uniqueness", "timeliness", "cross_table", "accuracy"]
STATUSES = ["flagged", "accepted", "rejected", "considered"]
STATUS_LABEL = {"flagged": "⚪ Flagged", "accepted": "✅ Accepted", "rejected": "❌ Rejected", "considered": "🤔 Considered"}
SEVERITY_COLOR = ui.SEVERITY
SOURCE_STYLE = ui.SOURCE
ALL = "(all)"
TREND_STYLE = ui.TREND
TRENDS = ["new", "recurring", "worsening", "improving", "stable"]



SCORE_GOOD, SCORE_WARN = 80, 50
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
        return ui.SAGE
    if value >= SCORE_WARN:
        return ui.MUSTARD
    return ui.CORAL


@st.cache_resource
def get_conn():
    # Autocommit: this connection lives as long as the server. Without it,
    # the first SELECT opens a transaction that never ends ("idle in
    # transaction"), which holds locks that block schema migrations.
    conn = repository.connect()
    conn.autocommit = True
    return conn


def _db():
    """The cached connection, reconnected once if the server dropped it
    (the hosted database suspends after a few idle minutes)."""
    conn = get_conn()
    try:
        conn.execute("SELECT 1")
        return conn
    except psycopg.OperationalError:
        get_conn.clear()
        return get_conn()


def _aliases(conn, source_client: str) -> dict:
    return docs_store.display_aliases(conn, source_client)


def _none_if_all(value: str) -> str | None:
    return None if value == ALL else value


def _pill(text: str, color: str) -> str:
    return ui.pill(text, color)


def _outline_pill(text: str) -> str:
    return f'<span class="pill-outline">{html.escape(text)}</span>'


def _current_user() -> dict | None:
    return st.session_state.get("user")


def _username() -> str | None:
    user = _current_user()
    return user["username"] if user else None


def _is_participant() -> bool:
    user = _current_user()
    return bool(user and user["role"] == "participant")


def _study_log(event: str, detail: dict | None = None) -> None:
    """Record what a study participant did (system verification of tasks)."""
    if _is_participant():
        sp.log_event(_db(), _username(), event, detail)


def _sus_url(code: str) -> str:
    url = os.environ.get("SUS_FORM_PREFILL_URL", "").strip()
    return url.replace("P00", code) if url else ""


def _is_admin() -> bool:
    user = _current_user()
    return bool(user and user["role"] == "admin")


def _render_login(conn) -> None:
    _, staff, gap, study, _ = st.columns([0.35, 1.2, 0.12, 1, 0.35])
    with study.container(border=True):
        st.markdown("### Taking part in the study?")
        st.caption("Enter the code you were given (for example P01).")
        with st.form("study_login"):
            code = st.text_input("Your code", max_chars=3, placeholder="P01")
            go = st.form_submit_button("Start", type="primary", width="stretch")
        if go:
            ok, msg = sp.check_code(conn, code)
            if ok:
                code = sp.normalise_code(code)
                st.session_state["user"] = {"id": 0, "username": code, "role": "participant"}
                st.session_state["username"] = code
                st.rerun()
            st.error(msg)
            if "already been used" in msg and _sus_url(sp.normalise_code(code)):
                st.markdown(f"If you haven't filled in the questionnaire yet: "
                            f"[open it here]({_sus_url(sp.normalise_code(code))})")
    with staff:
        st.markdown("### Sign in")
        st.caption("Use the account your administrator created for you.")
        if auth.count_users(conn) == 0:
            st.info(
                "No accounts exist yet. Create the first administrator from a terminal:\n\n"
                "`cd backend && python -m app.auth.users create admin --role=admin`"
            )
        with st.form("login"):
            username = st.text_input("Username", autocomplete="username")
            password = st.text_input("Password", type="password", autocomplete="current-password")
            submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
        if submitted:
            user = auth.authenticate(conn, username, password)
            if user:
                st.session_state["user"] = user
                st.session_state["username"] = user["username"]
                st.rerun()
            st.error("That username and password don't match. Please try again.")


def _render_account_sidebar() -> None:
    user = _current_user()
    role = {"admin": "Administrator", "participant": "Study participant"}.get(user["role"], "Reviewer")
    with st.sidebar.container(border=True):
        name_col, out_col = st.columns([3, 1.4], vertical_alignment="center")
        name_col.markdown(f"**{html.escape(user['username'])}**  \n:gray[{role}]")
        if out_col.button("", key="sign_out", icon=":material/logout:", help="Sign out", width="stretch"):
            for k in ("user", "username"):
                st.session_state.pop(k, None)
            st.rerun()


def main() -> None:
    st.set_page_config(page_title=APP_NAME, page_icon="🔍", layout="wide")
    ui.apply(st.context.theme.type)  # follows the viewer's Light / Dark setting
    st.markdown(ui.css(), unsafe_allow_html=True)
    try:
        conn = _db()
        repository.apply_schema(conn)
    except psycopg.OperationalError:
        st.error("We can't reach the database right now. Please wait a minute and refresh the page.",
                 icon=":material/cloud_off:")
        return

    brand = (f'<div class="brand"><div class="logo"></div><div><div class="name">{APP_NAME}</div>'
             f'<div class="tag">{APP_TAGLINE}</div></div></div>')
    if _current_user() is None:
        st.markdown(brand, unsafe_allow_html=True)
        _render_login(conn)
        return
    static = Path(__file__).resolve().parent / "static"
    st.logo(str(static / f"logo-{ui.MODE}.svg"), size="large", icon_image=str(static / "icon.svg"))
    if _is_participant():
        _participant_main(conn)
        return
    admin = _is_admin()

    ws.sync_from_uploads(conn)
    ws.refresh_names(conn)
    # study / test / replay copies are for admins only, and clearly labelled
    companies = ws.list_workspaces(conn, kinds=ws.KINDS if admin else ("real",))
    slug = _company_picker(conn, companies, admin)

    upload = None
    if slug != ALL_COMPANIES:
        uploads = repository.list_uploads(conn, source_client=slug)
        if uploads:
            by_id = {u["id"]: u for u in uploads}
            picked = st.session_state.get(f"audit_{slug}")
            upload = by_id.get(picked, uploads[0])

    navigation = _build_navigation(conn, slug, upload, admin)
    _render_sidebar_nav(admin)
    if upload is not None:
        counts = repository.triage_counts(conn, upload["id"])
        st.sidebar.markdown('<div class="sidebar-section">Review progress</div>', unsafe_allow_html=True)
        s1, s2 = st.sidebar.columns(2)
        s1.metric("To review", counts.get("flagged", 0))
        s2.metric("Reviewed", sum(counts.get(k, 0) for k in ("accepted", "rejected", "considered")))
    with st.sidebar.expander(f"About {APP_NAME}", icon=":material/info:"):
        st.markdown(ABOUT_MD)
    _render_account_sidebar()

    if st.session_state.get("flash"):
        st.toast(st.session_state.pop("flash"), icon=":material/check_circle:")
    # floating import action; not on Ask, where it would cover the message box
    if admin and navigation.url_path != "ask" and st.button("＋  Import", key="fab",
                                                            help="Upload a new export and run an audit"):
        _import_dialog(conn)
    navigation.run()


ALL_COMPANIES = "__all__"


def _device() -> tuple[str, str]:
    try:
        return sp.parse_user_agent(st.context.headers.get("User-Agent"))
    except Exception:  # noqa: BLE001 -- no headers in tests
        return "desktop", "Other"


def _participant_main(conn) -> None:
    """The unmoderated usability test: consent -> 5 tasks -> questionnaire.
    Participants work on the study copy only; nothing they do reaches real data."""
    code = _username()
    ws.refresh_names(conn)
    p = sp.get(conn, code)
    if p is None:
        st.error("This study code is no longer valid.")
        st.session_state.pop("user", None)
        return
    device, browser = _device()
    if device != "desktop":
        st.warning("This study works best on a laptop or computer.", icon=":material/computer:")
    if p["consent_at"] is None:
        _study_consent(conn, code, device, browser)
        return
    task = sp.current_task(conn, code)
    if p["status"] == "finished" or task is None:
        _study_finished(code)
        return
    slug = ws.study_workspace(conn)
    uploads = repository.list_uploads(conn, source_client=slug) if slug else []
    if not uploads:
        st.error("The study isn't ready yet. Please contact the researcher.")
        return
    _study_task_panel(conn, code, task)
    navigation = _build_navigation(conn, slug, uploads[0], admin=False, participant=True)
    _render_sidebar_nav(False)
    _render_account_sidebar()
    navigation.run()


def _study_consent(conn, code: str, device: str, browser: str) -> None:
    _, mid, _ = st.columns([0.5, 2, 0.5])
    with mid:
        st.markdown('<div class="page-title">Welcome — thank you for helping</div>', unsafe_allow_html=True)
        st.markdown(
            "- **ClarityDQ** checks a company's business data for mistakes — missing, wrong or inconsistent "
            "values — and explains each problem in plain English.\n"
            "- You'll try **5 short tasks** on a copy of anonymised audit results, then answer a short "
            "questionnaire.\n"
            "- Nothing you do here changes any real data. There are no right or wrong answers — we are testing "
            "the system, not you."
        )
        st.info("This takes about 10 minutes. Please use a laptop or computer.", icon=":material/schedule:")
        agree = st.checkbox("I agree to take part. My answers are anonymous and may be used in a university "
                            f"research project. (Your code: {code})")
        if st.button("Start", type="primary", disabled=not agree, icon=":material/play_arrow:", key="study_start"):
            sp.give_consent(conn, code, device, browser)
            st.rerun()


def _study_task_panel(conn, code: str, task: int) -> None:
    title, text = sp.TASKS[task]
    with st.sidebar.container(border=True, key="study_panel"):
        st.markdown(f'<div class="sidebar-section">Study · task {task} of {sp.N_TASKS}</div>',
                    unsafe_allow_html=True)
        st.progress((task - 1) / sp.N_TASKS)
        st.markdown(f"**{title}**")
        st.markdown(text)
        answer = None
        if task == 1:
            answer = st.text_area("In one sentence, what is wrong?", key=f"study_answer_{code}", height=90)
        if st.button("Done", type="primary", width="stretch", key=f"study_done_{task}", icon=":material/check:"):
            sp.end_task(conn, code, task, "done", answer)
            st.rerun()
        if st.button("I couldn't do this", width="stretch", key=f"study_cant_{task}"):
            sp.end_task(conn, code, task, "couldnt", answer)
            st.rerun()


def _study_finished(code: str) -> None:
    _, mid, _ = st.columns([0.5, 2, 0.5])
    with mid:
        st.markdown('<div class="page-title">Thank you — one last step</div>', unsafe_allow_html=True)
        url = _sus_url(code)
        if url:
            st.link_button("Last step: short questionnaire (about 4 minutes)", url, type="primary",
                           icon=":material/assignment:", width="stretch")
            st.caption(f"It opens in a new tab with your code ({code}) already filled in.")
        else:
            st.info("The questionnaire link hasn't been set up yet — please tell the researcher your code "
                    f"({code}).")
        st.success("Thank you for taking part! You can close this page once you've sent the questionnaire.")


def _render_study_admin(conn) -> None:
    st.markdown('<div class="page-title">Study</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">The unmoderated usability test. Participants sign in with a code, do 5 tasks '
                'on the study copy, then fill in the questionnaire. Nothing they do touches real data.</div>',
                unsafe_allow_html=True)
    slug = ws.study_workspace(conn)
    study_uploads = repository.list_uploads(conn, source_client=slug) if slug else []
    left, right = st.columns(2)
    with left.container(border=True):
        st.markdown('<div class="card-title">Study copy</div>', unsafe_allow_html=True)
        if study_uploads:
            u = study_uploads[0]
            st.markdown(f"**{html.escape(client_label(slug))}** — {u['issue_count']} issues (a copy; "
                        f"participants' decisions are kept separately).")
        else:
            st.warning("No study copy yet.")
        real = [c for c in ws.list_workspaces(conn, kinds=("real",)) if c["audit_count"]]
        if real:
            src = st.selectbox("Build the study copy from", [c["slug"] for c in real], key="study_src",
                               format_func=lambda k: f"{client_label(k)} — latest audit")
            if st.button("Build study copy", key="study_rebuild", icon=":material/content_copy:",
                         help="Replaces the current study copy — and any participant decisions on it."):
                build_study_workspace(conn, repository.list_uploads(conn, source_client=src)[0]["id"])
                st.toast("Study copy rebuilt", icon=":material/refresh:")
                st.rerun()
    with right.container(border=True):
        st.markdown('<div class="card-title">Questionnaire link</div>', unsafe_allow_html=True)
        url = os.environ.get("SUS_FORM_PREFILL_URL", "")
        if url and "P00" in url:
            st.markdown(":green[Set] — the code placeholder P00 is replaced with each participant's code.")
        elif url:
            st.warning("SUS_FORM_PREFILL_URL is set but doesn't contain the placeholder P00.")
        else:
            st.warning("SUS_FORM_PREFILL_URL is not set yet (add it to the secrets).")

    rows = sp.overview(conn)
    c1, c2, c3 = st.columns([1.2, 1.2, 2])
    if c1.button("Create codes P00–P15", icon=":material/add:", key="study_make_codes"):
        n = sp.create_codes(conn, [f"P{i:02d}" for i in range(16)])
        st.toast(f"{n} new code(s) created", icon=":material/check:")
        st.rerun()
    if c2.button("Reset pilot P00", icon=":material/restart_alt:", key="study_reset_p00",
                 disabled=not any(r["code"] == sp.PILOT_CODE for r in rows)):
        sp.reset(conn, sp.PILOT_CODE)
        st.toast("P00 reset — you can pilot it again", icon=":material/restart_alt:")
        st.rerun()
    if rows:
        import csv
        import io
        buf = io.StringIO()
        results = sp.results_rows(conn)
        writer = csv.DictWriter(buf, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
        c3.download_button("Download results (CSV)", data=buf.getvalue(), file_name="study_results.csv",
                           mime="text/csv", icon=":material/download:", key="study_csv")
        status_label = {"not_started": "Not started", "in_progress": "In progress", "finished": "Finished"}
        st.dataframe(
            [{"Code": r["code"], "Status": status_label[r["status"]],
              "Started": f"{r['started_at']:%d %b %H:%M}" if r["started_at"] else "",
              "Finished": f"{r['finished_at']:%d %b %H:%M}" if r["finished_at"] else "",
              "Device": r["device_type"] or "", "Browser": r["browser"] or "",
              "Tasks done (self)": r["tasks_done_self"], "Tasks verified": r["tasks_verified"]} for r in rows],
            hide_index=True, width="stretch",
        )
    else:
        st.info("No codes yet. Create P00–P15, pilot with P00, then send P01, P02… to participants.")


def _switch_company(slug: str, audit_id: int | None = None) -> None:
    """Request a company (and optionally audit) switch from anywhere on the
    page. Applied at the top of the next run, before the switcher widget
    exists -- Streamlit forbids changing a widget's value after it is drawn."""
    st.session_state["_pending_workspace"] = slug
    if audit_id is not None:
        st.session_state["_pending_audit"] = (slug, audit_id)


def _company_picker(conn, companies: list[dict], admin: bool) -> str:
    """Sidebar company switcher: "All companies" (default) or one company."""
    if "_pending_workspace" in st.session_state:
        st.session_state["workspace"] = st.session_state.pop("_pending_workspace")
    if "_pending_audit" in st.session_state:
        slug_, audit_id = st.session_state.pop("_pending_audit")
        st.session_state[f"audit_{slug_}"] = audit_id
    by_slug = {c["slug"]: c for c in companies}
    options = [ALL_COMPANIES, *by_slug]
    if st.session_state.get("workspace") not in options:
        st.session_state["workspace"] = ALL_COMPANIES
    slug = st.sidebar.selectbox(
        "Company", options, key="workspace",
        format_func=lambda k: "All companies" if k == ALL_COMPANIES else
        f"{by_slug[k]['name']}  ·  " + (f"{ws.KIND_LABEL[by_slug[k]['kind']]}" if by_slug[k]["kind"] != "real"
                                         else f"{by_slug[k]['audit_count']} audit(s)"),
    )
    if admin and st.sidebar.button("New company", icon=":material/add_business:", key="new_company",
                                   width="stretch"):
        _new_company_dialog(conn)
    return slug


@st.dialog("New company")
def _new_company_dialog(conn) -> None:
    st.caption("A company is a folder for one organisation's imports. Use any name — you can rename it later.")
    name = st.text_input("Company name", placeholder="e.g. Sunrise Traders")
    if st.button("Create", type="primary", disabled=not name.strip(), icon=":material/check:"):
        try:
            slug = ws.create_workspace(conn, name, created_by=_username())
        except ValueError as exc:
            st.error(str(exc))
            return
        _switch_company(slug)
        st.session_state["flash"] = f"Created {name.strip()} — import its first export to start."
        st.rerun()


def _render_sidebar_nav(admin: bool) -> None:
    pages = st.session_state["_pages"]
    section = None
    for key, (title, icon, sec) in PAGE_SPECS.items():
        if key not in pages:
            continue
        if sec != section:
            st.sidebar.markdown(f'<div class="sidebar-section">{sec}</div>', unsafe_allow_html=True)
            section = sec
        st.sidebar.page_link(pages[key], label=title, icon=f":material/{icon}:")


def _audit_bar(conn, slug: str) -> dict | None:
    """Top-of-page context for one company: which audit is shown (latest by
    default, older ones on request). None (with an empty state) if the
    company has no audits yet."""
    uploads = repository.list_uploads(conn, source_client=slug)
    name = client_label(slug)
    if not uploads:
        st.markdown(f'<div class="page-title">{html.escape(name)}</div>', unsafe_allow_html=True)
        st.info("No audits for this company yet.")
        if _is_admin() and st.button("Import the first export", type="primary", icon=":material/upload_file:",
                                     key="empty_import"):
            _import_dialog(conn)
        return None
    by_id = {u["id"]: u for u in uploads}
    key = f"audit_{slug}"
    if st.session_state.get(key) not in by_id:
        st.session_state[key] = uploads[0]["id"]
    latest = uploads[0]["id"]

    def _label(i: int) -> str:
        u = by_id[i]
        when = f"{(u['data_as_of'] or u['upload_date']):%d %b %Y}"
        return f"Latest · {when}  (#{i})" if i == latest else f"{when}  (#{i})"

    info, pick = st.columns([3, 1.3], vertical_alignment="bottom")
    uid = pick.selectbox("Audit", list(by_id), key=key, format_func=_label)
    u = by_id[uid]
    reviewed = u["issue_count"] - u["pending_count"]
    info.markdown(
        f'<div class="context-line"><b>{html.escape(name)}</b> · {u["issue_count"]} issues · '
        f'{reviewed} reviewed · {u["row_count"]:,} rows audited</div>',
        unsafe_allow_html=True,
    )
    return u


def _choose_company(conn, purpose: str) -> None:
    """Shown on one-company pages while "All companies" is selected."""
    st.markdown(f'<div class="page-title">{purpose}</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Choose a company to continue.</div>', unsafe_allow_html=True)
    _company_cards(conn, button_label="Open")


def _company_cards(conn, button_label: str = "Open") -> None:
    companies = ws.list_workspaces(conn, kinds=("real",))
    if not companies:
        st.info("No companies yet. Use **New company** in the sidebar to create one.")
        return
    cols = st.columns(3)
    for n, c in enumerate(companies):
        uploads = repository.list_uploads(conn, source_client=c["slug"])
        with cols[n % 3]:
            if uploads:
                latest = uploads[0]
                score_row = conn.execute("SELECT overall_score FROM dq_scores WHERE upload_id = %s",
                                         (latest["id"],)).fetchone()
                score = float(score_row[0]) if score_row else None
                when = f"{(latest['data_as_of'] or latest['upload_date']):%d %b %Y}"
                body = (f'<div class="cscore" style="color:{_status_color(score) if score is not None else ui.MUTED}">'
                        f'{score:.0f}<span style="font-size:1rem;color:var(--faint)">/100</span></div>'
                        if score is not None else '<div class="cscore">—</div>')
                body += (f'<div class="cstats"><span><b>{latest["pending_count"]}</b> to review</span>'
                         f'<span><b>{latest["issue_count"]}</b> issues</span>'
                         f'<span><b>{len(uploads)}</b> audit(s)</span></div>')
                meta = f"Latest audit: data as of {when}"
            else:
                body, meta = '<div class="cstats">No audits yet</div>', "Empty folder"
            st.markdown(f'<div class="company-card"><div class="cname">{html.escape(c["name"])}</div>'
                        f'<div class="cmeta">{meta}</div>{body}</div>', unsafe_allow_html=True)
            st.button(button_label, key=f"open_company_{c['slug']}", width="stretch",
                      icon=":material/arrow_forward:", on_click=_switch_company, args=(c["slug"],))


def _portfolio_rows(conn) -> list[dict]:
    """Each company's latest audit with its score and issue counts."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "WITH latest AS (SELECT DISTINCT ON (source_client) id, source_client, row_count, "
            "  COALESCE(data_as_of, upload_date::date) AS as_of FROM uploads ORDER BY source_client, id DESC) "
            "SELECT w.name, w.slug, l.id AS upload_id, l.as_of, l.row_count, s.overall_score, "
            "  (SELECT count(*) FROM uploads u WHERE u.source_client = w.slug) AS audits, "
            "  count(i.id) AS issues, count(i.id) FILTER (WHERE i.status = 'flagged') AS to_review, "
            "  count(i.id) FILTER (WHERE i.severity = 'high' AND i.status <> 'rejected') AS high, "
            "  count(i.id) FILTER (WHERE i.source = 'llm') AS ai "
            "FROM workspaces w JOIN latest l ON l.source_client = w.slug "
            "LEFT JOIN dq_scores s ON s.upload_id = l.id LEFT JOIN issues i ON i.upload_id = l.id "
            "WHERE w.kind = 'real' "
            "GROUP BY w.name, w.slug, l.id, l.as_of, l.row_count, s.overall_score ORDER BY w.name"
        ).fetchall()


def _render_portfolio(conn) -> None:
    st.markdown('<div class="page-title">All companies</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">An overview of every company, based on each one\'s latest audit. '
                'Pick a company in the sidebar (or below) to work on it.</div>', unsafe_allow_html=True)
    rows = _portfolio_rows(conn)
    if not rows:
        st.info("No audits yet. Use **New company** in the sidebar, then **＋ Import** to run the first audit.")
        return

    scores = [float(r["overall_score"]) for r in rows if r["overall_score"] is not None]
    avg = sum(scores) / len(scores) if scores else 0
    k = st.columns(4)
    k[0].markdown(ui.kpi_card("Companies", f"{len(rows)}", f"{sum(r['audits'] for r in rows)} audits in total",
                              ui.BLUE), unsafe_allow_html=True)
    k[1].markdown(ui.kpi_card("Average data quality", f"{avg:.0f}/100",
                              f"{sum(r['row_count'] for r in rows):,} rows in latest audits", _status_color(avg)),
                  unsafe_allow_html=True)
    k[2].markdown(ui.kpi_card("Waiting for review", f"{sum(r['to_review'] for r in rows):,}",
                              f"of {sum(r['issues'] for r in rows):,} issues", ui.MUSTARD), unsafe_allow_html=True)
    k[3].markdown(ui.kpi_card("High severity", f"{sum(r['high'] for r in rows):,}",
                              f"{sum(r['ai'] for r in rows):,} issues found by AI", ui.CORAL), unsafe_allow_html=True)

    left, right = st.columns([1.1, 1])
    with left.container(border=True):
        st.markdown('<div class="card-title">Data quality by company</div>', unsafe_allow_html=True)
        ordered = sorted((r for r in rows if r["overall_score"] is not None), key=lambda r: float(r["overall_score"]))
        fig = go.Figure(go.Bar(
            x=[float(r["overall_score"]) for r in ordered], y=[r["name"] for r in ordered], orientation="h",
            marker_color=[_status_color(float(r["overall_score"])) for r in ordered],
            text=[f"{float(r['overall_score']):.0f}" for r in ordered], textposition="outside",
            hovertemplate="%{y}: %{x:.0f}/100<extra></extra>",
        ))
        fig.update_xaxes(range=[0, 105])
        st.plotly_chart(ui.style_figure(fig, 280), config={"displayModeBar": False}, width="stretch",
                        key="pf_scores")
    with right.container(border=True):
        st.markdown('<div class="card-title">Issues by dimension — all companies</div>', unsafe_allow_html=True)
        latest_ids = [r["upload_id"] for r in rows]
        dims = conn.execute(
            "SELECT CASE WHEN dimension = 'accuracy' THEN 'validity' ELSE dimension END, count(*) FROM issues "
            "WHERE upload_id = ANY(%s) AND status <> 'rejected' GROUP BY 1 ORDER BY 2", (latest_ids,),
        ).fetchall()
        fig = go.Figure(go.Bar(x=[n for _, n in dims], y=[d.replace("_", " ") for d, _ in dims], orientation="h",
                               marker_color=ui.BLUE, hovertemplate="%{y}: %{x}<extra></extra>"))
        st.plotly_chart(ui.style_figure(fig, 280), config={"displayModeBar": False}, width="stretch",
                        key="pf_dims")

    with st.container(border=True):
        st.markdown('<div class="card-title">Data quality over time</div>', unsafe_allow_html=True)
        trend = conn.execute(
            "SELECT w.name, COALESCE(u.data_as_of, u.upload_date::date), s.overall_score FROM uploads u "
            "JOIN workspaces w ON w.slug = u.source_client JOIN dq_scores s ON s.upload_id = u.id "
            "WHERE w.kind = 'real' ORDER BY 2, u.id").fetchall()
        palette = [ui.SAGE, ui.LAVENDER, ui.MUSTARD, ui.BLUE, ui.CORAL, ui.MUTED]
        fig = go.Figure()
        for n, name in enumerate(sorted({t[0] for t in trend})):
            pts = [t for t in trend if t[0] == name]
            fig.add_scatter(x=[p[1] for p in pts], y=[float(p[2]) for p in pts], mode="lines+markers", name=name,
                            line=dict(color=palette[n % len(palette)], width=2.5, shape="spline"),
                            hovertemplate=name + " · %{x|%d %b %Y}: %{y:.0f}/100<extra></extra>")
        fig.update_yaxes(range=[0, 100])
        st.plotly_chart(ui.style_figure(fig, 280).update_layout(showlegend=True, legend=dict(orientation="h", y=-0.2)),
                        config={"displayModeBar": False}, width="stretch", key="pf_trend")

    st.markdown('<div class="card-title" style="margin-top:6px">Companies</div>', unsafe_allow_html=True)
    _company_cards(conn)


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@st.dialog("Reports", width="large")
def _report_dialog(conn, upload: dict, issues: list[dict] | None = None,
                   history: list[dict] | None = None, from_chat: bool = False) -> None:
    org = client_label(upload["source_client"])
    stem = f"{org.replace(' ', '_').replace('·', '')}_audit{upload['id']}"
    labels = ["Custom report with AI", "Standard audit report"] if from_chat else \
        ["Standard audit report", "Custom report with AI"]
    tabs = dict(zip(labels, st.tabs(labels)))

    with tabs["Standard audit report"]:
        st.caption(f"A ready-made report for **{org}**, audit #{upload['id']}: summary and score, scores by "
                   "dimension, change since the last audit, the issues that need attention with what to do, "
                   "and how they were found. Statistics and findings only — no personal data.")
        a, b = st.columns(2)
        a.download_button("Word (.docx)", data=lambda: to_docx(standard_report_md(conn, upload)),
                          file_name=f"{stem}_report.docx", mime=DOCX_MIME, icon=":material/description:",
                          width="stretch", key="dl_std_docx")
        b.download_button("PDF", data=lambda: to_pdf(standard_report_md(conn, upload)),
                          file_name=f"{stem}_report.pdf", mime="application/pdf", icon=":material/picture_as_pdf:",
                          width="stretch", key="dl_std_pdf",
                          on_click=_study_log, args=("pdf_downloaded", {"kind": "standard"}))

    with tabs["Custom report with AI"]:
        scope = (f"{len(issues)} selected issue(s)" if issues else "the whole audit") + \
            (" and this conversation" if history else "")
        st.caption(f"Describe the report you need and the AI writes it from {scope}. "
                   "It only uses audit results and statistics, and won't invent figures.")
        picked = st.pills("Suggestions", assistant.REPORT_SUGGESTIONS, key="report_sugg",
                          label_visibility="collapsed")
        instruction = st.text_area("What should the report cover?", value=picked or "", height=90,
                                   key=f"report_instr_{picked}",
                                   placeholder="e.g. A short report for the finance manager about supplier data problems")
        state_key = f"ai_report_{upload['id']}"
        if st.button("Write report", type="primary", icon=":material/auto_awesome:",
                     disabled=not instruction.strip(), key="report_write"):
            with st.spinner("Writing the report… this can take up to a minute."):
                try:
                    st.session_state[state_key] = assistant.write_report(conn, upload, instruction, issues, history)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"The AI couldn't write the report right now ({exc.__class__.__name__}).")
        md = st.session_state.get(state_key)
        if md:
            with st.container(border=True, height=330):
                st.markdown(md)
            a, b = st.columns(2)
            a.download_button("Word (.docx)", data=to_docx(md), file_name=f"{stem}_custom_report.docx",
                              mime=DOCX_MIME, icon=":material/description:", width="stretch", key="dl_ai_docx")
            b.download_button("PDF", data=to_pdf(md), file_name=f"{stem}_custom_report.pdf",
                              mime="application/pdf", icon=":material/picture_as_pdf:", width="stretch",
                              key="dl_ai_pdf", on_click=_study_log, args=("pdf_downloaded", {"kind": "custom"}))


@st.dialog("Import an export and run an audit", width="large")
def _import_dialog(conn) -> None:
    _render_upload(conn, in_dialog=True)


# url path -> (title, material icon, section); order = sidebar order.
PAGE_SPECS = {
    "overview": ("Overview", "dashboard", "Review"),
    "triage": ("Triage", "fact_check", "Review"),
    "ask": ("Ask", "forum", "Review"),
    "rules": ("Rules", "rule", "Understand"),
    "scores": ("Scores & trend", "monitoring", "Understand"),
    "relationships": ("Relationships", "account_tree", "Understand"),
    "evaluation": ("Evaluation", "query_stats", "Understand"),
    "new-audit": ("New audit", "upload_file", "Administration"),
    "schema": ("Schema review", "menu_book", "Administration"),
    "organisation": ("Organisation", "domain", "Administration"),
    "settings": ("Settings", "settings", "Administration"),
    "study": ("Study", "science", "Administration"),
}
ADMIN_PAGES = {"new-audit", "schema", "organisation", "settings", "study"}


def _build_navigation(conn, slug: str, upload: dict | None, admin: bool, participant: bool = False):
    """Pages render only when selected (st.tabs would run all of them on
    every click). Navigation is drawn by _render_sidebar_nav so the company
    switcher can sit above it. Start page: ?page=<path> or
    session_state["start_page"] (tests)."""
    all_mode = slug == ALL_COMPANIES

    def company_page(title: str, render, needs_audit: bool = True):
        def run():
            if all_mode:
                _choose_company(conn, title)
                return
            current = _audit_bar(conn, slug) if needs_audit else None
            if needs_audit and current is None:
                return
            render(current)
        return run

    def overview():
        if all_mode:
            _render_portfolio(conn)
        else:
            current = _audit_bar(conn, slug)
            if current is not None:
                _render_overview(conn, current)

    def evaluation():
        if all_mode:
            _render_evaluation(conn, None)
        else:
            current = _audit_bar(conn, slug)
            if current is not None:
                _render_evaluation(conn, current)

    renderers = {
        "overview": overview,
        "triage": company_page("Triage", lambda u: _render_triage(conn, u)),
        "ask": company_page("Ask", lambda u: _render_chat(conn, u)),
        "rules": company_page("Rules", lambda u: _render_rules(conn, u)),
        "scores": company_page("Scores & trend", lambda u: _render_scores(conn, u)),
        "relationships": company_page("Relationships", lambda u: _render_relationships(conn, u)),
        "evaluation": evaluation,
        "new-audit": lambda: _render_upload(conn),
        "schema": company_page("Schema review", lambda u: _render_schema_review(conn, u)),
        "organisation": company_page("Organisation", lambda u: _render_organisation(conn, u)),
        "settings": lambda: _render_settings(conn),
        "study": lambda: _render_study_admin(conn),
    }
    start = st.session_state.get("start_page") or st.query_params.get("page") or "overview"
    allowed = [k for k in PAGE_SPECS if admin or k not in ADMIN_PAGES]
    if participant:
        allowed = ["overview", "triage", "ask"]
    if start not in allowed:
        start = "overview"
    pages = {}
    for key in allowed:
        title, icon, _section = PAGE_SPECS[key]
        pages[key] = st.Page(renderers[key], title=title, icon=f":material/{icon}:",
                             url_path=key, default=(key == start))
    st.session_state["_pages"] = pages
    return st.navigation(list(pages.values()), position="hidden")


FILTER_LABELS = {"rule": "Rules", "llm": "AI", "flagged": "To review", "yes": "Yes", "no": "No"}


def _filter_bar(tables: list[str]) -> dict:
    cols = st.columns(7)
    specs = [
        ("table", "Table", tables, ALL),
        ("source", "Found by", ["rule", "llm"], ALL),
        # participants keep the issue they just decided in view (study tasks 2-3)
        ("status", "Status", STATUSES, ALL if _is_participant() else "flagged"),
        ("severity", "Severity", ["high", "medium", "low"], ALL),
        ("dimension", "Dimension", DIMENSIONS, ALL),
        ("trend", "Trend", TRENDS, ALL),
        ("fixable", "Fixable", ["yes", "no"], ALL),
    ]
    filters = {}
    for col, (key, label, options, default) in zip(cols, specs):
        choices = [ALL, *options]
        filters[key] = col.selectbox(
            label, choices, index=choices.index(default), key=f"filter_{key}",
            format_func=lambda v: "All" if v == ALL else FILTER_LABELS.get(
                v, v if key == "table" else v.replace("_", " ").capitalize()),
        )
    return filters


def _render_triage(conn, upload: dict) -> None:
    uid = upload["id"]
    issues = repository.fetch_issues(conn, uid)
    if _is_participant():   # a participant sees only their own decisions
        issues = sp.overlay(issues, sp.decisions(conn, _username()), _username())
    filters = _filter_bar(sorted({i["table_name"] for i in issues}))
    for key in ("table", "source", "dimension", "severity", "status"):
        field = "table_name" if key == "table" else key
        if filters[key] != ALL:
            issues = [i for i in issues if i.get(field) == filters[key]]
    if filters["fixable"] != ALL:
        issues = [i for i in issues if i.get("fixable") == filters["fixable"]]
    if filters["trend"] == "recurring":
        issues = [i for i in issues if i.get("trend_status") not in (None, "new")]
    elif filters["trend"] != ALL:
        issues = [i for i in issues if i.get("trend_status") == filters["trend"]]
    aliases = _aliases(conn, upload["source_client"])
    head_l, head_csv, head_xlsx = st.columns([3, 1, 1])
    head_l.caption(f"Showing {len(issues)} issue(s)")
    if issues:
        org = client_label(upload["source_client"])
        stem = f"{org.replace(' ', '_').replace('·', '')}_audit{uid}_issues"
        head_csv.download_button(
            "⬇️ CSV",
            data=issues_to_csv(issues, aliases, organisation=org),
            file_name=f"{stem}.csv",
            mime="text/csv",
            width="stretch",
            help="Downloads exactly the issues shown (current filters), with triage status.",
        )
        head_xlsx.download_button(
            "⬇️ Excel",
            data=issues_to_xlsx(issues, aliases, organisation=org,
                                title=f"{org} — audit #{uid} issues", filters=filters),
            file_name=f"{stem}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
            help="Formatted workbook with an issue sheet and a summary by dimension and severity.",
            on_click=_study_log, args=("excel_downloaded", {"table": _none_if_all(filters["table"]),
                                                            "rows": len(issues)}),
        )
        if not _is_participant():
            _render_bulk_actions(conn, [i["id"] for i in issues])
    else:
        st.info("No issues match these filters. Try setting **Status** to *All*.")
    for issue in issues:
        _issue_row(conn, issue, aliases)


def _render_settings(conn) -> None:
    st.markdown("#### AI model")
    st.caption(
        "Choose which AI model the auditor, schema drafting and chat use. Options come from the "
        "server's configuration file. Whichever you choose, names, phone numbers and other personal "
        "details are hidden (pseudonymised) before anything is sent."
    )
    options = llm_selection.llm_options()
    current = llm_selection.active_option(conn)
    names = [o["name"] for o in options]

    def _fmt(name: str) -> str:
        o = next(x for x in options if x["name"] == name)
        where = "🖥️ runs on this computer" if llm_selection.is_local(o) else "☁️ cloud service"
        return f"{name} — {o['model']} · {where}"

    picked = st.radio("Active model", names, index=names.index(current["name"]), format_func=_fmt)
    if picked != current["name"]:
        llm_selection.set_selected(conn, picked, _username())
        st.toast(f"AI model set to {picked}", icon="⚙️")
        st.rerun()
    if len(options) == 1:
        st.caption("Only one option is configured. Add more with LLM_OPTIONS in backend/.env.")

    st.markdown("#### Companies")
    st.caption("Each company is a folder of its imports. Rename one at any time — audits and history stay attached.")
    for c in ws.list_workspaces(conn):
        with st.form(f"ws_{c['slug']}", border=False):
            a, b, d = st.columns([2.4, 1.6, 0.9])
            new = a.text_input("Name", value=c["name"], key=f"ws_name_{c['slug']}", label_visibility="collapsed")
            last = f"{c['last_as_of']:%d %b %Y}" if c["last_as_of"] else "no audits yet"
            b.markdown(f'<div class="card-sub" style="padding-top:8px">{c["audit_count"]} audit(s) · latest {last}</div>',
                       unsafe_allow_html=True)
            if d.form_submit_button("Rename", width="stretch") and new.strip() != c["name"]:
                try:
                    ws.rename_workspace(conn, c["slug"], new)
                    st.toast(f"Renamed to {new.strip()}", icon=":material/edit:")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    st.markdown("#### People")
    people = auth.list_users(conn)
    st.dataframe(
        [{"Username": u["username"], "Role": u["role"].title(), "Created": f"{u['created_at']:%d %b %Y}"}
         for u in people],
        width="stretch", hide_index=True,
    )
    with st.form("new_user", clear_on_submit=True):
        st.markdown("**Add a person**")
        c1, c2, c3 = st.columns([1.2, 1.2, 0.8])
        new_name = c1.text_input("Username")
        new_pw = c2.text_input("Temporary password", type="password",
                               help=f"At least {auth.MIN_PASSWORD_LENGTH} characters.")
        new_role = c3.selectbox("Role", auth.ROLES, index=1, format_func=str.title)
        if st.form_submit_button("➕ Add person"):
            try:
                auth.create_user(conn, new_name, new_pw, new_role)
                st.toast(f"Added {new_name}", icon="👤")
                st.rerun()
            except Exception as exc:  # noqa: BLE001 -- show validation / duplicate errors
                conn.rollback()
                st.error(f"Could not add this person: {exc}")


CONFIDENCE_STYLE = {
    "LOW": ("🟠", "orange", "Needs review"),
    "MEDIUM": ("🔵", "blue", "Medium confidence"),
    "HIGH": ("🟢", "green", "High confidence"),
    "VERIFIED": ("✅", "green", "Verified"),
}
DOC_VIEWS = {"All": None, "Needs review": "LOW", "Drafts": "draft", "Verified": "VERIFIED"}


RULE_VIEWS = {"Found problems": "violated", "Passed": "passed", "All": "all"}
EVIDENCE_LABEL = {
    "null_pct": "blank %", "null_count": "blank rows", "row_count": "rows",
    "negative_count": "negative rows", "negative_pct": "negative %", "min": "smallest value",
    "duplicate_rows": "duplicate rows", "distinct_pct": "distinct %", "distinct_count": "distinct values",
    "non_null_count": "filled rows", "duplicated_values": "repeated values",
    "max_date": "latest date", "conformance_pct": "held in %", "valid_pairs": "rows compared",
    "informative_rows": "rows compared", "overlap_pct": "matching %", "orphan_count": "unmatched rows",
}
EVIDENCE_HIDDEN = {"type", "reference_date"}


def _evidence_text(rule: dict) -> str:
    ev = rule.get("evidence") or {}
    bits = []
    for key, value in ev.items():
        if key in EVIDENCE_HIDDEN or value is None:
            continue
        shown = f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) else value
        bits.append(f"{EVIDENCE_LABEL.get(key, key.replace('_', ' '))}: {shown}")
    return "Evidence — " + " · ".join(bits) if bits else "Evidence — none recorded"


GENERAL_STARTERS = [
    "What are the most serious problems in this audit?",
    "Which table should we fix first, and why?",
    "Explain the data quality score in simple terms.",
]
ISSUE_STARTERS = [
    "Explain these issues in simple terms.",
    "Why do they matter for the business?",
    "How should we fix them, step by step?",
]


def _issue_option_label(issue: dict, aliases: dict) -> str:
    col = display_columns(aliases, issue["table_name"], issue["column_name"])
    where = issue["table_name"] + (f".{col}" if col else "")
    return f"#{issue['id']} · {where} · {issue['dimension'].replace('_', ' ')} · {issue['severity']}"


def _open_issue_thread(conn, issue: dict, aliases: dict) -> None:
    """Start a thread about one issue and make it the active one."""
    col = display_columns(aliases, issue["table_name"], issue["column_name"])
    title = f"Issue #{issue['id']} · {issue['table_name']}" + (f".{col}" if col else "")
    tid = chat_threads.create_thread(conn, issue["source_client"], _username(), title, [issue["id"]],
                                     study_code=_username() if _is_participant() else None)
    st.session_state[f"chat_thread_{issue['source_client']}"] = tid


def _render_chat(conn, upload: dict) -> None:
    """ChatGPT-style: thread list on the left; on the right an open
    conversation filling the screen with the question bar directly under it."""
    uid, client = upload["id"], upload["source_client"]
    username = _username()
    aliases = _aliases(conn, client)
    key = f"chat_thread_{client}"

    threads = chat_threads.list_threads(conn, client, username)
    ids = {t["id"] for t in threads}
    if st.session_state.get(key) not in ids:
        st.session_state[key] = threads[0]["id"] if threads else None
    active = st.session_state[key]

    side, main = st.columns([1, 3.4], gap="large")
    with side:
        if st.button("New chat", icon=":material/add:", type="primary", width="stretch", key="chat_new"):
            st.session_state[key] = chat_threads.create_thread(
                conn, client, username, study_code=username if _is_participant() else None)
            st.rerun()
        with st.container(height=600, border=False, key="chat_threads"):
            if not threads:
                st.caption("Your chats appear here.")
            for t in threads:
                label = t["title"] if len(t["title"]) <= 34 else t["title"][:33] + "…"
                if st.button(label, key=f"thread_{t['id']}", width="stretch",
                             type="secondary" if t["id"] == active else "tertiary",
                             help=f"{t['message_count']} message(s) · {t['updated_at']:%d %b %H:%M}"):
                    st.session_state[key] = t["id"]
                    st.rerun()

    with main:
        audit_issues = repository.fetch_issues(conn, uid)
        attached = chat_threads.thread_issues(conn, active) if active else []
        options = {i["id"]: i for i in audit_issues} | {i["id"]: i for i in attached}
        head, report_b, tools = st.columns([4.6, 1, 1], vertical_alignment="bottom")
        chosen = head.multiselect(
            "Issues in this chat", list(options), default=[i["id"] for i in attached],
            format_func=lambda i: _issue_option_label(options[i], aliases),
            placeholder="General question about this audit — or pick issues to discuss",
            key=f"chat_issues_{active}", label_visibility="collapsed",
        )
        if active and set(chosen) != {i["id"] for i in attached}:
            for i in set(chosen) - {i["id"] for i in attached}:
                chat_threads.attach_issue(conn, active, i)
            for i in {i["id"] for i in attached} - set(chosen):
                chat_threads.detach_issue(conn, active, i)
            st.rerun()
        focus = [options[i] for i in chosen]
        history = chat_threads.messages(conn, active) if active else []
        if report_b.button("Report", icon=":material/summarize:", key=f"chat_report_{active}", width="stretch",
                           help="Create a report from this chat"):
            _report_dialog(conn, upload, focus, [dict(m) for m in history], from_chat=True)
        if active and tools.button("", icon=":material/archive:", key=f"chat_archive_{active}",
                                   width="stretch", help="Archive this chat"):
            chat_threads.archive_thread(conn, active)
            st.session_state[key] = None
            st.rerun()

        picked = None
        with st.container(height=520, border=False, key="chat_scroll"):
            if not history:
                st.markdown(
                    '<div class="chat-empty"><div class="chat-empty-title">What would you like to know?</div>'
                    '<div class="chat-empty-sub">Ask about this audit — what to fix first, what an issue means, or '
                    'how to explain the results to a manager. Answers use only audit results and statistics.'
                    '</div></div>', unsafe_allow_html=True)
                picked = st.pills("Suggestions", ISSUE_STARTERS if focus else GENERAL_STARTERS,
                                  key=f"starter_{active}", label_visibility="collapsed")
            for m in history:
                with st.chat_message(m["role"], avatar=":material/person:" if m["role"] == "user"
                                     else ":material/auto_awesome:"):
                    st.markdown(m["content"])
        question = st.chat_input("Ask a question…", key=f"chat_input_{client}") or picked

    if question:
        if not active:
            active = chat_threads.create_thread(conn, client, username, issue_ids=[i["id"] for i in focus],
                                                study_code=username if _is_participant() else None)
            st.session_state[key] = active
        chat_threads.add_message(conn, active, uid, "user", question, username)
        _study_log("chat_sent", {"issues": [i["id"] for i in focus]})
        with st.spinner("Thinking…"):
            try:
                reply = assistant.answer(conn, upload, question, [dict(m) for m in history], focus)
            except Exception as exc:  # noqa: BLE001 -- show the problem instead of crashing the page
                reply = f"Sorry — the assistant is unavailable right now ({exc.__class__.__name__}). Please try again."
                _study_log("chat_failed", {"error": exc.__class__.__name__})
        chat_threads.add_message(conn, active, uid, "assistant", reply, username)
        st.rerun()


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def _decisions_caption(acc: int, rej: int) -> str:
    n = acc + rej
    if not n:
        return "no decisions yet"
    return f"from {n} decision(s)" + (" · provisional" if n < live_eval.MIN_DECISIONS else "")


EVAL_SCOPES = ["This audit", "This company", "All companies"]


def _render_evaluation(conn, upload: dict | None) -> None:
    st.markdown('<div class="page-title">Evaluation</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">How accurate is the tool? Every accept / reject in Triage is a ground-truth '
                'label, so these numbers are measured on real data and update as people review.</div>',
                unsafe_allow_html=True)
    if upload is None:
        uid, client, label, scope = None, None, "all companies", "All companies"
    else:
        uid, client = upload["id"], upload["source_client"]
        label = client_label(client)
        scope = st.segmented_control("Scope", ["This company", "This audit", "All companies"], default="This company",
                                     key="eval_scope", label_visibility="collapsed") or "This company"
    issues = live_eval.scope_issues(
        conn, upload_id=uid if scope == "This audit" else None,
        source_client=client if scope == "This company" else None,
    )
    tm = compute_triage_metrics(issues)
    rule, ai = tm["by_source"]["rule"], tm["by_source"]["llm"]
    ai_decided = ai["accepted"] + ai["rejected"]

    k = st.columns(4)
    k[0].markdown(ui.kpi_card("Rule precision", _pct(rule["empirical_precision"]),
                              _decisions_caption(rule["accepted"], rule["rejected"]), ui.BLUE), unsafe_allow_html=True)
    k[1].markdown(ui.kpi_card("AI precision", _pct(ai["empirical_precision"]),
                              _decisions_caption(ai["accepted"], ai["rejected"]), ui.LAVENDER), unsafe_allow_html=True)
    k[2].markdown(ui.kpi_card("AI rejection rate", _pct(ai["rejected"] / ai_decided if ai_decided else None),
                              "hallucination indicator (lower is better)", ui.CORAL), unsafe_allow_html=True)
    k[3].markdown(ui.kpi_card("Reviewed", f"{tm['reviewed']}/{tm['total']}",
                              f"{tm['by_status']['considered']} marked unsure", ui.MUSTARD), unsafe_allow_html=True)
    if not tm["reviewed"]:
        st.info("No reviews yet in this scope. Accept or reject issues in **Triage** — these figures fill in "
                f"automatically (about {live_eval.MIN_DECISIONS}+ decisions per detector give stable numbers).")

    left, right = st.columns(2)
    with left.container(border=True):
        st.markdown('<div class="card-title">Precision by dimension</div>', unsafe_allow_html=True)
        rows = [r for r in live_eval.precision_by_dimension(issues) if r["precision"] is not None]
        if rows:
            fig = go.Figure()
            for source, color, name in (("rule", ui.BLUE, "Rules"), ("llm", ui.LAVENDER, "AI")):
                sub = [r for r in rows if r["source"] == source]
                if sub:
                    fig.add_bar(y=[r["dimension"].replace("_", " ") for r in sub], x=[r["precision"] * 100 for r in sub],
                                orientation="h", name=name, marker_color=color,
                                customdata=[r["accepted"] + r["rejected"] for r in sub],
                                hovertemplate=name + " · %{y}: %{x:.0f}% of %{customdata} decisions<extra></extra>")
            fig.update_layout(barmode="group", showlegend=True, legend=dict(orientation="h", y=-0.15))
            fig.update_xaxes(range=[0, 100], ticksuffix="%")
            st.plotly_chart(ui.style_figure(fig, 280).update_layout(showlegend=True), config={"displayModeBar": False},
                            width="stretch", key="eval_prec_dim")
        else:
            st.markdown('<div class="card-sub">Appears once issues have been accepted or rejected.</div>',
                        unsafe_allow_html=True)
    with right.container(border=True):
        st.markdown('<div class="card-title">Rules vs AI — do they find different problems?</div>',
                    unsafe_allow_html=True)
        comp = real_complementarity(issues if uid is None else repository.fetch_issues(conn, uid))
        o = comp["overall"]
        if comp["n_locations"]:
            st.plotly_chart(
                ui.donut(["Rules only", "AI only", "Both"], [o["rule_only"], o["llm_only"], o["both"]],
                         [ui.BLUE, ui.LAVENDER, ui.SAGE], f"{o['both_pct']:.0f}%", "found by both"),
                config={"displayModeBar": False}, width="stretch", key="eval_comp",
            )
            st.caption(f"{comp['n_locations']} problem locations: {o['rule_only']} rules only, {o['llm_only']} AI only, "
                       f"{o['both']} both. A small overlap means the two detectors complement each other.")
        else:
            st.markdown('<div class="card-sub">No issues in this audit.</div>', unsafe_allow_html=True)

    left, right = st.columns(2)
    with left.container(border=True):
        st.markdown(f'<div class="card-title">Is the AI learning? — {html.escape(label)}</div>', unsafe_allow_html=True)
        history = live_eval.ai_rejection_by_audit(conn, client) if client else []
        decided = [h for h in history if h["rejection_rate"] is not None]
        if len(decided) >= 2:
            fig = go.Figure()
            fig.add_bar(x=[f"#{h['upload_id']}" for h in history], y=[h["feedback_examples"] for h in history],
                        name="Feedback examples given", marker_color=ui.SURFACE_2, yaxis="y2",
                        hovertemplate="%{y} examples<extra></extra>")
            fig.add_scatter(x=[f"#{h['upload_id']}" for h in decided], y=[h["rejection_rate"] * 100 for h in decided],
                            mode="lines+markers", name="AI rejection rate", line=dict(color=ui.CORAL, width=3),
                            hovertemplate="%{y:.0f}% rejected<extra></extra>")
            fig.update_layout(yaxis=dict(range=[0, 100], ticksuffix="%"),
                              yaxis2=dict(overlaying="y", side="right", showgrid=False), showlegend=True,
                              legend=dict(orientation="h", y=-0.2))
            fig.update_xaxes(type="category")
            st.plotly_chart(ui.style_figure(fig, 260).update_layout(showlegend=True),
                            config={"displayModeBar": False}, width="stretch", key="eval_learning")
            st.caption("If feedback works, the rejection rate should fall as more reviewed examples are given to the AI.")
        else:
            st.markdown('<div class="card-sub">' + ("Choose a company to see this. " if client is None else "")
                        + 'Needs reviewed AI findings in at least two audits of a company. '
                        'Review AI issues, then run another audit with AI analysis.</div>', unsafe_allow_html=True)
    with right.container(border=True):
        st.markdown('<div class="card-title">Trend predictions</div>', unsafe_allow_html=True)
        pe = live_eval.projection_error(conn, None if scope == "All companies" else client)
        if pe["n"]:
            st.markdown(ui.kpi_card("Typical prediction error", f"{pe['median_ape']:.1f}%",
                                    f"median over {pe['n']} predictions of changing issues, checked against the next audit "
                                    f"(mean {pe['mean_ape']:.1f}%)", ui.SAGE), unsafe_allow_html=True)
        else:
            st.markdown('<div class="card-sub">Predictions are checked when the next audit arrives '
                        '(needs 4+ audits of a company).</div>', unsafe_allow_html=True)
        st.markdown('<div class="card-title" style="margin-top:14px">AI schema descriptions</div>',
                    unsafe_allow_html=True)
        docs = live_eval.schema_doc_accuracy(conn, None if scope == "All companies" else client)
        if docs:
            st.dataframe([{"AI confidence": d["llm_confidence"].title(), "Verified": d["verified"],
                           "Confirmed without edits": f"{d['unedited']} ({d['unedited_pct']:.0f}%)"} for d in docs],
                         hide_index=True, width="stretch")
        else:
            st.markdown('<div class="card-sub">Appears once columns are verified in Schema review.</div>',
                        unsafe_allow_html=True)

    with st.expander("How these numbers are measured"):
        st.markdown(
            "- **Precision** = accepted ÷ (accepted + rejected), for rules and the AI separately. "
            "Issues marked *unsure* or not yet reviewed are left out.\n"
            "- **AI rejection rate** = the share of decided AI findings that reviewers rejected — the "
            "hallucination indicator. Tracked per audit next to the number of past decisions the AI was shown, "
            "to test whether in-context feedback reduces false findings.\n"
            "- **Rules vs AI** groups issues by location (table, column, dimension). A low *both* share means the "
            "detectors find different kinds of problems (deterministic vs contextual), i.e. they are complementary.\n"
            "- **Recall** (how many real problems were missed) is estimated separately: a fixed-seed random sample "
            "of rows that were *not* flagged is checked by hand, plus a controlled test with known injected errors.\n"
            "- **Trend predictions** compare each straight-line projection with the value seen at the next audit.\n"
            "- **Schema descriptions**: the share of AI-drafted column descriptions a person verified without "
            "changing, by the confidence the AI reported."
        )


def _render_rules(conn, upload: dict) -> None:
    uid = upload["id"]
    st.markdown("#### Rules — what was checked")
    st.caption(
        "Every rule below was created automatically from this upload's data profile; "
        "none were written by hand. Each one says what it checks and why it was created."
    )
    all_rules = repository.fetch_rules(conn, uid)
    if not all_rules:
        st.info("No rules were recorded for this upload. Rules are saved for audits run from now on — "
                "re-run the audit in ⬆️ New audit to see them.")
        return

    violated = sum(1 for r in all_rules if r["violation_count"])
    m = st.columns(4)
    m[0].metric("Rules checked", len(all_rules))
    m[1].metric("✅ Passed", len(all_rules) - violated)
    m[2].metric("⚠️ Found problems", violated)
    m[3].metric("Tables covered", len({r["table_name"] for r in all_rules}))

    f1, f2, f3 = st.columns([1.2, 1.2, 2])
    tables = sorted({r["table_name"] for r in all_rules})
    categories = sorted({r["category"] for r in all_rules})
    table = f1.selectbox("Table", [ALL, *tables], key=f"rules_table_{uid}",
                         format_func=lambda t: "All tables" if t == ALL else t)
    category = f2.selectbox("Rule type", [ALL, *categories], key=f"rules_cat_{uid}",
                            format_func=lambda c: "All types" if c == ALL else CATEGORY_LABEL.get(c, c))
    with f3:
        view = st.segmented_control("Show", list(RULE_VIEWS), default="Found problems", key=f"rules_view_{uid}")
    mode = RULE_VIEWS.get(view or "All")

    rules = [
        r for r in all_rules
        if (table == ALL or r["table_name"] == table)
        and (category == ALL or r["category"] == category)
        and (mode == "all" or (mode == "violated") == bool(r["violation_count"]))
    ]
    st.caption(f"Showing {len(rules)} of {len(all_rules)} rules")
    client = base_client(upload["source_client"])
    suppressed = repository.active_suppressions(conn, client)
    admin = _is_admin()
    for r in rules:
        broken = r["violation_count"]
        off = r["rule_key"] in suppressed
        accent = ui.FAINT if off else (ui.CORAL if broken else ui.SAGE)
        if off:
            s_row = suppressed[r["rule_key"]]
            badge = _pill(f"Not applicable · {s_row['disabled_by'] or 'admin'}, {s_row['disabled_at']:%d %b}", "#667085")
        elif broken:
            badge = _pill(f"⚠ {broken:,} row(s) break this rule", ui.CORAL)
        else:
            badge = _pill("✓ Passed", ui.SAGE)
        st.markdown(
            f'''<div class="rule-card" style="--accent:{accent}">
  <div class="issue-head"><span class="pills">{_outline_pill(CATEGORY_LABEL.get(r["category"], r["category"]))}{_outline_pill(r["table_name"])}</span>
  <span class="pills">{badge}</span></div>
  <div class="rule-what">{html.escape(r["what_text"])}</div>
  <div class="rule-why">{html.escape(r["why_text"])}</div>
  <div class="rule-ev">{html.escape(_evidence_text(r))}</div>
</div>''',
            unsafe_allow_html=True,
        )
        if admin and (broken or off):
            label = "↩️ Apply this rule again" if off else "🚫 Not applicable to us"
            if st.button(label, key=f"suppress_{r['id']}", type="tertiary",
                         help="Disabled rules still run but raise no issues in future audits for this organisation."):
                repository.set_rule_suppressed(conn, client, r["rule_key"], not off, by=_username())
                st.toast("Rule re-enabled for future audits" if off else
                         "Rule disabled for future audits of this organisation", icon="📏")
                st.rerun()


def _render_organisation(conn, upload: dict) -> None:
    client = upload["source_client"]
    label = client_label(client)
    st.markdown(f"#### What the AI knows about {label}")
    st.caption(
        "The AI auditor adapts to this organisation without any training: before each audit it is "
        "given your notes below, plus recent AI findings your reviewers **accepted** (real issues) "
        "and **rejected** (not problems), so it stops repeating findings you have already dismissed."
    )

    notes = feedback.list_notes(conn, client)
    stats = feedback.feedback_stats(conn, client)
    m = st.columns(3)
    m[0].metric("📝 Active notes", sum(1 for n in notes if n["active"]))
    m[1].metric("✅ Accepted AI findings", stats["accepted"])
    m[2].metric("❌ Rejected AI findings", stats["rejected"])
    if not (stats["accepted"] or stats["rejected"]):
        st.caption("No AI findings have been reviewed yet. Accept or reject 🤖 LLM issues in 🧾 Triage "
                   "and they will be used as examples in the next audit.")

    st.markdown("##### Organisation notes")
    with st.form(f"org_note_{client}", clear_on_submit=True):
        text = st.text_area(
            "Add a note the AI should always know",
            placeholder="e.g. We are VAT-exempt, so a zero tax amount is normal.",
            height=70,
        )
        if st.form_submit_button("➕ Add note", type="primary") and text.strip():
            feedback.add_note(conn, client, text, created_by=_username())
            st.toast("Note added — it will be used from the next AI audit", icon="📝")
            st.rerun()

    if not notes:
        st.caption("No notes yet.")
    for n in notes:
        with st.container(border=True):
            left, right = st.columns([5, 1.3])
            state = "" if n["active"] else " · :gray[inactive]"
            left.markdown(f"{'📝' if n['active'] else '💤'} {html.escape(n['note'])}")
            left.caption(f"Added {n['created_at']:%d %b %Y}" + (f" by {n['created_by']}" if n["created_by"] else "") + state)
            label_btn = "Deactivate" if n["active"] else "Reactivate"
            if right.button(label_btn, key=f"note_toggle_{n['id']}", width="stretch"):
                feedback.set_note_active(conn, n["id"], not n["active"])
                st.rerun()

    st.markdown("##### Preview what the AI will be told")
    tables = sorted({r["table_name"] for r in repository.fetch_rules(conn, upload["id"])}
                    or {i["table_name"] for i in repository.fetch_issues(conn, upload["id"])})
    if tables:
        table = st.selectbox("For table", tables, key=f"org_preview_{client}")
        schema_ctx = docs_store.get_schema_context(client, table, conn=conn)
        org_ctx, _ = feedback.feedback_context(conn, client, table, base_tokens=0)
        preview = "\n\n".join(c for c in (schema_ctx, org_ctx) if c)
        if preview:
            st.code(preview, language=None, wrap_lines=True)
        else:
            st.caption("Nothing extra yet — the AI audits this table with the profile and sample only.")

    log = feedback.fetch_prompt_log(conn, upload["id"])
    if log:
        with st.expander(f"🧾 What the AI was given in this audit ({len(log)} tables)"):
            st.dataframe(
                [
                    {
                        "Table": e["table_name"],
                        "Notes": len(e["note_ids"]),
                        "Accepted examples": len(e["accepted_issue_ids"]),
                        "Rejected examples": len(e["rejected_issue_ids"]),
                        "Verified schema docs": "yes" if e["schema_context"] else "no",
                        "Prompt size (≈tokens)": e["est_prompt_tokens"],
                        "Examples trimmed": e["trimmed_examples"],
                    }
                    for e in log
                ],
                width="stretch", hide_index=True,
            )


def _render_schema_review(conn, upload: dict) -> None:
    client = upload["source_client"]
    label = client_label(client)
    st.markdown(f"#### Schema documentation — {label}")
    st.caption(
        "The AI drafted what each table and column means from its statistics. "
        "Check each description and correct anything wrong. Only **verified** "
        "descriptions are given to the AI auditor in later audits."
    )

    tables = docs_store.fetch_doc_tables(conn, client)
    if not tables:
        st.info(
            f"No schema documentation for {label} yet. In **⬆️ New audit**, tick "
            "*Draft schema documentation with AI*, or run "
            "`python -m app.schema_docs.generate <data_dir> --client=<key>`."
        )
        return

    stats = docs_store.doc_stats(conn, client)
    m = st.columns(4)
    m[0].metric("Tables verified", f"{stats['tables_verified']} / {stats['tables']}")
    m[1].metric("Columns verified", f"{stats['columns_verified']} / {stats['columns']}")
    m[2].metric("🟠 Need review", sum(t["low_count"] for t in tables))
    m[3].metric("Drafts left", stats["columns"] - stats["columns_verified"])
    if stats["columns"]:
        st.progress(
            stats["columns_verified"] / stats["columns"],
            text=f"{stats['columns_verified']} of {stats['columns']} columns verified",
        )

    by_name = {t["table_name"]: t for t in tables}
    table = st.selectbox(
        "Table",
        list(by_name),
        key=f"docs_table_{client}",
        format_func=lambda n: (
            f"{'✅' if by_name[n]['verified_at'] else '📄'} {n} · {by_name[n]['table_module'] or 'Other'} · "
            f"{by_name[n]['verified_count']}/{by_name[n]['column_count']} columns verified"
        ),
    )
    tdoc = by_name[table]

    with st.container(border=True):
        status = (
            f":green[✅ Verified {tdoc['verified_at']:%d %b %Y}]" if tdoc["verified_at"]
            else ":orange[📄 Draft — not yet verified]"
        )
        st.markdown(f"**{table}** &nbsp; {status}")
        with st.form(f"doc_tbl_{client}_{table}"):
            desc = st.text_area("What this table holds", value=tdoc["table_description"] or "", height=80)
            current = tdoc["table_module"] if tdoc["table_module"] in MODULES else "Other"
            module = st.selectbox("Business area", MODULES, index=MODULES.index(current))
            c1, c2, _ = st.columns([1, 1.6, 2])
            save = c1.form_submit_button("💾 Save", width="stretch")
            verify = c2.form_submit_button(
                "✅ Verify table + all columns", type="primary", width="stretch",
                help="Marks the table and every remaining column as verified. Check the columns first.",
            )
        if save or verify:
            docs_store.update_table(conn, client, table, desc.strip(), module)
            if verify:
                n = docs_store.verify_table(conn, client, table, verified_by=_username())
                st.toast(f"{table} verified ({n} column(s) confirmed)", icon="✅")
            st.rerun()
        fixed = []
        if tdoc["missing_columns"]:
            fixed.append(f"{len(tdoc['missing_columns'])} column(s) the AI skipped were added as *Needs review*")
        if tdoc["hallucinated_columns"]:
            fixed.append(f"{len(tdoc['hallucinated_columns'])} column(s) the AI invented were removed")
        if fixed:
            st.caption("Draft check: " + "; ".join(fixed) + ".")

    view = st.segmented_control("Show", list(DOC_VIEWS), default="All", key=f"docs_view_{client}_{table}")
    wanted = DOC_VIEWS.get(view or "All")
    columns = docs_store.fetch_doc_columns(conn, client, table)
    if wanted == "draft":
        columns = [c for c in columns if c["confidence"] != "VERIFIED"]
    elif wanted:
        columns = [c for c in columns if c["confidence"] == wanted]
    if not columns:
        st.caption("Nothing to show in this view.")
    for col in columns:
        _render_doc_column(conn, col)


def _render_doc_column(conn, col: dict) -> None:
    conf = col["confidence"]
    icon, color, text = CONFIDENCE_STYLE[conf]
    name = col["display_name"] or "unnamed"
    title = f"{icon} **{col['column_name']}** · {name} — :{color}[{text}]"
    with st.expander(title, expanded=(conf == "LOW")):
        with st.form(f"doc_col_{col['id']}", border=False):
            a, b = st.columns([1, 2])
            display = a.text_input("Short name", value=col["display_name"] or "")
            domain = b.text_input("Expected values", value=col["expected_domain"] or "")
            desc = st.text_area("What it means", value=col["description"] or "", height=68)
            rules = st.text_area(
                "Rules (one per line)", value="\n".join(col["business_rules"] or []), height=88,
            )
            c1, c2, _ = st.columns([1, 1.3, 3])
            save = c1.form_submit_button("💾 Save", width="stretch")
            verify = c2.form_submit_button(
                "✅ Save & verify", type="primary", width="stretch", disabled=(conf == "VERIFIED"),
            )
        if save or verify:
            docs_store.update_column(
                conn, col["id"], display.strip(), desc.strip(), domain.strip(),
                [r.strip() for r in rules.splitlines() if r.strip()],
            )
            if verify:
                docs_store.verify_column(conn, col["id"], verified_by=_username())
            st.rerun()

        if conf == "VERIFIED":
            how = "edited before verifying" if col["edited_before_verify"] else "confirmed as drafted"
            st.caption(f"Verified {col['verified_at']:%d %b %Y} · {how}")
        elif col["llm_confidence"]:
            st.caption(f"AI draft confidence: {col['llm_confidence'].title()}")
        if col["llm_description"] and " ".join((col["description"] or "").split()) != " ".join(col["llm_description"].split()):
            st.caption(f"Original AI draft: {col['llm_description']}")


def _render_relationships(conn, upload: dict) -> None:
    st.markdown("#### Discovered cross-table relationships")
    st.caption(
        "Foreign keys inferred from value overlap. Confirm the real ones and "
        "unconfirm spurious candidates — higher overlap % is stronger evidence. "
        "Detection stays automatic; this just records your review."
    )
    rels = repository.fetch_relationships(conn, upload["id"])
    if not rels:
        st.info("No cross-table relationships were discovered for this upload.")
        return

    aliases = _aliases(conn, upload["source_client"])
    confirmed_n = sum(1 for r in rels if r["confirmed"])
    st.caption(f"**{confirmed_n} of {len(rels)}** confirmed.")

    for r in rels:
        child = f"{r['child_table']}.{display_columns(aliases, r['child_table'], r['child_column']) or r['child_column']}"
        parent = f"{r['parent_table']}.{display_columns(aliases, r['parent_table'], r['parent_column']) or r['parent_column']}"
        strength = "🟢" if r["overlap_pct"] >= 99 else ("🟠" if r["overlap_pct"] >= 90 else "🔴")
        status = "✅ Confirmed" if r["confirmed"] else "⚪ Unconfirmed"
        with st.container(border=True):
            left, right = st.columns([5, 1.4])
            left.markdown(
                f"{strength} **{html.escape(child)}** → **{html.escape(parent)}**  \n"
                f"{r['overlap_pct']}% value overlap · {status}"
            )
            label = "↩️ Unconfirm" if r["confirmed"] else "✅ Confirm"
            if right.button(label, key=f"rel_{r['id']}", width="stretch"):
                repository.set_relationship_confirmed(conn, r["id"], not r["confirmed"])
                st.rerun()


def _render_overview(conn, upload: dict) -> None:
    uid = upload["id"]
    issues = repository.fetch_issues(conn, uid)
    score = repository.score_upload(conn, uid)
    tm = compute_triage_metrics(issues)
    total = len(issues)
    overall = score["overall_score"]
    sev = Counter(i["severity"] for i in issues)
    src = Counter(i["source"] for i in issues)
    summary = recurrence.recurrence_summary(conn, uid)

    title, action = st.columns([5, 1], vertical_alignment="bottom")
    title.markdown(f'<div class="page-title">{html.escape(client_label(upload["source_client"]))}</div>',
                   unsafe_allow_html=True)
    if action.button("Reports", icon=":material/summarize:", width="stretch", key="open_reports"):
        _report_dialog(conn, upload)

    k = st.columns(4)
    k[0].markdown(ui.kpi_card("Data quality score", f"{overall:.0f}/100",
                              f"{upload['row_count']:,} rows audited", _status_color(overall)),
                  unsafe_allow_html=True)
    k[1].markdown(ui.kpi_card("Waiting for review", f"{tm['by_status']['flagged']:,}",
                              f"{tm['reviewed']} of {total} reviewed", ui.MUSTARD), unsafe_allow_html=True)
    k[2].markdown(ui.kpi_card("High severity", f"{sev.get('high', 0):,}",
                              f"{summary['worsening']} getting worse" if summary["has_previous"] else "first audit",
                              ui.CORAL), unsafe_allow_html=True)
    k[3].markdown(ui.kpi_card("Found by AI", f"{src.get('llm', 0):,}",
                              f"{src.get('rule', 0):,} found by rules", ui.LAVENDER), unsafe_allow_html=True)

    if not total:
        st.info("No issues recorded for this audit.")
        return

    left, right = st.columns([1, 2])
    with left.container(border=True):
        st.markdown('<div class="card-title">Issues by severity</div>', unsafe_allow_html=True)
        order = ["high", "medium", "low"]
        st.plotly_chart(
            ui.donut([o.title() for o in order], [sev.get(o, 0) for o in order],
                     [ui.CORAL, ui.MUSTARD, ui.MUTED], f"{total}", "issues"),
            config={"displayModeBar": False}, width="stretch", key="donut_sev",
        )
        st.markdown(
            "".join(f'<div class="meter-top"><span>{ui.pill(o.title(), c)}</span>'
                    f'<span class="meter-val">{sev.get(o, 0)}</span></div>'
                    for o, c in zip(order, [ui.CORAL, ui.MUSTARD, ui.MUTED])),
            unsafe_allow_html=True,
        )
    with right.container(border=True):
        trend = repository.fetch_score_trend(conn, upload["source_client"])
        if len(trend) >= 2:
            st.markdown('<div class="card-title">Data quality score over time</div>', unsafe_allow_html=True)
            fig = go.Figure(go.Scatter(
                x=[f"#{r['upload_id']}" for r in trend], y=[float(r["overall_score"]) for r in trend],
                mode="lines+markers", line=dict(color=ui.SAGE, width=3, shape="spline"),
                marker=dict(size=8, color=ui.SAGE), fill="tozeroy", fillcolor="rgba(143,201,180,0.12)",
                hovertemplate="Audit %{x}: %{y:.0f}/100<extra></extra>",
            ))
            fig.update_yaxes(range=[0, 100])
            fig.update_xaxes(type="category", title_text="audit")
        else:
            st.markdown('<div class="card-title">Issues by dimension</div>', unsafe_allow_html=True)
            by_dim = Counter(DIMENSION_ALIASES.get(i["dimension"], i["dimension"]) for i in issues)
            fig = go.Figure(go.Bar(x=list(by_dim.values()), y=list(by_dim), orientation="h",
                                   marker=dict(color=ui.BLUE), hovertemplate="%{y}: %{x}<extra></extra>"))
        st.plotly_chart(ui.style_figure(fig, height=318), config={"displayModeBar": False},
                        width="stretch", key="trend_chart")

    c1, c2 = st.columns([2, 1])
    with c1.container(border=True):
        st.markdown('<div class="card-title">Issues needing attention</div>', unsafe_allow_html=True)
        aliases = _aliases(conn, upload["source_client"])
        top = sorted(
            (i for i in issues if i["status"] == "flagged"),
            key=lambda i: ({"high": 0, "medium": 1}.get(i["severity"], 2), -(i.get("affected_row_count") or 0)),
        )[:7]
        rows = []
        for i in top:
            col = display_columns(aliases, i["table_name"], i["column_name"])
            trend_pill = ui.pill(*ui.TREND[i["trend_status"]]) if i.get("trend_status") in ui.TREND else ""
            rows.append(
                f'<tr><td><span class="loc">{html.escape(i["table_name"])}</span>'
                f'{f" <span class=col>· {html.escape(col)}</span>" if col else ""}</td>'
                f'<td>{ui.pill(i["severity"].title(), ui.SEVERITY[i["severity"]])}</td>'
                f'<td>{ui.pill(*ui.SOURCE[i["source"]])}</td>'
                f'<td>{html.escape(i["dimension"].replace("_", " "))}</td>'
                f'<td class="num">{(i.get("affected_row_count") or 0):,}</td><td>{trend_pill}</td></tr>'
            )
        st.markdown(
            '<table class="itable"><tr><th>Where</th><th>Severity</th><th>Found by</th><th>Dimension</th>'
            '<th class="num">Rows</th><th>Trend</th></tr>' + "".join(rows) + "</table>"
            if rows else '<div class="card-sub">Everything has been reviewed. 🎉</div>',
            unsafe_allow_html=True,
        )
        pages = st.session_state.get("_pages", {})
        if "triage" in pages:
            st.page_link(pages["triage"], label="Review all issues", icon=":material/arrow_forward:")
    with c2.container(border=True):
        st.markdown('<div class="card-title">Since the last audit</div>', unsafe_allow_html=True)
        if not summary["has_previous"]:
            st.markdown('<div class="card-sub">This is the first audit for this organisation. '
                        'Trends appear from the second audit.</div>', unsafe_allow_html=True)
        else:
            for label, value, color in [
                ("New issues", summary["new"], ui.BLUE), ("Recurring", summary["recurring"], ui.MUTED),
                ("Getting worse", summary["worsening"], ui.CORAL), ("Improving", summary["improving"], ui.SAGE),
                ("Not seen this time", summary["not_seen"], ui.LAVENDER),
            ]:
                st.markdown(f'<div class="meter-top" style="margin:10px 0"><span>{ui.pill(label, color)}</span>'
                            f'<span class="meter-val">{value}</span></div>', unsafe_allow_html=True)


def _render_recurrence_summary(conn, upload: dict) -> None:
    summary = recurrence.recurrence_summary(conn, upload["id"])
    st.markdown("##### Compared with the previous audit")
    if not summary["has_previous"]:
        st.caption(f"This is the first audit for {client_label(upload['source_client'])}; "
                   "new / recurring trends appear from the second audit onwards.")
        return
    c = st.columns(5)
    c[0].metric("🆕 New", summary["new"])
    c[1].metric("🔁 Recurring", summary["recurring"])
    c[2].metric("▲ Worsening", summary["worsening"])
    c[3].metric("▼ Improving", summary["improving"])
    c[4].metric("👋 Not seen this time", summary["not_seen"])
    if summary["not_seen"]:
        with st.expander(f"Issues from the previous audit not seen in this one ({summary['not_seen']})"):
            st.caption("Kept for the record, not deleted — they may have been fixed, or the data changed.")
            aliases = _aliases(conn, upload["source_client"])
            st.dataframe(
                [
                    {
                        "Location": i["table_name"] + (
                            "." + display_columns(aliases, i["table_name"], i["column_name"])
                            if i["column_name"] else ""),
                        "Dimension": i["dimension"],
                        "Source": i["source"],
                        "Rows last time": i["affected_row_count"],
                        "What was found": (i.get("description") or "")[:160],
                    }
                    for i in recurrence.not_seen_issues(conn, upload["id"])
                ],
                width="stretch", hide_index=True,
            )


def _process_upload(files, client: str, run_llm: bool, draft_docs: bool, data_as_of=None) -> dict:
    """Write uploaded files to a temp dir and run the full audit pipeline.

    Schema docs are drafted first so that, once verified, a later audit's
    LLM pass can use them; drafting only covers tables not yet documented.
    """
    tmp = Path(tempfile.mkdtemp(prefix="dq_upload_"))
    for f in files:
        (tmp / f.name).write_bytes(f.getvalue())

    drafted = document_directory(tmp, source_client=client) if draft_docs else {}
    llm_issues, prompt_log = None, []
    if run_llm:
        audited = audit_directory(tmp, source_client=client, prompt_log=prompt_log)
        llm_issues = [issue for table_issues in audited.values() for issue in table_issues]
    result = persist_audit(tmp, source_client=client, llm_issues=llm_issues, prompt_log=prompt_log,
                           data_as_of=data_as_of, filename=", ".join(f.name for f in files)[:200])
    result["drafted_tables"] = len(drafted)
    return result


NEW_COMPANY = "__new__"


def _render_upload(conn, in_dialog: bool = False) -> None:
    import datetime as _dt

    if not in_dialog:
        st.markdown('<div class="page-title">New audit</div>', unsafe_allow_html=True)
    st.caption(
        "Choose the company, add its export files (CSV or Excel — headerless ERP exports are fine) "
        "and run the audit. Upload related tables together (e.g. an invoice header and its lines) so "
        "cross-table checks can run."
    )
    companies = ws.list_workspaces(conn)
    names = {c["slug"]: c["name"] for c in companies}
    current = st.session_state.get("workspace")
    options = [*names, NEW_COMPANY]
    picked = st.selectbox(
        "Company", options,
        index=options.index(current) if current in names else (0 if names else len(options) - 1),
        format_func=lambda k: "＋ New company…" if k == NEW_COMPANY else names[k],
        key=f"imp_company_{in_dialog}",
    )
    new_name = ""
    if picked == NEW_COMPANY:
        new_name = st.text_input("New company name", placeholder="e.g. Sunrise Traders",
                                 key=f"imp_new_{in_dialog}").strip()

    c1, c2 = st.columns([1, 2])
    as_of = c1.date_input("Data as of", value=_dt.date.today(), key=f"imp_asof_{in_dialog}",
                          help="The date the export was taken. Imports are ordered by this date for trends.")
    files = c2.file_uploader("Export files", type=["csv", "xlsx", "xls"], accept_multiple_files=True,
                             key=f"imp_files_{in_dialog}")

    run_llm = st.checkbox("Also run AI contextual analysis (finds issues rules can't; sends only masked samples)",
                          value=True, key=f"imp_llm_{in_dialog}")
    draft_docs = st.checkbox("Draft schema documentation with AI for new tables (review it in Schema review)",
                             value=False, key=f"imp_docs_{in_dialog}")

    ready = bool(files) and (picked != NEW_COMPANY or bool(new_name))
    if st.button("Run audit", type="primary", disabled=not ready, icon=":material/play_arrow:",
                 key=f"imp_run_{in_dialog}"):
        try:
            client = ws.create_workspace(conn, new_name, created_by=_username()) if picked == NEW_COMPANY else picked
        except ValueError as exc:
            st.error(str(exc))
            return
        steps = ["profiling", "running rules"] + (["drafting schema docs"] if draft_docs else []) \
            + (["AI analysis"] if run_llm else []) + ["scoring"]
        with st.spinner(", ".join(steps).capitalize() + "… this can take a few minutes for large exports."):
            try:
                result = _process_upload(files, client, run_llm, draft_docs, data_as_of=as_of)
            except Exception as exc:  # noqa: BLE001 -- surface any failure to the user
                st.error(f"Audit failed: {exc}")
                return
        _switch_company(client, result["upload_id"])
        st.session_state["flash"] = (
            f"Audit #{result['upload_id']} complete — {result['issues']} issues "
            f"({result['rule_issues']} by rules, {result['llm_issues']} by AI) · "
            f"data quality score {result['overall_score']:.0f}/100"
        )
        st.rerun()


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

    with st.expander("⚖️ How the score is weighted"):
        weights = load_weights()
        contribs = score_contributions(score)
        rows = [
            {
                "Dimension": label,
                "Weight": f"{weights[key[:-6]] * 100:.0f}%",
                "Health (0–100)": f"{score[key]:.0f}",
                "Contribution": contribs[key[:-6]],
            }
            for label, key in DIMENSION_ROWS
        ]
        st.dataframe(rows, width="stretch", hide_index=True)
        st.caption(
            f"Weight × health = contribution; contributions sum to the overall "
            f"score ({overall:.0f}). Retune in config/score_weights.json."
        )

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
        st.line_chart(df, y="DQ score", color=ui.BLUE)
    else:
        st.caption(
            f"Only one scored upload for **{client_label(upload['source_client'])}** so far — "
            "the trend line appears once another upload for this client is audited "
            "(e.g. a later export, or re-auditing after fixes)."
        )

    _render_triage_metrics(conn, upload)
    _render_complementarity(conn, upload)


def _render_complementarity(conn, upload: dict) -> None:
    st.markdown("#### Rule–LLM complementarity (E2)")
    comp = real_complementarity(repository.fetch_issues(conn, upload["id"]))
    if comp["n_locations"] == 0:
        st.caption("No issues to analyse yet.")
        return

    o = comp["overall"]
    c1, c2, c3 = st.columns(3)
    c1.metric("⚙️ Rule-only", f"{o['rule_only']}", f"{o['rule_only_pct']}%")
    c2.metric("🤖 LLM-only", f"{o['llm_only']}", f"{o['llm_only_pct']}%")
    c3.metric("🔗 Both", f"{o['both']}", f"{o['both_pct']}%")
    st.caption(
        f"{comp['n_locations']} distinct issue locations. A low **Both** share means "
        "the rules and the LLM catch *different* problems — complementary, not redundant."
    )

    by_dim = comp["by_dimension"]
    if by_dim:
        df = pd.DataFrame(
            [
                {"dimension": d, "Rule only": v["rule_only"], "LLM only": v["llm_only"], "Both": v["both"]}
                for d, v in by_dim.items()
            ]
        ).set_index("dimension")
        st.bar_chart(df, color=["#2563eb", "#7c3aed", "#14b8a6"], horizontal=True, stack=True)

        st.download_button(
            "⬇️ Download figure (PNG, for the dissertation)",
            data=_e2_figure_png(json.dumps(by_dim, sort_keys=True),
                                f"Rule–LLM complementarity — {client_label(upload['source_client'])}"),
            file_name=f"complementarity_{client_label(upload['source_client']).replace(' ', '_')}.png",
            mime="image/png",
        )


@st.cache_data(show_spinner=False)
def _e2_figure_png(by_dim_json: str, title: str) -> bytes:
    return figure_png_bytes(json.loads(by_dim_json), title)


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
    aliases = _aliases(conn, upload["source_client"])
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
    when = upload.get("data_as_of") or upload["upload_date"]
    st.markdown(
        f'<div class="context-line"><b>{html.escape(client_label(upload["source_client"]))}</b> · '
        f'audit #{upload["id"]} · data as of {when:%d %b %Y} · {total} issues · {reviewed} reviewed</div>',
        unsafe_allow_html=True,
    )


def _render_bulk_actions(conn, issue_ids: list[int]) -> None:
    with st.expander(f"⚡ Bulk actions — apply to all {len(issue_ids)} shown"):
        cols = st.columns(4)
        actions = [
            ("✅ Accept all", "accepted", "bulk_acc"),
            ("❌ Reject all", "rejected", "bulk_rej"),
            ("🤔 Consider all", "considered", "bulk_con"),
            ("↩️ Reset all", "flagged", "bulk_reset"),
        ]
        for col, (label, status, key) in zip(cols, actions):
            if col.button(label, key=key, width="stretch"):
                repository.set_issue_statuses(conn, issue_ids, status, reviewed_by=_username())
                st.rerun()
        st.caption("Applies to the current filtered view. Reversible — re-filter and reset any time.")


def _trend_html(issue: dict, history: list[dict] | None, projection: dict | None) -> str:
    if not history or len(history) < 2:
        return ""
    counts = [h["affected_row_count"] for h in history]
    if len(set(counts)) == 1:
        out = (f"📈 Seen in {len(history)} audits since {history[0]['as_of']:%d %b %Y} · "
               f"unchanged at <b>{counts[0]:,}</b> rows")
        return f'<div class="issue-trend">{out}</div>'
    same_day = len({h["as_of"] for h in history}) < len(history)
    steps = " → ".join(
        (f"#{h['upload_id']}" if same_day else f"{h['as_of']:%d %b}") + f": <b>{h['affected_row_count']:,}</b>"
        for h in history[-6:]
    )
    out = f"📈 Seen in {len(history)} audits · {steps}"
    if projection and float(projection["slope"]) != 0:
        out += (f'<br><span class="est">Next audit, if the trend continues: about '
                f'{float(projection["projected_value"]):,.0f} rows (estimate from a straight-line '
                f'fit over {projection["n_points"]} audits)</span>')
    return f'<div class="issue-trend">{out}</div>'


def _issue_pills(issue: dict, with_status: bool = True) -> str:
    pills = ""
    if issue.get("trend_status") in TREND_STYLE:
        pills += _pill(*TREND_STYLE[issue["trend_status"]])
    pills += _pill(issue["severity"].title(), SEVERITY_COLOR.get(issue["severity"], ui.MUTED))
    pills += _pill(*SOURCE_STYLE.get(issue["source"], (issue["source"], ui.MUTED)))
    if with_status and issue["status"] != "flagged":
        label, color = ui.STATUS[issue["status"]]
        who = f" · {issue['reviewed_by']}" if issue.get("reviewed_by") else ""
        pills += _pill(label + who, color)
    return pills


def _location_html(issue: dict, aliases: dict) -> str:
    col = display_columns(aliases or {}, issue["table_name"], issue["column_name"])
    return html.escape(issue["table_name"]) + (f' <span class="col">· {html.escape(col)}</span>' if col else "")


def _issue_row(conn, issue: dict, aliases: dict) -> None:
    """One compact, scannable row with quick decisions; Open shows everything."""
    sev_color = SEVERITY_COLOR.get(issue["severity"], ui.MUTED)
    rows = issue.get("affected_row_count")
    if rows is None:
        rows = (issue.get("row_reference") or {}).get("count")
    meta = [issue["dimension"].replace("_", " ")]
    if rows is not None:
        meta.append(f"{rows:,} row(s) affected")
    if issue.get("fixable") == "yes":
        meta.append("fixable")
    text, decide_a, decide_r, open_b = st.columns([8.4, 0.75, 0.75, 1.1], vertical_alignment="center")
    text.markdown(
        f'''<div class="issue-row" style="--sev:{sev_color}">
  <div class="top"><span class="loc">{_location_html(issue, aliases)}</span>
  <span class="pills">{_issue_pills(issue)}</span></div>
  <div class="sum">{html.escape(issue["description"] or "")}</div>
  <div class="meta">{html.escape(" · ".join(meta))}</div>
</div>''',
        unsafe_allow_html=True,
    )
    if decide_a.button("", key=f"acc_{issue['id']}", icon=":material/check:", help="Accept — this is a real issue",
                       width="stretch"):
        _decide(conn, issue["id"], "accepted")
    if decide_r.button("", key=f"rej_{issue['id']}", icon=":material/close:", help="Reject — not a problem",
                       width="stretch"):
        _decide(conn, issue["id"], "rejected")
    if open_b.button("Open", key=f"open_{issue['id']}", icon=":material/open_in_full:", width="stretch"):
        _study_log("detail_opened", {"issue_id": issue["id"], "severity": issue["severity"]})
        _issue_dialog(conn, issue["id"])


@st.dialog("Issue details", width="large")
def _issue_dialog(conn, issue_id: int) -> None:
    issue = repository.fetch_issue(conn, issue_id)
    if issue is None:
        st.error("This issue no longer exists.")
        return
    if _is_participant():
        issue = sp.overlay([issue], sp.decisions(conn, _username()), _username())[0]
    aliases = _aliases(conn, issue["source_client"])
    sev_color = SEVERITY_COLOR.get(issue["severity"], ui.MUTED)
    fix = ("Fixable from the data itself" if issue.get("fixable") == "yes"
           else "Needs a business decision to fix" if issue.get("fixable") == "no" else "")
    st.markdown(
        f'''<div class="issue-card" style="--sev:{sev_color}">
  <div class="issue-head"><span class="issue-title">{_location_html(issue, aliases)}</span>
  <span class="pills">{_issue_pills(issue)}</span></div>
  <div class="issue-meta">{html.escape(issue["dimension"].replace("_", " ").capitalize())} · issue #{issue["id"]}
   · audit #{issue["upload_id"]} (data as of {issue["as_of"]:%d %b %Y}){" · " + fix if fix else ""}</div>
  <div class="section-label">What was found</div>
  <div class="issue-desc">{html.escape(issue["description"] or "")}</div>
  {f'<div class="section-label">What to do</div><div class="issue-rec">{html.escape(issue["recommendation"])}</div>'
   if issue.get("recommendation") else ""}
  {_trend_html(issue, recurrence.issue_history(conn, issue_id),
               recurrence.projections_for_upload(conn, issue["upload_id"]).get(issue_id))}
</div>''',
        unsafe_allow_html=True,
    )

    why, rows_tab = st.tabs(["Why this was flagged", "Affected rows"])
    with why:
        rule = repository.fetch_rule(conn, issue["rule_ref"]) if issue.get("rule_ref") else None
        if rule:
            st.markdown(f"**{rule['what_text']}**")
            st.markdown(rule["why_text"])
            st.caption(_evidence_text(rule) + " · rule created automatically from the data profile")
        elif issue["source"] == "llm":
            st.markdown("Found by the **AI auditor**, which looked at the table's statistics and a small, "
                        "privacy-masked sample of rows for problems fixed rules can't express "
                        "(unusual values, business-logic conflicts, near-duplicates).")
            st.caption("AI findings can be wrong — your accept / reject decisions measure how often, "
                       "and rejected findings teach the AI what not to report for this company.")
        else:
            st.caption("No rule details were recorded for this issue.")
    with rows_tab:
        ref = issue.get("row_reference") or {}
        samples = ref.get("samples")
        if samples:
            st.caption(f"Sample of {len(samples)} of {ref.get('count', len(samples)):,} affected row(s), as they "
                       f"were at audit time.")
            st.dataframe(
                [{"row #": smp["row"], **{display_columns(aliases, issue["table_name"], c): v
                                          for c, v in smp["values"].items()}} for smp in samples],
                width="stretch", hide_index=True,
            )
        elif ref.get("sample_rows"):
            st.caption("Example row numbers: " + ", ".join(str(r) for r in ref["sample_rows"][:10]))
        else:
            st.caption("No example rows were recorded for this issue.")

    st.markdown('<div class="section-label">Your decision</div>', unsafe_allow_html=True)
    note = st.text_area("Note (optional)", value=issue.get("user_note") or "", key=f"dlg_note_{issue_id}",
                        placeholder="e.g. Known promotion — discounts above 50% are allowed in December.",
                        height=70)
    b = st.columns([1, 1, 1, 1.5])
    choices = [("Accept", "accepted", ":material/check:", "primary"), ("Reject", "rejected", ":material/close:", "secondary"),
               ("Unsure", "considered", ":material/help:", "secondary")]
    for col, (label, status, icon, kind) in zip(b, choices):
        if col.button(label, key=f"dlg_{status}_{issue_id}", icon=icon, type=kind, width="stretch"):
            if _is_participant():
                sp.save_decision(conn, _username(), issue_id, status, note)
            else:
                repository.update_issue_status(conn, issue_id, status, note.strip() or None, reviewed_by=_username())
            st.session_state["flash"] = f"Issue #{issue_id} marked {ui.STATUS[status][0].lower()}"
            st.rerun()
    if b[3].button("Discuss in chat", key=f"dlg_chat_{issue_id}", icon=":material/forum:", width="stretch"):
        _open_issue_thread(conn, issue, aliases)
        st.switch_page(st.session_state["_pages"]["ask"])
    if issue["status"] != "flagged":
        st.caption(f"Currently **{ui.STATUS[issue['status']][0].lower()}**"
                   + (f" by {issue['reviewed_by']}" if issue.get("reviewed_by") else "")
                   + (f" on {issue['reviewed_at']:%d %b %Y %H:%M}" if issue.get("reviewed_at") else "") + ".")


def _decide(conn, issue_id: int, status: str) -> None:
    if _is_participant():
        own = sp.decisions(conn, _username()).get(issue_id) or {}
        sp.save_decision(conn, _username(), issue_id, status, own.get("note"))
        st.rerun()
    issue = repository.fetch_issue(conn, issue_id)
    repository.update_issue_status(conn, issue_id, status, (issue or {}).get("user_note"), reviewed_by=_username())
    st.rerun()


if __name__ == "__main__":
    main()
