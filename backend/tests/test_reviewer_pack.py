import random

import pandas as pd
from openpyxl import Workbook

from app.eval import agreement as ag
from app.eval.reviewer_pack import allocate, named, systematic
from app.rules.engine import run_rules


def test_allocate_is_proportional_with_minimums_and_caps():
    a = allocate({"big": 1000, "mid": 300, "tiny": 3}, 100, minimum=10)
    assert sum(a.values()) == 100
    assert a["tiny"] == 3                     # capped at what exists
    assert a["mid"] >= 10 and a["big"] > a["mid"]
    assert allocate({"x": 5}, 100, 10) == {"x": 5}


def test_systematic_sample_is_seeded_and_spread():
    pool = list(range(1000))
    s1, s2 = systematic(pool, 10, random.Random(1)), systematic(pool, 10, random.Random(1))
    assert s1 == s2 and len(set(s1)) == 10
    assert max(b - a for a, b in zip(s1, s1[1:])) <= 101      # every ~100th item
    assert systematic([1, 2], 5, random.Random(0)) == [1, 2]


def test_collect_rows_lists_every_affected_row(tmp_path):
    df = pd.DataFrame({"id": range(1, 41), "qty": [None] * 3 + list(range(37)),
                       "price": [-5] * 2 + [10] * 38})
    df.to_csv(tmp_path / "T.csv", index=False)
    plain = run_rules(tmp_path, source_client="t")
    assert "tables" not in plain and not any("_rows" in i for i in plain["issues"])
    full = run_rules(tmp_path, source_client="t", collect_rows=True)
    by_rule = {i["rule_id"]: i for i in full["issues"]}
    assert by_rule["completeness_null_pct"]["_rows"] == [0, 1, 2]
    assert by_rule["validity_negative_value"]["_rows"] == [0, 1]
    assert {i["rule_key"] for i in plain["issues"]} == {i["rule_key"] for i in full["issues"]}  # same detection


def test_kappa_and_wilson():
    assert ag.cohen_kappa(["A", "A", "R", "R"], ["A", "R", "R", "R"]) == 0.5
    assert ag.cohen_kappa(["A", "A"], ["A", "A"]) is None                       # undefined: one category
    assert ag.fleiss_kappa([["A", "A", "A"], ["R", "R", "R"]]) == 1.0
    lo, hi = ag.wilson(0, 50)
    assert lo == 0.0 and 0.05 < hi < 0.09


def _triage_sheet(path, decisions):
    wb = Workbook()
    ws = wb.active
    ws.title = "Triage"
    ws.append(["No.", "Company", "Table", "Column", "What", "Why", "Examples", "Fix", "Decision", "Reason"])
    for n, d in enumerate(decisions, start=1):
        ws.append([n, "Company A", "T", "c", "", "", "", "", d, "looks normal" if (d or "").lower() == "reject" else None])
    ws.append([])
    ws.append([])
    ws.append(["Role / job title:", "Accountant"])
    ws.append(["Years of ERP / business-data experience:", "6"])
    wb.save(path)


def test_read_and_score_triage(tmp_path):
    p = tmp_path / "Triage_Agreement_R2.xlsx"
    _triage_sheet(p, ["Accept", "reject", "Unsure", None])
    decisions, reasons, decl = ag.read_triage(p)
    assert decisions == {1: "Accept", 2: "Reject", 3: "Unsure"} and reasons == {2: "looks normal"}
    assert decl == {"role": "Accountant", "years": "6"} and ag._reviewer(p) == "R2"
    key = [{"no": str(n), "issue_id": str(100 + n), "source": s}
           for n, s in enumerate(["rule", "llm", "rule", "llm"], start=1)]
    raters = {"MHG": {1: "Accept", 2: "Accept", 3: "Unsure", 4: "Reject"}, "R2": decisions}
    rows, confusion, disagreements = ag.score_triage(raters, key)
    allrow = next(r for r in rows if r["detector"] == "All" and r["unsure"] == "own category")
    assert allrow["n"] == 3 and allrow["agreement_pct"] == 66.7
    excl = next(r for r in rows if r["detector"] == "All" and r["unsure"] == "excluded")
    assert excl["n"] == 2
    assert [d["no"] for d in disagreements] == [2]


def test_read_and_score_recall(tmp_path):
    p = tmp_path / "Recall_Check_R2.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "A T"
    ws.append(["Company A - T"])
    ws.append(["Already reported"])
    ws.append([])
    ws.append(["No.", "col_1", "Issue found?", "Dimension", "Note"])
    ws.append(["R001", "x", "Y", "Validity", "odd"])
    ws.append(["R002", "y", "n", None, None])
    wb.save(p)
    answers, _ = ag.read_recall(p)
    assert answers["R001"]["found"] == "Y" and answers["R002"]["found"] == "N"
    key = [{"no": "R001", "in_subset": "Y"}, {"no": "R002", "in_subset": "Y"}, {"no": "R003", "in_subset": "N"}]
    rows, agree = ag.score_recall({"MHG": {"R001": {"found": "N", "dimension": None},
                                           "R002": {"found": "N", "dimension": None},
                                           "R003": {"found": "N", "dimension": None}},
                                   "R2": answers}, key)
    mhg, r2 = rows
    assert mhg["rows_rated"] == 3 and mhg["miss_rate_pct"] == 0.0
    assert r2["rows_rated"] == 2 and r2["issues_found"] == 1
    assert agree[0]["n"] == 2 and agree[0]["agreement_pct"] == 50.0


def test_named_replaces_positional_columns():
    al = {"Lines": {"col_10": "Item Order", "col_1": "Line No"}, "Head": {"col_1": "Receipt Code"}}
    text = "810 rows in Lines have a value in col_10 that does not match any col_1 in Head."
    assert named(text, "Lines", al) == ("810 rows in Lines have a value in Item Order that does not match any "
                                        "Receipt Code in Head.")
    assert named("Checks Item Order (col_10) and col_1", "Lines", al) == "Checks Item Order (col_10) and Line No"
    assert named("col_99 stays", "Lines", al) == "col_99 stays" and named(None, "Lines", al) == ""
