import pytest

from app.chat import threads
from app.db import repository

CLIENT = "__pytest_threads__"


@pytest.fixture
def conn():
    try:
        c = repository.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available: {exc}")
    repository.apply_schema(c)
    yield c
    c.execute("DELETE FROM chat_threads WHERE source_client = %s", (CLIENT,))
    c.execute("DELETE FROM uploads WHERE source_client = %s", (CLIENT,))
    c.commit()
    c.close()


def test_thread_lifecycle(conn):
    uid = repository.create_upload(conn, "f", CLIENT, 1)
    repository.save_issues(conn, uid, [{"table_name": "T", "column_name": "c", "dimension": "validity",
                                        "source": "rule", "error_type": "type1", "severity": "low"}])
    issue_id = repository.fetch_issues(conn, uid)[0]["id"]

    tid = threads.create_thread(conn, CLIENT, "alice")
    threads.add_message(conn, tid, uid, "user", "Which table should we fix first and why is that the case?", "alice")
    threads.add_message(conn, tid, uid, "assistant", "GRNHED.", "alice")
    t = threads.list_threads(conn, CLIENT, "alice")[0]
    assert t["title"].startswith("Which table should we fix first") and t["message_count"] == 2
    assert threads.list_threads(conn, CLIENT, "bob") == []          # threads are per user

    threads.attach_issue(conn, tid, issue_id)
    threads.attach_issue(conn, tid, issue_id)                         # idempotent
    assert [i["id"] for i in threads.thread_issues(conn, tid)] == [issue_id]
    threads.detach_issue(conn, tid, issue_id)
    assert threads.thread_issues(conn, tid) == []

    threads.archive_thread(conn, tid)
    assert threads.list_threads(conn, CLIENT, "alice") == []
