"""Show that the thesis numbers come from the system: a check to run in front of the examiners.

Usage (from backend/):
    python -m app.eval.verify_snapshot [--workbook ../evaluation/results/workbooks/evaluation_results_<stamp>.xlsx]

1. Integrity: every result file named in the frozen workbook's README tab still has the
   SHA-256 recorded at the freeze, so nothing was edited afterwards.
2. Reproduction: the tables computed from the database (T3 precision, T6 reproducibility,
   T8 rules vs AI, T10 projection error, T11 schema docs) are rebuilt now, into a temporary
   folder, with the same code, and compared with the frozen files.

Read-only: nothing in evaluation/results is written. Without --workbook the newest frozen
workbook in evaluation/results/workbooks/ is used.
"""
from __future__ import annotations

import argparse
import hashlib
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
RESULTS = ROOT / "evaluation" / "results"
REBUILT = {"T3 Precision": "precision.csv", "T6 Reproducibility": "reproducibility.csv",
           "T8 Rules vs AI": "complementarity.csv", "T10 Projection": "projection_error.csv",
           "T11 Schema docs": "schema_docs.csv"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recorded_hashes(workbook: Path) -> list[tuple[str, str, str]]:
    """(tab, source file, SHA-256) rows from the README tab."""
    readme = pd.read_excel(workbook, sheet_name="README", header=None).fillna("")
    start = next(i for i, v in enumerate(readme[0]) if str(v) == "Tab") + 1
    out = []
    for _, row in readme.iloc[start:].iterrows():
        tab, src = str(row[0]).strip(), str(row[1]).strip()
        digest = next((str(v) for v in row[2:] if len(str(v)) == 64), "")
        if tab and src:
            out.append((tab, src, digest))
    return out


def same_table(a: Path, b: Path) -> bool:
    x, y = pd.read_csv(a), pd.read_csv(b)
    try:
        pd.testing.assert_frame_equal(x, y, check_dtype=False, check_exact=False, rtol=1e-9)
        return True
    except AssertionError:
        return False


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workbook", type=Path)
    args = ap.parse_args(argv)
    workbook = args.workbook or max((RESULTS / "workbooks").glob("evaluation_results_*.xlsx"),
                                    key=lambda p: p.name)
    facts = pd.read_excel(workbook, sheet_name="README", header=None).fillna("")
    print(f"Frozen workbook: {workbook.name}")
    for label in ("Generated (Sri Lanka time)", "Git branch / commit", "AI model (thesis numbers)"):
        print(f"  {label}: " + next((str(r[1]) for _, r in facts.iterrows() if str(r[0]) == label), "?"))

    print("\n1. Result files unchanged since the freeze (SHA-256)")
    bad = 0
    for tab, src, digest in recorded_hashes(workbook):
        path = RESULTS / src
        ok = path.exists() and digest and sha256(path) == digest
        bad += not ok
        print(f"  {'OK     ' if ok else 'CHANGED'} {tab:<20} {src}")

    print("\n2. Database tables rebuilt now with the same code, compared with the frozen files")
    from app.eval import thesis_tables
    with tempfile.TemporaryDirectory() as tmp:
        thesis_tables.main(["--out", tmp])
        for tab, src in REBUILT.items():
            ok = same_table(Path(tmp) / src, RESULTS / src)
            bad += not ok
            print(f"  {'MATCH  ' if ok else 'DIFFERS'} {tab:<20} {src}")

    print("\nResult: " + ("every check passed; the thesis numbers come from these files and this database."
                          if not bad else f"{bad} check(s) did not pass; see the lines above."))


if __name__ == "__main__":
    main()
