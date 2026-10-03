"""Plain-language rule descriptions (deterministic templates, no LLM).

Every rule instance gets two sentences:
  what -- what the rule checks, phrased as a standing rule;
  why  -- why the engine created it, citing the profile evidence.

Column references are rendered through the display-name overlay
(config aliases + verified schema docs) as "Bill Date (col_3)", so a
non-technical reviewer can read them while the positional name stays
visible for traceability.
"""
from __future__ import annotations

from collections.abc import Callable

CATEGORY_LABEL = {
    "completeness": "Completeness",
    "validity": "Validity",
    "consistency": "Consistency",
    "uniqueness": "Uniqueness",
    "timeliness": "Timeliness",
    "cross_table": "Cross-table",
}


def column_namer(aliases: dict[str, dict[str, str]]) -> Callable[[str, str], str]:
    def name(table: str, col: str) -> str:
        alias = aliases.get(table, {}).get(col)
        return f"{alias} ({col})" if alias else col
    return name


def _pct(value) -> str:
    return f"{float(value):g}%"


def describe_rule(rule: dict, aliases: dict | None = None) -> tuple[str, str]:
    """Return (what_text, why_text) for one rule instance."""
    n = column_namer(aliases or {})
    t, table = rule["template"], rule["table_name"]
    p, e = rule["parameters"], rule["evidence"]
    cols = (rule["column_name"] or "").split(",")
    col = n(table, cols[0]) if rule["column_name"] else None

    if t == "completeness_null_pct":
        what = f"Checks that {col} in {table} is filled in: no more than {_pct(p['max_null_pct'])} of rows may be blank."
        why = (f"Applied to every column. Profiling found {_pct(e['null_pct'])} blank "
               f"({e['null_count']:,} of {e['row_count']:,} rows).")
        if p.get("fk_role"):
            why += " This column links to another table, so blanks are treated as more serious."
        return what, why

    if t == "validity_negative_value":
        what = f"Checks that {col} in {table} never holds a negative number."
        why = f"Applied because {col} holds {'whole numbers' if e['type'] == 'int' else 'amounts'}; the smallest value found was {e['min']}."
        if e.get("negative_count"):
            why += f" {e['negative_count']:,} rows are below zero."
        return what, why

    if t == "uniqueness_full_row_duplicate":
        what = f"Checks that no row in {table} is an exact copy of another row."
        why = (f"Applied to every table, because a repeated row usually means a double entry "
               f"or a repeated import. {table} has {e['row_count']:,} rows.")
        return what, why

    if t == "uniqueness_near_key_duplicate":
        what = f"Checks that every value in {col} is unique within {table}, like an ID number."
        why = (f"Created because {_pct(e['distinct_pct'])} of its {e['non_null_count']:,} values are "
               f"different from each other, so it behaves like an identifier.")
        return what, why

    if t == "timeliness_future_date":
        what = f"Checks that no date in {col} in {table} is more than {p['max_days_ahead']} days in the future."
        why = f"Applied because {col} holds dates; the latest one is {e['max_date']} (audit date {e['reference_date']})."
        return what, why

    if t == "timeliness_stale_column":
        what = f"Checks that {col} in {table} has received new dates within the last {p['max_age_years']} years."
        why = f"Applied because {col} holds dates; the latest one is {e['max_date']} (audit date {e['reference_date']})."
        return what, why

    if t == "consistency_ordering_violation":
        lo, hi = n(table, cols[0]), n(table, cols[1])
        relation = "is on or before" if p.get("is_date") else "is not greater than"
        what = f"Checks that {lo} {relation} {hi} in every {table} row."
        why = (f"Created because this held in {_pct(e['conformance_pct'])} of {e['valid_pairs']:,} rows, "
               f"which points to a business rule rather than chance.")
        return what, why

    if t == "consistency_arithmetic_violation":
        a, b, c = (n(table, x) for x in cols)
        what = f"Checks that {c} equals {a} × {b} in every {table} row (allowing for rounding)."
        why = (f"Created because this calculation held in {_pct(e['conformance_pct'])} of "
               f"{e['informative_rows']:,} rows with non-zero values.")
        return what, why

    if t == "cross_table_fk_orphan":
        parent = f"{p['parent_table']} · {n(p['parent_table'], p['parent_column'])}"
        what = f"Checks that every {col} in {table} exists in {parent}."
        why = (f"Created because {_pct(e['overlap_pct'])} of the distinct {col} values match "
               f"{parent}, which shows the tables are linked.")
        return what, why

    return f"Checks {rule['category']} for {table}.", "Derived automatically from the data profile."
