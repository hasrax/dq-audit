import pytest

from app import aliases
from app import workspaces as ws
from app.db import repository


def test_slugify_handles_any_name():
    assert ws.slugify("Sunrise Traders (Pvt) Ltd") == "sunrise-traders-pvt-ltd"
    assert ws.slugify("  ") == "company"


@pytest.fixture
def conn():
    try:
        c = repository.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available: {exc}")
    repository.apply_schema(c)
    yield c
    c.execute("DELETE FROM uploads WHERE source_client LIKE 'pytest-ws%%'")
    c.execute("DELETE FROM workspaces WHERE slug LIKE 'pytest-ws%%'")
    c.commit()
    c.close()


def test_create_rename_and_label(conn):
    slug = ws.create_workspace(conn, "pytest ws Sunrise", created_by="tester")
    assert slug == "pytest-ws-sunrise"
    with pytest.raises(ValueError):
        ws.create_workspace(conn, "PYTEST WS SUNRISE")          # names are unique, case-insensitive
    ws.rename_workspace(conn, slug, "pytest ws Sunrise Traders")
    ws.refresh_names(conn)
    assert aliases.client_label(slug) == "pytest ws Sunrise Traders"


def test_existing_clients_are_adopted(conn):
    repository.create_upload(conn, "f", "pytest-ws-legacy", 1)
    assert ws.sync_from_uploads(conn) >= 1
    assert any(w["slug"] == "pytest-ws-legacy" and w["audit_count"] == 1 for w in ws.list_workspaces(conn))
