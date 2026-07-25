"""Real-data rule/LLM complementarity (E2) from persisted issues.

For a single audit, groups issues by location (table, dimension, column)
and classifies each location as caught only by rules, only by the LLM, or
by both. This evidences the two-error-type taxonomy on real client data:
if rules and the LLM mostly light up different locations, they are
complementary rather than redundant. Rejected issues (confirmed false
positives) are excluded so the split reflects genuine findings.
"""
from __future__ import annotations

from app.scoring.score import DIMENSION_ALIASES

_SCORED_DIMENSIONS = ("completeness", "validity", "consistency", "uniqueness", "timeliness", "cross_table")


def _classify(sources: set[str]) -> str:
    if "rule" in sources and "llm" in sources:
        return "both"
    return "rule_only" if "rule" in sources else "llm_only"


def real_complementarity(issues: list[dict]) -> dict:
    groups: dict[tuple, set[str]] = {}
    for issue in issues:
        if issue.get("status") == "rejected":
            continue
        dim = DIMENSION_ALIASES.get(issue["dimension"], issue["dimension"])
        key = (issue["table_name"], dim, issue.get("column_name"))
        groups.setdefault(key, set()).add(issue["source"])

    overall = {"rule_only": 0, "llm_only": 0, "both": 0}
    by_dimension: dict[str, dict[str, int]] = {}
    for (_table, dim, _col), sources in groups.items():
        cls = _classify(sources)
        overall[cls] += 1
        by_dimension.setdefault(dim, {"rule_only": 0, "llm_only": 0, "both": 0})[cls] += 1

    n = len(groups) or 1
    overall_pct = {f"{k}_pct": round(100 * v / n, 1) for k, v in overall.items()}
    return {
        "n_locations": len(groups),
        "overall": {**overall, **overall_pct},
        "by_dimension": by_dimension,
    }
