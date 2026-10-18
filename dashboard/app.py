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

import hashlib
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

from app.aliases import client_label, display_column_code, display_columns, load_client_labels
from app.db import repository
from app.db.persist import persist_audit
import json

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
from app.reports import audit_trail, grouping
from app.aliases import named   # col_N in issue text -> the company's column names
from app.reports.wording import DATE_FORMAT, plain, show   # stored issue / rule text -> plain English
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
STATIC = Path(__file__).resolve().parent / "static"   # logo, wordmark, browser-tab icon

ABOUT_MD = f"""
**{APP_NAME}** is an explainable data quality audit tool for SME ERP
exports. It reads CSV/Excel files (no database access) and flags issues in
plain language.

**How issues are found:**
- **Rules**: fixed checks over *every row*: missing values, wrong
  values, inconsistent values, duplicates, date problems and broken links
  between tables.
- **AI**: an AI auditor looks at statistics and a small, privacy-masked
  sample to catch problems in context that rules can't express (unlikely
  discounts, quantities 100 times too large, price outliers, near-duplicates).

The two are **complementary**, not redundant — see the complementarity
figure on the Scores tab.

**DQ score** is severity-weighted (not a flat average), so a critical
cross-table break outweighs a handful of trivial nulls.

**Triage** — mark each issue accept / reject / consider. Those decisions
become the ground truth that measures the tool's own precision.
"""

DIMENSIONS = ["completeness", "validity", "consistency", "uniqueness", "timeliness", "cross_table", "accuracy"]
STATUSES = ["flagged", "accepted", "rejected", "considered"]
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


@st.cache_resource
def _schema_ready() -> bool:
    """Apply pending migrations once per server process, not on every click
    (each check is a round trip to the hosted database)."""
    repository.apply_schema(get_conn())
    return True


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


def _pill(text: str, color: str, title: str = "") -> str:
    return ui.pill(text, color, title)


def _severity_pill(severity: str) -> str:
    return _pill(severity.title(), SEVERITY_COLOR.get(severity, ui.MUTED), grouping.SEVERITY_HELP.get(severity, ""))


def _type_html(dimension: str) -> str:
    """The plain issue type with its one-line explanation on hover."""
    return (f'<span class="hint" title="{html.escape(grouping.type_help(dimension))}">'
            f'{html.escape(grouping.type_label(dimension))}</span>')


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


def _brand_html() -> str:
    """The sign-in hero: logo, headline, one line, an example issue and three chips."""
    mark = (STATIC / "logo.svg").read_text(encoding="utf-8")
    return f'''<div class="auth-hero">
  <div class="auth-logo"><span class="mark">{mark}</span><span>{html.escape(APP_NAME)}</span></div>
  <div class="auth-copy">
    <div class="auth-h1" role="heading" aria-level="1">Clear answers about <span>messy data.</span></div>
    <p>Upload an ERP export. See what's wrong, why it matters and what to do.</p>
    <div class="auth-example" aria-hidden="true">
      <div class="top"><span class="sev">High</span><b>Invoice lines · Price</b><span class="ex">Example</span></div>
      <div class="what">Some items are sold at a price of zero.</div>
      <div class="todo"><b>What to do:</b> check these lines against the price list.</div>
    </div>
  </div>
  <ul class="auth-chips"><li>Rules + AI</li><li>Plain English</li><li>Personal data masked</li></ul>
</div>'''


def _render_login(conn) -> None:
    """One sign-in screen: staff form, and below it the study code. Participants can be
    sent straight here with the code filled in: <site>/?study=P03."""
    link = st.query_params.get("study")
    prefill = sp.normalise_code(link or "")
    prefill = prefill if sp.CODE_RE.match(prefill) else ""
    # after signing out the browser keeps the (now empty) sidebar open; hide it here
    st.markdown('<style>[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], '
                '[data-testid="stExpandSidebarButton"] { display: none !important; }</style>',
                unsafe_allow_html=True)
    hero, side = st.container(key="auth_page").columns([1, 1.1], gap="large", vertical_alignment="center")
    hero.markdown(_brand_html(), unsafe_allow_html=True)
    with side.container(key="auth_card"):
        st.markdown(f'<div class="auth-mlogo"><span class="mark">{(STATIC / "logo.svg").read_text(encoding="utf-8")}'
                    f'</span>{html.escape(APP_NAME)}</div><div class="auth-title">Sign in</div>',
                    unsafe_allow_html=True)
        _staff_login(conn)
        if sp.signin_open(conn):
            st.markdown('<div class="auth-divider"><span>Study participant?</span></div>', unsafe_allow_html=True)
            _study_login(conn, prefill)
        elif link:   # an old study link after the study closed
            st.caption("The usability study has finished. Thank you for taking part.")


def _study_login(conn, prefill: str) -> None:
    with st.container(key="row_study"), st.form("study_login", border=False):   # code and Start stay side by side
        code_col, go_col = st.columns([3, 1], vertical_alignment="bottom")
        code = code_col.text_input("Study code", max_chars=3, placeholder="Study code, e.g. P03", value=prefill,
                                   label_visibility="collapsed")
        go = go_col.form_submit_button("Start", width="stretch")
    st.caption("About 10 minutes. No real data is changed.")
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


def _staff_login(conn) -> None:
    if auth.count_users(conn) == 0:
        st.info(
            "No accounts exist yet. Create the first administrator from a terminal:\n\n"
            "`cd backend && python -m app.auth.users create admin --role=admin`"
        )
    with st.form("login", border=False):
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
    with st.sidebar.container(border=True, key="account_card"):
        name_col, out_col = st.columns([3, 1.1], vertical_alignment="center")
        name_col.markdown(f"**{html.escape(user['username'])}**  \n:gray[{role}]")
        if out_col.button("", key="sign_out", icon=":material/logout:", help="Sign out", width="stretch"):
            for k in ("user", "username"):
                st.session_state.pop(k, None)
            st.rerun()


def main() -> None:
    # SVG, not PNG: the hosted server returned errors for the PNG logo files (18 Oct)
    st.set_page_config(page_title=APP_NAME, page_icon=str(STATIC / "logo.svg"), layout="wide")
    ui.apply(st.context.theme.type)  # follows the viewer's Light / Dark setting
    st.markdown(ui.css(), unsafe_allow_html=True)
    try:
        conn = _db()
        _schema_ready()
    except psycopg.OperationalError:
        st.error("We can't reach the database right now. Please wait a minute and refresh the page.",
                 icon=":material/cloud_off:")
        return

    if _current_user() is None:
        _apply_appearance("Dark", only_if_unset=True)   # dark is the default for a browser with no choice yet
        _render_login(conn)
        return
    st.logo(str(STATIC / f"logo-{ui.MODE}.svg"), size="large", icon_image=str(STATIC / "logo.svg"))
    if _is_participant():
        _participant_main(conn)
        return
    admin = _is_admin()

    ws.sync_from_uploads(conn)
    ws.refresh_names(conn)
    # study / test / replay copies never appear in the picker (Settings lists them, tagged)
    companies = ws.list_workspaces(conn, kinds=("real",))
    # top of the sidebar: company, then audit, then the admin's New company / Import
    slug = _company_picker(conn, companies, admin)
    upload = None
    if slug != ALL_COMPANIES:
        uploads = repository.list_uploads(conn, source_client=slug)
        if uploads:
            upload = _audit_picker(slug, uploads)
    if admin:
        if st.sidebar.button("New company", icon=":material/add_business:", key="new_company", width="stretch",
                             help="Create a new company"):
            _new_company_dialog(conn)
        # one import screen: the New audit page (a dialog ran off small screens)
        go_import = st.sidebar.button("Import", key="import_btn", icon=":material/upload_file:", width="stretch",
                                      help="Upload a new export and run an audit")
    else:
        go_import = False

    navigation = _build_navigation(conn, slug, upload, admin)
    if go_import:
        st.switch_page(st.session_state["_pages"]["new-audit"])
    def review_progress() -> None:       # sits right under the Review pages
        counts = repository.triage_counts(conn, upload["id"])
        done = sum(counts.get(k, 0) for k in ("accepted", "rejected", "considered"))
        total = done + counts.get("flagged", 0)
        pct = round(100 * done / total) if total else 0
        st.sidebar.markdown(
            f'<div class="progress-card"><div class="top"><b>Review progress</b><span>{done:,} / {total:,}</span></div>'
            f'<div class="bar"><div style="width:{pct}%"></div></div>'
            f'<div class="sub">{counts.get("flagged", 0):,} issues still to review · this audit</div></div>',
            unsafe_allow_html=True)

    _render_sidebar_nav(admin, after_review=review_progress if upload is not None else None)
    with st.sidebar.expander(f"About {APP_NAME}", icon=":material/info:"):
        st.markdown(ABOUT_MD)
    _render_account_sidebar()

    if st.session_state.get("flash"):
        st.toast(st.session_state.pop("flash"), icon=":material/check_circle:")
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
    navigation = _build_navigation(conn, slug, uploads[0], admin=False, participant=True)
    if device == "desktop":
        _study_task_panel(conn, code, task, st.sidebar)
        _render_sidebar_nav(False)
        _render_account_sidebar()
    else:   # phones / tablets hide the sidebar, so the task and the pages sit on top
        _study_task_panel(conn, code, task, st)
        _render_top_nav(navigation.url_path or "overview")
    navigation.run()


def _render_top_nav(current: str) -> None:
    pages = st.session_state["_pages"]
    with st.container(key="row_topnav"):
        for col, (key, page) in zip(st.columns(len(pages)), pages.items()):
            title, icon, _section = PAGE_SPECS[key]
            with col.container(key="topnav_on" if key == current else f"topnav_{key}"):
                st.page_link(page, label=title, icon=f":material/{icon}:")


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
        st.info("This takes about 10 minutes, on a phone or a computer.", icon=":material/schedule:")
        agree = st.checkbox("I agree to take part. My answers are anonymous and may be used in a university "
                            f"research project. (Your code: {code})")
        if st.button("Start", type="primary", disabled=not agree, icon=":material/play_arrow:", key="study_start"):
            sp.give_consent(conn, code, device, browser)
            st.rerun()


def _study_task_panel(conn, code: str, task: int, where=st.sidebar) -> None:
    title, text = sp.TASKS[task]
    with where.container(border=True, key="study_panel"):
        st.markdown(f'<div class="sidebar-section">Study · task {task} of {sp.N_TASKS}</div>',
                    unsafe_allow_html=True)
        st.progress((task - 1) / sp.N_TASKS)
        st.markdown(f"**{title}**")
        st.markdown(text)
        answer = None
        if task == 1:
            answer = st.text_area("In one sentence, what is wrong?", key=f"study_answer_{code}", height=90)
        if where is st.sidebar:
            done_col = cant_col = st
        else:   # phones: one line of actions
            done_col, cant_col = st.container(key="row_study_actions").columns(2)
        if done_col.button("Done", type="primary", width="stretch", key=f"study_done_{task}",
                           icon=":material/check:"):
            sp.end_task(conn, code, task, "done", answer)
            st.rerun()
        if cant_col.button("I couldn't do this", width="stretch", key=f"study_cant_{task}"):
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
    is_open = sp.signin_open(conn)
    want = st.toggle("Participants can sign in", value=is_open, key="study_signin_toggle",
                     help="Shows the study-code box on the sign-in page. Closed once the study has finished; "
                          "codes, results and the study copy are kept either way.")
    if want != is_open:
        sp.set_signin_open(conn, want, updated_by=_username())
        st.toast("Study sign-in " + ("opened" if want else "closed"), icon=":material/check:")
        st.rerun()
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
        st.dataframe(   # short headers and fixed widths, so every column fits; Minutes = finished minus started
            [{"Code": r["code"], "Status": status_label[r["status"]],
              "Started": f"{r['started_at']:%d %b %H:%M}" if r["started_at"] else "",
              "Finished": f"{r['finished_at']:%d %b %H:%M}" if r["finished_at"] else "",
              "Minutes": round((r["finished_at"] - r["started_at"]).total_seconds() / 60, 1)
              if r["started_at"] and r["finished_at"] else None,
              "Device": r["device_type"] or "", "Browser": r["browser"] or "",
              "Tasks done": f"{r['tasks_done_self']} / 5", "Verified": f"{r['tasks_verified']} / 5"} for r in rows],
            hide_index=True, width="stretch",
            column_config={"Code": st.column_config.TextColumn(width="small"),
                           "Minutes": st.column_config.NumberColumn(format="%.1f", width="small"),
                           "Tasks done": st.column_config.TextColumn(width="small"),
                           "Verified": st.column_config.TextColumn(width="small")},
        )
        st.caption("Minutes = finished minus started. Tasks done is what the participant ticked; Verified is what "
                   "the system confirmed from the session log.")
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
    # real companies first; study / test / replay copies last, marked as evaluation workspaces
    options = [ALL_COMPANIES, *sorted(by_slug, key=lambda s: by_slug[s]["kind"] != "real")]
    if st.session_state.get("workspace") not in options:
        st.session_state["workspace"] = ALL_COMPANIES
    slug = st.sidebar.selectbox(
        "Company", options, key="workspace",
        format_func=lambda k: "All companies" if k == ALL_COMPANIES else
        (f"{by_slug[k]['name']}  ·  {by_slug[k]['audit_count']} audit(s)" if by_slug[k]["kind"] == "real"
         else f"Evaluation workspace · {by_slug[k]['name']}"),
    )
    return slug


