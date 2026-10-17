"""T15 privacy and access-control check.

Usage (from backend/):
    python -m app.eval.privacy_check [--companies hemas,ussawatte,...] [--out ../evaluation/results]

1. AI prompts: for each company, every auditor prompt is BUILT but not sent
   (a recording stand-in replaces the model), plus the chat context of the
   first issues of its latest audit (with their masked example rows). The text is
   scanned for Sri Lankan phone numbers, national ID numbers, e-mail
   addresses, GPS coordinates, and any raw value of a privacy-listed column.
   Expected: zero.
2. Forbidden names: evaluation outputs, reviewer sheets and screenshots
   (file names and text-based files) are scanned for the names in
   evaluation/forbidden_names.txt (git-ignored; one name per line).
3. Access control: listed from the automated tests that enforce it.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

from app import workspaces as ws
from app.chat import assistant
from app.db import repository
from app.io.loader import load_table
from app.llm.run_audit import audit_directory
from app.privacy.pseudonymize import load_overrides

ROOT = Path(__file__).resolve().parents[3]
OUT_DEFAULT = ROOT / "evaluation" / "results"
DATA_DIRS = {"hemas": "hemas", "ussawatte": "ussawatte", "company-d": "company_d/2026-10-05",
             "company-e": "company_e/2026-05-12", "company-f": "company_f/2026-10-05"}
PATTERNS = {
    "phone number": r"(?<![\d.])(?:\+?94|0)7\d{8}(?![\d.])",
    "national ID": r"(?<![\d.])(?:\d{9}[VvXx]|(?:19|20)\d{10})(?![\d.])",
    "e-mail": r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}",
    "GPS coordinates": r"(?<![\d.])[5-9]\.\d{5,}\s*,\s*(?:79|80|81)\.\d{5,}",
}
ACCESS_TESTS = [
    ("Reviewer sees only Review + Insights pages", "tests/test_dashboard.py::test_reviewer_gets_no_admin_pages"),
    ("Signed-out users see only the sign-in form", "tests/test_dashboard.py::test_signed_out_users_see_only_the_login_form"),
    ("Participant gets only three pages and no bulk actions",
     "tests/test_study_ui.py::test_participant_gets_task_panel_and_only_three_pages"),
    ("Finished study code cannot restart; decisions never reach real issues",
     "tests/test_study.py::test_participant_lifecycle_and_isolation"),
    ("Local-only and test workspaces are never copied to the hosted database",
     "tests/test_study.py::test_cloud_copy_skips_local_only_and_test_workspaces"),
]


class _Recorder:
    """Stands in for the AI: records each prompt and returns no findings. Nothing is sent."""

    def __init__(self):
        self.sent: list[str] = []

    def complete(self, system, user, temperature=0.0, max_tokens=2048):
        self.sent.append(user)
        return '{"issues": []}'

    def chat(self, system, messages, temperature=0.0, max_tokens=2048):
        self.sent.append("\n".join(m["content"] for m in messages))
        return '{"issues": []}'


def raw_value_hits(text: str, data_dir: Path, slug: str) -> int:
    """Raw values (>= 6 characters) of privacy-listed columns found verbatim in the text."""
    hits = 0
    for table, spec in load_overrides(slug).items():
        path = data_dir / f"{table}.csv"
        if not path.exists():
            continue
        df = load_table(path)
        for col in spec.get("mask", []):
            if col in df.columns:
                vals = {str(v).strip() for v in df[col].dropna() if len(str(v).strip()) >= 6}
                hits += sum(1 for v in vals if v in text)
    return hits


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--companies", default=",".join(DATA_DIRS))
    ap.add_argument("--chat-issues", type=int, default=10)
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    conn = repository.connect()
    ws.refresh_names(conn)
    rows = []
    for slug in args.companies.split(","):
        data_dir = ROOT / "data" / DATA_DIRS[slug]
        rec = _Recorder()
        audit_directory(data_dir, client=rec, source_client=slug)
        up = repository.list_uploads(conn, source_client=slug)[0]
        for issue in repository.fetch_issues(conn, up["id"])[:args.chat_issues]:
            rec.sent.append(assistant.build_context(conn, up, issue))
        text = "\n".join(rec.sent)
        row = {"company": ws.get(conn, slug)["name"], "prompts_checked": len(rec.sent),
               "characters": len(text)}
        row.update({k: len(re.findall(p, text)) for k, p in PATTERNS.items()})
        row["raw privacy-listed values (>= 6 chars)"] = raw_value_hits(text, data_dir, slug)
        rows.append(row)
        print(row, flush=True)
    prompts = pd.DataFrame(rows)

    names_file = ROOT / "evaluation" / "forbidden_names.txt"
    names = [n.strip() for n in names_file.read_text(encoding="utf-8").splitlines()
             if n.strip() and not n.startswith("#")] if names_file.exists() else []
    leaks, scanned = [], 0
    for folder in (ROOT / "evaluation", ROOT / "output" / "study-screens"):
        for f in folder.rglob("*"):
            if not f.is_file() or f.name == names_file.name:
                continue
            scanned += 1
            text = f.name
            if f.suffix.lower() in (".md", ".csv", ".json", ".txt"):
                text += f.read_text(encoding="utf-8", errors="ignore")
            for n in names:
                if re.search(re.escape(n), text, re.I):
                    leaks.append({"file": str(f.relative_to(ROOT)), "name_found": f"(name #{names.index(n) + 1})"})
    access = pd.DataFrame([{"check": c, "enforced by": t} for c, t in ACCESS_TESTS])

    def md(df):
        if df.empty:
            return "_none_\n"
        cols = list(df.columns)
        return "\n".join(["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
                         + ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)]) + "\n"

    prompts.to_csv(out / "privacy_prompts.csv", index=False)
    (out / "privacy_check.md").write_text("\n".join([
        "# Privacy and access-control check (T15)", "",
        "## 1. Personal data in AI prompts (built, not sent)", "", md(prompts),
        "Expected: zero in every column.", "",
        f"## 2. Forbidden names in outputs ({len(names)} names, {scanned} files scanned)", "",
        md(pd.DataFrame(leaks)) if leaks else "No forbidden names found.\n",
        "## 3. Access control", "", md(access)]), encoding="utf-8")
    print(f"forbidden-name hits: {len(leaks)} in {scanned} files; wrote {out / 'privacy_check.md'}")


if __name__ == "__main__":
    main()
