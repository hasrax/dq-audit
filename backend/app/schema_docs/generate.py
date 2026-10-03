"""Schema documentation generator (Layer 0 -> LLM draft -> human verify).

After profiling, the LLM drafts a description for every table and
column; a person verifies them in the dashboard; verified docs are then
prepended to Layer 2 auditor prompts. The draft is cross-checked against
the profile: columns the model skipped are added as LOW / "Pending" and
columns it invented are dropped (and counted, as a hallucination signal).

Usage:
    python -m app.schema_docs.generate <input_dir> [--client=NAME] [--tables=T1,T2] [--regenerate]

By default only tables with no docs yet are drafted. --regenerate redrafts
every unverified column; VERIFIED columns are never overwritten.
"""
from __future__ import annotations

import sys
from pathlib import Path

from app.aliases import client_label
from app.llm.client import LLMClient
from app.llm.structured import complete_json
from app.privacy.pseudonymize import classify_pii_columns, load_overrides, pseudonymize_profile
from app.profiler.profile import profile_table
from app.profiler.relationships import find_relationships
from app.profiler.types import infer_types
from app.io.loader import list_data_files, load_table
from app.schema_docs.prompt import CONFIDENCES, MODULES, SYSTEM_PROMPT, build_doc_payload

PENDING_DESCRIPTION = "Pending — review manually"
MAX_OUTPUT_TOKENS = 4096


def validate_doc(parsed: dict | None, profile: dict) -> dict:
    """Reconcile an LLM draft with the profile's real columns.

    Returns {table_description, table_module, columns: {col: {...}},
    missing, hallucinated}. Every profiled column is present exactly
    once, in profile order; each column carries `from_llm` so the store
    can keep the original draft only where the model actually wrote one.
    """
    parsed = parsed if isinstance(parsed, dict) else {}
    profiled = list(profile["columns"])
    by_name = {c.lower(): c for c in profiled}

    drafted: dict[str, dict] = {}
    hallucinated: list[str] = []
    for raw in parsed.get("columns") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("column", "")).strip()
        real = by_name.get(name.lower())
        if real is None:
            hallucinated.append(name)
        elif real not in drafted:
            drafted[real] = raw

    columns, missing = {}, []
    for col in profiled:
        raw = drafted.get(col)
        if raw is None:
            missing.append(col)
            columns[col] = {
                "display_name": None, "description": PENDING_DESCRIPTION,
                "expected_domain": None, "business_rules": [],
                "confidence": "LOW", "from_llm": False,
            }
            continue
        confidence = str(raw.get("confidence", "")).upper()
        rules = raw.get("business_rules") or []
        columns[col] = {
            "display_name": _clean(raw.get("display_name")),
            "description": _clean(raw.get("description")) or PENDING_DESCRIPTION,
            "expected_domain": _clean(raw.get("expected_domain")),
            "business_rules": [str(r).strip() for r in rules if str(r).strip()] if isinstance(rules, list) else [str(rules)],
            "confidence": confidence if confidence in CONFIDENCES else "LOW",
            "from_llm": True,
        }

    module = str(parsed.get("table_module", "")).strip()
    module = next((m for m in MODULES if m.lower() == module.lower()), "Other")
    return {
        "table_description": _clean(parsed.get("table_description")),
        "table_module": module,
        "columns": columns,
        "missing": missing,
        "hallucinated": hallucinated,
    }


def generate_table_doc(
    profile: dict,
    relationships: list[dict],
    other_tables: list[str],
    client: LLMClient | None = None,
) -> dict:
    """One LLM call for one (already pseudonymized) table profile."""
    client = client or LLMClient()
    user = build_doc_payload(profile, relationships, other_tables)
    parsed = complete_json(client, SYSTEM_PROMPT, user, max_tokens=MAX_OUTPUT_TOKENS)
    doc = validate_doc(parsed, profile)
    doc["llm_raw"] = parsed
    return doc


def document_directory(
    data_dir: Path | str,
    source_client: str | None = None,
    tables: list[str] | None = None,
    regenerate: bool = False,
    client: LLMClient | None = None,
    conn=None,
    progress=None,
) -> dict[str, dict]:
    """Profile a client directory and draft + store docs per table.

    Returns {table: {"columns", "missing", "hallucinated"}} for the
    tables drafted in this run. The privacy overrides for the client are
    always applied: profile top values leave the machine.
    """
    from app.db import repository
    from app.schema_docs import store

    data_dir = Path(data_dir)
    source_client = source_client or data_dir.name
    client = client or LLMClient()
    overrides = load_overrides(source_client)

    loaded, types_by, profiles = {}, {}, {}
    for path in list_data_files(data_dir):
        df = load_table(path)
        types = infer_types(df)
        loaded[path.stem], types_by[path.stem] = df, types
        profiles[path.stem] = profile_table(df, path.stem, types)
    relationships = find_relationships(loaded, types_by)

    own_conn = conn is None
    conn = conn or repository.connect()
    try:
        repository.apply_schema(conn)
        already = set() if regenerate else store.documented_tables(conn, source_client)
        targets = [t for t in (tables or list(loaded)) if t in loaded and t not in already]
        summary = {}
        for i, name in enumerate(targets):
            if progress:
                progress(i, len(targets), name)
            ov = overrides.get(name, {})
            pii = classify_pii_columns(loaded[name], profiles[name],
                                       mask_override=ov.get("mask"), keep_override=ov.get("keep"))
            masked = pseudonymize_profile(profiles[name], pii)
            doc = generate_table_doc(masked, relationships, list(loaded), client=client)
            store.save_table_doc(conn, source_client, name, doc, model=client.settings.llm_model)
            summary[name] = {
                "columns": len(doc["columns"]),
                "missing": doc["missing"],
                "hallucinated": doc["hallucinated"],
            }
    finally:
        if own_conn:
            conn.close()
    return summary


def _clean(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        raise SystemExit(1)

    input_dir = Path(argv[0])
    source_client, tables = None, None
    for arg in argv[1:]:
        if arg.startswith("--client="):
            source_client = arg.split("=", 1)[1]
        elif arg.startswith("--tables="):
            tables = arg.split("=", 1)[1].split(",")

    source_client = source_client or input_dir.name
    summary = document_directory(
        input_dir, source_client=source_client, tables=tables,
        regenerate="--regenerate" in argv,
        progress=lambda i, n, t: print(f"[{i + 1}/{n}] drafting {t} ..."),
    )
    label = client_label(source_client)
    if not summary:
        print(f"{label}: every table already has docs (use --regenerate to redraft unverified columns)")
    for table, s in summary.items():
        print(f"{label} - {table}: {s['columns']} columns, "
              f"{len(s['missing'])} missing from draft, {len(s['hallucinated'])} invented and dropped")


if __name__ == "__main__":
    main()