def _audit_picker(slug: str, uploads: list[dict]) -> dict:
    """Sidebar, under the company: which audit is shown (latest by default)."""
    by_id = {u["id"]: u for u in uploads}
    key = f"audit_{slug}"
    if st.session_state.get(key) not in by_id:
        st.session_state[key] = uploads[0]["id"]
    latest = uploads[0]["id"]

    def label(i: int) -> str:
        when = f"{(by_id[i]['data_as_of'] or by_id[i]['upload_date']):{DATE_FORMAT}}"
        return f"Latest · {when} (#{i})" if i == latest else f"{when} (#{i})"

    if not _is_participant():   # the study copy has a single audit
        st.sidebar.selectbox("Audit", list(by_id), key=key, format_func=label)
    return by_id[st.session_state[key]]


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


def _render_sidebar_nav(admin: bool, after_review=None) -> None:
    """Sidebar pages by section; `after_review` (e.g. the review progress
    summary) is drawn right after the Review pages."""
    pages = st.session_state["_pages"]
    section = None
    for key, (title, icon, sec) in PAGE_SPECS.items():
        if key not in pages:
            continue
        if sec != section:
            if section == "Review" and after_review:
                after_review()
            st.sidebar.markdown(f'<div class="sidebar-section">{sec}</div>', unsafe_allow_html=True)
            section = sec
        st.sidebar.page_link(pages[key], label=title, icon=f":material/{icon}:")
    if section == "Review" and after_review:      # only the Review section was shown
        after_review()


