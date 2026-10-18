import json

import pytest

from app.db import repository
from app.llm.auditor import audit_table
from app.schema_docs import store
from app.schema_docs.generate import PENDING_DESCRIPTION, generate_table_doc, validate_doc

TEST_CLIENT = "__pytest_docs__"

PROFILE = {
    "table_name": "GRNHED",
    "row_count": 3,
    "primary_key": "col_1",
    "columns": {
        "col_1": {"type": "int", "null_pct": 0.0, "distinct_count": 3, "min": 1, "max": 3, "mean": 2.0, "top_values": {"1": 1}},
        "col_2": {"type": "date", "null_pct": 0.0, "distinct_count": 3, "min": "2024-01-01", "max": "2024-03-01", "mean": None, "top_values": {}},
    },
}


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, user, **kwargs):
        self.calls.append(user)
        return self.replies.pop(0)


def test_validate_drops_invented_columns_and_pends_missing_ones():
    parsed = {
        "table_description": "Goods received notes.",
        "table_module": "purchasing",
        "columns": [
            {"column": "col_1", "description": "GRN number.", "confidence": "high"},
            {"column": "col_9", "description": "Invented.", "confidence": "HIGH"},
        ],
    }
    doc = validate_doc(parsed, PROFILE)
    assert list(doc["columns"]) == ["col_1", "col_2"]
    assert doc["hallucinated"] == ["col_9"]
    assert doc["missing"] == ["col_2"]
    assert doc["columns"]["col_1"]["confidence"] == "HIGH"
    assert doc["columns"]["col_2"] == {
        "display_name": None, "description": PENDING_DESCRIPTION, "expected_domain": None,
        "business_rules": [], "confidence": "LOW", "from_llm": False,
    }
    assert doc["table_module"] == "Purchasing"


def test_validate_normalises_bad_confidence_and_module():
    doc = validate_doc({"table_module": "Logistics", "columns": [{"column": "col_1", "confidence": "sure"}]}, PROFILE)
    assert doc["table_module"] == "Other"
    assert doc["columns"]["col_1"]["confidence"] == "LOW"


def test_generate_retries_once_then_pends_everything():
    client = FakeClient(["not json", "still not json"])
    doc = generate_table_doc(PROFILE, [], ["GRNHED", "GRNDtl"], client=client)
    assert len(client.calls) == 2
    assert doc["missing"] == ["col_1", "col_2"]
    assert '"other_tables": ["GRNDtl"]' in client.calls[0]


def test_context_uses_verified_columns_only():
    cols = [
        {"column_name": "col_1", "display_name": "GRN Number", "description": "GRN number.",
         "expected_domain": "positive integer", "business_rules": ["unique"], "confidence": "VERIFIED"},
        {"column_name": "col_2", "display_name": None, "description": "Draft.", "expected_domain": None,
         "business_rules": [], "confidence": "HIGH"},
    ]
    text = store.build_schema_context("GRNHED", None, cols)
    assert 'col_1 "GRN Number": GRN number. Expected: positive integer. Rules: unique.' in text
    assert "col_2" not in text
    assert store.build_schema_context("GRNHED", None, cols[1:]) == ""


def test_auditor_prompt_unchanged_without_context():
    reply = '{"issues": []}'
    plain, empty = FakeClient([reply]), FakeClient([reply])
    sample = {"sampled_rows": []}
    audit_table(PROFILE, sample, [], client=plain)
    audit_table(PROFILE, sample, [], client=empty, schema_context="")
    assert plain.calls == empty.calls


@pytest.fixture
def conn():
    try:
        c = repository.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not available: {exc}")
    repository.apply_schema(c)
    yield c
    c.execute("DELETE FROM schema_doc_columns WHERE source_client = %s", (TEST_CLIENT,))
    c.execute("DELETE FROM schema_doc_tables WHERE source_client = %s", (TEST_CLIENT,))
    c.execute("DELETE FROM uploads WHERE source_client = %s", (TEST_CLIENT,))   # cascades to profiles
    c.commit()
    c.close()


