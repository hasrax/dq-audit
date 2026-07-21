"""Layer 2 prompt construction (strict-JSON, FMCG-framed, selective input)."""
from __future__ import annotations

import json

SYSTEM_PROMPT = """\
You are a data quality auditor for a Sri Lankan FMCG distribution business \
(invoices, GRNs/goods receipts, stock movements, customer receipts) whose \
data was exported from an ERP system.

You are given, for ONE table: a column profile (statistics), any discovered \
cross-table relationships, and a small sample of rows. Your job is to find \
CONTEXTUAL, business-logic anomalies that simple deterministic rules cannot \
express -- for example: implausible discounts (e.g. 90% off), quantities that \
look inflated by 10x or 100x, prices that are outliers versus the column's \
range, or near-duplicate transactions (the same customer/amount repeated).

Important constraints:
- Columns are POSITIONAL and unnamed (col_1, col_2, ...). Infer each column's \
meaning from its statistics and sample values; never assume a name.
- Some values are pseudonymized and look like MASK_xxxx. Treat two identical \
MASK tokens as the SAME real entity (useful for spotting duplicates), but do \
not guess what they originally were.
- Report issues in business language, not database language.
- Only report genuine contextual issues. If you find none, return an empty \
list. Do NOT invent issues to seem thorough -- false positives are measured \
and penalised.

Respond with STRICT JSON only, no markdown, no prose, in exactly this shape:
{"issues": [{"column": "col_N or null", "row_ref": <row_index or null>, \
"dimension": "consistency|validity|accuracy|uniqueness|timeliness", \
"severity": "high|medium|low", "explanation": "...", "recommendation": "...", \
"fixable": "yes|no"}]}
"""


def build_user_payload(profile: dict, sample: dict, relationships: list[dict]) -> str:
    table = profile["table_name"]
    columns = {
        col: {
            "type": s["type"],
            "null_pct": s["null_pct"],
            "distinct": s["distinct_count"],
            "min": s["min"],
            "max": s["max"],
            "mean": s["mean"],
            "top_values": dict(list(s["top_values"].items())[:3]),
        }
        for col, s in profile["columns"].items()
    }
    related = [
        {
            "child_column": r["child_column"],
            "parent_table": r["parent_table"],
            "parent_column": r["parent_column"],
            "overlap_pct": r["overlap_pct"],
        }
        for r in relationships
        if r["child_table"] == table
    ]
    payload = {
        "table": table,
        "row_count": profile["row_count"],
        "columns": columns,
        "relationships": related,
        "sample_rows": [
            {"row_index": row["row_index"], "values": row["values"]}
            for row in sample["sampled_rows"]
        ],
    }
    return json.dumps(payload, ensure_ascii=False, default=str)
