"""Per-client column aliases for display (Layer 3 readability).

Detection is fully positional (col_1..col_n) and schema-agnostic -- this
module is a DISPLAY-ONLY overlay so a non-technical reviewer sees
"Invoice No" instead of "col_5". Aliases live in editable JSON at
config/aliases/<source_client>.json as {table: {col_N: "Friendly"}}.
Headered uploads already carry real names, so aliasing mainly serves the
headerless SeefaERP exports (Hemas/Ussawatte).
"""
from __future__ import annotations

import json
from pathlib import Path

ALIASES_DIR = Path(__file__).resolve().parents[2] / "config" / "aliases"


def load_aliases(source_client: str, config_dir: Path | str | None = None) -> dict[str, dict[str, str]]:
    directory = Path(config_dir) if config_dir else ALIASES_DIR
    path = directory / f"{source_client}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def display_columns(aliases: dict, table: str, column: str | None) -> str | None:
    """Alias a column reference for display; handles comma column-pairs
    (e.g. consistency issues report 'col_3,col_4') and passes through
    unmapped columns unchanged."""
    if not column:
        return column
    table_map = aliases.get(table, {})
    parts = [p.strip() for p in column.split(",")]
    return ", ".join(table_map.get(p, p) for p in parts)
