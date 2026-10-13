"""Presentation of an audit's issues for people (v2, after the usability study).

Detection is unchanged and every issue is still stored, scored and
counted in the evaluation; this only decides what a reader sees first:

  - unused fields: a completeness issue on a column that is empty in
    (almost) every row describes a field the business does not use. These
    are listed once per table instead of as separate issues;
  - groups: rule issues of the same check in the same table (e.g. ten
    "mostly empty" columns) become one card that opens to its members;
  - needs attention: high-severity issues that are not unused fields
    (medium ones if there are no high ones).
"""
from __future__ import annotations

from functools import lru_cache

UNUSED_NULL_PCT = 99.9        # empty in at least this share of rows -> an unused field
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

GROUP_TITLES = {
    "completeness_null_pct": "{n} columns are often left empty",
    "cross_table_fk_orphan": "{n} columns have values that don't match the linked table",
    "consistency_ordering_violation": "{n} pairs of values are in an unexpected order",
    "consistency_arithmetic_violation": "{n} calculations don't add up",
    "uniqueness_near_key_duplicate": "{n} columns have repeated values that should be unique",
    "timeliness_stale_column": "{n} date columns look out of date",
    "timeliness_future_date": "{n} date columns have dates in the future",
    "validity_negative_value": "{n} columns have values below zero",
}


def unused_rule_ids(rules: list[dict]) -> set[int]:
    """Ids of completeness rules whose column is empty in (almost) every row."""
    out = set()
    for r in rules:
        if r.get("template") == "completeness_null_pct":
            pct = (r.get("evidence") or {}).get("null_pct")
            if pct is not None and float(pct) >= UNUSED_NULL_PCT:
                out.add(r["id"])
    return out


def is_unused(issue: dict, unused: set[int]) -> bool:
    return issue.get("source") == "rule" and issue.get("rule_ref") in unused


def unused_fields(issues: list[dict], unused: set[int]) -> dict[str, list[str]]:
    """{table: [columns]} for the unused-field issues, in issue order."""
    out: dict[str, list[str]] = {}
    for i in issues:
        if is_unused(i, unused):
            out.setdefault(i["table_name"], []).append(i["column_name"] or "")
    return out


def needs_attention(issues: list[dict], unused: set[int]) -> list[dict]:
    useful = [i for i in issues if not is_unused(i, unused)]
    high = [i for i in useful if i["severity"] == "high"]
    return high or [i for i in useful if i["severity"] == "medium"]


def group(issues: list[dict]) -> list[dict]:
    """Ordered list of {"kind": "issue", "issue": ...} and
    {"kind": "group", "title", "table", "severity", "rows", "issues"} items.
    Only rule issues of the same check in the same table group (2+ members);
    AI findings are always shown on their own."""
    buckets: dict[tuple, list[dict]] = {}
    for i in issues:
        if i.get("source") == "rule" and i.get("rule_id"):
            buckets.setdefault((i["table_name"], i["rule_id"]), []).append(i)
    items, placed = [], set()
    for i in issues:
        key = (i["table_name"], i.get("rule_id"))
        members = buckets.get(key) if i.get("source") == "rule" else None
        if members and len(members) > 1:
            if key in placed:
                continue
            placed.add(key)
            title = GROUP_TITLES.get(i["rule_id"], "{n} similar issues").format(n=len(members))
            items.append({
                "kind": "group", "key": f"{i['table_name']}:{i['rule_id']}", "title": title,
                "table": i["table_name"], "issues": members,
                "severity": min((m["severity"] for m in members), key=lambda s: SEVERITY_RANK.get(s, 3)),
                "rows": sum(m.get("affected_row_count") or 0 for m in members),
            })
        else:
            items.append({"kind": "issue", "issue": i})
    return items


# --- importance order and plain issue types (v2) ---------------------------------

TYPE_LABELS = {
    "completeness": "Missing values", "validity": "Wrong values", "accuracy": "Wrong values",
    "consistency": "Inconsistent values", "uniqueness": "Duplicates", "cross_table": "Broken links",
    "timeliness": "Date problems",
}


def type_label(dimension: str) -> str:
    return TYPE_LABELS.get(dimension, dimension.replace("_", " ").capitalize())


def priority(issue: dict) -> tuple:
    """Sort key, most important first: severity, then how much that kind of
    problem weighs in the DQ score (consistency / cross-table highest), then
    the number of rows affected."""
    from app.scoring.score import DIMENSION_ALIASES, SEVERITY_POINTS

    dim = DIMENSION_ALIASES.get(issue["dimension"], issue["dimension"])
    return (SEVERITY_POINTS.get(issue["severity"], 1), _weights().get(dim, 0.1),
            issue.get("affected_row_count") or 0)


@lru_cache(maxsize=1)
def _weights() -> dict[str, float]:
    from app.scoring.score import load_weights
    return load_weights()


def by_priority(items: list[dict]) -> list[dict]:
    """Most important first; a group counts as its most important member."""
    def score(item):
        return priority(item["issue"]) if item["kind"] == "issue" else max(priority(i) for i in item["issues"])
    return sorted(items, key=score, reverse=True)


# --- action categories (what to do first) ------------------------------------------

ACTIONS = {
    "fix_now": "Fix now",
    "check": "Check with the business",
    "can_wait": "Can wait",
    "not_used": "Not in use",
}
FIX_NOW_DIMENSIONS = {"cross_table", "consistency", "uniqueness", "validity", "accuracy"}


def action_category(issue: dict, unused: set[int]) -> str:
    """Fix now = high-severity rule findings on links, consistency, duplicates or
    wrong values; Check = AI findings (need a person's judgement of the business
    context); Not in use = fields never filled in; everything else can wait."""
    if is_unused(issue, unused):
        return "not_used"
    if issue.get("source") == "llm":
        return "check"
    if issue["severity"] == "high" and issue["dimension"] in FIX_NOW_DIMENSIONS:
        return "fix_now"
    return "can_wait"