def _audit_bar(conn, slug: str) -> dict | None:
    """Top-of-page context line for the audit picked in the sidebar. None (with
    an empty state) if the company has no audits yet."""
    uploads = repository.list_uploads(conn, source_client=slug)
    name = client_label(slug)
    if not uploads:
        st.markdown(f'<div class="page-title">{html.escape(name)}</div>', unsafe_allow_html=True)
        st.info("No audits for this company yet.")
        if _is_admin() and st.button("Import the first export", type="primary", icon=":material/upload_file:",
                                     key="empty_import"):
            st.switch_page(st.session_state["_pages"]["new-audit"])
        return None
    by_id = {u["id"]: u for u in uploads}
    u = by_id.get(st.session_state.get(f"audit_{slug}"), uploads[0])   # picked in the sidebar
    reviewed = u["issue_count"] - u["pending_count"]
    st.markdown(
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
        st.info("No audits yet. Use **New company** in the sidebar, then **Import** to run the first audit.")
        return

    scores = [float(r["overall_score"]) for r in rows if r["overall_score"] is not None]
    avg = sum(scores) / len(scores) if scores else 0
    k = st.columns(4)
    k[0].markdown(ui.kpi_card("Companies", f"{len(rows)}", f"{sum(r['audits'] for r in rows)} audits in total",
                              ui.BLUE), unsafe_allow_html=True)
    k[1].markdown(ui.kpi_card("Average data quality", f"{avg:.0f}",
                              f"{sum(r['row_count'] for r in rows):,} rows · latest audit of each company",
                              _status_color(avg), suffix="/ 100"), unsafe_allow_html=True)
    k[2].markdown(ui.kpi_card("Waiting for review", f"{sum(r['to_review'] for r in rows):,}",
                              f"of {sum(r['issues'] for r in rows):,} issues · latest audits", ui.MUSTARD),
                  unsafe_allow_html=True)
    k[3].markdown(ui.kpi_card("High severity", f"{sum(r['high'] for r in rows):,}",
                              f"{sum(r['ai'] for r in rows):,} found by AI · latest audits", ui.CORAL),
                  unsafe_allow_html=True)

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
                            line=dict(color=palette[n % len(palette)], width=2.5),
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


# url path -> (title, material icon, section); order = sidebar order.
# Sidebar sections by purpose. Reviewers (non-technical) see Review and
# Understand (Scores, Rules); admins also see Relationships, Set up and Research;
# study participants see only Overview / Triage / Ask.
PAGE_SPECS = {   # sidebar board of the design canvas: Review / Understand / Set up / Research
    "overview": ("Overview", "dashboard", "Review"),
    "triage": ("Triage", "fact_check", "Review"),
    "ask": ("Ask", "forum", "Review"),
    "scores": ("Scores and trend", "monitoring", "Understand"),
    "rules": ("Rules", "rule", "Understand"),
    "relationships": ("Relationships", "link", "Understand"),
    "new-audit": ("New audit", "upload_file", "Set up"),
    "schema": ("Schema review", "menu_book", "Set up"),
    "organisation": ("Organisation", "domain", "Set up"),
    "settings": ("Settings", "settings", "Set up"),
    "evaluation": ("Evaluation", "query_stats", "Research"),
    "study": ("Study", "science", "Research"),
}
ADMIN_PAGES = {"new-audit", "schema", "relationships", "organisation", "evaluation", "study", "settings"}


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
        current = None if all_mode else _audit_bar(conn, slug)
        if all_mode or current is not None:
            with st.spinner("Loading the evaluation…"):   # the charts take a few seconds on the hosted app
                _render_evaluation(conn, current)

    renderers = {
        "overview": overview,
        "triage": company_page("Triage", lambda u: _render_triage(conn, u)),
        "ask": company_page("Ask", lambda u: _render_chat(conn, u)),
        "rules": company_page("Rules", lambda u: _render_rules(conn, u)),
        "scores": company_page("Scores and trend", lambda u: _render_scores(conn, u)),
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
    """Status, Severity and Table in view; the rest under "More filters"."""
    main = [
        # participants keep the issue they just decided in view (study tasks 2-3)
        ("status", "Status", STATUSES, ALL if _is_participant() else "flagged"),
        ("severity", "Severity", ["high", "medium", "low"], ALL),
        ("table", "Table", tables, ALL),
    ]
    more = [
        ("source", "Found by", ["rule", "llm"], ALL),
        ("dimension", "Dimension", DIMENSIONS, ALL),
        ("trend", "Trend", TRENDS, ALL),
        ("fixable", "Fixable", ["yes", "no"], ALL),
    ]

    tips = {"severity": "  \n".join(grouping.SEVERITY_HELP.values()),
            "dimension": "  \n".join(f"**{grouping.type_label(d)}** ({d.replace('_', ' ')}): {h}"
                                     for d, h in grouping.TYPE_HELP.items() if d != "accuracy")}

    def box(where, key, label, options, default):
        choices = [ALL, *options]
        return where.selectbox(
            label, choices, index=choices.index(default), key=f"filter_{key}", help=tips.get(key),
            format_func=lambda v: "All" if v == ALL else FILTER_LABELS.get(
                v, v if key == "table" else v.replace("_", " ").capitalize()),
        )

    cols = st.columns([1, 1, 1.4, 1.1], vertical_alignment="bottom")
    filters = {spec[0]: box(col, *spec) for col, spec in zip(cols, main)}
    active = sum(st.session_state.get(f"filter_{k}", ALL) != ALL for k, *_ in more)
    with cols[3].popover(f"More filters ({active})" if active else "More filters", width="stretch"):
        filters.update({spec[0]: box(st, *spec) for spec in more})
    return filters


def _render_triage(conn, upload: dict) -> None:
    uid = upload["id"]
    issues = repository.fetch_issues(conn, uid)
    if _is_participant():   # a participant sees only their own decisions
        issues = sp.overlay(issues, sp.decisions(conn, _username()), _username())
    tables = sorted({i["table_name"] for i in issues})
    if _device()[0] == "desktop":
        filters = _filter_bar(tables)
    else:   # seven stacked drop-downs would fill a phone screen
        with st.expander("Filter by table, severity, status…", icon=":material/filter_list:"):
            filters = _filter_bar(tables)
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
    # v2 (after the usability study): what people see first. Every issue is
    # still stored and counted; see app.reports.grouping.
    rules = repository.fetch_rules(conn, uid)
    unused, mostly_blank = grouping.unused_rule_ids(rules), grouping.mostly_blank_rule_ids(rules)
    # what to do first: Fix now / Check with the business / Can wait (+ All issues)
    by_action = {a: [i for i in issues if grouping.action_category(i, unused, mostly_blank) == a]
                 for a in grouping.ACTIONS}
    views = [a for a in ("fix_now", "check", "can_wait") if by_action[a]] + ["all"]
    view = st.segmented_control(
        "Show", views, default=views[0], key="triage_action", label_visibility="collapsed",
        format_func=lambda v: f"All issues ({len(issues)})" if v == "all"
        else f"{grouping.ACTIONS[v]} ({len(by_action[v])})") or views[0]
    fields = grouping.unused_fields(issues, unused)
    if fields:
        n_fields = sum(len(c) for c in fields.values())
        with st.expander(f"{n_fields} field(s) are never filled in — listed here once, not as separate issues",
                         icon=":material/info:"):
            st.caption("These columns are empty in (almost) every row, so they are probably not used by the "
                       "business. They still appear under All issues.")
            for table, cols in fields.items():
                names = ", ".join(display_columns(aliases, table, c) or c for c in cols)
                st.markdown(f"**{html.escape(table)}**: {html.escape(names)}")
    if view != "all":
        issues = by_action[view]
    types = Counter(grouping.type_label(i["dimension"]) for i in issues)
    if len(types) > 1:   # plain issue types, so the mix is visible at a glance
        pick = st.pills("Issue types", list(types), selection_mode="single", key=f"triage_type_{view}",
                        format_func=lambda t: f"{t} {types[t]}", label_visibility="collapsed")
        if pick:
            issues = [i for i in issues if grouping.type_label(i["dimension"]) == pick]
    # not a "row_" container: on phones the count and the download buttons stack
    head_l, head_r = st.container(key="downloads_bar").columns([3, 2], vertical_alignment="center")
    head_l.caption(f"Showing {len(issues)} issue(s)")
    if issues:
        org = client_label(upload["source_client"])
        stem = f"{org.replace(' ', '_').replace('·', '')}_audit{uid}_issues"
        downloads = head_r.container(horizontal=True, horizontal_alignment="right", gap="small")
        downloads.download_button(
            "CSV",
            data=issues_to_csv(issues, aliases, organisation=org),
            file_name=f"{stem}.csv",
            mime="text/csv",
            icon=":material/download:",
            help="Downloads exactly the issues shown (current filters), with triage status.",
        )
        downloads.download_button(
            "Excel",
            data=issues_to_xlsx(issues, aliases, organisation=org,
                                title=f"{org}: audit #{uid} issues", filters=filters),
            file_name=f"{stem}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            icon=":material/download:",
            help="Formatted workbook with an issue sheet and a summary by dimension and severity.",
            on_click=_study_log, args=("excel_downloaded", {"table": _none_if_all(filters["table"]),
                                                            "rows": len(issues)}),
        )
        if not _is_participant():   # audit trail: every decision in this audit, who made it and when
            downloads.download_button(
                "Decision log",
                data=audit_trail.decision_log_csv(audit_trail.decision_log(conn, uid),
                                                  lambda t, c: display_column_code(aliases, t, c)),
                file_name=f"{stem.removesuffix('_issues')}_decision_log.csv", mime="text/csv",
                icon=":material/history:",
                help="Every decision in this audit: issue, decision, reviewer, time and note (CSV).",
            )
            _render_selection_bar(conn, issues)
            _render_bulk_actions(conn, [i["id"] for i in issues])
    else:
        st.info("No issues match these filters. Try setting **Status** to *All*, or show **All issues**.")
    # mined value-order checks are often coincidental: one folded section at the bottom
    order = [i for i in issues if grouping.is_value_order(i)]
    for item in grouping.by_priority(grouping.group([i for i in issues if not grouping.is_value_order(i)])):
        _render_triage_item(conn, item, aliases)      # most important first
    if order:
        with st.expander(f"Unusual value order ({len(order)})", icon=":material/swap_vert:"):
            st.caption("One value is normally not larger than another in the same row (for example a discount "
                       "and a total), but some rows break that pattern. Many of these patterns are coincidence, "
                       "so check them last.")
            for issue in sorted(order, key=grouping.priority, reverse=True):   # no group cards: no nested expanders
                _issue_row(conn, issue, aliases)


def _render_triage_item(conn, item: dict, aliases: dict) -> None:
    if item["kind"] == "issue":
        _issue_row(conn, item["issue"], aliases)
    else:
        _group_card(conn, item, aliases)


def _group_card(conn, item: dict, aliases: dict) -> None:
    """Several issues from the same check on one table, as one card."""
    color = SEVERITY_COLOR.get(item["severity"], ui.MUTED)
    cols = [(display_columns(aliases, item["table"], i["column_name"]) or "whole row").replace(", ", " & ")
            for i in item["issues"]]
    preview = ", ".join(cols[:4]) + (f" and {len(cols) - 4} more" if len(cols) > 4 else "")
    meta = [f"{len(item['issues'])} issues"]
    if item["rows"]:
        meta.append(f"{item['rows']:,} rows in total")
    labels = _severity_pill(item["severity"]) + _pill("Rule", ui.BLUE)
    key = hashlib.md5(item["key"].encode()).hexdigest()[:10]
    with st.container(key=f"grp_{key}"):
        st.markdown(
            f'''<div class="issue-row group-card" style="--sev:{color}">
  <div class="top"><span class="loc"><span class="dot" style="background:{color}"></span>{html.escape(item["table"])}
  <span class="col">· {html.escape(item["title"])}</span></span><span class="pills">{labels}</span></div>
  <div class="sum">{html.escape(preview)}</div>
  <div class="meta">{html.escape(" · ".join(meta))}</div>
</div>''',
            unsafe_allow_html=True,
        )
        with st.expander(f"Show the {len(item['issues'])} issues"):
            if not _is_participant():
                ids = [i["id"] for i in item["issues"]]
                st.button(f"Select all {len(ids)} in this group", key=f"selgrp_{key}", icon=":material/done_all:",
                          on_click=_set_picks, args=(ids, True))
            for issue in item["issues"]:
                _issue_row(conn, issue, aliases)


APPEARANCE = {"Dark": "Dark (default)", "Light": "Light", "System": "Match my device"}
STUDY_MODEL = "openai/gpt-oss-120b"   # the model every thesis number and the study used
WORKSPACE_TAG = {"study": "Study", "replay": "Evaluation", "test": "Evaluation"}


def _appearance(conn) -> str:
    """The signed-in person's theme choice (Dark unless they chose otherwise)."""
    user = _current_user()
    if not user:
        return "Dark"
    row = conn.execute("SELECT value FROM app_settings WHERE key = %s", (f"appearance:{user['username']}",)).fetchone()
    return row[0] if row and row[0] in APPEARANCE else "Dark"


def _apply_appearance(choice: str, only_if_unset: bool = False) -> None:
    """Make this browser use a theme. Streamlit keeps the viewer's choice in localStorage
    and reads it when the page loads, so write it and reload once. A reload ends the
    Streamlit session, so it is only used on the sign-in page (only_if_unset: a browser
    with no choice yet gets the Dark default) and right after a choice in Settings."""
    import streamlit.components.v1 as components
    components.html(f"""<script>
try {{
  const w = window.parent, key = "stActiveTheme-" + w.location.pathname + "-v2", want = {json.dumps(json.dumps(choice))};
  // Streamlit itself stores "System" on first load, so "chosen" is our own marker of a real choice
  const have = w.localStorage.getItem(key), chosen = w.localStorage.getItem("cdq-appearance");
  if (!{json.dumps(only_if_unset)}) w.localStorage.setItem("cdq-appearance", want);
  const needed = {'chosen === null && have !== want' if only_if_unset else 'have !== want'};
  if (needed && !w.sessionStorage.getItem("cdq-theme-" + want)) {{
    w.sessionStorage.setItem("cdq-theme-" + want, "1");   // at most one reload per choice and session
    w.localStorage.setItem(key, want);
    w.location.reload();
  }}
}} catch (e) {{}}
</script>""", height=0)


def _render_settings(conn) -> None:
    st.markdown('<div class="page-title">Settings</div>', unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown('<div class="card-title">Appearance</div><div class="card-sub">Saved for your account. Dark is '
                    'the default for everyone.</div>', unsafe_allow_html=True)
        current = _appearance(conn)
        choice = st.radio("Appearance", list(APPEARANCE), index=list(APPEARANCE).index(current), horizontal=True,
                          format_func=APPEARANCE.get, label_visibility="collapsed", key="appearance")
        st.caption("Changing it reloads the page in this browser, so you will be asked to sign in again.")
        if choice != current:
            conn.execute("INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES (%s, %s, %s, now()) "
                         "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, "
                         "updated_at = now()", (f"appearance:{_username()}", choice, _username()))
            conn.commit()
            _apply_appearance(choice)
        elif st.context.theme.type and choice != "System" and st.context.theme.type != choice.lower():
            if st.button(f"Use {APPEARANCE[choice]} in this browser", icon=":material/contrast:"):
                _apply_appearance(choice)

    with st.container(border=True):
        st.markdown('<div class="card-title">AI model</div><div class="card-sub">Used by the AI auditor, schema '
                    'drafting and Ask. Names, phone numbers and other personal details are masked before anything '
                    'is sent.</div>', unsafe_allow_html=True)
        options = llm_selection.llm_options()
        active = llm_selection.active_option(conn)
        names = [o["name"] for o in options]

        def _fmt(name: str) -> str:
            o = next(x for x in options if x["name"] == name)
            where = "runs on this computer" if llm_selection.is_local(o) else "cloud service"
            study = "Used in the study" if o["model"] == STUDY_MODEL else "Not used in the study"
            return f"{name} · {o['model']} · {where} · {study}"

        picked = st.radio("Active model", names, index=names.index(active["name"]), format_func=_fmt,
                          label_visibility="collapsed")
        if picked != active["name"]:
            llm_selection.set_selected(conn, picked, _username())
            st.toast(f"AI model set to {picked}", icon=":material/settings:")
            st.rerun()
        if len(options) == 1:
            st.caption("Only one option is configured. Add more with LLM_OPTIONS in the server settings.")

    with st.container(border=True):
        st.markdown('<div class="card-title">Companies</div><div class="card-sub">Each company is a folder of its '
                    'imports. Renaming keeps every audit attached. Study and evaluation copies never appear in All '
                    'companies or the company picker.</div>', unsafe_allow_html=True)
        for c in sorted(ws.list_workspaces(conn), key=lambda c: (c["kind"] != "real", c["name"])):
            with st.form(f"ws_{c['slug']}", border=False):
                a, b, d = st.columns([2.4, 1.8, 0.9], vertical_alignment="center")
                new = a.text_input("Name", value=c["name"], key=f"ws_name_{c['slug']}", label_visibility="collapsed")
                last = f"{c['last_as_of']:{DATE_FORMAT}}" if c["last_as_of"] else "no audits yet"
                tag = _pill(WORKSPACE_TAG[c["kind"]], ui.LAVENDER) + " " if c["kind"] in WORKSPACE_TAG else ""
                b.markdown(f'<div class="card-sub">{tag}{c["audit_count"]} audit{"s" if c["audit_count"] != 1 else ""}'
                           f' · latest {last}</div>', unsafe_allow_html=True)
                if d.form_submit_button("Rename", width="stretch") and new.strip() != c["name"]:
                    try:
                        ws.rename_workspace(conn, c["slug"], new)
                        st.toast(f"Renamed to {new.strip()}", icon=":material/edit:")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))

    with st.container(border=True):
        st.markdown('<div class="card-title">People</div>', unsafe_allow_html=True)
        people = auth.list_users(conn)
        st.dataframe(
            [{"Username": u["username"], "Role": "Administrator" if u["role"] == "admin" else u["role"].title(),
              "Added": f"{u['created_at']:{DATE_FORMAT}}"} for u in people],
            width="stretch", hide_index=True,
        )
        with st.form("new_user", clear_on_submit=True, border=False):
            c1, c2, c3 = st.columns([1.2, 1.2, 0.8])
            new_name = c1.text_input("Username")
            new_pw = c2.text_input("Temporary password", type="password",
                                   help=f"At least {auth.MIN_PASSWORD_LENGTH} characters.")
            new_role = c3.selectbox("Role", auth.ROLES, index=1,
                                    format_func=lambda r: "Administrator" if r == "admin" else r.title())
            if st.form_submit_button("Add person", icon=":material/person_add:"):
                try:
                    auth.create_user(conn, new_name, new_pw, new_role)
                    st.toast(f"Added {new_name}", icon=":material/person:")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001 -- show validation / duplicate errors
                    conn.rollback()
                    st.error(f"Could not add this person: {exc}")


CONFIDENCE_STYLE = {
    "LOW": (":material/error:", "orange", "Needs a closer look"),
    "MEDIUM": (":material/help:", "blue", "Medium confidence"),
    "HIGH": (":material/check:", "green", "High confidence"),
    "VERIFIED": (":material/verified:", "green", "Verified"),
}
DOC_VIEWS = {"All": None, "Needs review": "LOW", "Drafts": "draft", "Verified": "VERIFIED"}


RULE_VIEWS = {"Found problems": "violated", "Passed": "passed", "All": "all"}
EVIDENCE_LABEL = {
    "null_pct": "blank %", "null_count": "blank rows", "row_count": "rows checked",
    "negative_count": "negative rows", "negative_pct": "negative %", "min": "smallest value",
    "duplicate_rows": "duplicate rows", "distinct_pct": "distinct %", "distinct_count": "distinct values",
    "non_null_count": "filled rows", "duplicated_values": "repeated values",
    "max_date": "latest date", "conformance_pct": "held in %", "valid_pairs": "rows compared",
    "informative_rows": "rows compared", "overlap_pct": "different values matched %", "orphan_count": "rows with no match",
}
EVIDENCE_HIDDEN = {"type", "reference_date"}


def _evidence_text(rule: dict) -> str:
    ev = rule.get("evidence") or {}
    bits = []
    for key, value in ev.items():
        if key in EVIDENCE_HIDDEN or value is None:
            continue
        shown = (f"{value:,}" if isinstance(value, int) and not isinstance(value, bool)
                 else f"{value:,.1f}".removesuffix(".0") if isinstance(value, float) else value)
        bits.append(f"{EVIDENCE_LABEL.get(key, key.replace('_', ' '))}: {shown}")
    return "Evidence · " + " · ".join(bits) if bits else "Evidence · none recorded"


GENERAL_STARTERS = [
    "What are the most serious problems in this audit?",
    "Which table should we fix first, and why?",
    "Explain the data quality score in simple terms.",
]
ISSUE_STARTERS = [
    "Is this a real problem?",
    "Which rows are affected?",
    "How do I fix this?",
    "Show example rows",
]


def _issue_option_label(issue: dict, aliases: dict) -> str:
    col = display_columns(aliases, issue["table_name"], issue["column_name"])
    where = issue["table_name"] + (f" · {col}" if col else "")
    return f"#{issue['id']} · {where} · {grouping.type_label(issue['dimension'])} · {issue['severity'].title()}"


def _open_issue_thread(conn, issue: dict, aliases: dict) -> None:
    """Start a thread about one issue and make it the active one."""
    col = display_columns(aliases, issue["table_name"], issue["column_name"])
    title = f"Issue #{issue['id']} · {issue['table_name']}" + (f" · {col}" if col else "")
    tid = chat_threads.create_thread(conn, issue["source_client"], _username(), title, [issue["id"]],
                                     study_code=_username() if _is_participant() else None)
    st.session_state[f"chat_thread_{issue['source_client']}"] = tid


def _short(text: str, n: int) -> str:
    """At most n characters, cut at a whole word with "…" (never mid-word)."""
    if len(text) <= n:
        return text
    cut = text[:n - 1]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(" ,;:·-") + "…"


def _render_chat(conn, upload: dict) -> None:
    """Previous chats in a dropdown at the top, a "Discussing" line naming what the
    chat is about, then the conversation with the question bar directly under it."""
    uid, client = upload["id"], upload["source_client"]
    username = _username()
    aliases = _aliases(conn, client)
    key = f"chat_thread_{client}"

    threads = chat_threads.list_threads(conn, client, username)
    by_id = {t["id"]: t for t in threads}
    if st.session_state.get(key) not in by_id:
        st.session_state[key] = threads[0]["id"] if threads else None
    active = st.session_state[key]

    pick_col, new_col, report_col, archive_col = st.container(key="wrap_chathead").columns(
        [4.4, 1.2, 1.1, 0.55], vertical_alignment="bottom")
    if threads:
        ids = list(by_id)
        # keyed by the active chat, so opening a chat from elsewhere (an issue) is not undone here
        chosen = pick_col.selectbox(
            "Previous chats", ids, index=ids.index(active), key=f"chat_pick_{client}_{active}",
            format_func=lambda i: f"{_short(by_id[i]['title'], 52)} · {by_id[i]['updated_at']:%d %b %H:%M}")
        if chosen != active:
            st.session_state[key] = chosen
            st.rerun()
    else:
        pick_col.caption("No previous chats yet. Ask a question below to start one.")
    if new_col.button("New chat", icon=":material/add:", type="primary", width="stretch", key="chat_new"):
        st.session_state[key] = chat_threads.create_thread(
            conn, client, username, study_code=username if _is_participant() else None)
        st.rerun()

    audit_issues = repository.fetch_issues(conn, uid)
    attached = chat_threads.thread_issues(conn, active) if active else []
    options = {i["id"]: i for i in audit_issues} | {i["id"]: i for i in attached}
    focus = [options[i["id"]] for i in attached]
    history = chat_threads.messages(conn, active) if active else []
    if report_col.button("Report", icon=":material/summarize:", key=f"chat_report_{active}", width="stretch",
                         help="Create a report from this chat"):
        _report_dialog(conn, upload, focus, [dict(m) for m in history], from_chat=True)
    if active and archive_col.button("", icon=":material/archive:", key=f"chat_archive_{active}",
                                     width="stretch", help="Archive this chat"):
        chat_threads.archive_thread(conn, active)
        st.session_state[key] = None
        st.rerun()

    # what this chat is about, with a way back to the issue
    about, change = st.container(key="row_discussing").columns([5, 1.3], vertical_alignment="center")
    if focus:
        first = focus[0]
        col = display_columns(aliases, first["table_name"], first["column_name"])
        where = first["table_name"] + (f" · {col}" if col else "")
        more = f" (and {len(focus) - 1} more)" if len(focus) > 1 else ""
        about.markdown(
            f'<div class="discussing"><span class="label">Discussing:</span> <b>{html.escape(where)}</b>{more}: '
            f'{html.escape(_short(show(first["description"], first["table_name"], aliases), 110))}</div>',
            unsafe_allow_html=True)
        if about.button("Open issue", key=f"chat_open_{active}_{first['id']}", type="tertiary",
                        icon=":material/open_in_new:"):
            _issue_dialog(conn, first["id"])
    else:
        about.markdown(f'<div class="discussing"><span class="label">About:</span> audit #{uid} '
                       f'(general question)</div>', unsafe_allow_html=True)
    with change.popover("Choose issues", icon=":material/checklist:", width="stretch"):
        chosen_issues = st.multiselect(
            "Issues in this chat", list(options), default=[i["id"] for i in attached],
            format_func=lambda i: _issue_option_label(options[i], aliases),
            placeholder="None: a general question about this audit", key=f"chat_issues_{active}")
    if active and set(chosen_issues) != {i["id"] for i in attached}:
        for i in set(chosen_issues) - {i["id"] for i in attached}:
            chat_threads.attach_issue(conn, active, i)
        for i in {i["id"] for i in attached} - set(chosen_issues):
            chat_threads.detach_issue(conn, active, i)
        st.rerun()
    focus = [options[i] for i in chosen_issues]

    picked = None
    with st.container(height=520, border=False, key="chat_scroll"):
        if not history:
            st.markdown(
                '<div class="chat-empty"><div class="chat-empty-title">What would you like to know?</div>'
                '<div class="chat-empty-sub">Ask about this audit: what to fix first, what an issue means, or '
                'how to explain the results to a manager. Answers use only the audit results and statistics.'
                '</div></div>', unsafe_allow_html=True)
            if not focus:
                picked = st.pills("Suggestions", GENERAL_STARTERS, key=f"starter_{active}",
                                  label_visibility="collapsed")
        for m in history:
            with st.chat_message(m["role"], avatar=":material/person:" if m["role"] == "user"
                                 else ":material/auto_awesome:"):
                st.markdown(m["content"])
    if focus:   # questions about the chosen issue, always one click away
        picked = st.pills("Suggestions", ISSUE_STARTERS, key=f"issue_starter_{active}_{len(history)}",
                          label_visibility="collapsed") or picked
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
                if reply.endswith(assistant.FALLBACK_NOTE):
                    _study_log("chat_fallback", {})
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


SNAPSHOT_TITLES = {
    "T3 Precision": "Precision by company and detector (Wilson 95% CI)",
    "T5 Injection": "Planted-error coverage (known answer key)",
    "T6 Reproducibility": "Reproducibility and transferability across companies",
    "T7 AI stability": "AI run-to-run stability",
    "T7 Pairs": "AI stability: similarity between pairs of runs",
    "T8 Rules vs AI": "Rules vs AI: problem locations found by each",
    "T10 Projection": "Trend projection error (flat histories excluded)",
    "T11 Schema check": "AI-drafted column descriptions judged against the source schema",
    "T11 Schema docs": "Schema documentation status",
    "T13 Tasks v1": "Usability study, iteration 1: task completion",
    "T13 Tasks v2": "Usability study, iteration 2: task completion",
    "T13 SUS summary": "Usability study: SUS and explanation-clarity ratings",
    "T15 Privacy": "Privacy check of AI prompts (expected zero)",
}


@st.cache_data(show_spinner=False)
def _snapshot_tables(mtime: float) -> dict[str, pd.DataFrame]:   # mtime: re-read when the file changes
    from app.eval.export_workbook import SNAPSHOT
    return pd.read_excel(SNAPSHOT, sheet_name=None)


def _render_thesis_snapshot() -> None:
    """The frozen evaluation results, exactly as reported in the dissertation."""
    from app.eval.export_workbook import SNAPSHOT
    if not SNAPSHOT.exists():
        st.info("The thesis snapshot is kept on the researcher's computer: spreadsheets are not uploaded to the "
                "hosted site. The live numbers are under All companies.")
        return
    tables = _snapshot_tables(SNAPSHOT.stat().st_mtime)
    facts = dict(tables.pop("README").itertuples(index=False, name=None))
    st.markdown(
        '<div class="card-sub">These are the frozen numbers reported in the dissertation. They do not change '
        f'when people review issues. Generated {html.escape(str(facts.get("Generated (Sri Lanka time)", "")))} '
        f'(Sri Lanka time), commit {html.escape(str(facts.get("Git branch / commit", "")))}, '
        f'model {html.escape(str(facts.get("AI model (thesis numbers)", "")).split(" via ")[0])}.</div>',
        unsafe_allow_html=True)
    tab = st.selectbox("Table", list(tables), format_func=lambda t: f"{t.split()[0]} · {SNAPSHOT_TITLES.get(t, t)}",
                       key="eval_snapshot_tab")
    st.dataframe(tables[tab].astype(object).where(tables[tab].notna(), "–"), hide_index=True, width="stretch")


def _render_evaluation(conn, upload: dict | None) -> None:
    st.markdown('<div class="page-title">Evaluation</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">How accurate is the tool? Every accept / reject in Triage is a ground-truth '
                'label, so these numbers are measured on real data and update as people review.</div>',
                unsafe_allow_html=True)
    scopes = ["All companies", "Thesis snapshot"]
    if upload is None:
        uid, client, label = None, None, "all companies"
    else:
        uid, client = upload["id"], upload["source_client"]
        label = client_label(client)
        scopes = ["This company", "This audit", *scopes]
    scope = st.segmented_control("Scope", scopes, default=scopes[0], key="eval_scope",
                                 label_visibility="collapsed") or scopes[0]
    if scope == "Thesis snapshot":
        _render_thesis_snapshot()
        return
    st.caption({"This company": f"{label}: latest audit (as in the thesis tables).",
                "This audit": f"{label}: the audit selected in the sidebar.",
                "All companies": "Latest audit of each company in the main results; companies used only "
                                 "for the transferability test are left out, as in the thesis."}[scope])
    issues = live_eval.scope_issues(
        conn, upload_id=uid if scope == "This audit" else None,
        source_client=client if scope == "This company" else None,
    )
    audit_ids = sorted({i["upload_id"] for i in issues})
    tm = compute_triage_metrics(issues)
    rule, ai = tm["by_source"]["rule"], tm["by_source"]["llm"]
    ai_decided = ai["accepted"] + ai["rejected"]

    # every number says what it covers
    where = {"This company": "latest audit", "This audit": f"audit #{uid}",
             "All companies": f"{len(audit_ids)} companies, latest audits"}[scope]
    k = st.columns(4)
    k[0].markdown(ui.kpi_card("Rule precision", _pct(rule["empirical_precision"]),
                              f"{_decisions_caption(rule['accepted'], rule['rejected'])} · {where}", ui.BLUE),
                  unsafe_allow_html=True)
    k[1].markdown(ui.kpi_card("AI precision", _pct(ai["empirical_precision"]),
                              f"{_decisions_caption(ai['accepted'], ai['rejected'])} · {where}", ui.LAVENDER),
                  unsafe_allow_html=True)
    k[2].markdown(ui.kpi_card("AI rejection rate", _pct(ai["rejected"] / ai_decided if ai_decided else None),
                              f"decided AI findings rejected (lower is better) · {where}", ui.CORAL),
                  unsafe_allow_html=True)
    k[3].markdown(ui.kpi_card("Reviewed", f"{tm['reviewed']:,}",
                              f"{tm['by_status']['considered']} unsure · {where}", ui.MUSTARD,
                              suffix=f"/ {tm['total']:,}"), unsafe_allow_html=True)
    if not tm["reviewed"]:
        st.info("No reviews yet in this scope. Accept or reject issues in **Triage** and these figures fill in "
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
                                # value labels: a 0% bar must not look like "no data"
                                text=[f"{r['precision'] * 100:.0f}% (n={r['accepted'] + r['rejected']})" for r in sub],
                                textposition="outside", cliponaxis=False, textfont=dict(color=ui.TEXT, size=12),
                                customdata=[r["accepted"] + r["rejected"] for r in sub],
                                hovertemplate=name + " · %{y}: %{x:.0f}% of %{customdata} decisions<extra></extra>")
            fig.update_layout(barmode="group", showlegend=True, legend=dict(orientation="h", y=-0.15))
            fig.update_xaxes(range=[0, 125], tickvals=[0, 25, 50, 75, 100],
                             ticktext=["0%", "25%", "50%", "75%", "100%"])
            fig.add_vline(x=100, line=dict(color=ui.MUTED, width=1, dash="dot"))
            st.plotly_chart(ui.style_figure(fig, 280).update_layout(showlegend=True), config={"displayModeBar": False},
                            width="stretch", key="eval_prec_dim")
        else:
            st.markdown('<div class="card-sub">Appears once issues have been accepted or rejected.</div>',
                        unsafe_allow_html=True)
    with right.container(border=True):
        st.markdown('<div class="card-title">Rules vs AI — do they find different problems?</div>',
                    unsafe_allow_html=True)
        comp = live_eval.complementarity(conn, audit_ids)
        o = comp["overall"]
        if comp["n_locations"]:
            st.plotly_chart(
                ui.donut(["Rules only", "AI only", "Both"], [o["rule_only"], o["llm_only"], o["both"]],
                         [ui.BLUE, ui.LAVENDER, ui.SAGE], f"{o['both_pct']:.0f}%", "found by both"),
                config={"displayModeBar": False}, width="stretch", key="eval_comp",
            )
            st.caption(f"{comp['n_locations']} problem locations: {o['rule_only']} rules only, {o['llm_only']} AI only, "
                       f"{o['both']} both (rejected issues left out). A small overlap means the two detectors "
                       "complement each other.")
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
                                    f"(mean {pe['mean_ape']:.1f}%)", ui.SAGE, inside_panel=True), unsafe_allow_html=True)
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


