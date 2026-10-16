"""End-to-end injection evaluation (E1 + E2) on a real Company A extract.

Builds a cleaned baseline from real GRNHED (parent) + GRNDtl (child),
injects seeded errors across all six dimensions and the Type 2 classes,
writes the injected extracts to a temp directory, and runs the REAL
Layer 1 rules engine and Layer 2 LLM auditor on them -- so the detectors
under test are exactly the production ones. Then matches detections to
the ground-truth labels and reports precision/recall/F1 + complementarity.

Usage:
    python -m app.eval.run_eval [--no-llm] [--seed N]
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from statistics import mean, pstdev

import numpy as np

from app.eval import baseline
from app.eval import injection as inj
from app.eval.injection import INJECTION_VERSION
from app.eval.metrics import evaluate
from app.io.loader import load_csv
from app.llm.run_audit import audit_directory
from app.rules.engine import run_rules

DEFAULT_SEED = 20260722
EXTRACT_ROWS = 300
COMPANY_A_DIR = Path(__file__).resolve().parents[3] / "data" / "hemas"

# Column roles in GRNHED (positional, from Layer 0 profiling).
# col_1 is deliberately left CLEAN: it is the parent key GRNDtl references,
# so corrupting its uniqueness would stop the FK being discovered and break
# the cross_table test. col_3/col_4 are NOT clamped: clamping collapses their
# distinct values below the ordering miner's floor, hiding the consistency
# injection. Timeliness cleaning therefore targets only the other date columns.
GRNHED_TIMELINESS_CLEAN_COLS = ["col_11", "col_12", "col_16"]
GRNHED_DECIMAL_COLS = ["col_7", "col_8", "col_13"]

# (injector, kwargs, count) -- one entry per injected error class.
INJECTION_PLAN = [
    ("nulls", dict(column="col_2"), 25),           # completeness (>5% of 300)
    ("negatives", dict(column="col_7"), 10),        # validity
    ("duplicate_pk", dict(column="col_5"), 12),     # uniqueness (near-key, keeps col_1 clean)
    ("future_dates", dict(column="col_11"), 10),    # timeliness
    ("date_inversion", dict(lo_col="col_3", hi_col="col_4"), 4),  # consistency (<2%)
    ("price_outlier", dict(column="col_13"), 5),    # validity / Type 2
    ("quantity_magnitude", dict(column="col_10"), 5),  # Type 2
]


def build_scenario(data_dir: Path, seed: int):
    rng = np.random.default_rng(seed)

    parent = baseline.subset(load_csv(data_dir / "GRNHED.csv"), EXTRACT_ROWS, rng)
    baseline.make_all_non_null(parent)
    baseline.make_non_negative(parent, GRNHED_DECIMAL_COLS)
    baseline.clamp_dates(parent, GRNHED_TIMELINESS_CLEAN_COLS)
    baseline.order_dates(parent, "col_3", "col_4")

    parent_keys = set(parent["col_1"])
    child_pool = load_csv(data_dir / "GRNDtl.csv")
    child_pool = child_pool[child_pool["col_1"].isin(parent_keys)].reset_index(drop=True)
    child = baseline.subset(child_pool, EXTRACT_ROWS, rng)
    baseline.make_all_non_null(child)

    labels = []
    for name, kwargs, count in INJECTION_PLAN:
        fn = getattr(inj, f"inject_{name}")
        labels += fn(parent, "GRNHED", count=count, rng=rng, **kwargs)
    # cross_table + a Type 2 near-duplicate live on the child so the parent
    # key (col_1) stays unique and the FK relationship is still discovered.
    # Orphan count is kept small: too many distinct orphan values would pull
    # the child->parent value overlap below the 95% relationship-discovery
    # threshold, so the FK (and thus the orphan check) would never be found.
    labels += inj.inject_fk_orphans(child, "GRNDtl", "col_1", parent_keys, 5, rng)
    labels += inj.inject_near_duplicate(child, "GRNDtl", 5, rng)

    return {"GRNHED": parent, "GRNDtl": child}, labels


def run(data_dir: Path = COMPANY_A_DIR, with_llm: bool = True, seed: int = DEFAULT_SEED) -> dict:
    tables, labels = build_scenario(data_dir, seed)

    tmp = Path(tempfile.mkdtemp(prefix="dq_eval_"))
    for name, df in tables.items():
        df.to_csv(tmp / f"{name}.csv", header=False, index=False)

    issues = list(run_rules(tmp)["issues"])
    if with_llm:
        for table_issues in audit_directory(tmp).values():
            issues += table_issues

    m = evaluate(labels, issues)
    return {
        "injection_version": INJECTION_VERSION,
        "seed": seed,
        "with_llm": with_llm,
        "extract_rows": EXTRACT_ROWS,
        "n_injected": len(labels),
        "n_detections": len(issues),
        "metrics": m,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


DEFAULT_SEEDS = (20260722, 101, 202, 303, 404)


def _aggregate(values: list) -> dict:
    vals = [v for v in values if v is not None]
    if not vals:
        return {"mean": None, "std": None, "min": None, "max": None, "n": 0}
    return {
        "mean": round(mean(vals), 4),
        "std": round(pstdev(vals), 4),
        "min": round(min(vals), 4),
        "max": round(max(vals), 4),
        "n": len(vals),
    }


def run_multiseed(data_dir: Path = COMPANY_A_DIR, seeds=DEFAULT_SEEDS, with_llm: bool = False) -> dict:
    """Run the injection eval across seeds and aggregate the key metrics.

    Defaults to rules-only (deterministic, free) so robustness reflects the
    detectors, not LLM sampling noise; pass with_llm=True to include the
    combined figure at token cost.
    """
    runs = [run(data_dir, with_llm=with_llm, seed=s)["metrics"] for s in seeds]
    dims = sorted(runs[0]["recall_by_dimension"].keys())
    return {
        "seeds": list(seeds),
        "with_llm": with_llm,
        "aggregate": {
            "combined_recall": _aggregate([m["combined_recall"] for m in runs]),
            "type1_recall": _aggregate([m["recall_by_type"]["type1"] for m in runs]),
            "rule_precision": _aggregate([m["by_source"]["rule"]["precision"] for m in runs]),
            "recall_by_dimension": {
                d: _aggregate([m["recall_by_dimension"].get(d) for m in runs]) for d in dims
            },
        },
        "per_seed": [
            {"seed": s, "combined_recall": m["combined_recall"], "type1_recall": m["recall_by_type"]["type1"]}
            for s, m in zip(seeds, runs)
        ],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _report_markdown(report: dict) -> str:
    m = report["metrics"]
    lines = [
        "# Injection Evaluation (E1/E2)",
        "",
        f"- Injection version: `{report['injection_version']}` · seed: `{report['seed']}` "
        f"· LLM: {report['with_llm']} · extract rows: {report['extract_rows']}",
        f"- Injected errors: **{report['n_injected']}** · detections: **{report['n_detections']}**",
        f"- **Combined recall: {m['combined_recall']}** (target >= 0.90)",
        "",
        "## Per-source (precision / recall / F1)",
        "",
        "| Source | Recall (target type) | Precision (in-scope) | F1 |",
        "|---|---|---|---|",
    ]
    for src, s in m["by_source"].items():
        lines.append(f"| {src} | {s['recall']} | {s['precision']} | {s['f1']} |")
    lines += ["", "## Recall by dimension", "", "| Dimension | Recall |", "|---|---|"]
    for dim, r in m["recall_by_dimension"].items():
        lines.append(f"| {dim} | {r} |")
    c = m["complementarity"]
    lines += [
        "", "## Complementarity (E2)", "",
        f"- Rule-only: **{c['rule_only']}** ({c['rule_only_pct']}%)",
        f"- LLM-only: **{c['llm_only']}** ({c['llm_only_pct']}%)",
        f"- Both: **{c['both']}** ({c['both_pct']}%)",
        f"- Missed: **{c['missed']}** ({c['missed_pct']}%)",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    with_llm = "--no-llm" not in argv
    seed = DEFAULT_SEED
    for a in argv:
        if a.startswith("--seed"):
            seed = int(a.split("=")[1]) if "=" in a else DEFAULT_SEED

    if "--multiseed" in argv:
        report = run_multiseed(with_llm=with_llm)
        out_dir = Path("output") / "eval"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "robustness.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        agg = report["aggregate"]
        print(f"Multi-seed robustness over {len(report['seeds'])} seeds (LLM={report['with_llm']}):")
        print(f"  Type 1 recall: mean {agg['type1_recall']['mean']} +/- {agg['type1_recall']['std']} "
              f"(min {agg['type1_recall']['min']}, max {agg['type1_recall']['max']})")
        print(f"  Combined recall: mean {agg['combined_recall']['mean']} +/- {agg['combined_recall']['std']}")
        print(f"  Rule precision: mean {agg['rule_precision']['mean']} +/- {agg['rule_precision']['std']}")
        print(f"wrote {out_dir}/robustness.json")
        return

    report = run(with_llm=with_llm, seed=seed)

    out_dir = Path("output") / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "injection_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    md = _report_markdown(report)
    (out_dir / "injection_report.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"\nwrote {out_dir}/injection_report.json and .md")


if __name__ == "__main__":
    main()
