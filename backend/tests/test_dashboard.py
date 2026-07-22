"""Smoke test for the Streamlit triage app via Streamlit's AppTest harness."""
from pathlib import Path

import pytest

from app.db import repository

APP_PATH = Path(__file__).resolve().parents[2] / "dashboard" / "app.py"


@pytest.fixture(autouse=True)
def _require_db():
    try:
        repository.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available: {exc}")


def test_app_boots_without_exception():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=60).run()
    assert not at.exception
    assert any("Data Quality Audit" in t.value for t in at.title)


def test_app_renders_triage_controls_when_issues_exist():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=60).run()
    # sidebar has the audit-run selector and the status filter
    assert len(at.sidebar.selectbox) >= 2
    # if the selected run has flagged issues, accept/reject/consider buttons render
    conn = repository.connect()
    uploads = repository.list_uploads(conn)
    if uploads and uploads[0]["pending_count"] > 0:
        assert len(at.button) >= 3


def test_scores_tab_renders_overall_score():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=60).run()
    assert not at.exception
    markdown_blob = " ".join(m.value for m in at.markdown)
    assert "Overall Data Quality Score" in markdown_blob
    assert "By dimension" in markdown_blob
