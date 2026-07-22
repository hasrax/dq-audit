"""Match detected issues against injected ground-truth labels (E1/E2).

Detectors report AGGREGATED issues (one issue per column/rule with an
affected-row count and only a small row sample), not one issue per row.
So matching is at (table, column, dimension) granularity, which is the
honest level for a column-aggregated detector: if the corrupted column
was flagged for the right dimension, the detector caught that problem.
Row-level matching would under-credit detectors that legitimately report
per-column. (Documented as an evaluation design choice for Chapter 4.)
"""
from __future__ import annotations

# LLM 'accuracy' issues are contextual validity problems (same fold as scoring).
_DIMENSION_FOLD = {"accuracy": "validity"}


def issue_dimension(issue: dict) -> str:
    return _DIMENSION_FOLD.get(issue["dimension"], issue["dimension"])


def label_matches_issue(label, issue: dict) -> bool:
    if label.table != issue["table_name"]:
        return False
    if label.dimension != issue_dimension(issue):
        return False
    issue_col = issue.get("column_name")
    # row-level injections (e.g. near-duplicate) and whole-row detections
    # carry no single column -- table + dimension is the right granularity.
    if label.column is None or issue_col is None:
        return True
    return label.column in {c.strip() for c in issue_col.split(",")}


def detection_sources(label, issues: list[dict]) -> set[str]:
    """Which detector sources ({'rule','llm'}) caught this injected label."""
    return {iss["source"] for iss in issues if label_matches_issue(label, iss)}
