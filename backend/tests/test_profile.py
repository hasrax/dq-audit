import json
from pathlib import Path

from app.profiler.profile import profile_directory

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "hemas"


def test_profile_directory_covers_all_hemas_tables():
    profiles, relationships = profile_directory(DATA_DIR)
    assert set(profiles.keys()) == {
        "GRNDtl",
        "GRNHED",
        "InvoiceDetails",
        "InvoiceHeader",
        "SupplierMaster",
    }
    assert len(relationships) > 0


def test_grnhed_profile_has_expected_shape_and_pk():
    profiles, _ = profile_directory(DATA_DIR)
    grnhed = profiles["GRNHED"]
    assert grnhed["row_count"] == 2989
    assert grnhed["primary_key"] == "col_1"
    assert "col_1" in grnhed["primary_key_candidates"]
    assert len(grnhed["columns"]) == 16
    assert grnhed["columns"]["col_1"]["type"] == "int"
    assert grnhed["columns"]["col_1"]["null_count"] == 0


def test_detail_tables_have_no_primary_key():
    profiles, _ = profile_directory(DATA_DIR)
    assert profiles["GRNDtl"]["primary_key"] is None
    assert profiles["InvoiceDetails"]["primary_key"] is None


def test_profiles_and_relationships_are_json_serializable():
    profiles, relationships = profile_directory(DATA_DIR)
    for profile in profiles.values():
        json.dumps(profile, default=str)
    json.dumps(relationships, default=str)
