"""Layer 4 - severity-weighted DQ scoring.

DQS = 100 * Sum_d ( w_d * s_d )

  w_d  dimension weight (justified in writing; consistency and
       cross_table dominate because cross-file errors are the ones this
       project exists to surface).
  s_d  dimension health in (0, 1], computed from a severity-weighted
       penalty: s_d = K / (K + penalty_d), where penalty_d sums severity
       points over that dimension's issues. Smooth, always positive, and
       tunable through the single sensitivity constant K.

A flat average of dimensions is deliberately avoided (a locked
decision): trivial nulls should not be able to swamp a critical
cross-table breakage.

Score basis (locked decision): issues triaged as 'rejected' -- confirmed
false positives / LLM hallucinations -- are excluded, so the score
reflects genuine data quality and improves as false positives are
rejected during triage rather than penalising the data for a detector's
mistakes.
"""
from __future__ import annotations

DIMENSION_WEIGHTS = {
    "consistency": 0.25,
    "cross_table": 0.25,
    "validity": 0.20,
    "completeness": 0.15,
    "uniqueness": 0.10,
    "timeliness": 0.05,
}
SEVERITY_POINTS = {"high": 3, "medium": 2, "low": 1}
# LLM 'accuracy' issues are contextual validity problems; fold them in so
# the score stays on the six canonical, weight-justified dimensions.
DIMENSION_ALIASES = {"accuracy": "validity"}
DEFAULT_K = 10.0
EXCLUDED_STATUS = "rejected"


def compute_dq_score(issues: list[dict], k: float = DEFAULT_K) -> dict:
    """Compute the overall DQ score and per-dimension sub-scores (0-100)."""
    penalties = {dim: 0 for dim in DIMENSION_WEIGHTS}

    for issue in issues:
        if issue.get("status") == EXCLUDED_STATUS:
            continue
        dim = DIMENSION_ALIASES.get(issue["dimension"], issue["dimension"])
        if dim not in penalties:
            continue  # anything outside the weighted set doesn't score
        penalties[dim] += SEVERITY_POINTS.get(issue["severity"], 1)

    sub_scores = {dim: k / (k + penalties[dim]) for dim in DIMENSION_WEIGHTS}
    overall = 100 * sum(DIMENSION_WEIGHTS[dim] * sub_scores[dim] for dim in DIMENSION_WEIGHTS)

    return {
        "overall_score": round(overall, 2),
        "completeness_score": round(100 * sub_scores["completeness"], 2),
        "validity_score": round(100 * sub_scores["validity"], 2),
        "consistency_score": round(100 * sub_scores["consistency"], 2),
        "uniqueness_score": round(100 * sub_scores["uniqueness"], 2),
        "timeliness_score": round(100 * sub_scores["timeliness"], 2),
        "cross_table_score": round(100 * sub_scores["cross_table"], 2),
    }