RULES_PAGE = 20   # rule cards shown per "Show more"


def _render_rules(conn, upload: dict) -> None:
    uid = upload["id"]
    st.markdown('<div class="page-title">Rules</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Every rule was created automatically from this audit\'s data profile. None '
                'were written by hand. Each one says what it checks, why it was created and what it found.</div>',
                unsafe_allow_html=True)
    all_rules = repository.fetch_rules(conn, uid)
    if not all_rules:
        st.info("No rules were recorded for this audit. Rules are saved for audits run from now on, so run the "
                "audit again on the New audit page to see them.")
        return

    violated = sum(1 for r in all_rules if r["violation_count"])
    m = st.columns(4)
    m[0].metric("Rules checked", f"{len(all_rules):,}")
    m[1].metric("Passed", f"{len(all_rules) - violated:,}")
    m[2].metric("Found problems", f"{violated:,}")
    m[3].metric("Tables covered", len({r["table_name"] for r in all_rules}))

    f1, f2, f3 = st.columns([1.2, 1.2, 2], vertical_alignment="bottom")
    tables = sorted({r["table_name"] for r in all_rules})
    categories = sorted({r["category"] for r in all_rules})
    table = f1.selectbox("Table", [ALL, *tables], key=f"rules_table_{uid}",
                         format_func=lambda t: "All tables" if t == ALL else t)
    category = f2.selectbox("Type of problem", [ALL, *categories], key=f"rules_cat_{uid}",
                            format_func=lambda c: "All types" if c == ALL else CATEGORY_LABEL.get(c, c))
    counts = {"Found problems": violated, "Passed": len(all_rules) - violated, "All": len(all_rules)}
    with f3:
        view = st.segmented_control("Show", list(RULE_VIEWS), default="Found problems", key=f"rules_view_{uid}",
                                    format_func=lambda v: f"{v} · {counts[v]:,}")
    mode = RULE_VIEWS.get(view or "All")

    rules = sorted((
        r for r in all_rules
        if (table == ALL or r["table_name"] == table)
        and (category == ALL or r["category"] == category)
        and (mode == "all" or (mode == "violated") == bool(r["violation_count"]))
    ), key=lambda r: -(r["violation_count"] or 0))
    shown = st.session_state.get(f"rules_shown_{uid}", RULES_PAGE)
    st.caption(f"Showing {min(shown, len(rules)):,} of {len(rules):,} rules · most rows affected first")
    client = base_client(upload["source_client"])
    aliases = _aliases(conn, upload["source_client"])
    suppressed = repository.active_suppressions(conn, client)
    admin = _is_admin()
    pages = st.session_state.get("_pages", {})
    for r in rules[:shown]:
        broken = r["violation_count"]
        off = r["rule_key"] in suppressed
        if off:
            s_row = suppressed[r["rule_key"]]
            badge = _pill(f"Not applicable · {s_row['disabled_by'] or 'admin'}, "
                          f"{s_row['disabled_at']:{DATE_FORMAT}}", ui.MUTED)
        elif broken:
            badge = _pill(f"{broken:,} row{'s' if broken != 1 else ''} break this rule", ui.CORAL)
        else:
            badge = _pill("Passed", ui.SAGE)
        where = r["table_name"]
        p = r.get("parameters") or {}
        if r.get("template") == "cross_table_fk_orphan" and p.get("parent_table"):
            where = f"{r['table_name']} → {p['parent_table']}"
        disagree = r.get("template") == "cross_table_fk_orphan" and grouping.link_disagrees(r.get("evidence") or {})
        note = ('<div class="rule-note">These two numbers disagree a lot: most different values match, but most '
                'rows don\'t. The link itself may be wrong.</div>' if disagree else "")
        with st.container(border=True, key=f"rcard_{r['id']}"):
            st.markdown(
                f'''<div class="rule-body">
  <div class="issue-head"><span class="pills">{_outline_pill(CATEGORY_LABEL.get(r["category"], r["category"]))}{_outline_pill(where)}</span>
  <span class="pills">{badge}</span></div>
  <div class="rule-what">{html.escape(show(r["what_text"], r["table_name"], aliases, with_code=True))}</div>
  <div class="rule-why">{html.escape(show(r["why_text"], r["table_name"], aliases, with_code=True))}</div>
  {note}
  <div class="rule-ev">{html.escape(plain(_evidence_text(r)))}</div>
</div>''',
                unsafe_allow_html=True,
            )
            actions = st.container(horizontal=True, gap="small", vertical_alignment="center")
            if disagree and "relationships" in pages:
                actions.page_link(pages["relationships"], label="Check this link in Relationships",
                                  icon=":material/arrow_forward:")
            if admin and (broken or off):
                label = "Apply this rule again" if off else "Not applicable to us"
                if actions.button(label, key=f"suppress_{r['id']}", icon=":material/undo:" if off else
                                  ":material/block:",
                                  help="Disabled rules still run but raise no issues in future audits for this "
                                       "organisation."):
                    repository.set_rule_suppressed(conn, client, r["rule_key"], not off, by=_username())
                    st.toast("Rule applied again for future audits" if off else
                             "Rule disabled for future audits of this organisation", icon=":material/rule:")
                    st.rerun()
    if len(rules) > shown:
        if st.button(f"Show {min(RULES_PAGE, len(rules) - shown)} more", key=f"rules_more_{uid}",
                     icon=":material/expand_more:"):
            st.session_state[f"rules_shown_{uid}"] = shown + RULES_PAGE
            st.rerun()


