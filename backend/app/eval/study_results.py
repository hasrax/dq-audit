"""T13 usability results: task completion (self vs system-verified), time,
SUS and clarity scores, and integrity checks for the unmoderated study.

Definitions
  task completion (self)      share of participants who pressed "Done"
  task completion (verified)  share whose task the app confirmed from logged events
  SUS                         per participant: (sum over odd items of (score - 1) +
                              sum over even items of (5 - score)) x 2.5; benchmark 68
  C1-C5                       explanation-clarity items, mean on the 1-5 scale
P00 (the pilot) is always excluded.

Usage (from backend/):
    python -m app.eval.study_results [--cloud] [--sus "SUS Responses.csv"] [--out ../evaluation/results]
                                     [--form-utc-offset 5.5]

--cloud reads the hosted database (CLOUD_DATABASE_URL), where the study runs.
Google Forms timestamps are in the form owner's time zone; --form-utc-offset
(hours, default 5.5 for Sri Lanka) converts them to UTC to compare with the
hosted database's UTC session times.
"""
from __future__ import annotations

import argparse
import os
import re
import statistics
from pathlib import Path

import matplotlib
import pandas as pd
import psycopg
from psycopg.rows import dict_row

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from app.config import load_settings  # noqa: E402
from app.study.participants import N_TASKS, PILOT_CODE, TASKS  # noqa: E402

SUS_BENCHMARK = 68.0
MAX_GAP_HOURS = 2.0
OUT_DEFAULT = Path(__file__).resolve().parents[3] / "evaluation" / "results"


# --- SUS ----------------------------------------------------------------------

def sus_score(items: dict[int, float]) -> float:
    """Standard SUS: odd items score-1, even items 5-score, sum x 2.5 (0-100)."""
    total = sum((items[i] - 1) if i % 2 else (5 - items[i]) for i in range(1, 11))
    return total * 2.5


def _num(value) -> float | None:
    m = re.match(r"\s*([1-5])", str(value))
    return float(m.group(1)) if m else None


def parse_sus(path: Path) -> tuple[pd.DataFrame, list[str]]:
    """Read the Google Form CSV, matching columns by prefix (Q1.-Q10., C1.-C5.)."""
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    cols = {c: c for c in raw.columns}
    q = {int(m.group(1)): c for c in cols if (m := re.match(r"^\s*Q(\d+)\.", c))}
    cl = {int(m.group(1)): c for c in cols if (m := re.match(r"^\s*C(\d+)\.", c))}
    find = lambda prefix: next((c for c in cols if c.strip().lower().startswith(prefix)), None)  # noqa: E731
    code_c, consent_c, ts_c = find("participant code"), find("consent"), find("timestamp")
    role_c, erp_c = find("your main role"), find("how long")
    known = set(q.values()) | set(cl.values()) | {code_c, consent_c, ts_c, role_c, erp_c}
    open_cols = [c for c in raw.columns if c not in known]
    if code_c is None or len(q) < 10:
        raise SystemExit("SUS CSV must have a 'Participant code' column and Q1.-Q10. columns")
    rows = []
    for _, r in raw.iterrows():
        if consent_c and str(r[consent_c]).strip().lower().startswith("no"):
            continue
        items = {i: _num(r[q[i]]) for i in range(1, 11)}
        if any(v is None for v in items.values()):
            continue
        row = {"code": r[code_c].strip().upper(), "timestamp": r[ts_c] if ts_c else None,
               "role": r[role_c] if role_c else "", "erp_experience": r[erp_c] if erp_c else "",
               "sus": sus_score(items)}
        row.update({f"C{i}": _num(r[cl[i]]) for i in sorted(cl)})
        row.update({f"open: {c}": r[c] for c in open_cols})
        rows.append(row)
    return pd.DataFrame(rows), open_cols


# --- app data -----------------------------------------------------------------

