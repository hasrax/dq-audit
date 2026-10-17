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

import re
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


def _null_pct_rule_ids(rules: list[dict], low: float, high: float = 101.0) -> set[int]:
    out = set()
    for r in rules:
        if r.get("template") == "completeness_null_pct":
            pct = (r.get("evidence") or {}).get("null_pct")
            if pct is not None and low <= float(pct) < high:
                out.add(r["id"])
    return out


def unused_rule_ids(rules: list[dict]) -> set[int]:
    """Ids of completeness rules whose column is empty in (almost) every row."""
    return _null_pct_rule_ids(rules, UNUSED_NULL_PCT)


MOSTLY_BLANK_NULL_PCT = 50.0   # blank in at least half the rows (but still used)


def mostly_blank_rule_ids(rules: list[dict]) -> set[int]:
    """Ids of completeness rules whose column is blank in most rows but not all:
    whether that field matters is a business question."""
    return _null_pct_rule_ids(rules, MOSTLY_BLANK_NULL_PCT, UNUSED_NULL_PCT)


ORDERING_RULE = "consistency_ordering_violation"


def is_value_order(issue: dict) -> bool:
    """'Unusual value order' findings (mined orderings between two columns). Often
    coincidental on named columns, so the UI folds them into one section."""
    return issue.get("source") == "rule" and issue.get("rule_id") == ORDERING_RULE


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


# one-line plain-English explanations (tooltips) for each kind of problem and each severity
TYPE_HELP = {
    "completeness": "Fields that are left empty but should be filled in.",
    "validity": "Values that cannot be right, such as a quantity below zero.",
    "accuracy": "Values that cannot be right, such as a quantity below zero.",
    "consistency": "Values in the same row that contradict each other, such as a total that does not add up.",
    "uniqueness": "A record or a number that should appear once but appears more than once.",
    "cross_table": "A row that points to a record that does not exist in the linked table.",
    "timeliness": "Dates in the future, or a date field that stopped being filled in.",
}
SEVERITY_HELP = {
    "high": "High: can lead to wrong totals, payments or stock. Check these first.",
    "medium": "Medium: worth fixing, but less urgent.",
    "low": "Low: a minor problem. Fix it when convenient.",
}


def type_help(dimension: str) -> str:
    return TYPE_HELP.get(dimension, "")


# --- Relationships page: display hints for discovered links (detection unchanged) ---------

_COL_N = re.compile(r"^col_\d+$")
_SMALL_NUMBER = [   # (pattern on the source column's name, why it can match an ID by chance)
    (re.compile(r"\b(qty|quantity|quantities)\b", re.I), "quantities are small numbers"),
    (re.compile(r"\b(no\.? of|number of|count|items)\b", re.I), "item counts are small numbers"),
    (re.compile(r"\b(order|line)\b", re.I), "line order is 1, 2, 3…"),
]
_ID_ENDING = re.compile(r"\b(no|number|code|id|ref)\.?$", re.I)   # "Order No" is an identifier, not a count
LINK_GROUPS = {"same": "Same name on both sides", "careful": "Check carefully", "different": "Different names"}


def link_group(child_name: str, parent_name: str) -> tuple[str, str]:
    """('same' | 'careful' | 'different', reason) for a discovered link, from the column
    names people see. 'careful': a count, quantity or line order "matched" an ID column."""
    for rx, why in _SMALL_NUMBER:
        if rx.search(child_name) and not _ID_ENDING.search(child_name):
            return "careful", why
    known = not _COL_N.match(child_name) and not _COL_N.match(parent_name)
    if known and re.sub(r"[\W_]+", "", child_name).lower() == re.sub(r"[\W_]+", "", parent_name).lower():
        return "same", "the same name on both sides"
    return "different", ""


def rule_uses(rules: list[dict]) -> dict[tuple, int]:
    """How many cross-table rules use each link: {(child_table, child_col, parent_table, parent_col): n}."""
    out: dict[tuple, int] = {}
    for r in rules:
        if r.get("template") == "cross_table_fk_orphan":
            p = r.get("parameters") or {}
            key = (r["table_name"], r["column_name"], p.get("parent_table"), p.get("parent_column"))
            out[key] = out.get(key, 0) + 1
    return out


def link_disagrees(evidence: dict) -> bool:
    """A cross-table rule whose two numbers disagree: most different values match
    the linked key (>= 90%) but most rows do not (>= 50% unmatched)."""
    try:
        return (float(evidence.get("overlap_pct", 0)) >= 90
                and int(evidence.get("orphan_count", 0)) >= 0.5 * int(evidence.get("row_count") or 0) > 0)
    except (TypeError, ValueError):
        return False


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


def action_category(issue: dict, unused: set[int], mostly_blank: set[int] = frozenset()) -> str:
    """Fix now = high-severity rule findings on links, consistency, duplicates or
    wrong values; Check = AI findings and mostly-blank fields (need a person's
    judgement of the business context); Not in use = fields never filled in;
    everything else can wait."""
    if is_unused(issue, unused):
        return "not_used"
    if issue.get("source") == "llm" or (issue.get("source") == "rule" and issue.get("rule_ref") in mostly_blank):
        return "check"
    if issue["severity"] == "high" and issue["dimension"] in FIX_NOW_DIMENSIONS:
        return "fix_now"
    return "can_wait"
