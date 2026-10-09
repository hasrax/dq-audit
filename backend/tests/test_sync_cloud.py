from app.db import sync_cloud
from app.privacy.pseudonymize import mask_value


class StubMasker(sync_cloud.Masker):
    def __init__(self):
        self.types = {(1, "T", "col_1"): "int", (1, "T", "col_2"): "varchar", (1, "T", "col_3"): "int"}
        self.clients = {1: "x"}
        self._overrides = {"x": {"T": {"mask": {"col_3"}}}}
        self.excluded_clients, self.excluded_uploads = set(), set()


def test_scrub_masks_long_digit_runs_only():
    assert sync_cloud.scrub("call 0771234567 about 12 rows") == f"call {mask_value('0771234567')} about 12 rows"
    assert sync_cloud.scrub({"min": 771234567, "rows": 20608, "pct": 99.5}) == {
        "min": mask_value("771234567"), "rows": 20608, "pct": 99.5}


def test_issue_samples_mask_text_and_override_columns():
    row = {"upload_id": 1, "table_name": "T", "description": "d", "recommendation": None, "user_note": None,
           "row_reference": {"count": 1, "samples": [{"row": 0, "values": {
               "col_1": "42", "col_2": "Perera Stores", "col_3": "771234567"}}]}}
    out = sync_cloud.transform("issues", row, StubMasker())
    values = out["row_reference"]["samples"][0]["values"]
    assert values["col_1"] == "42"                              # numeric kept (audit subject)
    assert values["col_2"] == mask_value("Perera Stores")      # text masked
    assert values["col_3"] == mask_value("771234567")          # privacy override masked
