"""E4 - cross-client reproducibility (Hemas vs Ussawatte).

The identical pipeline (same rule catalogue, same prompts) runs on both
clients despite their schema differences (Ussawatte prefixes tables with
Dis_ and its columns sit in different positions). This module measures
how much the rule catalogue reproduces across clients: the fraction of
rule types and dimensions that fire on both, plus DQ-score comparability.

Claim wording stays "reproducibility across clients of the same ERP" --
never "generalizability".
"""
from __future__ import annotations


def fingerprint(issues: list[dict], label: str | None = None, dq_score: float | None = None) -> dict:
    """Which rule types / dimensions fired for one client's audit."""
    rule_issues = [i for i in issues if i.get("source") == "rule"]
    return {
        "label": label,
        "dimensions": sorted({i["dimension"] for i in rule_issues}),
        "rule_types": sorted({i["rule_id"] for i in rule_issues if i.get("rule_id")}),
        "rule_issue_count": len(rule_issues),
        "dq_score": dq_score,
    }


def _overlap_pct(both: set, union: set) -> float | None:
    return round(100 * len(both) / len(union), 2) if union else None


def compare_fingerprints(a: dict, b: dict) -> dict:
    dims_a, dims_b = set(a["dimensions"]), set(b["dimensions"])
    rules_a, rules_b = set(a["rule_types"]), set(b["rule_types"])
    return {
        "client_a": a,
        "client_b": b,
        "dimensions_both": sorted(dims_a & dims_b),
        "rule_types_both": sorted(rules_a & rules_b),
        "rule_types_only_a": sorted(rules_a - rules_b),
        "rule_types_only_b": sorted(rules_b - rules_a),
        "dimension_overlap_pct": _overlap_pct(dims_a & dims_b, dims_a | dims_b),
        "rule_type_overlap_pct": _overlap_pct(rules_a & rules_b, rules_a | rules_b),
        "dq_scores": {a["label"]: a["dq_score"], b["label"]: b["dq_score"]},
    }


def compare_clients(conn, client_a: str, client_b: str) -> dict:
    """Compare the latest persisted upload of two clients (reads the DB)."""
    from app.db import repository

    uploads = repository.list_uploads(conn)

    def latest(client):
        return next((u for u in uploads if u["source_client"] == client), None)

    ua, ub = latest(client_a), latest(client_b)
    if ua is None or ub is None:
        missing = client_a if ua is None else client_b
        raise ValueError(f"no persisted upload for client {missing!r}")

    fa = fingerprint(
        repository.fetch_issues(conn, ua["id"]), client_a,
        repository.score_upload(conn, ua["id"])["overall_score"],
    )
    fb = fingerprint(
        repository.fetch_issues(conn, ub["id"]), client_b,
        repository.score_upload(conn, ub["id"])["overall_score"],
    )
    return compare_fingerprints(fa, fb)


def main(argv: list[str] | None = None) -> None:
    """CLI: python -m app.eval.cross_client [client_a] [client_b]"""
    import sys

    from app.db import repository

    argv = argv if argv is not None else sys.argv[1:]
    client_a = argv[0] if len(argv) > 0 else "hemas"
    client_b = argv[1] if len(argv) > 1 else "ussawatte"

    cmp = compare_clients(repository.connect(), client_a, client_b)
    print(f"Cross-client reproducibility: {client_a} vs {client_b}")
    print(f"  DQ scores: {cmp['dq_scores']}")
    print(f"  Dimension overlap: {cmp['dimension_overlap_pct']}% -> {cmp['dimensions_both']}")
    print(f"  Rule-type overlap: {cmp['rule_type_overlap_pct']}%")
    print(f"  Rule types on both: {cmp['rule_types_both']}")
    print(f"  Only {client_a}: {cmp['rule_types_only_a']}")
    print(f"  Only {client_b}: {cmp['rule_types_only_b']}")


if __name__ == "__main__":
    main()
