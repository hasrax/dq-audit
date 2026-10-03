"""Layer 2 orchestrator + CLI: profile -> sample -> pseudonymize -> LLM audit.

Wires the whole selective-invocation path for a client directory: the
model only ever sees the profile, this table's relationships, and a
pseudonymized sample. Cloud endpoints (Groq/OpenRouter) therefore never
receive raw PII -- the pseudonymization step is mandatory and always
applied here.

Usage:
    python -m app.llm.run_audit <input_dir> [output_dir] [--tables T1,T2]

Per-client PII overrides (phone numbers stored as integers, national IDs,
geo-coordinates -- columns the heuristic cannot see) are loaded
automatically from config/privacy/<client>.json on every run, so no
entry point can skip them; explicit `overrides` are merged on top.
Verified schema documentation for the client, if any, is prepended to
each table's prompt.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.io.loader import list_data_files, load_table
from app.llm.auditor import audit_table
from app.llm.feedback import estimate_tokens, feedback_context
from app.llm.prompt import SYSTEM_PROMPT, build_user_payload
from app.llm.client import LLMClient
from app.privacy.pseudonymize import (
    classify_pii_columns,
    load_overrides,
    pseudonymize_profile,
    pseudonymize_sample,
)
from app.profiler.profile import profile_table
from app.profiler.relationships import find_relationships
from app.profiler.sampling import DEFAULT_SEED, build_stratified_sample
from app.profiler.types import infer_types
from app.schema_docs.store import get_schema_context

DEFAULT_SAMPLE_ROWS = 18


def audit_directory(
    data_dir: Path | str,
    tables: list[str] | None = None,
    overrides: dict[str, dict[str, set[str]]] | None = None,
    sample_max_rows: int = DEFAULT_SAMPLE_ROWS,
    seed: int = DEFAULT_SEED,
    client: LLMClient | None = None,
    source_client: str | None = None,
    prompt_log: list[dict] | None = None,
) -> dict[str, list[dict]]:
    """Run the LLM auditor over every (or the named) tables in a directory.

    prompt_log, when given, collects one entry per table recording which
    feedback examples and notes went into its prompt (saved with the
    upload by persist_audit -- the evaluation hook for feedback effects).
    """
    client = client or LLMClient()
    source_client = source_client or Path(data_dir).name
    overrides = _merge_overrides(load_overrides(source_client), overrides or {})

    loaded, types_by, profiles = {}, {}, {}
    for path in list_data_files(data_dir):
        df = load_table(path)
        types = infer_types(df)
        loaded[path.stem] = df
        types_by[path.stem] = types
        profiles[path.stem] = profile_table(df, path.stem, types)

    relationships = find_relationships(loaded, types_by)
    targets = tables or list(loaded)

    conn = _try_connect()
    results: dict[str, list[dict]] = {}
    for name in targets:
        df, profile = loaded[name], profiles[name]
        sample = build_stratified_sample(df, profile, seed=seed, max_rows=sample_max_rows)
        ov = overrides.get(name, {})
        pii = classify_pii_columns(
            df, profile, mask_override=ov.get("mask"), keep_override=ov.get("keep")
        )
        # Both the sample AND the profile statistics leave the machine, so
        # both must be pseudonymized before the (possibly cloud) model sees them.
        masked_profile = pseudonymize_profile(profile, pii)
        masked_sample = pseudonymize_sample(sample, pii)
        context = get_schema_context(source_client, name, conn=conn) if conn else ""
        base_tokens = estimate_tokens(
            SYSTEM_PROMPT, context, build_user_payload(masked_profile, masked_sample, relationships)
        )
        org_context, log = feedback_context(conn, source_client, name, base_tokens)
        if log is not None and prompt_log is not None:
            prompt_log.append({**log, "schema_context": bool(context)})
        results[name] = audit_table(
            masked_profile, masked_sample, relationships, client=client,
            schema_context=context, org_context=org_context,
        )
    if conn is not None:
        conn.close()
    return results


def _try_connect():
    """Repository connection for schema docs + feedback; None if unreachable
    (the audit then runs exactly as without those features)."""
    try:
        from app.db import repository

        conn = repository.connect()
        repository.apply_schema(conn)
        return conn
    except Exception:  # noqa: BLE001 -- the LLM audit must not depend on the DB
        return None


def _merge_overrides(base: dict, extra: dict) -> dict[str, dict[str, set[str]]]:
    merged = {t: {k: set(v.get(k, ())) for k in ("mask", "keep")} for t, v in base.items()}
    for table, v in extra.items():
        entry = merged.setdefault(table, {"mask": set(), "keep": set()})
        for k in ("mask", "keep"):
            entry[k] |= set(v.get(k) or ())
    return merged


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        raise SystemExit(1)

    input_dir = Path(argv[0])
    output_dir = Path(argv[1]) if len(argv) > 1 and not argv[1].startswith("--") else Path("output") / input_dir.name
    tables = None
    for arg in argv:
        if arg.startswith("--tables="):
            tables = arg.split("=", 1)[1].split(",")

    output_dir.mkdir(parents=True, exist_ok=True)
    results = audit_directory(input_dir, tables=tables)

    out_path = output_dir / "llm_issues.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)

    total = sum(len(v) for v in results.values())
    for name, issues in results.items():
        print(f"{name}: {len(issues)} LLM issues")
    print(f"wrote {out_path} ({total} issues across {len(results)} tables)")


if __name__ == "__main__":
    main()
