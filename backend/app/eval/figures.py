"""Dissertation-ready figures for the evaluation (E2 complementarity).

Renders the rule-only / LLM-only / both split as a horizontal stacked bar
per dimension -- the headline complementarity finding, as a PNG for
Chapter 5. Uses the Agg backend (no display) so it runs headless. Colours
follow the app: rule = blue (Type 1), LLM = purple (Type 2), both = teal;
segment counts are drawn on the bars so the figure is not colour-alone.
"""
from __future__ import annotations

import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RULE_COLOR = "#2563eb"
LLM_COLOR = "#7c3aed"
BOTH_COLOR = "#0d9488"


def figure_png_bytes(by_dimension: dict[str, dict[str, int]], title: str) -> bytes:
    dims = list(by_dimension.keys())
    rule = [by_dimension[d].get("rule_only", 0) for d in dims]
    llm = [by_dimension[d].get("llm_only", 0) for d in dims]
    both = [by_dimension[d].get("both", 0) for d in dims]

    fig, ax = plt.subplots(figsize=(8, max(2.5, 0.62 * len(dims) + 1.2)))
    y = range(len(dims))
    ax.barh(y, rule, color=RULE_COLOR, label="Rule only (Type 1)")
    ax.barh(y, llm, left=rule, color=LLM_COLOR, label="LLM only (Type 2)")
    ax.barh(y, both, left=[r + l for r, l in zip(rule, llm)], color=BOTH_COLOR, label="Both")

    for i, (r, l, b) in enumerate(zip(rule, llm, both)):
        if r:
            ax.text(r / 2, i, str(r), ha="center", va="center", color="white", fontsize=8)
        if l:
            ax.text(r + l / 2, i, str(l), ha="center", va="center", color="white", fontsize=8)
        if b:
            ax.text(r + l + b / 2, i, str(b), ha="center", va="center", color="white", fontsize=8)

    ax.set_yticks(list(y))
    ax.set_yticklabels(dims)
    ax.set_xlabel("Issue locations")
    ax.set_title(title)
    ax.legend(loc="upper right", frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    return buf.getvalue()


def save_complementarity_figure(by_dimension: dict, out_path: Path | str, title: str) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(figure_png_bytes(by_dimension, title))
    return out_path


def main(argv: list[str] | None = None) -> None:
    """CLI: python -m app.eval.figures [client]  (default hemas)"""
    import sys

    from app.db import repository
    from app.eval.complementarity import real_complementarity

    argv = argv if argv is not None else sys.argv[1:]
    client = argv[0] if argv else "hemas"

    conn = repository.connect()
    upload = next((u for u in repository.list_uploads(conn) if u["source_client"] == client), None)
    if upload is None:
        raise SystemExit(f"no persisted upload for client {client!r}")

    comp = real_complementarity(repository.fetch_issues(conn, upload["id"]))
    out = save_complementarity_figure(
        comp["by_dimension"],
        Path("output") / "eval" / f"complementarity_{client}.png",
        title=f"Rule–LLM complementarity — {client} (upload #{upload['id']})",
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
