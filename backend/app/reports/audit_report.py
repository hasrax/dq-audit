"""The standard audit report, built from the issue repository as Markdown.

A formal, printable report for managers (v2: standard sections, rendered
in black and white): title block, 1 executive summary, 2 scope, 3 method,
4 results, 5 key findings, 6 recommendations, appendices (definitions,
fields never filled in, privacy). Findings use the same grouping as the
Triage page (app.reports.grouping). Statistics and findings only -- no
raw rows -- so the report never carries personal data.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from app.aliases import client_label, display_columns
from app.db import repository
from app.eval.triage_metrics import compute_triage_metrics
from app.monitoring import recurrence
from app.reports import grouping
from app.schema_docs.store import display_aliases
from app.workspaces import refresh_names

TOP_FINDINGS = 15
DIMENSIONS = [("Completeness", "completeness"), ("Validity", "validity"), ("Consistency", "consistency"),
              ("Uniqueness", "uniqueness"), ("Timeliness", "timeliness"), ("Cross-table", "cross_table")]
DEFINITIONS = [
    ("Completeness", "a value that should be there is missing."),
    ("Validity", "a value is not allowed or not possible (for example a negative quantity)."),
    ("Consistency", "values in the same record contradict each other (for example a total that is not "
                    "price x quantity)."),
    ("Uniqueness", "something that should appear once appears more than once."),
    ("Timeliness", "a date is in the future or far out of date."),
    ("Cross-table", "a value does not match the record it refers to in another table."),
]


def _status(score: float) -> str:
    return "good" if score >= 80 else ("needs attention" if score >= 50 else "poor")


def _where(issue: dict, aliases: dict) -> str:
    col = display_columns(aliases, issue["table_name"], issue["column_name"])
    return issue["table_name"] + (f" - {col}" if col else "")


def build_markdown(conn, upload: dict) -> str:
    uid, client = upload["id"], upload["source_client"]
    refresh_names(conn)               # company display names, also outside the app
    aliases = display_aliases(conn, client)
    issues = repository.fetch_issues(conn, uid)
    score = repository.score_upload(conn, uid)
    tm = compute_triage_metrics(issues)
    summary = recurrence.recurrence_summary(conn, uid)
    rules = repository.fetch_rules(conn, uid)
    tables = [r[0] for r in conn.execute(
        "SELECT DISTINCT table_name FROM profiles WHERE upload_id = %s ORDER BY 1", (uid,)).fetchall()]
    unused = grouping.unused_rule_ids(rules)
    fields = grouping.unused_fields(issues, unused)
    open_issues = [i for i in issues if i["status"] != "rejected"]
    attention = grouping.needs_attention(open_issues, unused)
    sev = Counter(i["severity"] for i in issues)
    as_of = upload.get("data_as_of") or upload["upload_date"]
    overall = float(score["overall_score"])
    n_fields = sum(len(c) for c in fields.values())

    md = [
        "# Data quality audit report",
        f"**Organisation:** {client_label(client)}",
        f"**Audit reference:** #{uid}  ·  **Data as of:** {as_of:%d %B %Y}  ·  **Rows audited:** "
        f"{upload['row_count']:,}  ·  **Tables audited:** {len(tables)}",
        f"**Prepared:** {datetime.now():%d %B %Y}  ·  **Prepared with:** ClarityDQ (automated audit, "
        "reviewed by people)",

        "## 1. Executive summary",
        f"The overall data quality score is **{overall:.0f} out of 100** ({_status(overall)}). The audit "
        f"recorded **{len(issues)} issues** ({sev.get('high', 0)} high, {sev.get('medium', 0)} medium and "
        f"{sev.get('low', 0)} low severity). **{len(attention)} need attention first** (Section 5). "
        f"{n_fields} fields are never filled in and are listed once in Appendix B rather than as findings.",
        f"Reviewers have checked {tm['reviewed']} of {len(issues)} issues: {tm['by_status']['accepted']} "
        f"confirmed, {tm['by_status']['rejected']} rejected as not a problem and "
        f"{tm['by_status']['considered']} unsure.",
    ]
    if summary["has_previous"]:
        md.append(f"Since the previous audit: {summary['new']} new issues, {summary['recurring']} recurring "
                  f"({summary['worsening']} getting worse, {summary['improving']} improving) and "
                  f"{summary['not_seen']} no longer seen.")

    md += [
        "## 2. Scope",
        f"- **Tables audited ({len(tables)}):** {', '.join(tables)}.",
        f"- **Rows audited:** {upload['row_count']:,}, data as of {as_of:%d %B %Y}.",
        "- **Not in scope:** fixing the data; this report identifies and explains problems.",

        "## 3. Method",
        f"- **Automatic rules:** {len(rules)} checks were generated from the data itself (no hand-written "
        f"rules); {sum(1 for r in rules if r['violation_count'])} found problems.",
        "- **AI auditor:** reviewed each table's statistics and a small privacy-masked sample for business "
        "problems that rules cannot express.",
        "- **Human review:** people confirm, reject or mark each issue unsure; rejected issues do not count "
        "against the score.",
        "- **Score:** 0-100, weighted by dimension and severity (definitions in Appendix A).",

        "## 4. Results",
        "### 4.1 Score by dimension",
    ]
    by_dim = Counter(i["dimension"] if i["dimension"] != "accuracy" else "validity" for i in issues)
    rows = ["| Dimension | Score (0-100) | Status | Issues |", "|---|---|---|---|"]
    for label, key in DIMENSIONS:
        s = float(score[f"{key}_score"])
        rows.append(f"| {label} | {s:.0f} | {_status(s)} | {by_dim.get(key, 0)} |")
    md.append("\n".join(rows))
    md.append("### 4.2 Issues by severity and how they were found")
    rows = ["| Severity | Found by rules | Found by AI | Total |", "|---|---|---|---|"]
    for s in ("high", "medium", "low"):
        r = sum(1 for i in issues if i["severity"] == s and i["source"] == "rule")
        a = sum(1 for i in issues if i["severity"] == s and i["source"] == "llm")
        rows.append(f"| {s.title()} | {r} | {a} | {r + a} |")
    md.append("\n".join(rows))

    items = grouping.by_priority(grouping.group(attention))[:TOP_FINDINGS]
    md.append(f"## 5. Key findings (the first {len(items)} to address)")
    if not items:
        md.append("No open findings need attention.")
    for n, item in enumerate(items, start=1):
        if item["kind"] == "group":
            cols = [(display_columns(aliases, item["table"], i["column_name"]) or "whole row").replace(", ", " & ")
                    for i in item["issues"]]
            md += [f"### 5.{n} {item['table']} - {item['title']}",
                   f"**{item['severity'].title()} severity - {len(item['issues'])} issues - found by rules**",
                   f"**Columns:** {', '.join(cols)}.",
                   f"**What to do:** {item['issues'][0].get('recommendation') or ''}"]
            continue
        i = item["issue"]
        facts = [f"{i['severity'].title()} severity", i["dimension"].replace("_", " "),
                 "found by rules" if i["source"] == "rule" else "found by AI"]
        if i.get("affected_row_count"):
            facts.append(f"{i['affected_row_count']:,} rows")
        md += [f"### 5.{n} {_where(i, aliases)}", f"**{' - '.join(facts)}**",
               f"**What was found:** {i.get('description') or ''}"]
        if i.get("recommendation"):
            md.append(f"**What to do:** {i['recommendation']}")
        if i.get("user_note"):
            md.append(f"**Reviewer note:** {i['user_note']}")

    md += [
        "## 6. Recommendations",
        "1. Address the findings in Section 5 in the order shown, starting with high severity.",
        "2. Confirm with the system's users whether the fields in Appendix B are still needed; hide or retire "
        "them if not.",
        f"3. Review the remaining {len(open_issues) - len(attention)} lower-priority issues on the Triage page, "
        "and mark checks that do not apply to the business as not applicable.",
        "4. Run the audit again after corrections to confirm the score improves.",

        "## Appendix A. Definitions",
        "\n".join(f"- **{name}:** {text}" for name, text in DEFINITIONS),
        "- **Severity:** high = likely to cause wrong figures or decisions; medium = should be fixed; "
        "low = minor or cosmetic.",
        "## Appendix B. Fields never filled in",
    ]
    if fields:
        rows = ["| Table | Fields |", "|---|---|"]
        rows += [f"| {t} | {', '.join(display_columns(aliases, t, c) or c for c in cols)} |"
                 for t, cols in fields.items()]
        md.append("\n".join(rows))
    else:
        md.append("None.")
    md += [
        "## Appendix C. Privacy",
        "This report contains statistics and findings only. Names, phone numbers and other personal details "
        "are never included.",
    ]
    return "\n\n".join(md)
