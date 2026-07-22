"""Persist a full audit run (Layer 0 profiles + Layer 1 + Layer 2 issues).

Ties the pieces into one upload row: profiles and discovered
relationships from Layer 0, rule issues from the Layer 1 engine, and --
optionally -- the Type 2 issues from the Layer 2 LLM auditor. Both issue
sources land in the same issues table, which is what lets Layer 3 triage
and the complementarity analysis treat them as one population.

Usage:
    python -m app.db.persist <input_dir> [--llm]

--llm additionally runs the Layer 2 auditor (needs LLM_* in .env and, for
cloud endpoints, sends only pseudonymized samples).
"""
from __future__ import annotations

import sys
from pathlib import Path

from app.config import Settings
from app.db import repository
from app.rules.engine import run_rules


def persist_audit(
    data_dir: Path | str,
    source_client: str | None = None,
    llm_issues: list[dict] | None = None,
    settings: Settings | None = None,
) -> dict:
    """Run Layer 1 (and accept optional Layer 2 issues), persist everything."""
    result = run_rules(data_dir, source_client=source_client)
    profiles = result["profiles"]
    relationships = result["relationships"]
    all_issues = result["issues"] + (llm_issues or [])
    total_rows = sum(p["row_count"] for p in profiles.values())

    conn = repository.connect(settings)
    try:
        repository.apply_schema(conn)
        upload_id = repository.create_upload(
            conn, filename=result["source_client"],
            source_client=result["source_client"], row_count=total_rows,
        )
        n_profiles = repository.save_profiles(conn, upload_id, profiles)
        n_rels = repository.save_relationships(conn, upload_id, relationships)
        n_issues = repository.save_issues(conn, upload_id, all_issues)
        score = repository.score_upload(conn, upload_id)
    finally:
        conn.close()

    return {
        "upload_id": upload_id,
        "profiles": n_profiles,
        "relationships": n_rels,
        "issues": n_issues,
        "rule_issues": len(result["issues"]),
        "llm_issues": len(llm_issues or []),
        "overall_score": score["overall_score"],
    }


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        raise SystemExit(1)

    input_dir = Path(argv[0])
    llm_issues = None
    if "--llm" in argv:
        from app.llm.run_audit import audit_directory

        audited = audit_directory(input_dir)
        llm_issues = [issue for table_issues in audited.values() for issue in table_issues]

    result = persist_audit(input_dir, llm_issues=llm_issues)
    print(
        f"upload #{result['upload_id']}: "
        f"{result['profiles']} profile rows, {result['relationships']} relationships, "
        f"{result['issues']} issues "
        f"({result['rule_issues']} rule + {result['llm_issues']} llm) "
        f"· DQ score {result['overall_score']}/100"
    )


if __name__ == "__main__":
    main()
