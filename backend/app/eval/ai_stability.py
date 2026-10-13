"""T7 AI run-to-run stability (and T2 timing) on one company's data, in scratch mode.

Usage (from backend/):
    python -m app.eval.ai_stability [--company hemas] [--runs 3] [--out ../evaluation/results]

Runs the AI auditor several times on the same input (same masked profile and
sample, same prompt, temperature 0) WITHOUT saving any issue, and the rules
twice as a control. Findings are matched across runs: same table + column +
dimension, then description similarity >= 0.8 (difflib ratio). Reports the
count per run, pairwise Jaccard similarity, and how many findings appear in
all / some / one run. Rules are deterministic, so their Jaccard must be 1.0.
Also records the wall-clock time of each run (input to T2).
"""
from __future__ import annotations

import argparse
import itertools
import json
import time
from difflib import SequenceMatcher
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from app.llm.run_audit import audit_directory  # noqa: E402
from app.rules.engine import run_rules  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
OUT_DEFAULT = ROOT / "evaluation" / "results"
SIMILAR = 0.8


def same(a: dict, b: dict, strict: bool = True) -> bool:
    """Strict: same table + column + dimension AND similar description (>= 0.8).
    Loose: same table + column + dimension (the same problem, worded differently)."""
    key = lambda f: (f.get("table_name"), f.get("column_name") or "", f.get("dimension"))  # noqa: E731
    if key(a) != key(b):
        return False
    return not strict or SequenceMatcher(None, (a.get("description") or "").lower(),
                                         (b.get("description") or "").lower()).ratio() >= SIMILAR


def cluster(runs: list[list[dict]], strict: bool = True) -> list[set[int]]:
    """Group findings across runs into clusters of matching findings; each cluster
    records which runs contain it."""
    clusters: list[tuple[dict, set[int]]] = []
    for r, findings in enumerate(runs):
        for f in findings:
            for rep, members in clusters:
                if r not in members and same(rep, f, strict):
                    members.add(r)
                    break
            else:
                clusters.append((f, {r}))
    return [members for _, members in clusters]


def jaccard(clusters: list[set[int]], a: int, b: int) -> float:
    both = sum(1 for m in clusters if a in m and b in m)
    either = sum(1 for m in clusters if a in m or b in m)
    return both / either if either else 1.0


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--company", default="hemas", help="workspace slug (data folder data/<slug>)")
    ap.add_argument("--label", default="Company A")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args(argv)
    data = ROOT / "data" / args.company
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    runs, times = [], []
    for k in range(args.runs):
        t = time.time()
        found = audit_directory(data, source_client=args.company)
        times.append(round(time.time() - t, 1))
        runs.append([f for issues in found.values() for f in issues])
        print(f"AI run {k + 1}: {len(runs[-1])} findings in {times[-1]} s", flush=True)
    t = time.time()
    rules = [{i["rule_key"] for i in run_rules(data, source_client=args.company)["issues"]} for _ in range(2)]
    rule_time = round((time.time() - t) / 2, 1)

    (out / "ai_stability_runs.json").write_text(json.dumps(runs, indent=1, default=str), encoding="utf-8")
    loose = cluster(runs, strict=False)
    loose_j = [jaccard(loose, a, b) for a, b in itertools.combinations(range(args.runs), 2)]
    clusters = cluster(runs)
    pairs = [{"runs": f"{a + 1} vs {b + 1}", "jaccard": round(jaccard(clusters, a, b), 3)}
             for a, b in itertools.combinations(range(args.runs), 2)]
    presence = {f"in {n} of {args.runs} runs": sum(1 for m in clusters if len(m) == n)
                for n in range(args.runs, 0, -1)}
    rule_jaccard = len(rules[0] & rules[1]) / len(rules[0] | rules[1]) if rules[0] | rules[1] else 1.0
    summary = {"company": args.label, "runs": args.runs, "findings_per_run": [len(r) for r in runs],
               "distinct_findings": len(clusters), **presence,
               "mean_pairwise_jaccard": round(sum(p["jaccard"] for p in pairs) / len(pairs), 3),
               "loose_distinct_locations": len(loose),
               "loose_in_all_runs": sum(1 for m in loose if len(m) == args.runs),
               "loose_mean_pairwise_jaccard": round(sum(loose_j) / len(loose_j), 3),
               "rules_jaccard_control": round(rule_jaccard, 3), "ai_run_seconds": times,
               "rules_run_seconds": rule_time}
    pd.DataFrame(pairs).to_csv(out / "ai_stability_pairs.csv", index=False)
    pd.DataFrame([{k: (json.dumps(v) if isinstance(v, list) else v) for k, v in summary.items()}]).to_csv(
        out / "ai_stability.csv", index=False)
    lines = ["# AI run-to-run stability (T7)", "",
             f"{args.runs} AI runs on the same {args.label} input (temperature 0, nothing saved); rules run twice "
             "as the control. Match = same table + column + dimension and description similarity >= 0.8.", ""]
    lines += [f"- **{k.replace('_', ' ')}:** {v}" for k, v in summary.items()]
    lines += ["", "| runs | Jaccard |", "|---|---|"] + [f"| {p['runs']} | {p['jaccard']} |" for p in pairs]
    (out / "ai_stability.md").write_text("\n".join(lines), encoding="utf-8")

    fig, ax = plt.subplots(figsize=(5.5, 3.2), dpi=300)
    keys = list(presence)
    ax.bar(range(len(keys)), [presence[k] for k in keys], color="#777777", edgecolor="black")
    ax.set_xticks(range(len(keys)), keys, fontsize=8)
    ax.set_ylabel("Distinct AI findings")
    ax.set_title(f"How often each AI finding recurred ({args.label}, {args.runs} runs)")
    fig.tight_layout()
    fig.savefig(out / "fig_ai_stability.png")
    plt.close(fig)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
