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
CLIENT_LABELS_PATH = Path(__file__).resolve().parents[2] / "config" / "client_labels.json"


REPLAY_SUFFIX = "_replay"


_CONFIG_PARENT: dict[str, str] = {}


def set_config_parents(parents: dict[str, str]) -> None:
    """Workspaces that reuse another workspace's configuration (workspaces.
    config_from), e.g. the study copy of Company A; see app.workspaces."""
    _CONFIG_PARENT.clear()
    _CONFIG_PARENT.update(parents)


def base_client(source_client: str | None) -> str | None:
    """The client whose config (privacy overrides, aliases, schema docs) a
    source uses. A time-sliced replay ("hemas_replay") or a study copy is
    the same organisation's data, so it shares that configuration."""
    if source_client in _CONFIG_PARENT:
        return _CONFIG_PARENT[source_client]
    if source_client and source_client.endswith(REPLAY_SUFFIX):
        return source_client[: -len(REPLAY_SUFFIX)]
    return source_client


def load_client_labels(path: Path | str | None = None) -> dict[str, str]:
    """source_client -> anonymised label (e.g. "Company A")."""
    path = Path(path or CLIENT_LABELS_PATH)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


_WORKSPACE_NAMES: dict[str, str] = {}


def set_workspace_names(names: dict[str, str]) -> None:
    """Workspace display names (from the database) take precedence over the
    config labels; see app.workspaces.refresh_names."""
    _WORKSPACE_NAMES.clear()
    _WORKSPACE_NAMES.update(names)


def client_label(source_client: str | None, labels: dict[str, str] | None = None) -> str:
    """Anonymised display name for a client; real names never reach the UI,
    logs or exports. Unknown clients show as-is (the user typed them)."""
    if not source_client:
        return ""
    if source_client in _WORKSPACE_NAMES:
        return _WORKSPACE_NAMES[source_client]
    labels = load_client_labels() if labels is None else labels
    return labels.get(source_client, source_client)


def load_aliases(source_client: str, config_dir: Path | str | None = None) -> dict[str, dict[str, str]]:
    directory = Path(config_dir) if config_dir else ALIASES_DIR
    path = directory / f"{base_client(source_client)}.json"
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
