"""Rule instances: the concrete checks the engine derived from a profile.

A rule *template* (e.g. cross_table_fk_orphan) is a check type; a rule
*instance* is that template bound to one table/column with its
parameters, e.g. "GRNDtl.col_1 must exist in GRNHED.col_1". Every check
module records an instance for everything it evaluated -- including
checks that found nothing -- so the Rules page can show what was
checked, not only what failed. Each issue carries its instance's
rule_key, which persistence turns into issues.rule_ref.
"""
from __future__ import annotations


def rule_key(template: str, table_name: str, column_name: str | None) -> str:
    return f"{template}:{table_name}:{column_name or '*'}"


def record_rule(
    rules: list[dict] | None,
    template: str,
    category: str,
    table_name: str,
    column_name: str | None,
    parameters: dict,
    evidence: dict,
    violation_count: int,
    key_detail: str | None = None,
) -> str:
    """Append a rule instance to `rules` (when collecting) and return its key.

    key_detail disambiguates instances on the same column (e.g. one child
    column linked to two different parent tables)."""
    key = rule_key(template, table_name, column_name)
    if key_detail:
        key += f":{key_detail}"
    if rules is not None:
        rules.append({
            "rule_key": key,
            "template": template,
            "category": category,
            "table_name": table_name,
            "column_name": column_name,
            "parameters": parameters,
            "evidence": evidence,
            "violation_count": int(violation_count),
        })
    return key
