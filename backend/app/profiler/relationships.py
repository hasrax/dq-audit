"""Cross-file FK discovery via value-overlap scoring."""
from __future__ import annotations

import pandas as pd

from app.profiler.keys import find_pk_candidates
from app.profiler.types import TYPE_INT, TYPE_VARCHAR

_FK_TYPES = (TYPE_INT, TYPE_VARCHAR)


def find_relationships(
    tables: dict[str, pd.DataFrame],
    types_by_table: dict[str, dict[str, str]],
    min_overlap: float = 0.95,
    min_distinct: int = 20,
    cross_table_only: bool = True,
) -> list[dict]:
    """Score every (child column) against every (parent PK candidate).

    overlap_pct = |distinct(child) intersect distinct(parent)| / |distinct(child)|,
    i.e. what fraction of the child column's values are found in the
    candidate parent key (child_table.child_col subseteq parent_table.parent_col).
    Only int/varchar columns are compared, and only against the parent's
    own PK candidates -- a "relationship" target must look like a key.

    This is a value-overlap heuristic, not a semantic one: a low-
    cardinality status/flag column will trivially "overlap" a dense
    sequential ID range (small integers are contained in almost any
    large contiguous PK set). min_distinct raises the bar enough to drop
    the most obvious flag columns, but real FK columns can still be
    confused with status codes of similar cardinality. That is why
    every result here is a *candidate* -- the `relationships.confirmed`
    field exists for a human to confirm or reject it in Layer 3, the
    same human-in-the-loop mechanism used for issue triage.
    """
    pk_by_table = {name: find_pk_candidates(df) for name, df in tables.items()}
    value_sets = {
        name: {col: set(df[col].dropna()) for col in df.columns}
        for name, df in tables.items()
    }

    results = []
    for child_name, child_df in tables.items():
        for child_col in child_df.columns:
            child_type = types_by_table[child_name][child_col]
            if child_type not in _FK_TYPES:
                continue
            child_values = value_sets[child_name][child_col]
            if len(child_values) < min_distinct:
                continue

            for parent_name, parent_pks in pk_by_table.items():
                if cross_table_only and parent_name == child_name:
                    continue
                for parent_col in parent_pks:
                    if parent_name == child_name and parent_col == child_col:
                        continue
                    if types_by_table[parent_name][parent_col] != child_type:
                        continue

                    parent_values = value_sets[parent_name][parent_col]
                    overlap = len(child_values & parent_values) / len(child_values)
                    if overlap >= min_overlap:
                        results.append(
                            {
                                "child_table": child_name,
                                "child_column": child_col,
                                "parent_table": parent_name,
                                "parent_column": parent_col,
                                "overlap_pct": round(overlap * 100, 2),
                            }
                        )
    return results
