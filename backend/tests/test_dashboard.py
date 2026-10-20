"""Smoke tests for the Streamlit app via Streamlit's AppTest harness.

Navigation renders one page per run, so each test opens the page it
needs through the app's start-page hook (session_state["start_page"]).
"""
from pathlib import Path

import pytest

from app.db import repository
from app.study import participants as sp

APP_PATH = Path(__file__).resolve().parents[2] / "dashboard" / "app.py"
ADMIN = {"id": 0, "username": "pytest-admin", "role": "admin"}
REVIEWER = {"id": 0, "username": "pytest-reviewer", "role": "reviewer"}
ALL_PAGES = ["overview", "triage", "ask", "rules", "scores", "relationships", "evaluation", "study",
             "new-audit", "schema", "organisation", "settings"]


def _app(user=ADMIN, page: str | None = None, company: str | None = "hemas"):
    """The app as a signed-in user (login state lives in session_state),
    looking at one company (default) or all companies (company=None)."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=90)
    if user:
        at.session_state["user"] = user
        at.session_state["username"] = user["username"]
    if page:
        at.session_state["start_page"] = page
    if company:
        at.session_state["workspace"] = company
    return at.run()


def _text(at) -> str:
    return " ".join(m.value for m in at.markdown)


@pytest.fixture(autouse=True)
def _require_uploads():
    try:
        conn = repository.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available: {exc}")
    if not repository.list_uploads(conn):
        pytest.skip("no uploads in the repository to render")


@pytest.mark.parametrize("page", ALL_PAGES)
def test_every_admin_page_renders_without_error(page):
    at = _app(page=page)
    assert not at.exception, at.exception
    assert any("ClarityDQ" in m.value for m in at.markdown)   # branded header


def test_company_dashboard_renders_kpi_cards():
    at = _app(page="overview")
    text = _text(at)
    for label in ("Data quality score", "Waiting for review", "High severity", "Found by AI"):
        assert label in text


def test_scores_page_renders_overall_score():
    at = _app(page="scores")
    assert not at.exception, at.exception
    text = _text(at)
    for label in ("Data quality score · this audit", "Why is it", "Score by type of problem"):
        assert label in text


def test_triage_has_filters_and_decision_buttons():
    at = _app(page="triage")
    assert {"Found by", "Status", "Severity", "Trend"} <= {s.label for s in at.selectbox}
    uploads = repository.list_uploads(repository.connect())
    if uploads[0]["pending_count"] > 0:
        keys = [b.key or "" for b in at.button]
        assert any(k.startswith("acc_") for k in keys) and any(k.startswith("open_") for k in keys)


def test_switching_company_and_audit_does_not_break():
    at = _app()
    company = at.sidebar.selectbox[0]
    assert company.label == "Company"
    audit = next(sb for sb in at.selectbox if sb.label == "Audit")   # audit picker sits on the page
    audit.select(audit.options[-1]).run()       # oldest audit of this company
    assert not at.exception
    company = at.sidebar.selectbox[0]
    company.select(company.options[-1]).run()   # another company
    assert not at.exception


@pytest.mark.parametrize("page", ["overview", "triage", "evaluation", "settings"])
def test_all_companies_mode_renders(page):
    at = _app(page=page, company=None)
    assert not at.exception, at.exception
    text = _text(at)
    if page == "overview":
        assert "All companies" in text
    if page == "triage":
        assert "Choose a company to continue." in text


@pytest.fixture
def study_signin():
    """Open or close the study sign-in for one test; the previous setting is restored afterwards."""
    c = repository.connect()
    row = c.execute("SELECT value FROM app_settings WHERE key = %s", (sp.SIGNIN_KEY,)).fetchone()
    yield lambda is_open: sp.set_signin_open(c, is_open)
    if row:
        c.execute("UPDATE app_settings SET value = %s WHERE key = %s", (row[0], sp.SIGNIN_KEY))
    else:
        c.execute("DELETE FROM app_settings WHERE key = %s", (sp.SIGNIN_KEY,))
    c.commit()
    c.close()


def test_signed_out_users_see_only_the_login_form(study_signin):
    study_signin(False)
    at = _app(user=None)
    assert not at.exception
    # the study has finished: staff sign-in only
    assert sorted(t.label for t in at.text_input) == ["Password", "Username"]
    assert "_pages" not in at.session_state


def test_open_study_shows_the_code_box_under_the_staff_form(study_signin):
    study_signin(True)
    at = _app(user=None)
    assert not at.exception
    assert sorted(t.label for t in at.text_input) == ["Password", "Study code", "Username"]


def test_old_study_link_after_closing_says_it_has_finished(study_signin):
    from streamlit.testing.v1 import AppTest

    study_signin(False)
    at = AppTest.from_file(str(APP_PATH), default_timeout=90)
    at.query_params["study"] = "p03"
    at.run()
    assert not at.exception
    assert "Study code" not in [t.label for t in at.text_input]
    assert any("study has finished" in c.value for c in at.caption)


def test_study_link_prefills_the_code(study_signin):
    from streamlit.testing.v1 import AppTest

    study_signin(True)
    at = AppTest.from_file(str(APP_PATH), default_timeout=90)
    at.query_params["study"] = "p03"
    at.run()
    assert not at.exception
    assert next(t.value for t in at.text_input if t.label == "Study code") == "P03"


def test_reviewer_gets_no_admin_pages():
    at = _app(REVIEWER)
    assert not at.exception
    pages = set(at.session_state["_pages"])
    assert pages == {"overview", "triage", "ask", "scores", "rules"}      # Review + Insights only


def test_reviewer_cannot_open_an_admin_page_by_link():
    at = _app(REVIEWER, page="settings")
    assert not at.exception
    assert "AI model" not in _text(at)


def test_schema_review_offers_ai_drafting_when_a_company_has_no_docs():
    c = repository.connect()
    row = c.execute(
        "SELECT w.slug FROM workspaces w WHERE w.kind = 'real' "
        "AND EXISTS (SELECT 1 FROM uploads u JOIN profiles p ON p.upload_id = u.id WHERE u.source_client = w.slug) "
        "AND NOT EXISTS (SELECT 1 FROM schema_doc_tables d WHERE d.source_client = w.slug) ORDER BY w.slug LIMIT 1"
    ).fetchone()
    c.close()
    if not row:
        pytest.skip("every audited company already has schema docs")
    at = _app(page="schema", company=row[0])
    assert not at.exception, at.exception
    assert any((b.key or "").startswith("docs_draft_") for b in at.button)
    assert "No schema documentation" in " ".join(a.value for a in at.info)


def test_decided_issues_offer_undo_not_a_second_decision():
    """Status = All on Company A's latest audit: a decided issue has no tick box and no quick
    Accept / Reject, only Undo; an issue still to review keeps its tick box. Nothing is clicked."""
    c = repository.connect()
    uid = c.execute("SELECT max(id) FROM uploads WHERE source_client = 'hemas'").fetchone()[0]
    decided = c.execute("SELECT id FROM issues WHERE upload_id = %s AND status <> 'flagged' ORDER BY id LIMIT 1",
                        (uid,)).fetchone()
    todo = c.execute("SELECT id FROM issues WHERE upload_id = %s AND status = 'flagged' ORDER BY id LIMIT 1",
                     (uid,)).fetchone()
    c.close()
    if not (decided and todo):
        pytest.skip("needs one decided and one undecided issue in Company A's latest audit")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=120)
    at.session_state["user"] = ADMIN
    at.session_state["username"] = ADMIN["username"]
    at.session_state["start_page"] = "triage"
    at.session_state["workspace"] = "hemas"
    at.session_state["filter_status"] = "(all)"
    at.session_state["triage_action"] = "all"
    at.run()
    assert not at.exception, at.exception
    boxes = {cb.key for cb in at.checkbox}
    buttons = {b.key for b in at.button}
    assert f"pick_{decided[0]}" not in boxes and f"acc_{decided[0]}" not in buttons
    assert f"undo_{decided[0]}" in buttons
    assert f"pick_{todo[0]}" in boxes


def test_open_group_and_filters_stay_after_a_decision():
    """Deciding an issue inside an open group card keeps the card open (its title has no count
    and a fixed key) and keeps the filters and the All issues view. Uses a throwaway company."""
    import hashlib

    from app import workspaces as ws
    from streamlit.testing.v1 import AppTest

    c = repository.connect()
    for (old,) in c.execute("SELECT slug FROM workspaces WHERE name = 'Pytest keep open'").fetchall():
        c.execute("DELETE FROM uploads WHERE source_client = %s", (old,))
        c.execute("DELETE FROM workspaces WHERE slug = %s", (old,))
    c.commit()
    slug = ws.create_workspace(c, "Pytest keep open", kind="real", local_only=True)
    try:
        uid = repository.create_upload(c, "keep-open", slug, 10)
        base = {"table_name": "T", "dimension": "completeness", "source": "rule", "error_type": "type1",
                "severity": "medium", "recommendation": "r", "fixable": "no", "status": "flagged",
                "row_reference": {"count": 3}, "rule_id": "completeness_null_pct"}
        repository.save_issues(c, uid, [dict(base, column_name=f"c{k}", description=f"c{k} is empty in 3 of 10 rows.")
                                        for k in range(3)])
        first = min(i["id"] for i in repository.fetch_issues(c, uid))
        group_key = f"exp_grp_{hashlib.md5('T:completeness_null_pct'.encode()).hexdigest()[:10]}"

        at = AppTest.from_file(str(APP_PATH), default_timeout=120)
        at.session_state["user"] = ADMIN
        at.session_state["username"] = ADMIN["username"]
        at.session_state["start_page"] = "triage"
        at.session_state["workspace"] = slug
        at.session_state["filter_table"] = "T"
        at.session_state["triage_action"] = "all"
        at.session_state[group_key] = True            # the group card is open
        at.run()
        assert not at.exception, at.exception
        at.button(key=f"acc_{first}").click().run()    # decide one issue inside the group
        assert not at.exception, at.exception
        assert repository.fetch_issue(c, first)["status"] == "accepted"
        assert at.session_state[group_key] is True     # still open
        assert at.session_state["filter_table"] == "T" and at.session_state["triage_action"] == "all"
    finally:
        c.execute("DELETE FROM uploads WHERE source_client = %s", (slug,))
        c.execute("DELETE FROM workspaces WHERE slug = %s", (slug,))
        c.commit()
        c.close()
