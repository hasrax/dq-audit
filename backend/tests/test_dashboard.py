"""Smoke test for the Streamlit triage app via Streamlit's AppTest harness."""
from pathlib import Path

import pytest

from app.db import repository

APP_PATH = Path(__file__).resolve().parents[2] / "dashboard" / "app.py"
ADMIN = {"id": 0, "username": "pytest-admin", "role": "admin"}


def _app(user=ADMIN):
    """The app as a signed-in user (login state lives in session_state)."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    if user:
        at.session_state["user"] = user
        at.session_state["username"] = user["username"]
    return at.run()


@pytest.fixture(autouse=True)
def _require_db():
    try:
        repository.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available: {exc}")


def test_app_boots_without_exception():
    from streamlit.testing.v1 import AppTest

    at = _app()
    assert not at.exception
    # branded header (temporary working name) is rendered via markdown
    assert any("ClarityDQ" in m.value for m in at.markdown)


def test_app_renders_triage_controls_when_issues_exist():
    from streamlit.testing.v1 import AppTest

    at = _app()
    # sidebar has the audit-run selector and the status filter
    assert len(at.sidebar.selectbox) >= 1  # audit run (filters now live in Triage)
    # if the selected run has flagged issues, accept/reject/consider buttons render
    conn = repository.connect()
    uploads = repository.list_uploads(conn)
    if uploads and uploads[0]["pending_count"] > 0:
        assert len(at.button) >= 3


def test_scores_tab_renders_overall_score():
    from streamlit.testing.v1 import AppTest

    at = _app()
    assert not at.exception
    markdown_blob = " ".join(m.value for m in at.markdown)
    assert "Overall Data Quality Score" in markdown_blob
    assert "By dimension" in markdown_blob


def test_overview_tab_renders_kpis():
    from streamlit.testing.v1 import AppTest

    at = _app()
    assert not at.exception
    assert any("Total issues" in m.label for m in at.metric)
    assert any("DATA QUALITY SCORE" in m.value for m in at.markdown)


def test_selecting_an_upload_with_aliases_does_not_break():
    from streamlit.testing.v1 import AppTest

    at = _app()
    sel = at.sidebar.selectbox[0]
    if sel.options:
        sel.select(sel.options[-1]).run()  # oldest upload (aliased client if present)
    assert not at.exception


def test_signed_out_users_see_only_the_login_form():
    at = _app(user=None)
    assert not at.exception
    assert [t.label for t in at.text_input] == ["Username", "Password"]
    assert not at.tabs


def test_reviewer_does_not_get_admin_tabs():
    at = _app({"id": 0, "username": "pytest-reviewer", "role": "reviewer"})
    assert not at.exception
    labels = [t.label for t in at.tabs]
    assert any(l.endswith("Triage") for l in labels) and any(l.endswith("Ask") for l in labels)
    admin_only = ("New audit", "Schema review", "Organisation", "Settings")
    assert not [l for l in labels if l.endswith(admin_only)]
