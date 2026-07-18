"""Stratified row sampling for the LLM auditor (Layer 0 deliverable).

The LLM never sees full tables (selective-invocation principle:
scalability, privacy, token budget). Instead it gets a small, curated
set of rows per table, assembled from three strata:

  flagged  - rows referenced by Layer 1 issues (the actual problems the
             LLM should explain / contextualize)
  edge     - rows hitting a column's min or max (boundary behaviour)
  random   - a seeded random fill (typical rows for context)

The random draw is seeded and the seed is recorded in the output, so a
sample is reproducible -- a viva requirement (evaluation must be
re-runnable from a documented seed).
"""
from __future__ import annotations

import pandas as pd

from app.profiler.types import TYPE_DATE, TYPE_DECIMAL, TYPE_INT

DEFAULT_SEED = 42
DEFAULT_MAX_ROWS = 20
NUMERIC_OR_DATE = (TYPE_INT, TYPE_DECIMAL, TYPE_DATE)


def _edge_row_reasons(df: pd.DataFrame, profile: dict) -> dict[int, list[str]]:
    """Map row index -> list of 'edge:col:min|max' reasons."""
    reasons: dict[int, list[str]] = {}
    for col, stats in profile["columns"].items():
        if stats["type"] not in NUMERIC_OR_DATE or stats["min"] is None:
            continue
        if stats["type"] == TYPE_DATE:
            values = pd.to_datetime(df[col], errors="coerce")
            lo, hi = values.min(), values.max()
        else:
            values = pd.to_numeric(df[col], errors="coerce")
            lo, hi = values.min(), values.max()
        for bound_name, bound in (("min", lo), ("max", hi)):
            hits = df.index[values == bound]
            if len(hits):
                idx = int(hits[0])
                reasons.setdefault(idx, []).append(f"edge:{col}:{bound_name}")
    return reasons


def build_stratified_sample(
    df: pd.DataFrame,
    profile: dict,
    flagged_rows: set[int] | None = None,
    seed: int = DEFAULT_SEED,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> dict:
    """Assemble a reproducible stratified sample for one table.

    flagged rows are taken first (they are the point of the audit), then
    edge rows, then a seeded random fill, all deduped and capped at
    max_rows. Each returned row records why it was included.
    """
    flagged_rows = {int(r) for r in (flagged_rows or set()) if r in df.index}
    reasons: dict[int, list[str]] = {idx: ["flagged"] for idx in flagged_rows}

    for idx, edge_reasons in _edge_row_reasons(df, profile).items():
        reasons.setdefault(idx, []).extend(edge_reasons)

    # Deterministic priority: flagged first, then edge, then random fill.
    ordered: list[int] = []
    for idx in sorted(flagged_rows):
        ordered.append(idx)
    for idx in sorted(r for r in reasons if r not in flagged_rows):
        ordered.append(idx)

    selected = ordered[:max_rows]

    remaining_slots = max_rows - len(selected)
    if remaining_slots > 0:
        pool = df.index.difference(pd.Index(selected))
        if len(pool):
            n = min(remaining_slots, len(pool))
            random_idx = (
                pd.Series(pool)
                .sample(n=n, random_state=seed)
                .tolist()
            )
            for idx in random_idx:
                reasons.setdefault(int(idx), []).append("random")
                selected.append(int(idx))

    sampled_rows = [
        {
            "row_index": idx,
            "reasons": reasons.get(idx, ["random"]),
            "values": {col: _cell(df.at[idx, col]) for col in df.columns},
        }
        for idx in selected
    ]

    return {
        "table_name": profile["table_name"],
        "row_count": profile["row_count"],
        "seed": seed,
        "sampled_rows": sampled_rows,
    }


def _cell(value):
    """JSON-safe cell value: missing -> None, everything else -> str."""
    if pd.isna(value):
        return None
    return str(value)
