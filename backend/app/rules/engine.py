"""Layer 1 rules engine: run all six DQ checks over a client directory.

Everything the rules need is derived automatically from the Layer 0
profile and relationship discovery -- there are no hand-written,
per-table rules (a locked project requirement). Each rule contributes
issues carrying the full ontology tuple (dimension, source, error_type,
severity, fixability, status); the engine assigns a stable id and
records which client the issue came from.

The six locked dimensions map to modules as:
  completeness  -> completeness.py
  validity      -> validity.py
  consistency   -> ordering.py + arithmetic.py   (two miners)
  uniqueness    -> uniqueness.py
  timeliness    -> timeliness.py
  cross_table   -> cross_table.py                 (uses relationships)
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.io.loader import list_data_files, load_csv
from app.profiler.keys import choose_primary_key
from app.profiler.profile import profile_table
from app.profiler.relationships import find_relationships
from app.profiler.types import infer_types
from app.rules.arithmetic import check_arithmetic
from app.rules.completeness import check_completeness
from app.rules.cross_table import check_cross_table
from app.rules.ordering import check_ordering
from app.rules.timeliness import check_timeliness
from app.rules.uniqueness import check_uniqueness
from app.rules.validity import check_validity


def run_rules(directory, source_client=None, reference_date: datetime | None = None) -> dict:
    """Profile every file in a client directory and run all Layer 1 rules.

    Returns a dict with the discovered relationships and the full issue
    list (each issue tagged with a sequential id and the source client).
    """
    source_client = source_client or str(directory).replace("\\", "/").rstrip("/").split("/")[-1]

    tables: dict[str, pd.DataFrame] = {}
    types_by_table: dict[str, dict[str, str]] = {}
    profiles: dict[str, dict] = {}
    distinct_by_table: dict[str, dict[str, int]] = {}

    for path in list_data_files(directory):
        df = load_csv(path)
        types = infer_types(df)
        profile = profile_table(df, path.stem, types)
        tables[path.stem] = df
        types_by_table[path.stem] = types
        profiles[path.stem] = profile
        distinct_by_table[path.stem] = {c: s["distinct_count"] for c, s in profile["columns"].items()}

    relationships = find_relationships(tables, types_by_table)

    # Which columns act as a foreign key in some relationship, per table.
    fk_columns_by_table: dict[str, set[str]] = {}
    for rel in relationships:
        fk_columns_by_table.setdefault(rel["child_table"], set()).add(rel["child_column"])

    issues: list[dict] = []

    for name, df in tables.items():
        types = types_by_table[name]
        profile = profiles[name]
        distinct_counts = distinct_by_table[name]
        fk_columns = fk_columns_by_table.get(name, set())
        pk = choose_primary_key(df)
        ordering_exclude = set(fk_columns) | ({pk} if pk else set())

        issues += check_completeness(name, df, profile, fk_columns=fk_columns)
        issues += check_validity(name, df, profile)
        issues += check_uniqueness(name, df, profile)
        issues += check_timeliness(name, df, profile, reference_date=reference_date)
        issues += check_ordering(name, df, types, distinct_counts=distinct_counts, exclude_columns=ordering_exclude)
        issues += check_arithmetic(name, df, types, distinct_counts=distinct_counts)

    # Cross-table referential integrity runs once over all tables at once.
    issues += check_cross_table(tables, relationships)

    for idx, issue in enumerate(issues, start=1):
        issue["id"] = idx
        issue["source_client"] = source_client

    return {
        "source_client": source_client,
        "profiles": profiles,
        "relationships": relationships,
        "issues": issues,
    }


def summarize(result: dict) -> dict:
    """Small rollup for a run: issue counts by dimension and severity."""
    by_dimension: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for issue in result["issues"]:
        by_dimension[issue["dimension"]] = by_dimension.get(issue["dimension"], 0) + 1
        by_severity[issue["severity"]] = by_severity.get(issue["severity"], 0) + 1
    return {
        "source_client": result["source_client"],
        "total_issues": len(result["issues"]),
        "by_dimension": by_dimension,
        "by_severity": by_severity,
    }
