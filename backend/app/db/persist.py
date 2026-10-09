"""Persist a full audit run (Layer 0 profiles + Layer 1 + Layer 2 issues).

Ties the pieces into one upload row: profiles and discovered
relationships from Layer 0, rule issues from the Layer 1 engine, and --
optionally -- the Type 2 issues from the Layer 2 LLM auditor. Both issue
sources land in the same issues table, which is what lets Layer 3 triage
and the complementarity analysis treat them as one population.

Usage:
    python -m app.db.persist <input_dir> [--llm] [--client=KEY] [--as-of=YYYY-MM-DD] [--name=LABEL]

--llm additionally runs the Layer 2 auditor (needs LLM_* in .env and, for
cloud endpoints, sends only pseudonymized samples).
"""
from __future__ import annotations

import sys
from pathlib import Path

from app.config import Settings
from app.aliases import base_client
from app.db import repository
from app.llm.feedback import save_prompt_log
from app.monitoring.recurrence import link_recurring
from app.rules.engine import run_rules
from app.schema_docs.store import display_aliases


def persist_audit(
    data_dir: Path | str,
    source_client: str | None = None,
    llm_issues: list[dict] | None = None,
    settings: Settings | None = None,
    data_as_of=None,
    filename: str | None = None,
    prompt_log: list[dict] | None = None,
) -> dict:
    """Run Layer 1 (and accept optional Layer 2 issues), persist everything."""
    result = run_rules(data_dir, source_client=source_client)
    profiles = result["profiles"]
    relationships = result["relationships"]
    total_rows = sum(p["row_count"] for p in profiles.values())

    conn = repository.connect(settings)
    try:
        repository.apply_schema(conn)
        # Rules an admin marked "not applicable to us" still run (so their
        # counts stay visible) but are stored as disabled and raise no issues.
        suppressed = repository.active_suppressions(conn, base_client(result["source_client"]))
        for rule in result["rules"]:
            rule["enabled"] = rule["rule_key"] not in suppressed
        rule_issues = [i for i in result["issues"] if i.get("rule_key") not in suppressed]
        all_issues = rule_issues + (llm_issues or [])
        upload_id = repository.create_upload(
            conn, filename=filename or result["source_client"],
            source_client=result["source_client"], row_count=total_rows, data_as_of=data_as_of,
        )
        n_profiles = repository.save_profiles(conn, upload_id, profiles)
        n_rels = repository.save_relationships(conn, upload_id, relationships)
        rule_ids = repository.save_rules(
            conn, upload_id, result["source_client"], result["rules"],
            aliases=display_aliases(conn, result["source_client"]),
        )
        n_issues = repository.save_issues(conn, upload_id, all_issues, rule_ids=rule_ids)
        if prompt_log:
            save_prompt_log(conn, upload_id, prompt_log)
        recurrence = link_recurring(conn, upload_id)
        score = repository.score_upload(conn, upload_id)
    finally:
        conn.close()

    return {
        "upload_id": upload_id,
        "profiles": n_profiles,
        "relationships": n_rels,
        "rules": len(rule_ids),
        "rules_violated": sum(1 for r in result["rules"] if r["violation_count"]),
        "issues": n_issues,
        "recurrence": recurrence,
        "rule_issues": len(rule_issues),
        "rules_suppressed": sum(1 for r in result["rules"] if not r["enabled"]),
        "llm_issues": len(llm_issues or []),
        "overall_score": score["overall_score"],
    }


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        raise SystemExit(1)

    input_dir = Path(argv[0])
    opts = dict(a[2:].split("=", 1) for a in argv[1:] if a.startswith("--") and "=" in a)
    client = opts.get("client")
    llm_issues, prompt_log = None, []
    if "--llm" in argv:
        from app.llm.run_audit import audit_directory

        audited = audit_directory(input_dir, source_client=client, prompt_log=prompt_log)
        llm_issues = [issue for table_issues in audited.values() for issue in table_issues]

    result = persist_audit(input_dir, source_client=client, llm_issues=llm_issues,
                           data_as_of=opts.get("as-of"), filename=opts.get("name"),
                           prompt_log=prompt_log)
    print(
        f"upload #{result['upload_id']}: "
        f"{result['profiles']} profile rows, {result['relationships']} relationships, "
        f"{result['rules']} rules ({result['rules_violated']} violated), "
        f"{result['issues']} issues "
        f"({result['rule_issues']} rule + {result['llm_issues']} llm) "
        f"· DQ score {result['overall_score']}/100 · "
        f"{result['recurrence']['new']} new, {result['recurrence']['recurring']} recurring, "
        f"{result['recurrence']['not_seen']} not seen since last audit"
    )


if __name__ == "__main__":
    main()
