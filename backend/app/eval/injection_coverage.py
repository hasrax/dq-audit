"""T5 injection coverage demonstration (planted errors, known answer key).

Usage (from backend/):
    python -m app.eval.injection_coverage [--seed 20261014] [--ai-runs 3] [--out ../evaluation/results]

A 300-row extract of Company A's goods-received tables is copied to a temporary
folder (nothing is stored), a fixed, seeded set of errors is planted (structural
Type 1 and contextual Type 2), then the production rules run once and the AI
auditor runs several times (its output varies between runs, see T7). For each
error class: planted, caught by rules, caught by the AI in each run, and caught
by the hybrid (rules or AI). A coverage demonstration, not a real-data recall estimate.
"""
from __future__ import annotations

import argparse
import tempfile
from collections import defaultdict
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from app.eval.injection import INJECTION_VERSION  # noqa: E402
from app.eval.matching import label_matches_issue  # noqa: E402
from app.eval.run_eval import COMPANY_A_DIR, build_scenario  # noqa: E402
from app.llm.run_audit import audit_directory  # noqa: E402
from app.rules.engine import run_rules  # noqa: E402

OUT_DEFAULT = Path(__file__).resolve().parents[3] / "evaluation" / "results"
CLASS_LABEL = {"null": "Missing value", "negative": "Negative amount", "duplicate_pk": "Duplicated ID",
               "future_date": "Date in the future", "date_inversion": "Dates in wrong order",
               "fk_orphan": "Broken link to another table", "price_outlier": "Price 50x too high",
               "quantity_x100": "Quantity x100", "near_duplicate": "Near-duplicate transaction"}


def caught(labels, issues) -> set[int]:
    return {k for k, lab in enumerate(labels) if any(label_matches_issue(lab, i) for i in issues)}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=20261014)
    ap.add_argument("--ai-runs", type=int, default=3)
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    tables, labels = build_scenario(COMPANY_A_DIR, args.seed)
    tmp = Path(tempfile.mkdtemp(prefix="dq_t5_"))
    for name, df in tables.items():
        df.to_csv(tmp / f"{name}.csv", header=False, index=False)
    rule_hits = caught(labels, run_rules(tmp)["issues"])
    ai_hits = []
    for k in range(args.ai_runs):
        found = [i for issues in audit_directory(tmp).values() for i in issues]
        ai_hits.append(caught(labels, found))
        print(f"AI run {k + 1}: {len(found)} findings, {len(ai_hits[-1])} planted errors matched", flush=True)

    by_class = defaultdict(list)
    for k, lab in enumerate(labels):
        by_class[(lab.error_class, lab.error_type)].append(k)
    rows = []
    for (cls, etype), ks in sorted(by_class.items(), key=lambda x: (x[0][1], x[0][0])):
        any_ai = set().union(*ai_hits) if ai_hits else set()
        row = {"error": CLASS_LABEL.get(cls, cls), "type": "structural (Type 1)" if etype == "type1"
               else "contextual (Type 2)", "planted": len(ks),
               "caught_by_rules": sum(k in rule_hits for k in ks)}
        for r, hits in enumerate(ai_hits, start=1):
            row[f"caught_by_ai_run{r}"] = sum(k in hits for k in ks)
        row["caught_by_ai_any_run"] = sum(k in any_ai for k in ks)
        row["caught_by_hybrid_run1"] = sum(k in rule_hits or k in ai_hits[0] for k in ks) if ai_hits else row[
            "caught_by_rules"]
        row["caught_by_hybrid_any_run"] = sum(k in rule_hits or k in any_ai for k in ks)
        rows.append(row)
    df = pd.DataFrame(rows)
    total = {"error": "All", "type": "", "planted": int(df["planted"].sum())}
    for c in df.columns[3:]:
        total[c] = int(df[c].sum())
    df = pd.concat([df, pd.DataFrame([total])], ignore_index=True)
    write(df, out, args.seed, args.ai_runs)


def write(df: pd.DataFrame, out: Path, seed: int, ai_runs: int) -> None:
    df.to_csv(out / "injection_coverage.csv", index=False)

    md = ["# Injection coverage demonstration (T5)", "",
          f"Injection version {INJECTION_VERSION}, seed {seed}; 300-row extract of Company A goods-received "
          f"tables in a temporary folder (nothing stored). Rules run once; AI run {ai_runs} times on the same "
          "data. A coverage demonstration with a known answer key, not a recall estimate on real data.", ""]
    cols = list(df.columns)
    md += ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    md += ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)]
    (out / "injection_coverage.md").write_text("\n".join(md), encoding="utf-8")

    plot = df[df["error"] != "All"]
    fig, ax = plt.subplots(figsize=(7.5, 3.6), dpi=300)
    x = range(len(plot))
    ax.bar([i - 0.27 for i in x], 100 * plot["caught_by_rules"] / plot["planted"], 0.27, label="Rules",
           color="#444444", edgecolor="black")
    ax.bar([i for i in x], 100 * plot["caught_by_ai_any_run"] / plot["planted"], 0.27,
           label=f"AI (any of {ai_runs} runs)", color="#bbbbbb", edgecolor="black", hatch="//")
    ax.bar([i + 0.27 for i in x], 100 * plot["caught_by_hybrid_run1"] / plot["planted"], 0.27,
           label="Hybrid (one run)", color="white", edgecolor="black")
    ax.set_xticks(list(x), plot["error"], fontsize=7, rotation=20, ha="right")
    ax.set_ylabel("Planted errors caught (%)")
    ax.set_ylim(0, 105)
    ax.set_title("Planted-error coverage by detector")
    ax.legend(fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(out / "fig_injection_coverage.png")
    plt.close(fig)
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
