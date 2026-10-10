import pytest

from app.db import repository, sync_cloud
from app.eval import live
from app.study import participants as sp
from app.study.setup import build_study_workspace

CLIENT = "__pytest_study_src__"
STUDY = "__pytest_study__"
CODES = ["P90", "P91"]


def test_user_agent_parsing():
    chrome = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/129.0 Safari/537.36"
    assert sp.parse_user_agent(chrome) == ("desktop", "Chrome")
    assert sp.parse_user_agent("Mozilla/5.0 (iPhone) Mobile/15E148 Safari/604.1") == ("mobile", "Safari")
    assert sp.parse_user_agent("Mozilla/5.0 (iPad) Safari/604.1")[0] == "tablet"
    assert sp.parse_user_agent(chrome.replace("Chrome/129.0", "Chrome/129.0 Edg/129.0"))[1] == "Edge"


@pytest.fixture
def conn():
    try:
        c = repository.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available: {exc}")
    repository.apply_schema(c)
    yield c
    c.execute("DELETE FROM study_participants WHERE code = ANY(%s)", (CODES,))
    c.execute("DELETE FROM uploads WHERE source_client IN (%s, %s)", (CLIENT, STUDY))
    c.execute("DELETE FROM workspaces WHERE slug IN (%s, %s)", (CLIENT, STUDY))
    c.commit()
    c.close()


def _source_audit(conn):
    conn.execute("INSERT INTO workspaces (slug, name) VALUES (%s, %s) ON CONFLICT DO NOTHING", (CLIENT, "pytest src"))
    uid = repository.create_upload(conn, "f", CLIENT, 100)
    rule = {"rule_key": "k1", "template": "completeness_null_pct", "category": "completeness", "table_name": "T",
            "column_name": "c", "parameters": {"max_null_pct": 5, "fk_role": False},
            "evidence": {"null_pct": 50, "null_count": 50, "row_count": 100}, "violation_count": 50}
    ordering = {"rule_key": "k2", "template": "consistency_ordering_violation", "category": "consistency",
                "table_name": "T", "column_name": "a,b", "parameters": {},
                "evidence": {"conformance_pct": 97.0, "valid_pairs": 100}, "violation_count": 3}
    rule_ids = repository.save_rules(conn, uid, CLIENT, [rule, ordering])
    repository.save_issues(conn, uid, [
        {"table_name": "T", "column_name": "c", "dimension": "completeness", "source": "rule", "error_type": "type1",
         "severity": "high", "description": "c is half empty", "rule_id": "completeness_null_pct", "rule_key": "k1"},
        {"table_name": "T", "column_name": "a,b", "dimension": "consistency", "source": "rule", "error_type": "type1",
         "severity": "high", "description": "a is usually <= b", "rule_id": "consistency_ordering_violation",
         "rule_key": "k2"},
        {"table_name": "T", "column_name": "d", "dimension": "validity", "source": "llm", "error_type": "type2",
         "severity": "low", "description": "odd value"},
    ], rule_ids=rule_ids)
    real = repository.fetch_issues(conn, uid)
    repository.update_issue_status(conn, real[0]["id"], "accepted", "my real decision", reviewed_by="mhg")
    return uid


def test_study_copy_is_fresh_linked_and_excluded(conn):
    src = _source_audit(conn)
    dst = build_study_workspace(conn, src, name="pytest study", slug=STUDY, replace_others=False)
    copied = repository.fetch_issues(conn, dst)
    assert len(copied) == 2 and all(i["status"] == "flagged" and i["user_note"] is None for i in copied)
    assert not any(i["rule_id"] == "consistency_ordering_violation" for i in copied)   # study curation
    assert conn.execute("SELECT count(*) FROM rules WHERE upload_id = %s", (dst,)).fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM dq_scores WHERE upload_id = %s", (dst,)).fetchone()[0] == 1
    rule_issue = next(i for i in copied if i["source"] == "rule")
    assert repository.fetch_rule(conn, rule_issue["rule_ref"])["upload_id"] == dst     # linked to the copied rule
    assert conn.execute("SELECT kind, config_from FROM workspaces WHERE slug = %s", (STUDY,)).fetchone() == \
        ("study", CLIENT)
    all_ids = {i["id"] for i in live.scope_issues(conn)}
    assert not all_ids & {i["id"] for i in copied}                                    # never in real metrics


def test_participant_lifecycle_and_isolation(conn):
    src = _source_audit(conn)
    dst = build_study_workspace(conn, src, name="pytest study", slug=STUDY, replace_others=False)
    sp.create_codes(conn, CODES)
    assert sp.check_code(conn, "p90") == (True, "")
    assert sp.check_code(conn, "P99")[0] is False

    sp.give_consent(conn, "P90", "desktop", "Chrome")
    assert sp.current_task(conn, "P90") == 1
    high = next(i for i in repository.fetch_issues(conn, dst) if i["severity"] == "high")
    sp.log_event(conn, "P90", "detail_opened", {"issue_id": high["id"], "severity": "high"})
    sp.end_task(conn, "P90", 1, "done", "Half of the values are missing.")
    sp.save_decision(conn, "P90", high["id"], "rejected", "looks fine to me")
    sp.end_task(conn, "P90", 2, "done")
    sp.end_task(conn, "P90", 3, "couldnt")          # no chat event -> not verified

    t = sp.tasks(conn, "P90")
    assert t[1]["system_verified"] and t[1]["answer_text"] == "Half of the values are missing."
    assert t[2]["system_verified"] and not t[3]["system_verified"] and sp.current_task(conn, "P90") == 4
    # the participant's decision never reaches the real issue
    assert repository.fetch_issue(conn, high["id"])["status"] == "flagged"
    view = sp.overlay(repository.fetch_issues(conn, dst), sp.decisions(conn, "P90"), "P90")
    assert next(i for i in view if i["id"] == high["id"])["status"] == "rejected"
    assert sp.decisions(conn, "P91") == {}            # each participant starts fresh

    sp.end_task(conn, "P90", 4, "done")
    sp.end_task(conn, "P90", 5, "done")
    assert sp.get(conn, "P90")["status"] == "finished"
    assert sp.check_code(conn, "P90") == (False, "This code has already been used. Thank you for taking part.")

    sp.reset(conn, "P90")
    assert sp.get(conn, "P90")["status"] == "not_started" and sp.tasks(conn, "P90") == {}


def test_cloud_copy_skips_local_only_and_test_workspaces(conn):
    _source_audit(conn)
    conn.execute("UPDATE workspaces SET local_only = TRUE WHERE slug = %s", (CLIENT,))
    conn.commit()
    m = sync_cloud.Masker(conn)
    assert CLIENT in m.excluded_clients
    assert sync_cloud.transform("workspaces", {"slug": CLIENT, "name": "x"}, m) is None
    upload = next(iter(u for u, c in m.clients.items() if c == CLIENT))
    assert sync_cloud.transform("issues", {"upload_id": upload, "table_name": "T", "row_reference": None,
                                           "description": "", "recommendation": "", "user_note": None}, m) is None