def _render_organisation(conn, upload: dict) -> None:
    """Board 14: what the AI is told about this company, readable first, the exact text folded away."""
    client = upload["source_client"]
    aliases = _aliases(conn, client)
    st.markdown('<div class="page-title">Organisation</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">The AI auditor learns about this company without any training. Before each '
                'audit it is given your notes and the AI findings your reviewers accepted or rejected, so it stops '
                'repeating findings you have already dismissed.</div>', unsafe_allow_html=True)

    notes = feedback.list_notes(conn, client)
    stats = feedback.feedback_stats(conn, client)
    m = st.columns(3)
    m[0].metric("Your notes", sum(1 for n in notes if n["active"]))
    m[1].metric("AI findings accepted", stats["accepted"])
    m[2].metric("AI findings rejected", stats["rejected"])

    left, right = st.columns([1, 1.25])
    with left.container(border=True):
        st.markdown('<div class="card-title">Notes the AI should always know</div>', unsafe_allow_html=True)
        with st.form(f"org_note_{client}", clear_on_submit=True, border=False):
            text = st.text_area("New note", height=80,
                                placeholder="For example: we are VAT-exempt, so a zero tax amount is normal.")
            if st.form_submit_button("Add note", icon=":material/add:") and text.strip():
                feedback.add_note(conn, client, text, created_by=_username())
                st.toast("Note added. It is used from the next AI audit.", icon=":material/sticky_note_2:")
                st.rerun()
        if not notes:
            st.markdown('<div class="card-sub">No notes yet. Good notes explain something that looks wrong but is '
                        'normal here, such as a field you never use or a value that is allowed to be zero.</div>',
                        unsafe_allow_html=True)
        for n in notes:
            with st.container(border=True):
                body, toggle = st.columns([4, 1.4], vertical_alignment="center")
                state = "" if n["active"] else " · not in use"
                body.markdown(f'<div class="org-note{"" if n["active"] else " off"}">{html.escape(n["note"])}</div>'
                              f'<div class="card-sub">Added {n["created_at"]:{DATE_FORMAT}}'
                              f'{" by " + html.escape(n["created_by"]) if n["created_by"] else ""}{state}</div>',
                              unsafe_allow_html=True)
                if toggle.button("Stop using" if n["active"] else "Use again", key=f"note_toggle_{n['id']}",
                                 width="stretch"):
                    feedback.set_note_active(conn, n["id"], not n["active"])
                    st.rerun()

    with right.container(border=True):
        st.markdown('<div class="card-title">What the AI will be told</div><div class="card-sub">Before the next '
                    'audit of this table</div>', unsafe_allow_html=True)
        tables = sorted({r["table_name"] for r in repository.fetch_rules(conn, upload["id"])}
                        or {i["table_name"] for i in repository.fetch_issues(conn, upload["id"])})
        if not tables:
            st.caption("No tables in this audit.")
        else:
            table = st.selectbox("Table", tables, key=f"org_preview_{client}")
            for status, title, sub in (
                    ("accepted", "Confirmed real issues", "Reviewers accepted these, so similar findings are likely "
                                                          "genuine."),
                    ("rejected", "Rejected as not problems", "The AI is asked not to report these again unless the "
                                                             "evidence is clearly different.")):
                examples = feedback.fetch_examples(conn, client, table, status)
                st.markdown(f'<div class="section-head">{title} <span>· {len(examples)}</span></div>'
                            f'<div class="card-sub">{sub}</div>', unsafe_allow_html=True)
                items = []
                for e in examples[:3]:
                    where = e["table_name"] + (" · " + display_column_code(aliases, e["table_name"], e["column_name"])
                                               if e.get("column_name") else "")
                    note = f' Reviewer note: “{html.escape(_short(e["user_note"], 120))}”' if e.get("user_note") else ""
                    items.append(f'<li><b>{html.escape(where)}</b>: '
                                 f'{html.escape(_short(show(e["description"], e["table_name"], aliases, True), 180))}'
                                 f'{note}</li>')
                if len(examples) > 3:
                    items.append(f'<li class="more">… {len(examples) - 3} more</li>')
                st.markdown(f'<ul class="org-list">{"".join(items)}</ul>' if items else
                            '<div class="card-sub">None yet.</div>', unsafe_allow_html=True)
            schema_ctx = docs_store.get_schema_context(client, table, conn=conn)
            org_ctx, _ = feedback.feedback_context(conn, client, table, base_tokens=0)
            preview = "\n\n".join(c for c in (schema_ctx, org_ctx) if c)
            with st.expander("Show the exact text sent to the AI", icon=":material/code:"):
                if preview:
                    st.caption("Exactly what the AI receives. Long findings are shortened before sending, to keep "
                               "the prompt small.")
                    st.code(preview, language=None, wrap_lines=True)
                else:
                    st.caption("Nothing extra yet: the AI audits this table with the profile and sample only.")

    log = feedback.fetch_prompt_log(conn, upload["id"])
    if log:
        with st.expander(f"What the AI was given in this audit ({len(log)} tables)", icon=":material/receipt_long:"):
            st.dataframe(
                [{"Table": e["table_name"], "Notes": len(e["note_ids"]),
                  "Accepted examples": len(e["accepted_issue_ids"]), "Rejected examples": len(e["rejected_issue_ids"]),
                  "Verified schema docs": "Yes" if e["schema_context"] else "No",
                  "Prompt size (about, tokens)": e["est_prompt_tokens"], "Examples trimmed": e["trimmed_examples"]}
                 for e in log],
                width="stretch", hide_index=True,
            )


