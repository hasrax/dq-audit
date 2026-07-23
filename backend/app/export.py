"""CSV export of audit results for the dissertation appendix / records.

Pure string builders (no I/O) so they are testable and reusable by the
dashboard's download buttons and any CLI. Column names are aliased for
readability where an alias map is supplied.
"""
from __future__ import annotations

import csv
import io

from app.aliases import display_columns

ISSUE_FIELDS = [
    "table", "column", "dimension", "source", "error_type", "severity",
    "status", "fixable", "affected_rows", "description", "recommendation", "rule_id",
]
SCORE_FIELDS = [
    "overall_score", "completeness_score", "validity_score", "consistency_score",
    "uniqueness_score", "timeliness_score", "cross_table_score",
]


def issues_to_csv(issues: list[dict], aliases: dict | None = None) -> str:
    aliases = aliases or {}
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=ISSUE_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for issue in issues:
        ref = issue.get("row_reference") or {}
        writer.writerow({
            "table": issue["table_name"],
            "column": display_columns(aliases, issue["table_name"], issue.get("column_name")) or "",
            "dimension": issue["dimension"],
            "source": issue["source"],
            "error_type": issue["error_type"],
            "severity": issue["severity"],
            "status": issue.get("status"),
            "fixable": issue.get("fixable"),
            "affected_rows": ref.get("count"),
            "description": issue.get("description"),
            "recommendation": issue.get("recommendation"),
            "rule_id": issue.get("rule_id"),
        })
    return buf.getvalue()


def score_to_csv(score: dict) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["metric", "value"])
    for field in SCORE_FIELDS:
        writer.writerow([field, score.get(field)])
    return buf.getvalue()
