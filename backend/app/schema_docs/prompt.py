"""Prompt for drafting schema documentation from a table profile.

One call per table. The model sees only the (pseudonymized) profile,
the table's discovered relationships, and the names of the other
tables -- never rows -- consistent with selective invocation.
"""
from __future__ import annotations

import json

MODULES = ("Sales", "Purchasing", "Inventory", "Finance", "Master data", "Other")
CONFIDENCES = ("HIGH", "MEDIUM", "LOW")
TOP_VALUES_PER_COLUMN = 3

SYSTEM_PROMPT = f"""\
You are documenting the database of a Sri Lankan FMCG distribution business \
(sales invoices, GRNs/goods receipts, stock, customers, suppliers) exported \
from an ERP system. Your draft will be checked by a person who knows the \
business, so be honest about uncertainty.

You are given ONE table's column profile (types, statistics, most common \
values), its relationships to other tables, and the names of the other \
tables. Many exports have no headers, so columns may be named col_1, col_2, \
...: infer each column's meaning from its statistics, values and \
relationships.

For the table and for EVERY column in the profile, write:
- description: one plain-English sentence a non-technical accountant or \
storekeeper would understand.
- display_name: a short human label (2-4 words), e.g. "Bill Date".
- expected_domain: what valid values look like (e.g. "positive amount in \
rupees", "date, not in the future", "one of A/C/D").
- business_rules: 0-3 short rules the values should obey.
- confidence: HIGH (statistics make the meaning clear), MEDIUM (plausible), \
LOW (guess). Prefer LOW over a confident wrong answer.

Use only the column names given. Do not add columns. Some values are \
pseudonymized (MASK_xxxx); do not guess their real values.

Respond with STRICT JSON only, no markdown, in exactly this shape:
{{"table_description": "...", "table_module": "{'|'.join(MODULES)}", \
"columns": [{{"column": "col_1", "display_name": "...", "description": "...", \
"expected_domain": "...", "business_rules": ["..."], \
"confidence": "{'|'.join(CONFIDENCES)}"}}]}}
"""


def build_doc_payload(profile: dict, relationships: list[dict], other_tables: list[str]) -> str:
    table = profile["table_name"]
    columns = {}
    for col, s in profile["columns"].items():
        columns[col] = {
            "type": s["type"],
            "null_pct": s["null_pct"],
            "distinct": s["distinct_count"],
            "min": s["min"],
            "max": s["max"],
            "mean": s["mean"],
            "top_values": dict(list(s["top_values"].items())[:TOP_VALUES_PER_COLUMN]),
        }
    links = [
        f"{r['child_table']}.{r['child_column']} -> {r['parent_table']}.{r['parent_column']} "
        f"({r['overlap_pct']}% overlap)"
        for r in relationships
        if table in (r["child_table"], r["parent_table"])
    ]
    payload = {
        "table": table,
        "row_count": profile["row_count"],
        "primary_key": profile.get("primary_key"),
        "columns": columns,
        "relationships": links,
        "other_tables": sorted(t for t in other_tables if t != table),
    }
    return json.dumps(payload, ensure_ascii=False, default=str)
