"""T14 automated tests: the test suite's result per component, from a pytest JUnit XML file.

Usage (from backend/):
    python -m app.eval.automated_tests [--xml ../evaluation/testing/pytest_<date>.xml] [--out ../evaluation/results]

Without --xml the newest evaluation/testing/pytest_*.xml is used. To make one:
    python -m pytest -v --junitxml=../evaluation/testing/pytest_<date>.xml > ../evaluation/testing/pytest_<date>.txt

Writes automated_tests.csv (one row per test file: component, what it checks,
tests, passed, failed, skipped, seconds) and automated_tests.md (the same,
plus totals per component). Skipped tests are the ones that need something
absent on the machine (for example the study workspace); they are listed, not hidden.
"""
from __future__ import annotations

import argparse
import csv
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TESTING = ROOT / "evaluation" / "testing"
RESULTS = ROOT / "evaluation" / "results"

# test file -> (component, what it checks); the component order is the reading order of the table
COMPONENTS = [
    "Layer 0 · loading and profiling", "Privacy and access", "Layer 1 · rules",
    "Layer 2 · AI auditor and schema docs", "Layer 4 · monitoring and scoring", "Persistence and hosting",
    "Layer 3 · dashboard and people-facing output", "Usability study", "Evaluation code", "Configuration",
]
FILES = {
    "test_loader": ("Layer 0 · loading and profiling", "file reading, header detection, NULL handling"),
    "test_load_table": ("Layer 0 · loading and profiling", "CSV / Excel loading edge cases"),
    "test_types": ("Layer 0 · loading and profiling", "column type inference"),
    "test_stats": ("Layer 0 · loading and profiling", "column statistics"),
    "test_keys": ("Layer 0 · loading and profiling", "primary-key candidates"),
    "test_relationships": ("Layer 0 · loading and profiling", "link discovery between tables"),
    "test_profile": ("Layer 0 · loading and profiling", "profiling a folder of exports"),
    "test_sampling": ("Layer 0 · loading and profiling", "stratified, seeded sample for the AI"),
    "test_pseudonymize": ("Privacy and access", "masking of personal data in samples and profiles"),
    "test_auth": ("Privacy and access", "sign-in, password hashing, roles"),
    "test_rule_completeness": ("Layer 1 · rules", "completeness check"),
    "test_rule_validity": ("Layer 1 · rules", "validity check (values below zero)"),
    "test_rule_uniqueness": ("Layer 1 · rules", "duplicate rows and near-key duplicates"),
    "test_rule_timeliness": ("Layer 1 · rules", "future and stale dates"),
    "test_rule_ordering": ("Layer 1 · rules", "mined ordering consistency (A <= B)"),
    "test_rule_arithmetic": ("Layer 1 · rules", "mined arithmetic consistency (A x B = C)"),
    "test_rule_cross_table": ("Layer 1 · rules", "broken links between tables"),
    "test_rule_descriptions": ("Layer 1 · rules", "plain-English rule descriptions"),
    "test_engine": ("Layer 1 · rules", "the rules engine end to end"),
    "test_auditor": ("Layer 2 · AI auditor and schema docs", "AI findings parsing, strict JSON, retry"),
    "test_run_audit": ("Layer 2 · AI auditor and schema docs", "AI audit of a folder: sample, mask, audit"),
    "test_feedback": ("Layer 2 · AI auditor and schema docs", "past decisions and notes in the prompt"),
    "test_schema_docs": ("Layer 2 · AI auditor and schema docs", "AI-drafted schema documentation"),
    "test_recurrence": ("Layer 4 · monitoring and scoring", "recurring issues, trends, projections"),
    "test_score": ("Layer 4 · monitoring and scoring", "severity-weighted score"),
    "test_score_persistence": ("Layer 4 · monitoring and scoring", "stored scores and re-scoring"),
    "test_score_weights": ("Layer 4 · monitoring and scoring", "dimension weights from config"),
    "test_persist": ("Persistence and hosting", "full audit saved to the database"),
    "test_repository": ("Persistence and hosting", "database reads and writes"),
    "test_repository_relationships": ("Persistence and hosting", "stored links and confirmation"),
    "test_repository_triage": ("Persistence and hosting", "triage decisions saved"),
    "test_sync_cloud": ("Persistence and hosting", "masked copy to the hosted database"),
    "test_workspaces": ("Persistence and hosting", "companies (workspaces) and local-only marks"),
    "test_dashboard": ("Layer 3 · dashboard and people-facing output", "every page renders; sign-in; roles"),
    "test_grouping": ("Layer 3 · dashboard and people-facing output", "what readers see first, action groups"),
    "test_wording": ("Layer 3 · dashboard and people-facing output", "plain-English display wording"),
    "test_aliases": ("Layer 3 · dashboard and people-facing output", "readable column names"),
    "test_export": ("Layer 3 · dashboard and people-facing output", "CSV and Excel export"),
    "test_reports": ("Layer 3 · dashboard and people-facing output", "audit report, Word and PDF"),
    "test_audit_trail": ("Layer 3 · dashboard and people-facing output", "issue history and decision log"),
    "test_chat": ("Layer 3 · dashboard and people-facing output", "chat context, masking, backup models"),
    "test_chat_threads": ("Layer 3 · dashboard and people-facing output", "saved chat threads"),
    "test_api": ("Layer 3 · dashboard and people-facing output", "HTTP API"),
    "test_study": ("Usability study", "codes, tasks, verified completion"),
    "test_study_ui": ("Usability study", "participant screens and the study admin page"),
    "test_study_results": ("Usability study", "SUS, task and integrity results"),
    "test_baseline": ("Evaluation code", "clean baseline for injection"),
    "test_injection": ("Evaluation code", "seeded error injection"),
    "test_metrics": ("Evaluation code", "precision, recall, F1, complementarity"),
    "test_run_eval": ("Evaluation code", "injection evaluation end to end"),
    "test_complementarity": ("Evaluation code", "rules-only / AI-only / both"),
    "test_cross_client": ("Evaluation code", "cross-company reproducibility"),
    "test_figures": ("Evaluation code", "dissertation figures"),
    "test_triage_metrics": ("Evaluation code", "triage-derived metrics"),
    "test_live_scope": ("Evaluation code", "which audits count in live metrics"),
    "test_reviewer_pack": ("Evaluation code", "second-reviewer sheets"),
    "test_config": ("Configuration", "settings from .env / secrets"),
}
FIELDS = ["component", "test_file", "checks", "tests", "passed", "failed", "skipped", "seconds"]


