"""CLI: run the Layer 1 rules engine over a client data directory.

Usage:
    python -m app.audit <input_dir> [output_dir]

Writes issues.json (the full issue list) and issues_summary.json (a
per-dimension / per-severity rollup) into output_dir
(default: output/<input_dir name>).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.rules.engine import run_rules, summarize


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        raise SystemExit(1)

    input_dir = Path(argv[0])
    output_dir = Path(argv[1]) if len(argv) > 1 else Path("output") / input_dir.name
    output_dir.mkdir(parents=True, exist_ok=True)

    result = run_rules(input_dir)
    summary = summarize(result)

    issues_path = output_dir / "issues.json"
    with issues_path.open("w", encoding="utf-8") as f:
        json.dump(result["issues"], f, indent=2, default=str)

    summary_path = output_dir / "issues_summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"wrote {issues_path} ({summary['total_issues']} issues)")
    print(f"  by dimension: {summary['by_dimension']}")
    print(f"  by severity:  {summary['by_severity']}")
    print(f"wrote {summary_path}")


if __name__ == "__main__":
    main()