def test_store_round_trip_verify_and_regenerate(conn):
    parsed = {"table_description": "GRNs.", "table_module": "Purchasing",
              "columns": [{"column": "col_1", "description": "GRN number.", "confidence": "MEDIUM"}]}
    doc = validate_doc(parsed, PROFILE)
    doc["llm_raw"] = parsed
    store.save_table_doc(conn, TEST_CLIENT, "GRNHED", doc, model="fake")

    cols = {c["column_name"]: c for c in store.fetch_doc_columns(conn, TEST_CLIENT, "GRNHED")}
    assert cols["col_2"]["confidence"] == "LOW"  # least certain listed first
    assert store.fetch_doc_columns(conn, TEST_CLIENT, "GRNHED")[0]["column_name"] == "col_2"

    store.verify_column(conn, cols["col_1"]["id"])               # unedited
    store.update_column(conn, cols["col_2"]["id"], "Bill Date", "Supplier bill date.", None, [])
    store.verify_column(conn, cols["col_2"]["id"])               # edited
    cols = {c["column_name"]: c for c in store.fetch_doc_columns(conn, TEST_CLIENT, "GRNHED")}
    assert cols["col_1"]["edited_before_verify"] is False
    assert cols["col_2"]["edited_before_verify"] is True
    assert cols["col_1"]["llm_confidence"] == "MEDIUM"

    # a redraft must not overwrite verified work
    redraft = validate_doc({"columns": [{"column": "col_1", "description": "Changed.", "confidence": "LOW"}]}, PROFILE)
    store.save_table_doc(conn, TEST_CLIENT, "GRNHED", redraft)
    cols = {c["column_name"]: c for c in store.fetch_doc_columns(conn, TEST_CLIENT, "GRNHED")}
    assert cols["col_1"]["description"] == "GRN number."

    assert "Supplier bill date." in store.get_schema_context(TEST_CLIENT, "GRNHED", conn=conn)
    assert store.doc_stats(conn, TEST_CLIENT)["columns_verified"] == 2
    assert json.loads(json.dumps(store.doc_aliases(conn, TEST_CLIENT))) == {"GRNHED": {"col_2": "Bill Date"}}


def test_stored_profile_masks_text_values_but_keeps_numbers_and_earlier_masks():
    from app.schema_docs.generate import mask_stored_profile

    profile = {"table_name": "Customers", "columns": {
        "Name": {"type": "varchar", "min": "Amal Perera", "max": "Nimal Silva"},
        "Region": {"type": "varchar", "min": "MASK_ab12cd34", "max": None},
        "CreditLimit": {"type": "decimal", "min": 0.0, "max": 5000000.0},
        "Joined": {"type": "date", "min": "2020-02-02", "max": "2026-07-01"},
    }}
    masked = mask_stored_profile(profile, TEST_CLIENT)["columns"]
    assert masked["Name"]["min"].startswith("MASK_") and "Nimal" not in masked["Name"]["max"]
    assert masked["Region"]["min"] == "MASK_ab12cd34" and masked["Region"]["max"] is None
    assert masked["CreditLimit"]["max"] == 5000000.0 and masked["Joined"]["min"] == "2020-02-02"
    assert profile["columns"]["Name"]["min"] == "Amal Perera"            # the input is not changed


def test_draft_from_stored_audits_needs_no_files_and_skips_documented_tables(conn):
    from app.schema_docs.generate import audited_tables, document_from_audits, stored_profiles

    old = repository.create_upload(conn, "old", TEST_CLIENT, 4)
    new = repository.create_upload(conn, "new", TEST_CLIENT, 4)
    rows = [  # (upload, table, column, type, null_count, null_pct, distinct, min, max, mean)
        (old, "Customers", "Code", "int", 0, 0, 3, "1", "3", 2),
        (new, "Customers", "Code", "int", 0, 0, 4, "1", "4", 2.5),      # the newest audit wins
        (new, "Customers", "Name", "varchar", 1, 25, 3, "Amal Perera", "Nimal Silva", None),
        (old, "Invoices", "InvoiceNo", "int", 0, 0, 10, "100", "109", 104.5),
    ]
    conn.cursor().executemany(
        "INSERT INTO profiles (upload_id, table_name, column_name, data_type, null_count, null_pct, "
        "distinct_count, min_val, max_val, mean_val) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", rows)
    conn.commit()

    assert audited_tables(conn, TEST_CLIENT) == ["Customers", "Invoices"]
    profiles, _ = stored_profiles(conn, TEST_CLIENT)
    assert list(profiles["Customers"]["columns"]) == ["Code", "Name"]
    assert profiles["Customers"]["row_count"] == 4                     # 1 empty = 25% of 4 rows
    assert profiles["Customers"]["primary_key"] == "Code"
    assert profiles["Invoices"]["row_count"] == 10                     # no empties: largest distinct count

    reply = json.dumps({"table_description": "Customers.", "table_module": "Sales",
                        "columns": [{"column": "Name", "description": "Customer name.", "confidence": "HIGH"}]})
    client = FakeClient([reply, reply])
    done = document_from_audits(conn, TEST_CLIENT, client=client)
    assert set(done) == {"Customers", "Invoices"} and len(client.calls) == 2
    assert not any("Amal" in call or "Nimal" in call for call in client.calls)   # text values masked
    assert '"max": "4"' in client.calls[0]                                       # numbers kept
    cols = {c["column_name"]: c for c in store.fetch_doc_columns(conn, TEST_CLIENT, "Customers")}
    assert cols["Name"]["description"] == "Customer name."
    assert cols["Code"]["description"] == PENDING_DESCRIPTION                   # skipped by the AI: needs review

    again = FakeClient([])
    assert document_from_audits(conn, TEST_CLIENT, client=again) == {} and again.calls == []
