import pandas as pd

from app.eval import study_results as sr


def test_sus_formula():
    assert sr.sus_score({i: 3 for i in range(1, 11)}) == 50.0
    best = {i: (5 if i % 2 else 1) for i in range(1, 11)}
    assert sr.sus_score(best) == 100.0


def _form(tmp_path, rows):
    cols = (["Timestamp", "Consent", "Participant code", "Your main role at work",
             "How long have you used an ERP or business system?"]
            + [f"Q{i}. statement {i}" for i in range(1, 11)] + [f"C{i}. clarity {i}" for i in range(1, 6)]
            + ["What was most useful?", "What was most confusing?"])
    path = tmp_path / "SUS Responses.csv"
    pd.DataFrame(rows, columns=cols).to_csv(path, index=False)
    return path


def test_parse_sus_by_prefix_and_skips_no_consent(tmp_path):
    neutral = ["3"] * 10
    path = _form(tmp_path, [
        ["10/10/2026 14:00:00", "Yes, I agree", "p01", "Accountant", "1-3 years", *neutral, *["4"] * 5, "Chat", "-"],
        ["10/10/2026 15:00:00", "No", "P02", "", "", *[""] * 10, *[""] * 5, "", ""],
        ["10/10/2026 16:00:00", "Yes", "P03", "Storekeeper", "<1 year", *["5", "1"] * 5, *["5"] * 5, "", ""],
    ])
    sus, open_cols = sr.parse_sus(path)
    assert list(sus["code"]) == ["P01", "P03"]               # P02 declined; codes upper-cased
    assert list(sus["sus"]) == [50.0, 100.0]
    assert sus["C1"].tolist() == [4.0, 5.0]
    assert open_cols == ["What was most useful?", "What was most confusing?"]


def test_integrity_flags(tmp_path):
    people = pd.DataFrame([
        {"code": "P01", "status": "finished", "started_at": pd.Timestamp("2026-10-10 08:20"),
         "finished_at": pd.Timestamp("2026-10-10 08:30")},
        {"code": "P04", "status": "finished", "started_at": pd.Timestamp("2026-10-10 09:00"),
         "finished_at": pd.Timestamp("2026-10-10 09:10")},
    ])
    sus = pd.DataFrame([
        {"code": "P01", "timestamp": "2026-10-10 14:05:00"},      # 13:35 local -> 08:35 UTC: fine
        {"code": "P01", "timestamp": "2026-10-10 14:06:00"},      # duplicate
        {"code": "P77", "timestamp": "2026-10-10 14:07:00"},      # never issued
    ])
    checks = sr.integrity(people, sus, utc_offset_h=5.5, chat_failed={"P04": 2})
    found = set(zip(checks["check"], checks["code"]))
    assert ("AI chat failed during session", "P04") in found
    no_form = sr.integrity(people, pd.DataFrame(), utc_offset_h=5.5, chat_failed={"P01": 1})
    assert list(no_form["check"]) == ["AI chat failed during session"]   # reported even before the form
    assert ("duplicate code in form", "P01") in found
    assert ("form code never issued", "P77") in found
    assert ("finished session without a form response", "P04") in found
    assert not any(c.startswith("form more than") for c in checks["check"])
