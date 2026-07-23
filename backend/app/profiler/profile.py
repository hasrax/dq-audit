"""Layer 0 orchestrator: profile every file in a client data directory."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.io.loader import list_data_files, load_table
from app.profiler.keys import choose_primary_key, find_pk_candidates
from app.profiler.relationships import find_relationships
from app.profiler.stats import compute_stats
from app.profiler.types import infer_types


def profile_table(df: pd.DataFrame, table_name: str, types: dict[str, str] | None = None) -> dict:
    """Build the profile dict for a single already-loaded table."""
    types = types if types is not None else infer_types(df)
    stats = compute_stats(df, types)
    pk_candidates = find_pk_candidates(df)
    return {
        "table_name": table_name,
        "row_count": len(df),
        "primary_key": choose_primary_key(df, pk_candidates),
        "primary_key_candidates": pk_candidates,
        "columns": {
            col: {"type": types[col], **stats[col]} for col in df.columns
        },
    }


def profile_directory(directory: Path | str) -> tuple[dict[str, dict], list[dict]]:
    """Profile every (non-legacy) CSV in a client directory.

    Returns (profiles, relationships): profiles maps table_name -> profile
    dict; relationships is the cross-file FK candidate list.
    """
    tables: dict[str, pd.DataFrame] = {}
    types_by_table: dict[str, dict[str, str]] = {}
    profiles: dict[str, dict] = {}

    for path in list_data_files(directory):
        df = load_table(path)
        types = infer_types(df)
        tables[path.stem] = df
        types_by_table[path.stem] = types
        profiles[path.stem] = profile_table(df, path.stem, types)

    relationships = find_relationships(tables, types_by_table)
    return profiles, relationships
