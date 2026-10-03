import pytest

from app.auth import users
from app.db import repository


def test_hash_round_trip_and_rejects_wrong_password():
    h = users.hash_password("correct horse")
    assert h.startswith("$2") and users.verify_password("correct horse", h)
    assert not users.verify_password("wrong", h)


@pytest.fixture
def conn():
    try:
        c = repository.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available: {exc}")
    repository.apply_schema(c)
    yield c
    c.execute("DELETE FROM users WHERE username LIKE '__pytest_%'")
    c.commit()
    c.close()


def test_create_and_authenticate(conn):
    users.create_user(conn, "__pytest_rev", "s3cret-pass", "reviewer")
    assert users.authenticate(conn, "__pytest_rev", "s3cret-pass")["role"] == "reviewer"
    assert users.authenticate(conn, "__pytest_rev", "nope") is None
    assert users.authenticate(conn, "__pytest_nobody", "s3cret-pass") is None
    with pytest.raises(ValueError):
        users.create_user(conn, "__pytest_short", "short", "reviewer")
    with pytest.raises(ValueError):
        users.create_user(conn, "__pytest_x", "long-enough", "superuser")
