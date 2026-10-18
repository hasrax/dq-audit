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

In the dashboard, Schema review's "Draft with AI" uses document_from_audits:
the same drafting from the statistics saved with the company's audits, so a
company imported without docs can get them later, without its export files.
"""
from __future__ import annotations

import sys
from pathlib import Path

from app.aliases import base_client, client_label
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
    source_client = base_client(source_client or data_dir.name)
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


VALUE_TYPES = {"int", "decimal", "date"}   # numbers and dates are the audit subject: never masked


def audited_tables(conn, source_client: str) -> list[str]:
    """Tables that have stored statistics in any of the company's audits."""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT p.table_name FROM profiles p JOIN uploads u ON u.id = p.upload_id "
        "WHERE u.source_client = %s ORDER BY 1", (base_client(source_client),)).fetchall()]


def stored_profiles(conn, source_client: str) -> tuple[dict[str, dict], list[dict]]:
    """Each table's profile as saved with the company's most recent audit that
    has the table, plus those audits' discovered links. No source files needed.

    The saved statistics hold no most-common values and no row count, so
    top_values is empty and the row count is worked out from a column's
    empty count and share (else the largest distinct count). The primary key
    is the first column that is never empty and unique on every row.
    """
    from app.db import repository

    latest = dict(conn.execute(
        "SELECT p.table_name, max(p.upload_id) FROM profiles p JOIN uploads u ON u.id = p.upload_id "
        "WHERE u.source_client = %s GROUP BY p.table_name ORDER BY p.table_name", (source_client,)).fetchall())
    profiles = {}
    for table, upload_id in latest.items():
        rows = conn.execute(
            "SELECT column_name, data_type, null_count, null_pct, distinct_count, min_val, max_val, mean_val "
            "FROM profiles WHERE upload_id = %s AND table_name = %s ORDER BY id", (upload_id, table)).fetchall()
        with_nulls = [(n, float(p)) for _, _, n, p, *_ in rows if n and p]
        if with_nulls:
            n, p = max(with_nulls)
            row_count = round(n * 100 / p)
        else:
            row_count = max((r[4] or 0 for r in rows), default=0) or None   # r[4] = distinct_count
        columns = {
            col: {"type": ty or "varchar", "null_pct": float(p) if p is not None else None, "distinct_count": d,
                  "min": lo, "max": hi, "mean": float(mean) if mean is not None else None, "top_values": {},
                  "null_count": n}
            for col, ty, n, p, d, lo, hi, mean in rows
        }
        pk = next((c for c, s in columns.items() if row_count and not s["null_count"]
                   and s["distinct_count"] == row_count), None)
        profiles[table] = {"table_name": table, "row_count": row_count, "primary_key": pk, "columns": columns,
                           "upload_id": upload_id}

    links, seen = [], set()
    for upload_id in sorted(set(latest.values())):
        for r in repository.fetch_relationships(conn, upload_id):
            key = (r["child_table"], r["child_column"], r["parent_table"], r["parent_column"])
            if key not in seen:
                seen.add(key)
                links.append(r)
    return profiles, links


def mask_stored_profile(profile: dict, source_client: str) -> dict:
    """Mask the min / max of text columns (and privacy-listed ones) before the
    profile leaves the machine. Without the rows the name heuristic can't run,
    so every text column is treated as personal unless the company's privacy
    list keeps it; values masked earlier (hosted copy) are left as they are."""
    from app.privacy.pseudonymize import mask_value

    lists = load_overrides(base_client(source_client)).get(profile["table_name"], {})
    mask, keep = lists.get("mask", set()), lists.get("keep", set())
    columns = {}
    for col, s in profile["columns"].items():
        personal = col in mask or (s["type"] not in VALUE_TYPES and col not in keep)
        s = dict(s)
        if personal:
            for k in ("min", "max"):
                v = s.get(k)
                if v is not None and not str(v).startswith("MASK_"):
                    s[k] = mask_value(str(v))
        columns[col] = s
    return {**profile, "columns": columns}


def document_from_audits(
    conn,
    source_client: str,
    tables: list[str] | None = None,
    regenerate: bool = False,
    client: LLMClient | None = None,
    progress=None,
) -> dict[str, dict]:
    """Draft docs for a company's tables from the statistics stored with its
    audits (Schema review's "Draft with AI"; works on the hosted site, where
    no export files are kept). Same prompt, checks and store as
    document_directory; tables that already have docs are skipped unless
    regenerate, and verified text is never overwritten. Each table is saved
    as soon as it is drafted, so a stop part-way keeps the finished ones."""
    from app.schema_docs import store

    source_client = base_client(source_client)
    profiles, links = stored_profiles(conn, source_client)
    already = set() if regenerate else store.documented_tables(conn, source_client)
    targets = [t for t in (tables or list(profiles)) if t in profiles and t not in already]
    client = client or (LLMClient() if targets else None)
    model = getattr(getattr(client, "settings", None), "llm_model", None)
    summary = {}
    for i, name in enumerate(targets):
        if progress:
            progress(i, len(targets), name)
        doc = generate_table_doc(mask_stored_profile(profiles[name], source_client), links, list(profiles),
                                 client=client)
        store.save_table_doc(conn, source_client, name, doc, model=model)
        summary[name] = {"columns": len(doc["columns"]), "missing": doc["missing"],
                         "hallucinated": doc["hallucinated"]}
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
