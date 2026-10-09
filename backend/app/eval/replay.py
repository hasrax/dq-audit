"""Time-sliced replay: audit one real export as a series of past snapshots.

A single export has no history, so recurring-issue trends and projection
error can't be shown or measured on it. Replay rebuilds what the export
would have looked like at earlier cut-off dates and audits each snapshot
in order under a separate client key, giving real-data growth curves.

The slicing spec lives in config/replay/<client>.json (header tables with
their date column, detail tables with the key to their header); nothing
here names a table. Source files are never modified -- snapshots are
written to data/_replay/<client>/<cut-off>/ (git-ignored).

Limitation (threats to validity): snapshots are cumulative, so issues can
only grow or hold; replay cannot show an organisation fixing data.

Usage:
    python -m app.eval.replay <input_dir> [--client=KEY] [--llm] [--build-only]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from app.aliases import client_label
from app.io.loader import list_data_files, load_table

ROOT = Path(__file__).resolve().parents[3]
REPLAY_CONFIG_DIR = ROOT / "config" / "replay"
REPLAY_DATA_DIR = ROOT / "data" / "_replay"


def load_replay_config(source_client: str) -> dict:
    return json.loads((REPLAY_CONFIG_DIR / f"{source_client}.json").read_text(encoding="utf-8"))


def slice_tables(tables: dict[str, pd.DataFrame], spec: dict, cutoff: str) -> dict[str, pd.DataFrame]:
    """Return every table as it stood at `cutoff`."""
    cut = pd.Timestamp(cutoff)
    out = dict(tables)
    kept_keys: dict[str, set] = {}
    for name, h in spec.get("headers", {}).items():
        df = tables[name]
        dates = pd.to_datetime(df[h["date_column"]], errors="coerce")
        out[name] = df[dates <= cut]
        kept_keys[name] = set(out[name][h["key"]].dropna())
    for name, d in spec.get("details", {}).items():
        df = tables[name]
        out[name] = df[df[d["key"]].isin(kept_keys[d["header"]])]
    return out


def write_snapshot(tables: dict[str, pd.DataFrame], directory: Path) -> None:
    """Write headerless CSVs in the export's own format (NULL for missing)."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_csv(directory / f"{name}.csv", header=False, index=False, na_rep="NULL")


def build_snapshots(input_dir: Path | str, source_client: str) -> list[tuple[str, Path]]:
    spec = load_replay_config(source_client)
    tables = {p.stem: load_table(p) for p in list_data_files(input_dir)}
    built = []
    for cutoff in spec["cutoffs"]:
        directory = REPLAY_DATA_DIR / source_client / cutoff
        write_snapshot(slice_tables(tables, spec, cutoff), directory)
        built.append((cutoff, directory))
    return built


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        raise SystemExit(1)
    input_dir = Path(argv[0])
    opts = dict(a[2:].split("=", 1) for a in argv[1:] if a.startswith("--") and "=" in a)
    source_client = opts.get("client", input_dir.name)
    spec = load_replay_config(source_client)
    replay_client = spec["replay_client"]
    label = client_label(replay_client)

    snapshots = build_snapshots(input_dir, source_client)
    print(f"built {len(snapshots)} snapshots under {REPLAY_DATA_DIR / source_client}")
    if "--build-only" in argv:
        return

    from app.db.persist import persist_audit

    for cutoff, directory in snapshots:
        llm_issues, prompt_log = None, []
        if "--llm" in argv:
            from app.llm.run_audit import audit_directory

            audited = audit_directory(directory, source_client=replay_client, prompt_log=prompt_log)
            llm_issues = [i for issues in audited.values() for i in issues]
        r = persist_audit(directory, source_client=replay_client, llm_issues=llm_issues,
                          data_as_of=cutoff, filename=f"{label} @ {cutoff}", prompt_log=prompt_log)
        rec = r["recurrence"]
        print(f"{label} @ {cutoff}: upload #{r['upload_id']}, {r['issues']} issues "
              f"({rec['new']} new, {rec['recurring']} recurring: {rec['worsening']} worsening, "
              f"{rec['stable']} stable; {rec['not_seen']} not seen), DQ {r['overall_score']}")


if __name__ == "__main__":
    main()
