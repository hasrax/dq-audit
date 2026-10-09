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
import json
from pathlib import Path

# Default weights (justified in writing; consistency and cross_table dominate
# because cross-file errors are what this project exists to surface). These can
# be overridden without code changes via config/score_weights.json.
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
WEIGHTS_PATH = Path(__file__).resolve().parents[3] / "config" / "score_weights.json"


def load_weights(path: Path | None = None) -> dict[str, float]:
    """Dimension weights, from config/score_weights.json if present, else the
    coded defaults. Config values override per-dimension; any dimension the
    config omits keeps its default so all six are always present."""
    path = path or WEIGHTS_PATH
    weights = dict(DIMENSION_WEIGHTS)
    if path.exists():
        config = json.loads(path.read_text(encoding="utf-8"))
        for dim in DIMENSION_WEIGHTS:
            if dim in config:
                weights[dim] = float(config[dim])
    return weights


def _normalized_weights(weights: dict[str, float]) -> dict[str, float]:
    """Scale weights to sum to 1 so the score stays 0-100 even if a config's
    weights don't sum to 1 exactly."""
    total = sum(weights.get(dim, 0) for dim in DIMENSION_WEIGHTS)
    if total <= 0:
        return dict(DIMENSION_WEIGHTS)
    return {dim: weights.get(dim, 0) / total for dim in DIMENSION_WEIGHTS}


def compute_dq_score(issues: list[dict], k: float = DEFAULT_K,
                     weights: dict[str, float] | None = None) -> dict:
    """Compute the overall DQ score and per-dimension sub-scores (0-100)."""
    weights = _normalized_weights(weights or load_weights())
    penalties = {dim: 0 for dim in DIMENSION_WEIGHTS}

    for issue in issues:
        if issue.get("status") == EXCLUDED_STATUS:
            continue
        dim = DIMENSION_ALIASES.get(issue["dimension"], issue["dimension"])
        if dim not in penalties:
            continue  # anything outside the weighted set doesn't score
        penalties[dim] += SEVERITY_POINTS.get(issue["severity"], 1)

    sub_scores = {dim: k / (k + penalties[dim]) for dim in DIMENSION_WEIGHTS}
    overall = 100 * sum(weights[dim] * sub_scores[dim] for dim in DIMENSION_WEIGHTS)

    return {
        "overall_score": round(overall, 2),
        "completeness_score": round(100 * sub_scores["completeness"], 2),
        "validity_score": round(100 * sub_scores["validity"], 2),
        "consistency_score": round(100 * sub_scores["consistency"], 2),
        "uniqueness_score": round(100 * sub_scores["uniqueness"], 2),
        "timeliness_score": round(100 * sub_scores["timeliness"], 2),
        "cross_table_score": round(100 * sub_scores["cross_table"], 2),
    }


def score_contributions(score: dict, weights: dict[str, float] | None = None) -> dict[str, float]:
    """Each dimension's contribution to the overall score (weight x sub-score).
    Contributions sum to the overall score, so the number is fully explained."""
    weights = _normalized_weights(weights or load_weights())
    return {dim: round(weights[dim] * score[f"{dim}_score"], 2) for dim in DIMENSION_WEIGHTS}