def _render_schema_review(conn, upload: dict) -> None:
    client = upload["source_client"]
    label = client_label(client)
    st.markdown('<div class="page-title">Schema review</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">The AI drafted what each table and column means from its statistics. Check '
                'each one and correct anything wrong. Only descriptions you verify are given to the AI auditor in '
                'later audits.</div>', unsafe_allow_html=True)

    tables = docs_store.fetch_doc_tables(conn, client)
    if not tables:
        st.info(
            f"No schema documentation for {label} yet. On the **New audit** page, tick "
            "*Draft schema documentation with AI*, or run "
            "`python -m app.schema_docs.generate <data_dir> --client=<key>`."
        )
        return

    stats = docs_store.doc_stats(conn, client)
    m = st.columns(4)
    m[0].metric("Tables verified", f"{stats['tables_verified']} / {stats['tables']}")
    m[1].metric("Columns verified", f"{stats['columns_verified']} / {stats['columns']}")
    m[2].metric("Need a closer look", sum(t["low_count"] for t in tables), help="Low AI confidence")
    m[3].metric("Drafts left", stats["columns"] - stats["columns_verified"])
    if not stats["columns_verified"]:
        st.info("Nothing is verified yet, so the AI auditor currently works without any of these descriptions.",
                icon=":material/info:")
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
            f"{n} · {by_name[n]['table_module'] or 'Other'} · "
            f"{by_name[n]['verified_count']} of {by_name[n]['column_count']} columns verified"
            + (" · table verified" if by_name[n]["verified_at"] else "")
        ),
    )
    tdoc = by_name[table]

    with st.container(border=True):
        status = (_pill(f"Verified {tdoc['verified_at']:{DATE_FORMAT}}", ui.SAGE) if tdoc["verified_at"]
                  else _pill("Draft · not verified", ui.MUSTARD))
        st.markdown(f'<div class="issue-head"><span class="issue-title">{html.escape(table)}</span>'
                    f'<span class="pills">{status}</span></div>', unsafe_allow_html=True)
        with st.form(f"doc_tbl_{client}_{table}", border=False):
            desc = st.text_area("What this table holds", value=tdoc["table_description"] or "", height=80)
            current = tdoc["table_module"] if tdoc["table_module"] in MODULES else "Other"
            module = st.selectbox("Business area", MODULES, index=MODULES.index(current))
            c1, c2, _ = st.columns([1, 1.6, 2])
            save = c1.form_submit_button("Save draft", icon=":material/save:", width="stretch")
            verify = c2.form_submit_button(
                "Verify table and all columns", type="primary", icon=":material/check:", width="stretch",
                help="Marks the table and every remaining column as verified. Check the columns first.",
            )
        if save or verify:
            docs_store.update_table(conn, client, table, desc.strip(), module)
            if verify:
                n = docs_store.verify_table(conn, client, table, verified_by=_username())
                st.toast(f"{table} verified ({n} column(s) confirmed)", icon=":material/check_circle:")
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
    name = col["display_name"] or "no name yet"
    title = f"**{col['column_name']}** · {name} · :{color}[{text}]"
    with st.expander(title, expanded=(conf == "LOW"), icon=icon):
        with st.form(f"doc_col_{col['id']}", border=False):
            a, b = st.columns([1, 2])
            display = a.text_input("Short name", value=col["display_name"] or "")
            domain = b.text_input("Expected values", value=col["expected_domain"] or "")
            desc = st.text_area("What it means", value=col["description"] or "", height=68)
            rules = st.text_area(
                "Rules (one per line)", value="\n".join(col["business_rules"] or []), height=88,
            )
            c1, c2, _ = st.columns([1, 1.3, 3])
            save = c1.form_submit_button("Save draft", icon=":material/save:", width="stretch")
            verify = c2.form_submit_button(   # one primary per screen: the table's Verify
                "Save and verify", icon=":material/check:", width="stretch", disabled=(conf == "VERIFIED"),
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
            st.caption(f"Verified {col['verified_at']:{DATE_FORMAT}} · {how}")
        elif col["llm_confidence"]:
            st.caption(f"AI draft confidence: {col['llm_confidence'].title()}")
        if col["llm_description"] and " ".join((col["description"] or "").split()) != " ".join(col["llm_description"].split()):
            st.caption(f"Original AI draft: {col['llm_description']}")


def _render_relationships(conn, upload: dict) -> None:
    st.markdown('<div class="page-title">Relationships</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-sub">Links between tables, found automatically from matching values. Confirm the '
                'real ones. This records your review; it does not change how links are found.</div>',
                unsafe_allow_html=True)
    rels = repository.fetch_relationships(conn, upload["id"])
    if not rels:
        st.info("No links between tables were found for this audit.")
        return

    aliases = _aliases(conn, upload["source_client"])
    uses = grouping.rule_uses(repository.fetch_rules(conn, upload["id"]))
    groups: dict[str, list] = {k: [] for k in grouping.LINK_GROUPS}
    for r in rels:
        child_name = display_columns(aliases, r["child_table"], r["child_column"]) or r["child_column"]
        parent_name = display_columns(aliases, r["parent_table"], r["parent_column"]) or r["parent_column"]
        group, why = grouping.link_group(child_name, parent_name)
        groups[group].append((r, why))

    confirmed_n = sum(1 for r in rels if r["confirmed"])
    k = st.columns(3)
    k[0].metric("Links found", len(rels))
    k[1].metric("Confirmed", confirmed_n)
    k[2].metric("Check carefully", len(groups["careful"]))
    st.markdown('<div class="card-sub"><b>A high match alone does not prove a link.</b> Small numbers, such as '
                'quantities or item counts, often match ID numbers by chance. Those are grouped under '
                '"Check carefully".</div>', unsafe_allow_html=True)

    hints = {"same": "likely real", "careful": "a count or quantity matched an ID", "different":
             "decide from what you know"}
    for group, label in grouping.LINK_GROUPS.items():
        items = groups[group]
        if not items:
            continue
        st.markdown(f'<div class="section-head">{label} <span>· {hints[group]} · {len(items)}</span></div>',
                    unsafe_allow_html=True)
        for r, why in items:
            child = display_column_code(aliases, r["child_table"], r["child_column"]) or r["child_column"]
            parent = display_column_code(aliases, r["parent_table"], r["parent_column"]) or r["parent_column"]
            n_rules = uses.get((r["child_table"], r["child_column"], r["parent_table"], r["parent_column"]), 0)
            facts = [f"{float(r['overlap_pct']):.1f}".removesuffix(".0") + "% of different values match",
                     f"used by {n_rules} rule{'s' if n_rules != 1 else ''}"]
            if why:
                facts.append(why)
            status = (_pill("Confirmed", ui.SAGE) if r["confirmed"] else
                      _pill("Check carefully", ui.MUSTARD) if group == "careful" else _pill("Not confirmed", ui.MUTED))
            with st.container(border=True, key=f"rel_card_{r['id']}"):
                text, act = st.columns([5, 1.3], vertical_alignment="center")
                text.markdown(
                    f'<div class="issue-head"><span class="rel-link"><b>{html.escape(r["child_table"])} · '
                    f'{html.escape(child)}</b> → <b>{html.escape(r["parent_table"])} · {html.escape(parent)}</b>'
                    f'</span><span class="pills">{status}</span></div>'
                    f'<div class="rule-ev">{html.escape(" · ".join(facts))}</div>', unsafe_allow_html=True)
                if act.button("Undo confirm" if r["confirmed"] else "Confirm", key=f"rel_{r['id']}",
                              icon=":material/undo:" if r["confirmed"] else ":material/check:", width="stretch"):
                    repository.set_relationship_confirmed(conn, r["id"], not r["confirmed"])
                    st.rerun()
    st.caption("Grouping is a display hint based on column names. It does not change how links are found.")


GUIDE = [   # first-visit guide: participants found the first 2-3 minutes confusing
    ("Overview", "overview", "This page shows the data quality score and how many problems are waiting "
                             "for you. You are here now."),
    ("Triage", "triage", "Start with Fix now. Open an issue, read What was found and What to do, then "
                         "choose Accept (a real problem), Reject (not a problem) or Unsure."),
    ("Ask", "ask", "Ask any question about an issue in your own words, for example "
                   "\"Is this a real problem?\" or \"How do I fix this?\"."),
]


def _welcome_card() -> None:
    """First-visit guide for staff in three steps (participants have their task card instead)."""
    if _is_participant() or st.session_state.get("welcome_seen"):
        return
    step = min(st.session_state.get("welcome_step", 0), len(GUIDE) - 1)
    title, page, text = GUIDE[step]
    with st.container(border=True, key="welcome_card"):
        st.markdown(f'<div class="guide-step">New here? Step {step + 1} of {len(GUIDE)}</div>'
                    f'<div class="card-title">{html.escape(title)}</div>'
                    f'<div class="guide-text">{html.escape(text)}</div>', unsafe_allow_html=True)
        row = st.container(horizontal=True, gap="small", vertical_alignment="center")
        if step and row.button("Back", key="welcome_back", icon=":material/arrow_back:"):
            st.session_state["welcome_step"] = step - 1
            st.rerun()
        last = step == len(GUIDE) - 1
        if row.button("Got it" if last else "Next", key="welcome_next", type="primary",
                      icon=":material/check:" if last else ":material/arrow_forward:"):
            if last:
                st.session_state["welcome_seen"] = True
            else:
                st.session_state["welcome_step"] = step + 1
            st.rerun()
        if step and page in st.session_state.get("_pages", {}):
            row.page_link(st.session_state["_pages"][page], label=f"Open {title}", icon=":material/open_in_new:")
        if not last and row.button("Skip the guide", key="welcome_skip", type="tertiary"):
            st.session_state["welcome_seen"] = True
            st.rerun()


def _render_overview(conn, upload: dict) -> None:
    _welcome_card()
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
    k[0].markdown(ui.kpi_card("Data quality score", f"{overall:.0f}",
                              f"{upload['row_count']:,} rows · this audit", _status_color(overall), suffix="/ 100"),
                  unsafe_allow_html=True)
    k[1].markdown(ui.kpi_card("Waiting for review", f"{tm['by_status']['flagged']:,}",
                              f"{tm['reviewed']} of {total} reviewed · this audit", ui.MUSTARD), unsafe_allow_html=True)
    k[2].markdown(ui.kpi_card("High severity", f"{sev.get('high', 0):,}",
                              f"{summary['worsening']} getting worse since the last audit" if summary["has_previous"]
                              else "first audit", ui.CORAL), unsafe_allow_html=True)
    k[3].markdown(ui.kpi_card("Found by AI", f"{src.get('llm', 0):,}",
                              f"{src.get('rule', 0):,} found by rules · this audit", ui.LAVENDER),
                  unsafe_allow_html=True)

    if not total:
        st.info("No issues recorded for this audit.")
        return

    left, right = st.columns([1, 2])
    with left.container(border=True, height=450):   # both panels in this row the same height
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
    with right.container(border=True, height=450):
        trend = repository.fetch_score_trend(conn, upload["source_client"])
        if len(trend) >= 2:
            st.markdown('<div class="card-title">Data quality score over time</div>', unsafe_allow_html=True)
            fig = _score_trend_figure(trend)
        else:
            st.markdown('<div class="card-title">Issues by dimension</div>', unsafe_allow_html=True)
            by_dim = Counter(DIMENSION_ALIASES.get(i["dimension"], i["dimension"]) for i in issues)
            fig = go.Figure(go.Bar(x=list(by_dim.values()), y=list(by_dim), orientation="h",
                                   marker=dict(color=ui.BLUE), hovertemplate="%{y}: %{x}<extra></extra>"))
        st.plotly_chart(ui.style_figure(fig, height=318), config={"displayModeBar": False},
                        width="stretch", key="trend_chart")

    c1, c2 = st.columns([2, 1])
    with c1.container(border=True):
        aliases = _aliases(conn, upload["source_client"])
        unused = grouping.unused_rule_ids(repository.fetch_rules(conn, uid))
        waiting = [i for i in issues if i["status"] == "flagged" and not grouping.is_unused(i, unused)]
        top = sorted(waiting, key=grouping.priority, reverse=True)
        st.markdown(f'<div class="card-title">Needs attention</div><div class="card-sub">{len(waiting)} of {total} '
                    f'issues · most important first · this audit</div>', unsafe_allow_html=True)
        rows = []
        for i in top[:5]:
            col = display_columns(aliases, i["table_name"], i["column_name"])
            n = i.get("affected_row_count")
            rows.append(
                f'<div class="att-row">{_severity_pill(i["severity"])}'
                f'<div class="att-main"><b>{html.escape(i["table_name"])}{" · " + html.escape(col) if col else ""}</b>'
                f'<span>{html.escape(_short(show(i["description"], i["table_name"], aliases), 110))}</span></div>'
                f'<div class="att-side">{ui.pill(*ui.SOURCE[i["source"]])}'
                f'<span>{f"{n:,} rows" if n is not None else ""}</span></div></div>')
        st.markdown("".join(rows) if rows else '<div class="card-sub">Everything has been reviewed.</div>',
                    unsafe_allow_html=True)
        pages = st.session_state.get("_pages", {})
        if "triage" in pages:
            more = len(top) - len(rows)
            st.page_link(pages["triage"], icon=":material/arrow_forward:",
                         label=f"… and {more} more in Triage" if more > 0 else "Open Triage")
    with c2.container(border=True):
        prev = recurrence.previous_upload_id(conn, uid)
        st.markdown('<div class="card-title">Since the last audit</div>'
                    + (f'<div class="card-sub">Compared with audit #{prev}</div>' if prev else ""),
                    unsafe_allow_html=True)
        if not summary["has_previous"]:
            st.markdown('<div class="card-sub">This is the first audit for this organisation. '
                        'Trends appear from the second audit.</div>', unsafe_allow_html=True)
        else:
            for label, value, color in [
                ("New issues", summary["new"], ui.BLUE), ("Seen before", summary["recurring"], ui.MUTED),
                ("Getting worse", summary["worsening"], ui.CORAL), ("Improving", summary["improving"], ui.SAGE),
                ("Not seen this time", summary["not_seen"], ui.LAVENDER),
            ]:
                st.markdown(f'<div class="meter-top" style="margin:10px 0"><span>{ui.pill(label, color)}</span>'
                            f'<span class="meter-val">{value}</span></div>', unsafe_allow_html=True)


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
        "Choose the company, add its export files (CSV or Excel; headerless ERP exports are fine) "
        "and run the audit. Upload related tables together (for example an invoice header and its lines) so "
        "cross-table checks can run."
    )
    companies = ws.list_workspaces(conn, kinds=("real",))
    names = {c["slug"]: c["name"] for c in companies}
    current = st.session_state.get("workspace")
    options = [*names, NEW_COMPANY]
    picked = st.selectbox(
        "Company", options,
        index=options.index(current) if current in names else (0 if names else len(options) - 1),
        format_func=lambda k: "New company…" if k == NEW_COMPANY else names[k],
        key=f"imp_company_{in_dialog}",
    )
    new_name = ""
    if picked == NEW_COMPANY:
        new_name = st.text_input("New company name", placeholder="e.g. Sunrise Traders",
                                 key=f"imp_new_{in_dialog}").strip()

    c1, c2 = st.columns([1, 2])
    as_of = c1.date_input("Data as of", value=_dt.date.today(), key=f"imp_asof_{in_dialog}", format="DD/MM/YYYY",
                          help="The date the export was taken. Imports are ordered by this date for trends.")
    c1.caption(f"{as_of:{DATE_FORMAT}}")   # the date in words under the DD/MM/YYYY input
    files = c2.file_uploader("Export files", type=["csv", "xlsx", "xls"], accept_multiple_files=True,
                             key=f"imp_files_{in_dialog}")

    run_llm = st.checkbox("Also run AI contextual analysis (finds issues rules can't; sends only masked samples)",
                          value=True, key=f"imp_llm_{in_dialog}")
    draft_docs = st.checkbox("Draft schema documentation with AI for new tables (review it in Schema review)",
                             value=False, key=f"imp_docs_{in_dialog}")

    ready = bool(files) and (picked != NEW_COMPANY or bool(new_name))
    if not ready:
        st.caption("Add at least one file" + ("" if picked != NEW_COMPANY or new_name else " and a company name")
                   + " to run the audit.")
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


