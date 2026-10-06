"""Smoke tests for the Streamlit app via Streamlit's AppTest harness.

Navigation renders one page per run, so each test opens the page it
needs through the app's start-page hook (session_state["start_page"]).
"""
from pathlib import Path

import pytest

from app.db import repository

APP_PATH = Path(__file__).resolve().parents[2] / "dashboard" / "app.py"
ADMIN = {"id": 0, "username": "pytest-admin", "role": "admin"}
REVIEWER = {"id": 0, "username": "pytest-reviewer", "role": "reviewer"}
ALL_PAGES = ["overview", "triage", "ask", "rules", "scores", "relationships",
             "new-audit", "schema", "organisation", "settings"]


def _app(user=ADMIN, page: str | None = None):
    """The app as a signed-in user (login state lives in session_state)."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=90)
    if user:
        at.session_state["user"] = user
        at.session_state["username"] = user["username"]
    if page:
        at.session_state["start_page"] = page
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


def test_dashboard_renders_kpi_cards():
    at = _app(page="overview")
    text = _text(at)
    for label in ("Data quality score", "Waiting for review", "High severity", "Found by AI"):
        assert label in text


def test_scores_page_renders_overall_score():
    at = _app(page="scores")
    assert "Overall Data Quality Score" in _text(at)
    assert "By dimension" in _text(at)


def test_triage_has_filters_and_decision_buttons():
    at = _app(page="triage")
    assert {"Found by", "Status", "Severity", "Trend"} <= {s.label for s in at.selectbox}
    uploads = repository.list_uploads(repository.connect())
    if uploads[0]["pending_count"] > 0:
        keys = [b.key or "" for b in at.button]
        assert any(k.startswith("acc_") for k in keys) and any(k.startswith("open_") for k in keys)


def test_switching_company_and_audit_does_not_break():
    at = _app()
    company, audit = at.sidebar.selectbox[0], at.sidebar.selectbox[1]
    assert company.label == "Company" and audit.label == "Audit"
    audit.select(audit.options[-1]).run()       # oldest audit of this company
    assert not at.exception
    company = at.sidebar.selectbox[0]
    company.select(company.options[-1]).run()   # another company
    assert not at.exception


def test_signed_out_users_see_only_the_login_form():
    at = _app(user=None)
    assert not at.exception
    assert [t.label for t in at.text_input] == ["Username", "Password"]
    assert "_pages" not in at.session_state


def test_reviewer_gets_no_admin_pages():
    at = _app(REVIEWER)
    assert not at.exception
    pages = set(at.session_state["_pages"])
    assert {"triage", "ask", "rules"} <= pages
    assert not pages & {"new-audit", "schema", "organisation", "settings"}


def test_reviewer_cannot_open_an_admin_page_by_link():
    at = _app(REVIEWER, page="settings")
    assert not at.exception
    assert "AI model" not in _text(at)