def summarise(xml_path: Path) -> list[dict]:
    rows: dict[str, dict] = {}
    for case in ET.parse(xml_path).getroot().iter("testcase"):
        stem = case.get("classname", "").split(".")[-1]   # tests.test_score -> test_score
        component, checks = FILES.get(stem, ("Other", ""))
        r = rows.setdefault(stem, {"component": component, "test_file": f"{stem}.py", "checks": checks,
                                   "tests": 0, "passed": 0, "failed": 0, "skipped": 0, "seconds": 0.0})
        r["tests"] += 1
        r["seconds"] += float(case.get("time") or 0)
        if case.find("failure") is not None or case.find("error") is not None:
            r["failed"] += 1
        elif case.find("skipped") is not None:
            r["skipped"] += 1
        else:
            r["passed"] += 1
    order = {c: i for i, c in enumerate(COMPONENTS)}
    out = sorted(rows.values(), key=lambda r: (order.get(r["component"], len(order)), r["test_file"]))
    for r in out:
        r["seconds"] = round(r["seconds"], 1)
    return out


def write(rows: list[dict], xml_path: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "automated_tests.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    per = defaultdict(lambda: {"files": 0, "tests": 0, "passed": 0, "failed": 0, "skipped": 0})
    for r in rows:
        p = per[r["component"]]
        p["files"] += 1
        for k in ("tests", "passed", "failed", "skipped"):
            p[k] += r[k]
    total = {k: sum(r[k] for r in rows) for k in ("tests", "passed", "failed", "skipped")}
    lines = [f"# T14 Automated tests ({xml_path.stem.removeprefix('pytest_')})", "",
             f"Source: `{xml_path.name}` (pytest JUnit XML; the full log is the .txt beside it).", "",
             f"**{total['tests']} tests in {len(rows)} files: {total['passed']} passed, {total['failed']} failed, "
             f"{total['skipped']} skipped.**", "",
             "| Component | Files | Tests | Passed | Failed | Skipped |", "|---|---|---|---|---|---|"]
    lines += [f"| {c} | {p['files']} | {p['tests']} | {p['passed']} | {p['failed']} | {p['skipped']} |"
              for c, p in per.items()]
    lines += ["", "| Test file | Component | What it checks | Tests | Passed | Failed | Skipped |",
              "|---|---|---|---|---|---|---|"]
    lines += [f"| {r['test_file']} | {r['component']} | {r['checks']} | {r['tests']} | {r['passed']} | "
              f"{r['failed']} | {r['skipped']} |" for r in rows]
    (out / "automated_tests.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xml", type=Path)
    ap.add_argument("--out", type=Path, default=RESULTS)
    args = ap.parse_args(argv)
    xml_path = args.xml or max(TESTING.glob("pytest_*.xml"), key=lambda p: p.stat().st_mtime)
    rows = summarise(xml_path)
    write(rows, xml_path, args.out)
    t = {k: sum(r[k] for r in rows) for k in ("tests", "passed", "failed", "skipped")}
    print(f"{xml_path.name}: {t['tests']} tests, {t['passed']} passed, {t['failed']} failed, "
          f"{t['skipped']} skipped -> {args.out / 'automated_tests.md'}")


if __name__ == "__main__":
    main()