def load_study(conn: psycopg.Connection) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Participants, task events, and failed AI chat answers per code (pilot excluded)."""
    with conn.cursor(row_factory=dict_row) as cur:
        people = cur.execute("SELECT * FROM study_participants WHERE code <> %s ORDER BY code",
                             (PILOT_CODE,)).fetchall()
        tasks = cur.execute(
            "SELECT t.*, EXTRACT(EPOCH FROM (t.ended_at - t.started_at)) AS seconds FROM study_task_events t "
            "WHERE t.code <> %s ORDER BY t.code, t.task_no", (PILOT_CODE,)).fetchall()
    chat_failed = dict(conn.execute(
        "SELECT code, count(*) FROM study_events WHERE event = 'chat_failed' AND code <> %s GROUP BY code",
        (PILOT_CODE,)).fetchall())
    return pd.DataFrame(people), pd.DataFrame(tasks), chat_failed


def per_participant(people: pd.DataFrame, tasks: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, p in people.iterrows():
        t = tasks[tasks["code"] == p["code"]] if not tasks.empty else tasks
        row = {"code": p["code"], "status": p["status"], "device": p["device_type"], "browser": p["browser"],
               "tasks_done_self": int((t["self_status"] == "done").sum()) if not t.empty else 0,
               "tasks_verified": int(t["system_verified"].sum()) if not t.empty else 0,
               "total_minutes": round((p["finished_at"] - p["started_at"]).total_seconds() / 60, 1)
               if pd.notna(p["finished_at"]) and pd.notna(p["started_at"]) else None}
        for n in range(1, N_TASKS + 1):
            tn = t[t["task_no"] == n] if not t.empty else t
            row[f"task{n}_seconds"] = round(float(tn["seconds"].iloc[0]), 1) if len(tn) and pd.notna(
                tn["seconds"].iloc[0]) else None
        a = t[t["task_no"] == 1]["answer_text"] if not t.empty else []
        row["task1_answer"] = a.iloc[0] if len(a) and a.iloc[0] else ""
        rows.append(row)
    return pd.DataFrame(rows)


def per_task(tasks: pd.DataFrame, n_participants: int) -> pd.DataFrame:
    rows = []
    for n in range(1, N_TASKS + 1):
        t = tasks[(tasks["task_no"] == n) & tasks["ended_at"].notna()] if not tasks.empty else tasks
        secs = [float(s) for s in t["seconds"] if pd.notna(s)] if not t.empty else []
        rows.append({
            "task": n, "title": TASKS[n][0], "attempted": len(t),
            "completed_self_pct": round(100 * (t["self_status"] == "done").sum() / n_participants, 1)
            if n_participants else None,
            "completed_verified_pct": round(100 * t["system_verified"].sum() / n_participants, 1)
            if n_participants else None,
            "median_seconds": round(statistics.median(secs), 1) if secs else None,
        })
    return pd.DataFrame(rows)


def integrity(people: pd.DataFrame, sus: pd.DataFrame, utc_offset_h: float,
              chat_failed: dict[str, int] | None = None) -> pd.DataFrame:
    issued = set(people["code"]) if not people.empty else set()
    # the AI was down during a session: task 3 (and clarity scores) may reflect that, not the design
    checks = [{"check": "AI chat failed during session", "code": code, "detail": f"{n} failed answer(s)"}
              for code, n in sorted((chat_failed or {}).items())]
    if sus.empty:
        return pd.DataFrame(checks, columns=["check", "code", "detail"])
    for code in sorted(set(sus["code"]) - issued - {PILOT_CODE}):
        checks.append({"check": "form code never issued", "code": code, "detail": ""})
    dup = sus["code"].value_counts()
    for code in dup[dup > 1].index:
        checks.append({"check": "duplicate code in form", "code": code, "detail": f"{dup[code]} responses"})
    times = pd.to_datetime(sus["timestamp"], errors="coerce") - pd.Timedelta(hours=utc_offset_h)
    for code, ts in zip(sus["code"], times):
        p = people[people["code"] == code]
        if p.empty or pd.isna(ts):
            continue
        ref = p["finished_at"].iloc[0] if pd.notna(p["finished_at"].iloc[0]) else p["started_at"].iloc[0]
        if pd.notna(ref) and abs((ts - ref).total_seconds()) > MAX_GAP_HOURS * 3600:
            checks.append({"check": f"form more than {MAX_GAP_HOURS:g} h from app session", "code": code,
                           "detail": f"form {ts:%d %b %H:%M} UTC vs app {ref:%d %b %H:%M} UTC"})
    for code in sorted(set(people[people["status"] == "finished"]["code"]) - set(sus["code"])):
        checks.append({"check": "finished session without a form response", "code": code, "detail": ""})
    return pd.DataFrame(checks, columns=["check", "code", "detail"])


# --- figures ------------------------------------------------------------------

def fig_tasks(tasks_df: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 3.6), dpi=300)
    x = range(len(tasks_df))
    ax.bar([i - 0.2 for i in x], tasks_df["completed_self_pct"], 0.4, label="Participant said done",
           color="#cccccc", edgecolor="black", hatch="//")
    ax.bar([i + 0.2 for i in x], tasks_df["completed_verified_pct"], 0.4, label="Confirmed by the app",
           color="#555555", edgecolor="black")
    ax.set_xticks(list(x), [f"Task {t}\n{title}" for t, title in zip(tasks_df["task"], tasks_df["title"])],
                  fontsize=7)
    ax.set_ylabel("Participants completing (%)")
    ax.set_ylim(0, 105)
    ax.set_title("Task completion in the usability study")
    ax.legend(fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_sus(sus: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(6, 3.2), dpi=300)
    ys = list(range(len(sus)))
    ax.scatter(sus["sus"], ys, color="black", zorder=3)
    ax.axvline(SUS_BENCHMARK, color="black", linestyle="--", linewidth=1)
    ax.text(SUS_BENCHMARK + 1, len(sus) - 0.5, "benchmark 68", fontsize=7)
    ax.set_yticks(ys, sus["code"], fontsize=7)
    ax.set_xlim(0, 100)
    ax.set_xlabel("SUS score (0-100)")
    ax.set_title("System Usability Scale score per participant")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# --- report -------------------------------------------------------------------

def _md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_none_\n"
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cloud", action="store_true")
    ap.add_argument("--sus")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--form-utc-offset", type=float, default=5.5)
    args = ap.parse_args(argv)

    settings = load_settings()
    url = os.environ.get("CLOUD_DATABASE_URL", "") if args.cloud else settings.db_url
    if not url:
        raise SystemExit("CLOUD_DATABASE_URL is not set")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(url) as conn:
        people, tasks, chat_failed = load_study(conn)

    n = len(people[people["status"] != "not_started"]) if not people.empty else 0
    print(__doc__.split("Usage")[0].strip())
    print(f"\nn = {n} participant(s) who started (P00 excluded)")

    pp = per_participant(people[people["status"] != "not_started"], tasks) if n else pd.DataFrame()
    pt = per_task(tasks, n) if n else pd.DataFrame()
    sus = parse_sus(Path(args.sus))[0] if args.sus else pd.DataFrame()
    if not sus.empty:
        sus = sus[sus["code"] != PILOT_CODE]
    integ = integrity(people, sus, args.form_utc_offset, chat_failed)

    md = ["# Usability study results (T13, RQ5)", "",
          f"Participants who started: **{n}** (pilot P00 excluded). Source: {'hosted' if args.cloud else 'local'} "
          "database" + (f"; SUS responses: {Path(args.sus).name}" if args.sus else "; no SUS file given") + ".", "",
          "## Task completion", _md_table(pt)]
    if not pp.empty:
        pp.to_csv(out / "usability.csv", index=False)
        md += ["## Per participant", _md_table(pp.drop(columns=["task1_answer"])),
               "## Task 1 answers (\"in one sentence, what is wrong?\")", ""]
        md += [f"- **{c}**: {a}" for c, a in zip(pp["code"], pp["task1_answer"]) if a] + [""]
        pt.to_csv(out / "usability_tasks.csv", index=False)
        fig_tasks(pt, out / "fig_task_completion.png")
    if not sus.empty:
        s = sus["sus"]
        summary = {"n": len(s), "mean": round(s.mean(), 1), "sd": round(s.std(ddof=1), 1) if len(s) > 1 else None,
                   "median": round(s.median(), 1), "min": s.min(), "max": s.max(),
                   "above_68": int((s > SUS_BENCHMARK).sum())}
        md += ["## SUS", _md_table(pd.DataFrame([summary])),
               f"Mean vs benchmark {SUS_BENCHMARK:g}: {summary['mean'] - SUS_BENCHMARK:+.1f} points.", ""]
        c_cols = [c for c in sus.columns if re.fullmatch(r"C\d", c)]
        if c_cols:
            md += ["## Explanation clarity (C items, 1-5)",
                   _md_table(pd.DataFrame([{c: round(sus[c].mean(), 2) for c in c_cols}]))]
        open_cols = [c for c in sus.columns if c.startswith("open: ")]
        for c in open_cols:
            answers = [(code, a) for code, a in zip(sus["code"], sus[c]) if str(a).strip()]
            if answers:
                md += [f"### {c[6:]}", ""] + [f"- **{code}**: {a}" for code, a in answers] + [""]
        sus.to_csv(out / "sus_scores.csv", index=False)
        fig_sus(sus.sort_values("sus"), out / "fig_sus.png")
    md += ["## Integrity checks", _md_table(integ)]
    integ.to_csv(out / "integrity.csv", index=False)
    (out / "integrity.md").write_text("# Integrity checks\n\n" + _md_table(integ), encoding="utf-8")
    (out / "usability.md").write_text("\n".join(md), encoding="utf-8")
    print(f"wrote usability.md / integrity.md (+ CSVs and figures where data exists) to {out}")


if __name__ == "__main__":
    main()