def _score_trend_figure(trend: list[dict]) -> go.Figure:
    """Score per audit with the data date on the x-axis (audit number only when two audits share a date)."""
    trend = sorted(trend, key=lambda r: (r["as_of"], r["upload_id"]))
    days = Counter(r["as_of"] for r in trend)
    fmt = "%d %b" if len({r["as_of"].year for r in trend}) == 1 else DATE_FORMAT   # the year once, in the title
    labels = [f"{r['as_of']:{fmt}}" + (f" #{r['upload_id']}" if days[r["as_of"]] > 1 else "") for r in trend]
    fig = go.Figure(go.Scatter(
        x=labels, y=[float(r["overall_score"]) for r in trend],
        mode="lines+markers", line=dict(color=ui.SAGE, width=3, shape="spline"),
        marker=dict(size=8, color=ui.SAGE), fill="tozeroy", fillcolor=ui._rgba(ui.SAGE, .10),
        customdata=[r["upload_id"] for r in trend],
        hovertemplate="%{x} · audit #%{customdata}: %{y:.0f}/100<extra></extra>",
    ))
    fig.update_yaxes(range=[0, 100])
    year = f" ({trend[0]['as_of']:%Y})" if fmt != DATE_FORMAT else ""
    fig.update_xaxes(type="category", title_text=f"data as of{year}")
    return fig


def _render_scores(conn, upload: dict) -> None:
    """Board 10: the score, why it is what it is, the six types behind it, and the trend."""
    # Recompute live so the score reflects current triage (rejected issues
    # are excluded); this also refreshes the stored value for the trend.
    score = repository.score_upload(conn, upload["id"])
    overall = score["overall_score"]
    issues = [i for i in repository.fetch_issues(conn, upload["id"]) if i.get("status") != "rejected"]
    aliases = _aliases(conn, upload["source_client"])
    weights = load_weights()
    trend = sorted(repository.fetch_score_trend(conn, upload["source_client"]),
                   key=lambda r: (r["as_of"], r["upload_id"]))
    ids = [r["upload_id"] for r in trend]
    before = trend[:ids.index(upload["id"])] if upload["id"] in ids else []   # earlier audits, by data date

    head, dl = st.columns([4, 1], vertical_alignment="bottom")
    head.markdown('<div class="page-title">Scores and trend</div>', unsafe_allow_html=True)
    dl.download_button("Score summary (CSV)", data=score_to_csv(score), file_name=f"dq_score_audit_{upload['id']}.csv",
                       mime="text/csv", icon=":material/download:", width="stretch")

    by_dim: dict[str, list] = {}
    for i in issues:
        by_dim.setdefault(DIMENSION_ALIASES.get(i["dimension"], i["dimension"]), []).append(i)
    dims = sorted(((key.removesuffix("_score"), label, score[key]) for label, key in DIMENSION_ROWS),
                  key=lambda d: d[2])

    left, right = st.columns([1, 1.6])
    with left.container(border=True, height=330):
        verdict = "Good" if overall >= SCORE_GOOD else "Needs work" if overall >= SCORE_WARN else "Poor"
        delta = ""
        if before:
            change = overall - float(before[-1]["overall_score"])
            delta = (f" {'Up' if change > 0 else 'Down'} {abs(change):.0f} since the last audit."
                     if round(change) else " About the same as the last audit.")
        st.markdown(ui.kpi_card("Data quality score · this audit", f"{overall:.0f}", verdict, _status_color(overall),
                                inside_panel=True, suffix="/ 100"), unsafe_allow_html=True)
        st.markdown(f'<div class="card-sub">Counts every issue except the ones you rejected, so it changes as you '
                    f'review.{delta}</div>', unsafe_allow_html=True)
    with right.container(border=True, height=330):
        st.markdown(f'<div class="card-title">Why is it {overall:.0f}?</div>', unsafe_allow_html=True)
        unused = grouping.unused_rule_ids(repository.fetch_rules(conn, upload["id"]))
        points = []
        for dim, label, value in dims[:2]:
            mine = by_dim.get(dim, [])
            why = f"{len(mine)} issue{'s' if len(mine) != 1 else ''}."
            if dim == "completeness":
                n_unused = sum(grouping.is_unused(i, unused) for i in mine)
                if n_unused:
                    why = (f"{n_unused} of its {len(mine)} issues are fields that are never filled in. If the "
                           "business doesn't use them, reject them in Triage, so they stop lowering the score.")
            elif dim == "cross_table":
                why += " Some may come from links that are not real, so check the links first."
            points.append(f"<li><b>{label} is {value:.0f}.</b> {html.escape(why)}</li>")
        rest = dims[2:]
        if rest:
            points.append(f"<li><b>The other {len(rest)} are between {rest[0][2]:.0f} and {rest[-1][2]:.0f}.</b> "
                          f"Together they hold {sum(len(by_dim.get(d, [])) for d, _, _ in rest)} issues.</li>")
        st.markdown(f'<ol class="why-list">{"".join(points)}</ol>', unsafe_allow_html=True)
        pages = st.session_state.get("_pages", {})
        links = st.container(horizontal=True, gap="small")
        if "triage" in pages:
            links.page_link(pages["triage"], label="Review in Triage", icon=":material/arrow_forward:")
        if "relationships" in pages and any(d == "cross_table" for d, _, _ in dims[:2]):
            links.page_link(pages["relationships"], label="Check the links", icon=":material/arrow_forward:")

    st.markdown('<div class="section-head">Score by type of problem</div><div class="card-sub">The overall score '
                'is a weighted average of these six. Open one to see the issues behind it.</div>',
                unsafe_allow_html=True)
    for dim, label, value in dims:
        mine = by_dim.get(dim, [])
        icon = (":material/check_circle:" if value >= SCORE_GOOD else ":material/error:" if value >= SCORE_WARN
                else ":material/cancel:")
        with st.expander(f"{grouping.type_label(dim)} ({label}) · {value:.0f} / 100 · weight "
                         f"{weights[dim] * 100:.0f}% · {len(mine)} issue{'s' if len(mine) != 1 else ''}", icon=icon):
            st.markdown(f'<div class="card-sub">{html.escape(grouping.type_help(dim))}</div>'
                        + _meter(label, value), unsafe_allow_html=True)
            sev = Counter(i["severity"] for i in mine)
            penalty = sum(SEVERITY_POINTS.get(i["severity"], 1) for i in mine)
            st.caption(f"{sev.get('high', 0)} high · {sev.get('medium', 0)} medium · {sev.get('low', 0)} low → "
                       f"{penalty} points → score = 100 × {DEFAULT_K:g} / ({DEFAULT_K:g} + {penalty}) = {value:.0f}")
            if mine:
                st.dataframe(
                    [{"Where": i["table_name"] + (" · " + (display_columns(aliases, i["table_name"], i["column_name"])
                                                          or "") if i["column_name"] else ""),
                      "Severity": i["severity"].title(), "Found by": "AI" if i["source"] == "llm" else "Rule",
                      "Status": ui.STATUS.get(i.get("status"), (i.get("status"),))[0],
                      "What was found": _short(show(i.get("description"), i["table_name"], aliases), 200)}
                     for i in sorted(mine, key=grouping.priority, reverse=True)],
                    width="stretch", hide_index=True)
            else:
                st.caption("No issues of this type, so it scores a perfect 100.")

    with st.expander("How the score is worked out", icon=":material/calculate:"):
        st.markdown(
            "Each issue adds points to its type: **3** for high, **2** for medium and **1** for low severity. A type "
            f"with *p* points scores 100 × {DEFAULT_K:g} / ({DEFAULT_K:g} + *p*), so it never drops below zero and "
            "the first issues count most. The six type scores are then combined using the weights: "
            + ", ".join(f"{grouping.type_label(d).lower()} {weights[d] * 100:.0f}%" for d in weights)
            + ". Links between tables and consistency weigh most, because errors across tables are the ones this "
              "tool exists to find. Issues you reject are left out.")

    st.markdown('<div class="section-head">Score over time</div>', unsafe_allow_html=True)
    if len(trend) >= 2:
        st.markdown(f'<div class="card-sub">{len(trend)} audits, in order of the date the data was exported · '
                    'higher is better</div>', unsafe_allow_html=True)
        st.plotly_chart(ui.style_figure(_score_trend_figure(trend), height=300), config={"displayModeBar": False},
                        width="stretch", key="scores_trend")
    else:
        st.caption(f"Only one scored audit for **{client_label(upload['source_client'])}** so far. The trend line "
                   "appears once another export for this company is audited (for example after fixes).")
    pages = st.session_state.get("_pages", {})
    if "evaluation" in pages:
        st.page_link(pages["evaluation"], icon=":material/query_stats:",
                     label="Looking for precision, or how rules and the AI compare? See Evaluation")


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


