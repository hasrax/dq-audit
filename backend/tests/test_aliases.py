import json

from app.aliases import display_columns, load_aliases


def test_load_aliases_missing_client_returns_empty(tmp_path):
    assert load_aliases("nope", config_dir=tmp_path) == {}


def test_load_and_display_alias(tmp_path):
    (tmp_path / "acme.json").write_text(
        json.dumps({"GRNHED": {"col_5": "Invoice No"}}), encoding="utf-8"
    )
    aliases = load_aliases("acme", config_dir=tmp_path)
    assert display_columns(aliases, "GRNHED", "col_5") == "Invoice No"


def test_unmapped_column_passes_through():
    aliases = {"GRNHED": {"col_5": "Invoice No"}}
    assert display_columns(aliases, "GRNHED", "col_9") == "col_9"
    assert display_columns(aliases, "OtherTable", "col_5") == "col_5"


def test_comma_column_pair_is_aliased_per_part():
    aliases = {"GRNHED": {"col_3": "Bill Date", "col_4": "Due Date"}}
    assert display_columns(aliases, "GRNHED", "col_3,col_4") == "Bill Date, Due Date"


def test_none_column_stays_none():
    assert display_columns({}, "T", None) is None


def test_shipped_hemas_alias_file_is_valid_and_maps_grn_number():
    aliases = load_aliases("hemas")  # from config/aliases/hemas.json
    assert display_columns(aliases, "GRNHED", "col_1") == "GRN Number"


def test_shipped_ussawatte_alias_file_is_valid_and_maps_verified_names():
    aliases = load_aliases("ussawatte")  # from config/aliases/ussawatte.json
    assert display_columns(aliases, "CustomerMaster", "col_1") == "Customer Code"   # verified against the source database
    assert display_columns(aliases, "WhyNotBilling", "col_2") == "Customer Code"
    # unmapped column falls back to positional
    assert display_columns(aliases, "CustomerMaster", "col_99") == "col_99"
