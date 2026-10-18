"""Chapter 5 tables from the repository as it stands (T3, T6, T8, T10, T11).

Usage (from backend/):
    python -m app.eval.thesis_tables [--out ../evaluation/results]

Writes, each as CSV + Markdown (and a figure where noted):
  precision        T3  accepted / (accepted + rejected) per company and detector, Wilson 95% CI;
                       Unsure reported separately; also by dimension  (+ fig_precision.png)
  reproducibility  T6  per company: tables, columns, rows, rules generated / violated,
                       issues per detector, top dimensions, DQ score, manual configuration
  complementarity  T8  locations (table + dimension + column) found by rules only / AI only /
                       both, latest audit per company; rejected issues excluded, and a variant with them
  projection_error T10 settled projections (flat histories excluded)
  schema_docs      T11 AI-drafted column descriptions: verified unedited / edited / unverified
Latest audit per real company; study, test and replay copies never count.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from app import workspaces as ws  # noqa: E402
from app.aliases import client_label  # noqa: E402
from app.db import repository  # noqa: E402
from app.eval.agreement import wilson  # noqa: E402
from app.eval.complementarity import real_complementarity  # noqa: E402
from app.eval.live import projection_error, schema_doc_accuracy  # noqa: E402
from app.privacy.pseudonymize import load_overrides  # noqa: E402

OUT_DEFAULT = Path(__file__).resolve().parents[3] / "evaluation" / "results"
DETECTOR = {"rule": "Rules", "llm": "AI"}


def companies(conn) -> list[dict]:
    ws.refresh_names(conn)
    out = []
    for w in ws.list_workspaces(conn, kinds=("real",)):
        if w["slug"] in ws.DEMO_ONLY:   # demonstration data, never evidence
            continue
        uploads = repository.list_uploads(conn, source_client=w["slug"])
        if uploads:
            out.append({"slug": w["slug"], "label": client_label(w["slug"]), "local_only": w.get("local_only"),
                        "upload": max(uploads, key=lambda u: u["id"])})
    return sorted(out, key=lambda c: c["label"])


def _md(df: pd.DataFrame) -> str:
    if df.empty:
        return "_none_\n"
    cols = list(df.columns)
    rows = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    rows += ["| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join(rows) + "\n"


def _pct(x):
    return None if x is None else round(100 * x, 1)


def precision_rows(issues_by_company: dict[str, list[dict]]) -> list[dict]:
    rows = []
    for label, issues in issues_by_company.items():
        for src in ("rule", "llm"):
            st = Counter(i["status"] for i in issues if i["source"] == src)
            acc, rej = st.get("accepted", 0), st.get("rejected", 0)
            lo, hi = wilson(acc, acc + rej)
            rows.append({"company": label, "detector": DETECTOR[src], "issues": sum(st.values()),
                         "accepted": acc, "rejected": rej, "unsure": st.get("considered", 0),
                         "undecided": st.get("flagged", 0),
                         "precision_pct": _pct(acc / (acc + rej)) if acc + rej else None,
                         "ci95_low": _pct(lo) if acc + rej else None, "ci95_high": _pct(hi) if acc + rej else None})
    return rows


def fig_precision(df: pd.DataFrame, path: Path) -> None:
    d = df[df["precision_pct"].notna()]
    if d.empty:
        return
    fig, ax = plt.subplots(figsize=(7, 3.4), dpi=300)
    labels = [f"{c}\n{det}" for c, det in zip(d["company"], d["detector"])]
    y = d["precision_pct"].tolist()
    err = [[p - lo for p, lo in zip(y, d["ci95_low"])], [hi - p for p, hi in zip(y, d["ci95_high"])]]
    colors = ["#555555" if det == "Rules" else "#bbbbbb" for det in d["detector"]]
    ax.bar(range(len(y)), y, color=colors, edgecolor="black", yerr=err, capsize=4)
    ax.set_xticks(range(len(y)), labels, fontsize=7)
    ax.set_ylim(0, 105)
    ax.set_ylabel("Precision (%)")
    ax.set_title("Precision by company and detector (Wilson 95% CI)")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    conn = repository.connect()
    comps = companies(conn)
    issues = {c["label"]: repository.fetch_issues(conn, c["upload"]["id"]) for c in comps}

    # T3 precision
    prec = pd.DataFrame(precision_rows(issues))
    prec.to_csv(out / "precision.csv", index=False)
    by_dim = []
    for label, iss in issues.items():
        for (src, dim), g in pd.DataFrame(iss).groupby(["source", "dimension"]):
            acc, rej = int((g["status"] == "accepted").sum()), int((g["status"] == "rejected").sum())
            if acc + rej:
                by_dim.append({"company": label, "detector": DETECTOR[src], "dimension": dim,
                               "accepted": acc, "rejected": rej, "precision_pct": _pct(acc / (acc + rej))})
    fig_precision(prec, out / "fig_precision.png")
    (out / "precision.md").write_text(
        "# Precision (T3)\n\nprecision = accepted / (accepted + rejected); Unsure and undecided issues are "
        "not in the ratio. Latest audit per company.\n\n" + _md(prec) + "\n## By dimension (decided only)\n\n"
        + _md(pd.DataFrame(by_dim)), encoding="utf-8")

    # T6 reproducibility
    rep = []
    for c in comps:
        uid = c["upload"]["id"]
        r = conn.execute("SELECT count(*), count(*) FILTER (WHERE violation_count > 0) FROM rules WHERE upload_id = %s",
                         (uid,)).fetchone()
        t = conn.execute("SELECT count(DISTINCT table_name), count(*) FROM profiles WHERE upload_id = %s",
                         (uid,)).fetchone()
        s = conn.execute("SELECT overall_score FROM dq_scores WHERE upload_id = %s ORDER BY id DESC LIMIT 1",
                         (uid,)).fetchone()
        iss = issues[c["label"]]
        dims = Counter(i["dimension"] for i in iss).most_common(3)
        privacy = load_overrides(c["slug"])
        rep.append({"company": c["label"], "audit": f"#{uid}", "hosted": "no (local only)" if c["local_only"] else "yes",
                    "tables": t[0], "columns": t[1], "rows": f"{c['upload']['row_count']:,}",
                    "rules_generated": r[0] or "", "rules_violated": r[1] or "",
                    "rule_issues": sum(i["source"] == "rule" for i in iss),
                    "ai_issues": sum(i["source"] == "llm" for i in iss),
                    "top_dimensions": ", ".join(f"{d} ({n})" for d, n in dims),
                    "dq_score": float(s[0]) if s else None, "code_changes": "none",
                    "configuration": (f"privacy list ({sum(len(v.get('mask', [])) for v in privacy.values())} "
                                      "columns)") if privacy else "none"})
    rep = pd.DataFrame(rep)
    rep.to_csv(out / "reproducibility.csv", index=False)
    (out / "reproducibility.md").write_text(
        "# Reproducibility and transferability (T6)\n\nSame code for every company; latest audit each.\n\n"
        + _md(rep), encoding="utf-8")

    # T8 complementarity
    comp = []
    for label, iss in issues.items():
        for variant, subset in (("rejected excluded", iss),
                                ("rejected included", [{**i, "status": "flagged"} for i in iss])):
            res = real_complementarity(subset)
            o = res["overall"]
            comp.append({"company": label, "variant": variant, "locations": res["n_locations"],
                         "rules_only": o["rule_only"], "ai_only": o["llm_only"], "both": o["both"],
                         "rules_only_pct": o["rule_only_pct"], "ai_only_pct": o["llm_only_pct"],
                         "both_pct": o["both_pct"]})
    comp = pd.DataFrame(comp)
    comp.to_csv(out / "complementarity.csv", index=False)
    (out / "complementarity.md").write_text(
        "# Complementarity (T8)\n\nA location = table + dimension + column. Latest audit per company.\n\n"
        + _md(comp), encoding="utf-8")

    # T10 projection error
    pe = projection_error(conn)
    flat = conn.execute(
        "SELECT count(*) FROM issue_projections p JOIN uploads u ON u.id = p.upload_id "
        "WHERE p.actual_value IS NOT NULL AND p.slope = 0 AND u.source_client IN "
        "(SELECT slug FROM workspaces WHERE kind IN ('real', 'replay')) AND u.source_client <> ALL(%s)",
        (list(ws.DEMO_ONLY),)).fetchone()[0]
    (out / "projection_error.md").write_text(
        "# Projection error (T10)\n\nProjected vs actual affected rows for settled projections; flat "
        f"histories (slope 0) are excluded: **{flat}** excluded.\n\n" + _md(pd.DataFrame([pe])), encoding="utf-8")
    pd.DataFrame([{**pe, "flat_excluded": flat}]).to_csv(out / "projection_error.csv", index=False)

    # T11 schema docs
    sd = []
    for c in comps:
        row = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE confidence = 'VERIFIED'), "
            "count(*) FILTER (WHERE confidence = 'VERIFIED' AND edited_before_verify = FALSE), "
            "count(*) FILTER (WHERE confidence = 'VERIFIED' AND edited_before_verify) "
            "FROM schema_doc_columns WHERE source_client = %s", (c["slug"],)).fetchone()
        if row[0]:
            sd.append({"company": c["label"], "columns_documented": row[0], "verified": row[1],
                       "verified_unedited": row[2], "verified_edited": row[3], "unverified": row[0] - row[1]})
    sd = pd.DataFrame(sd)
    acc = pd.DataFrame(schema_doc_accuracy(conn))
    sd.to_csv(out / "schema_docs.csv", index=False)
    (out / "schema_docs.md").write_text("# Schema-description accuracy (T11)\n\n" + _md(sd)
                                        + "\n## Verified drafts by the AI's confidence\n\n" + _md(acc), encoding="utf-8")
    print(_md(prec))
    print(_md(rep[["company", "tables", "rows", "rules_generated", "rule_issues", "ai_issues", "configuration"]]))
    print(_md(comp[comp["variant"] == "rejected excluded"]))
    print("projection error:", pe, "| flat excluded:", flat)
    print(_md(sd))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
