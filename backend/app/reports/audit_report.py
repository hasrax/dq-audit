"""The standard audit report, built from the issue repository as Markdown.

Plain business language for managers: a summary, scores by dimension,
change since the previous audit, the issues that need attention (with
what was found and what to do), and how the issues were detected.
Statistics and findings only -- no raw rows -- so the report never
carries personal data.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from app.aliases import client_label, display_columns
from app.db import repository
from app.eval.triage_metrics import compute_triage_metrics
from app.monitoring import recurrence
from app.schema_docs.store import display_aliases

TOP_ISSUES = 15
DIMENSIONS = [("Completeness", "completeness"), ("Validity", "validity"), ("Consistency", "consistency"),
              ("Uniqueness", "uniqueness"), ("Timeliness", "timeliness"), ("Cross-table", "cross_table")]
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _status(score: float) -> str:
    return "good" if score >= 80 else ("needs attention" if score >= 50 else "poor")


def _where(issue: dict, aliases: dict) -> str:
    col = display_columns(aliases, issue["table_name"], issue["column_name"])
    return issue["table_name"] + (f" · {col}" if col else "")


def build_markdown(conn, upload: dict) -> str:
    uid, client = upload["id"], upload["source_client"]
    aliases = display_aliases(conn, client)
    issues = repository.fetch_issues(conn, uid)
    score = repository.score_upload(conn, uid)
    tm = compute_triage_metrics(issues)
    summary = recurrence.recurrence_summary(conn, uid)
    rules = repository.fetch_rules(conn, uid)
    sev = Counter(i["severity"] for i in issues)
    src = Counter(i["source"] for i in issues)
    as_of = upload.get("data_as_of") or upload["upload_date"]
    overall = float(score["overall_score"])

    md = [
        "# Data quality audit report",
        f"**Organisation:** {client_label(client)}  ·  **Audit:** #{uid}  ·  **Data as of:** {as_of:%d %B %Y}  ·  "
        f"**Rows audited:** {upload['row_count']:,}  ·  **Prepared:** {datetime.now():%d %B %Y}",
        "## Summary",
        f"The overall data quality score is **{overall:.0f} out of 100**, which is **{_status(overall)}**. "
        f"The audit found **{len(issues)} issues**: {sev.get('high', 0)} high, {sev.get('medium', 0)} medium "
        f"and {sev.get('low', 0)} low severity. {src.get('rule', 0)} were found by automatic rules and "
        f"{src.get('llm', 0)} by the AI auditor.",
        f"Reviewers have checked **{tm['reviewed']} of {len(issues)}** issues so far "
        f"({tm['by_status']['accepted']} confirmed, {tm['by_status']['rejected']} rejected as not a problem, "
        f"{tm['by_status']['considered']} unsure).",
    ]
    if summary["has_previous"]:
        md.append(
            f"Compared with the previous audit: **{summary['new']} new** issues, {summary['recurring']} recurring "
            f"({summary['worsening']} getting worse, {summary['improving']} improving), and "
            f"{summary['not_seen']} no longer seen."
        )

    by_dim = Counter(i["dimension"] if i["dimension"] != "accuracy" else "validity" for i in issues)
    table = ["| Dimension | Score (0-100) | Issues |", "|---|---|---|"]
    table += [f"| {label} | {float(score[f'{key}_score']):.0f} | {by_dim.get(key, 0)} |" for label, key in DIMENSIONS]
    md += ["## Scores by dimension", "\n".join(table)]   # table rows must stay on consecutive lines

    open_issues = sorted(
        (i for i in issues if i["status"] != "rejected"),
        key=lambda i: (SEVERITY_ORDER.get(i["severity"], 3), -(i.get("affected_row_count") or 0)),
    )[:TOP_ISSUES]
    md.append(f"## Issues that need attention (top {len(open_issues)})")
    if not open_issues:
        md.append("No open issues.")
    for n, i in enumerate(open_issues, start=1):
        rows = i.get("affected_row_count")
        facts = [f"{i['severity'].title()} severity", i["dimension"].replace("_", " "),
                 "found by rules" if i["source"] == "rule" else "found by AI"]
        if rows is not None:
            facts.append(f"{rows:,} rows")
        if i.get("trend_status") and i["trend_status"] != "new":
            facts.append(i["trend_status"])
        md += [
            f"### {n}. {_where(i, aliases)}",
            f"**{' · '.join(facts)}**",
            f"**What was found:** {i.get('description') or ''}",
        ]
        if i.get("recommendation"):
            md.append(f"**What to do:** {i['recommendation']}")
        if i.get("user_note"):
            md.append(f"**Reviewer note:** {i['user_note']}")

    passed = sum(1 for r in rules if not r["violation_count"])
    md += [
        "## How these issues were found",
        f"- **Automatic rules:** {len(rules)} checks were created from the data itself (no hand-written rules); "
        f"{passed} passed and {len(rules) - passed} found problems.",
        "- **AI auditor:** looked at table statistics and a small, privacy-masked sample of rows for problems "
        "rules cannot express. AI findings are reviewed by people before they are trusted.",
        "- **Human review:** each issue can be confirmed, rejected or marked unsure; those decisions measure "
        "the tool's own accuracy.",
        "## Note on privacy",
        "This report contains statistics and findings only. Names, phone numbers and other personal details "
        "are never included.",
    ]
    return "\n\n".join(md)
