"""Layer 2 prompt construction (strict-JSON, industry-neutral, selective input).

The wording is deliberately industry-neutral and works with both named
and positional (col_N) columns: an earlier version assumed an FMCG
distribution business and unnamed columns, which produced wrong framing
on other systems (recorded as a transferability finding, T6)."""
from __future__ import annotations

import json

SYSTEM_PROMPT = """\
You are a data quality auditor for a Sri Lankan small or medium business \
whose data was exported from its business system (for example sales and \
invoices, purchases and goods receipts, stock, customers, orders, payments). \
Work out the kind of business from the table names, column names and values \
-- do not assume an industry.

You are given, for ONE table: a column profile (statistics), any discovered \
cross-table relationships, and a small sample of rows. Your job is to find \
CONTEXTUAL, business-logic anomalies that simple deterministic rules cannot \
express -- for example: implausible discounts (e.g. 90% off), quantities that \
look inflated by 10x or 100x, prices that are outliers versus the column's \
range, or near-duplicate transactions (the same customer/amount repeated).

Important constraints:
- Columns either have real names (e.g. InvoiceDate, Quantity) or, when the \
export had no header row, positional names (col_1, col_2, ...). Always refer \
to a column by EXACTLY the name used in the profile. For positional columns, \
infer the meaning from statistics and sample values.
- Some values are pseudonymized and look like MASK_xxxx. Treat two identical \
MASK tokens as the SAME real entity (useful for spotting duplicates), but do \
not guess what they originally were.
- Write "explanation" and "recommendation" for non-technical staff (cashiers, \
storekeepers, accountants, managers) in plain, everyday English: at most two \
short sentences each. Do not use technical words such as null, distinct, \
cardinality, outlier, foreign key, schema, identifier, token or mean; say \
"empty", "different values", "much higher than usual", "does not match any \
record in", "average". Name the column once, give one example value if it \
helps, and say briefly why it matters to the business. Never mention masked \
values or MASK tokens -- say "the same phone number" or "the same name" instead.
- Only report genuine contextual issues. If you find none, return an empty \
list. Do NOT invent issues to seem thorough -- false positives are measured \
and penalised.

Respond with STRICT JSON only, no markdown, no prose, in exactly this shape:
{"issues": [{"column": "col_N or null", "row_ref": <row_index or null>, \
"dimension": "consistency|validity|accuracy|uniqueness|timeliness", \
"severity": "high|medium|low", "explanation": "...", "recommendation": "...", \
"fixable": "yes|no"}]}
"""


# Cap the sample rows sent to the model, keeping the prompt within the design's
# ~6K-token budget (and within tight free-tier per-minute limits).
MAX_PROMPT_SAMPLE_ROWS = 10


def build_user_payload(profile: dict, sample: dict, relationships: list[dict]) -> str:
    table = profile["table_name"]
    columns = {}
    for col, s in profile["columns"].items():
        if s["null_pct"] >= 100:
            continue  # fully-null columns carry no signal — omit to save tokens
        entry = {
            "type": s["type"],
            "null_pct": s["null_pct"],
            "distinct": s["distinct_count"],
            "min": s["min"],
            "max": s["max"],
            "mean": s["mean"],
        }
        # top_values only help for low-cardinality columns; for high-cardinality
        # ones they are just unique values that bloat the prompt.
        if s["distinct_count"] <= 10:
            entry["top_values"] = dict(list(s["top_values"].items())[:3])
        columns[col] = entry
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
            for row in sample["sampled_rows"][:MAX_PROMPT_SAMPLE_ROWS]
        ],
    }
    return json.dumps(payload, ensure_ascii=False, default=str)
