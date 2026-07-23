"""E3 - triage-derived metrics ("the artifact evaluates itself").

Computed from the issues table after human triage: the accept/reject/
consider decisions are ground-truth labels generated as a by-product of
normal operation (evaluation-in-use). Reported separately per detector
source, never averaged blindly.

  empirical_precision = accepted / (accepted + rejected)   (target >= 0.80)
  hallucination_rate  = rejected LLM issues / total LLM issues
  ambiguity_rate      = considered / total

Issues still 'flagged' (un-reviewed) count toward totals but not toward
precision (which only weighs decided accept-vs-reject).
"""
from __future__ import annotations

from collections import Counter

STATUSES = ("flagged", "accepted", "rejected", "considered")


def _status_counts(issues: list[dict]) -> dict[str, int]:
    c = Counter(i.get("status", "flagged") for i in issues)
    return {s: c.get(s, 0) for s in STATUSES}


def _precision(counts: dict[str, int]) -> float | None:
    decided = counts["accepted"] + counts["rejected"]
    return round(counts["accepted"] / decided, 4) if decided else None


def compute_triage_metrics(issues: list[dict]) -> dict:
    overall = _status_counts(issues)
    total = len(issues)

    by_source = {}
    for source in ("rule", "llm"):
        subset = [i for i in issues if i.get("source") == source]
        counts = _status_counts(subset)
        by_source[source] = {
            "total": len(subset),
            **counts,
            "empirical_precision": _precision(counts),
        }

    llm_total = by_source["llm"]["total"]
    return {
        "total": total,
        "reviewed": total - overall["flagged"],
        "by_status": overall,
        "empirical_precision": _precision(overall),
        "by_source": by_source,
        "hallucination_rate": (
            round(by_source["llm"]["rejected"] / llm_total, 4) if llm_total else None
        ),
        "ambiguity_rate": round(overall["considered"] / total, 4) if total else None,
    }
