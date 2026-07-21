"""Offline orchestration test for the Layer 2 runner (no real API calls)."""
from pathlib import Path

from app.llm.run_audit import audit_directory


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.seen_users = []

    def complete(self, system, user, **kwargs):
        self.seen_users.append(user)
        return self.reply


def _write_csv(path: Path, rows: list[str]):
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_audit_directory_runs_full_pipeline_offline(tmp_path):
    data = tmp_path / "client"
    data.mkdir()
    # a header table and a detail table (no headers, positional cols)
    _write_csv(data / "Orders.csv", [f"{i},Acme Distributors {i},{i * 100}.00" for i in range(1, 31)])
    _write_csv(data / "Lines.csv", [f"{i % 30 + 1},{i},{i * 2}" for i in range(1, 61)])

    client = FakeClient('{"issues": [{"column": "col_3", "row_ref": 5, "dimension": "validity", "severity": "high", "explanation": "outlier", "recommendation": "check", "fixable": "yes"}]}')
    results = audit_directory(data, client=client)

    assert set(results.keys()) == {"Orders", "Lines"}
    assert results["Orders"][0]["source"] == "llm"
    assert results["Orders"][0]["error_type"] == "type2"


def test_pseudonymization_runs_before_the_model_sees_data(tmp_path):
    # Orders col_2 is a free-text company name -> must be masked in the
    # payload that reaches the (fake) model, proving cloud sends no raw PII.
    data = tmp_path / "client"
    data.mkdir()
    _write_csv(data / "Orders.csv", [f"{i},Acme Distributors Ltd {i},{i * 100}.00" for i in range(1, 31)])

    client = FakeClient('{"issues": []}')
    audit_directory(data, client=client)

    sent = "\n".join(client.seen_users)
    assert "Acme Distributors" not in sent
    assert "MASK_" in sent


def test_tables_filter_limits_which_tables_are_audited(tmp_path):
    data = tmp_path / "client"
    data.mkdir()
    _write_csv(data / "Orders.csv", [f"{i},X {i},{i}.00" for i in range(1, 31)])
    _write_csv(data / "Lines.csv", [f"{i},{i}" for i in range(1, 31)])

    client = FakeClient('{"issues": []}')
    results = audit_directory(data, tables=["Orders"], client=client)
    assert set(results.keys()) == {"Orders"}
