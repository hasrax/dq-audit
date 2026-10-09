"""Study mode in the app (AppTest): participant screens and the admin page."""
from pathlib import Path

import pytest

from app import workspaces as ws
from app.db import repository
from app.study import participants as sp

APP_PATH = Path(__file__).resolve().parents[2] / "dashboard" / "app.py"
CODE = "P93"


@pytest.fixture
def conn():
    try:
        c = repository.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available: {exc}")
    repository.apply_schema(c)
    if not ws.study_workspace(c):
        pytest.skip("no study workspace (python -m app.study.setup --from-upload=<id>)")
    sp.create_codes(c, [CODE])
    sp.reset(c, CODE)
    yield c
    c.execute("DELETE FROM study_participants WHERE code = %s", (CODE,))
    c.commit()
    c.close()


def _as(user, page=None):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=90)
    at.session_state["user"] = user
    at.session_state["username"] = user["username"]
    if page:
        at.session_state["start_page"] = page
    return at.run()


PARTICIPANT = {"id": 0, "username": CODE, "role": "participant"}


def _text(at) -> str:
    return " ".join(m.value for m in at.markdown)


def test_participant_sees_consent_first(conn):
    at = _as(PARTICIPANT)
    assert not at.exception
    assert "Welcome" in _text(at)
    assert at.button(key="study_start").disabled            # until consent is ticked


def test_participant_gets_task_panel_and_only_three_pages(conn):
    sp.give_consent(conn, CODE, "desktop", "Chrome")
    at = _as(PARTICIPANT, page="triage")
    assert not at.exception
    assert "Study · task 1 of 5" in _text(at)
    assert set(at.session_state["_pages"]) == {"overview", "triage", "ask"}
    assert any(s.label == "Table" for s in at.selectbox)       # task 4 needs a table filter
    assert not any((b.key or "").startswith("bulk_") for b in at.button)


def test_finished_participant_sees_questionnaire_step(conn):
    sp.give_consent(conn, CODE, "desktop", "Chrome")
    for n in range(1, 6):
        sp.end_task(conn, CODE, n, "done")
    at = _as(PARTICIPANT)
    assert not at.exception
    assert "one last step" in _text(at)


def test_admin_study_page(conn):
    at = _as({"id": 0, "username": "pytest-admin", "role": "admin"}, page="study")
    assert not at.exception
    assert "Study" in _text(at)
    assert any(b.key == "study_make_codes" for b in at.button)