def _set_picks(issue_ids: list[int], value: bool) -> None:
    for i in issue_ids:
        st.session_state[f"pick_{i}"] = value


def _decide_picked(conn, issue_ids: list[int], status: str) -> None:
    repository.set_issue_statuses(conn, issue_ids, status, reviewed_by=_username())
    _set_picks(issue_ids, False)
    st.session_state["flash"] = f"{len(issue_ids)} issue(s) marked {ui.STATUS[status][0].lower()}"


def _render_selection_bar(conn, issues: list[dict]) -> None:
    """Tick several issues (after checking them), then decide them in one go."""
    shown = [i["id"] for i in issues]
    picked = [i for i in shown if st.session_state.get(f"pick_{i}")]
    # once something is ticked, the bar floats at the bottom of the screen (stays in reach while scrolling)
    with st.container(border=True, key="selection_bar_active" if picked else "selection_bar"):
        if picked:
            label, all_b, acc, rej, uns, clear = st.columns([1.6, 1.3, 1, 1, 1, 0.9], vertical_alignment="center")
            label.markdown(f"**{len(picked)} of {len(shown)} selected**")
            all_b.button(f"Select all {len(shown)}", key="sel_all_active", width="stretch",
                         disabled=len(picked) == len(shown), on_click=_set_picks, args=(shown, True))
            acc.button("Accept", key="sel_acc", icon=":material/check:", width="stretch",   # equal to Reject / Unsure
                       on_click=_decide_picked, args=(conn, picked, "accepted"))
            rej.button("Reject", key="sel_rej", icon=":material/close:", width="stretch",
                       on_click=_decide_picked, args=(conn, picked, "rejected"))
            uns.button("Unsure", key="sel_uns", icon=":material/help:", width="stretch",
                       on_click=_decide_picked, args=(conn, picked, "considered"))
            clear.button("Clear", key="sel_clear", width="stretch", on_click=_set_picks, args=(picked, False))
        else:
            text, all_b = st.columns([4, 1.2], vertical_alignment="center")
            text.caption("Tick the issues you have checked, then accept, reject or mark them unsure together.")
            all_b.button("Select all shown", key="sel_all", width="stretch", on_click=_set_picks, args=(shown, True))


def _render_bulk_actions(conn, issue_ids: list[int]) -> None:
    with st.expander(f"Bulk actions: apply to all {len(issue_ids)} shown", icon=":material/bolt:"):
        cols = st.columns(4)
        actions = [
            ("Accept all", "accepted", "bulk_acc", ":material/check:"),
            ("Reject all", "rejected", "bulk_rej", ":material/close:"),
            ("Unsure for all", "considered", "bulk_con", ":material/help:"),
            ("Reset all", "flagged", "bulk_reset", ":material/undo:"),
        ]
        for col, (label, status, key, icon) in zip(cols, actions):
            if col.button(label, key=key, icon=icon, width="stretch"):
                repository.set_issue_statuses(conn, issue_ids, status, reviewed_by=_username())
                st.rerun()
        st.caption("Applies to the current filtered view. Reversible: filter again and reset at any time.")


def _trend_html(issue: dict, history: list[dict] | None, projection: dict | None) -> str:
    if not history or len(history) < 2:
        return ""
    counts = [h["affected_row_count"] for h in history]
    if len(set(counts)) == 1:
        out = (f"Seen in {len(history)} audits since {history[0]['as_of']:%d %b %Y} · "
               f"unchanged at <b>{counts[0]:,}</b> rows")
        return f'<div class="issue-trend">{out}</div>'
    same_day = len({h["as_of"] for h in history}) < len(history)
    steps = " → ".join(
        (f"#{h['upload_id']}" if same_day else f"{h['as_of']:%d %b}") + f": <b>{h['affected_row_count']:,}</b>"
        for h in history[-6:]
    )
    out = f"Seen in {len(history)} audits · {steps}"
    if projection and float(projection["slope"]) != 0:
        out += (f'<br><span class="est">Next audit, if the trend continues: about '
                f'{float(projection["projected_value"]):,.0f} rows (estimate from a straight-line '
                f'fit over {projection["n_points"]} audits)</span>')
    return f'<div class="issue-trend">{out}</div>'


def _issue_pills(issue: dict, with_status: bool = True) -> str:
    pills = ""
    if issue.get("trend_status") in TREND_STYLE:
        pills += _pill(*TREND_STYLE[issue["trend_status"]])
    pills += _severity_pill(issue["severity"])
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
    meta = []
    if rows is not None:
        meta.append(f"{rows:,} row{'s' if rows != 1 else ''}")
    if issue.get("trend_status") in ("worsening", "improving"):
        meta.append("getting worse" if issue["trend_status"] == "worsening" else "improving")
    # small labels on top: severity and who found it (plus the decision, once made)
    status = (_severity_pill(issue["severity"])
              + _pill("AI" if issue["source"] == "llm" else "Rule", ui.LAVENDER if issue["source"] == "llm" else ui.BLUE))
    if issue["status"] != "flagged":
        label, color = ui.STATUS[issue["status"]]
        status += _pill(label, color)
    row = st.container(key=f"irow_{issue['id']}")   # phones: actions wrap under the text
    if _is_participant():
        text, decide_a, decide_r, open_b = row.columns([8.4, 0.75, 0.75, 1.1], vertical_alignment="center")
    else:   # staff can tick several issues and decide them together
        pick, text, decide_a, decide_r, open_b = row.columns([0.32, 8.1, 0.75, 0.75, 1.1],
                                                             vertical_alignment="center")
        pick.checkbox("Select", key=f"pick_{issue['id']}", label_visibility="collapsed")
    text.markdown(
        f'''<div class="issue-row" style="--sev:{sev_color}">
  <div class="top"><span class="loc"><span class="dot" style="background:{sev_color}"></span>{_location_html(issue, aliases)}</span>
  <span class="pills">{status}</span></div>
  <div class="sum">{html.escape(show(issue["description"], issue["table_name"], aliases))}</div>
  <div class="meta">{_type_html(issue["dimension"])}{html.escape("".join(" · " + m for m in meta))}</div>
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
    # summary first (what / what to do); the technical detail is folded away below
    st.markdown(
        f'''<div class="issue-card" style="--sev:{sev_color}">
  <div class="issue-head"><span class="issue-title">{_location_html(issue, aliases)}</span>
  <span class="pills">{_issue_pills(issue)}</span></div>
  <div class="issue-meta">{_type_html(issue["dimension"])}{" · " + fix if fix else ""}</div>
  <div class="section-label">What was found</div>
  <div class="issue-desc">{html.escape(show(issue["description"], issue["table_name"], aliases))}</div>
  {f'<div class="section-label">What to do</div><div class="issue-rec">{html.escape(show(issue["recommendation"], issue["table_name"], aliases))}</div>'
   if issue.get("recommendation") else ""}
  {_trend_html(issue, recurrence.issue_history(conn, issue_id),
               recurrence.projections_for_upload(conn, issue["upload_id"]).get(issue_id))}
</div>''',
        unsafe_allow_html=True,
    )

    with st.expander("Why this was flagged", icon=":material/rule:"):
        rule = repository.fetch_rule(conn, issue["rule_ref"]) if issue.get("rule_ref") else None
        if rule:
            st.markdown(f"**{show(rule['what_text'], issue['table_name'], aliases)}**")
            st.markdown(show(rule["why_text"], issue["table_name"], aliases))
            st.caption(plain(_evidence_text(rule)) + " · rule created automatically from the data profile")
        elif issue["source"] == "llm":
            st.markdown("Found by the **AI auditor**, which looked at the table's statistics and a small, "
                        "privacy-masked sample of rows for problems fixed rules can't express "
                        "(unusual values, business-logic conflicts, near-duplicates).")
            st.caption("AI findings can be wrong — your accept / reject decisions measure how often, "
                       "and rejected findings teach the AI what not to report for this company.")
        else:
            st.caption("No rule details were recorded for this issue.")
        st.caption(f"{issue['dimension'].replace('_', ' ').capitalize()} · issue #{issue['id']} · audit "
                   f"#{issue['upload_id']} (data as of {issue['as_of']:{DATE_FORMAT}})")
    with st.expander("Affected rows", icon=":material/table_rows:"):
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
    # the three answers look the same, so the layout does not nudge reviewers towards one (precision)
    note_col, btn_col = st.columns([2.2, 1], vertical_alignment="top")
    note = note_col.text_area("Why do you think so? (optional)", value=issue.get("user_note") or "",
                              key=f"dlg_note_{issue_id}", height=118)
    choices = [("Accept", "accepted", ":material/check:"), ("Reject", "rejected", ":material/close:"),
               ("Unsure", "considered", ":material/help:")]
    with btn_col:
        st.markdown('<div class="decision-gap"></div>', unsafe_allow_html=True)   # align with the note box
        for label, status, icon in choices:
            if st.button(label, key=f"dlg_{status}_{issue_id}", icon=icon, type="secondary", width="stretch"):
                if _is_participant():
                    sp.save_decision(conn, _username(), issue_id, status, note)
                else:
                    repository.update_issue_status(conn, issue_id, status, note.strip() or None,
                                                   reviewed_by=_username())
                st.session_state["flash"] = f"Issue #{issue_id} marked {ui.STATUS[status][0].lower()}"
                st.rerun()
    if not _is_participant():
        _render_history(conn, issue_id)
    if st.button("Not sure? Discuss this issue in chat", key=f"dlg_chat_{issue_id}", icon=":material/forum:",
                 type="tertiary"):
        _open_issue_thread(conn, issue, aliases)
        st.switch_page(st.session_state["_pages"]["ask"])


def _render_history(conn, issue_id: int) -> None:
    """Audit trail in the issue dialog: every audit that found this issue, newest first,
    with the decision recorded there (reviewer, time and note)."""
    trail = audit_trail.issue_trail(conn, issue_id)
    rows = []
    for n, t in enumerate(reversed(trail)):
        first = n == len(trail) - 1
        what = (f"{'First found' if first else 'Found again'} in audit #{t['upload_id']}"
                + (f" · {t['affected_row_count']:,} rows" if t.get("affected_row_count") is not None else ""))
        if t["status"] == "flagged":
            decision = "no decision yet"
        else:
            decision = (f"<b>{audit_trail.DECISION_LABEL[t['status']]}</b>"
                        + (f" by {html.escape(t['reviewed_by'])}" if t.get("reviewed_by") else "")
                        + (f" on {t['reviewed_at']:{DATE_FORMAT} %H:%M}" if t.get("reviewed_at") else ""))
            if t.get("user_note"):
                decision += f' · note: “{html.escape(t["user_note"])}”'
        rows.append(f'<li><span class="when">{t["as_of"]:{DATE_FORMAT}}</span><span>{html.escape(what)} · '
                    f'{decision}</span></li>')
    st.markdown('<div class="section-label">History · who decided what, and when</div>'
                f'<ul class="history">{"".join(rows)}</ul>'
                '<div class="card-sub">Each audit keeps the decision made on it, with the reviewer\'s username, '
                'the time and the note. If a decision is changed, the latest one is kept. The full log for this '
                'audit can be downloaded from Triage.</div>', unsafe_allow_html=True)


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
