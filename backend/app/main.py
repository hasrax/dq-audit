"""CLI: profile every CSV in a client data directory.

Usage:
    python -m app.main <input_dir> [output_dir]

Writes one <table>.profile.json per input file plus a relationships.json
relationship map into output_dir (default: output/<input_dir name>).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.profiler.profile import profile_directory


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        raise SystemExit(1)

    input_dir = Path(argv[0])
    output_dir = Path(argv[1]) if len(argv) > 1 else Path("output") / input_dir.name
    output_dir.mkdir(parents=True, exist_ok=True)

    profiles, relationships = profile_directory(input_dir)

    for table_name, profile in profiles.items():
        out_path = output_dir / f"{table_name}.profile.json"
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2, default=str)
        print(f"wrote {out_path} ({profile['row_count']} rows, "
              f"{len(profile['columns'])} columns, pk={profile['primary_key']})")

    rel_path = output_dir / "relationships.json"
    with rel_path.open("w", encoding="utf-8") as f:
        json.dump(relationships, f, indent=2, default=str)
    print(f"wrote {rel_path} ({len(relationships)} candidate relationships)")


if __name__ == "__main__":
    main()
