"""Detection metrics + complementarity (E1/E2).

Combined recall is the headline target (>=0.90). Per-source recall is
reported on the type that source targets (rules on Type 1, LLM on Type
2). Precision is computed WITHIN the injected scope -- only detections
whose (table, dimension) was actually injected count toward precision,
so unrelated pre-existing noise in the real base doesn't distort it
(documented caveat). Complementarity (E2) splits recalled labels into
rule-only / LLM-only / both, overall and by dimension.
"""
from __future__ import annotations

from app.eval.matching import detection_sources, issue_dimension, label_matches_issue

SOURCE_TARGET_TYPE = {"rule": "type1", "llm": "type2"}


def _ratio(numer: int, denom: int) -> float | None:
    return round(numer / denom, 4) if denom else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if not precision or not recall:
        return None
    return round(2 * precision * recall / (precision + recall), 4)


def evaluate(labels: list, issues: list[dict]) -> dict:
    n = len(labels)
    caught = [detection_sources(lab, issues) for lab in labels]
    scope = {(lab.table, lab.dimension) for lab in labels}

    combined_recall = _ratio(sum(1 for s in caught if s), n)

    # per-source: recall on the source's target type + in-scope precision + F1
    by_source = {}
    for source, target_type in SOURCE_TARGET_TYPE.items():
        target_idx = [i for i, lab in enumerate(labels) if lab.error_type == target_type]
        rec = _ratio(sum(1 for i in target_idx if source in caught[i]), len(target_idx))

        in_scope = [
            iss for iss in issues
            if iss["source"] == source and (iss["table_name"], issue_dimension(iss)) in scope
        ]
        tp = sum(1 for iss in in_scope if any(label_matches_issue(lab, iss) for lab in labels))
        fp = len(in_scope) - tp
        prec = _ratio(tp, tp + fp)
        by_source[source] = {"recall": rec, "precision": prec, "f1": _f1(prec, rec), "tp": tp, "fp": fp}

    return {
        "n_injected": n,
        "combined_recall": combined_recall,
        "recall_by_type": {
            "type1": _recall_for(labels, caught, lambda l: l.error_type == "type1"),
            "type2": _recall_for(labels, caught, lambda l: l.error_type == "type2"),
        },
        "recall_by_dimension": _recall_by_dimension(labels, caught),
        "by_source": by_source,
        "complementarity": _complementarity(caught),
        "complementarity_by_dimension": _complementarity_by_dimension(labels, caught),
    }


def _recall_for(labels, caught, predicate) -> float | None:
    idx = [i for i, lab in enumerate(labels) if predicate(lab)]
    return _ratio(sum(1 for i in idx if caught[i]), len(idx))


def _recall_by_dimension(labels, caught) -> dict:
    dims = sorted({lab.dimension for lab in labels})
    return {d: _recall_for(labels, caught, lambda l, d=d: l.dimension == d) for d in dims}


def _complementarity(caught) -> dict:
    counts = {"rule_only": 0, "llm_only": 0, "both": 0, "missed": 0}
    for s in caught:
        if "rule" in s and "llm" in s:
            counts["both"] += 1
        elif "rule" in s:
            counts["rule_only"] += 1
        elif "llm" in s:
            counts["llm_only"] += 1
        else:
            counts["missed"] += 1
    total = sum(counts.values()) or 1
    counts_pct = {f"{k}_pct": round(100 * v / total, 2) for k, v in counts.items()}
    return {**counts, **counts_pct}


def _complementarity_by_dimension(labels, caught) -> dict:
    out = {}
    for dim in sorted({lab.dimension for lab in labels}):
        idx = [i for i, lab in enumerate(labels) if lab.dimension == dim]
        out[dim] = _complementarity([caught[i] for i in idx])
    return out
