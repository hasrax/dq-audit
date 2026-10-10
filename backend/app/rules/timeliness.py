"""Timeliness rule: max_date > today+30d (future) or max_date < today-2y (stale).

Both conditions key off the column's max_date, per the locked spec --
this is a freshness check on the column as a whole, not a per-row
outlier scan. A future max_date usually means at least one bad
data-entry date; a stale max_date means the column (and maybe the
whole export) hasn't been updated recently, or is a frozen/sentinel
value (e.g. GRNHED's SystemDate column, permanently 2019-04-19).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from app.rules.registry import record_rule

FUTURE_RULE_ID = "timeliness_future_date"
STALE_RULE_ID = "timeliness_stale_column"
MAX_SAMPLE_ROWS = 5
FUTURE_DAYS = 30
STALE_YEARS = 2


def check_timeliness(
    table_name: str,
    df: pd.DataFrame,
    profile: dict,
    reference_date: datetime | None = None,
    future_days: int = FUTURE_DAYS,
    stale_years: int = STALE_YEARS,
    rules: list[dict] | None = None,
) -> list[dict]:
    reference_date = reference_date or datetime.now()
    future_cutoff = reference_date + timedelta(days=future_days)
    stale_cutoff = reference_date - timedelta(days=365 * stale_years)

    issues = []
    for col, stats in profile["columns"].items():
        if stats["type"] != "date" or stats["max"] is None:
            continue

        max_date = pd.to_datetime(stats["max"])
        dates = None  # lazily parsed only if needed
        evidence = {"max_date": str(max_date.date()), "reference_date": str(reference_date.date())}
        future_count = stale_count = 0

        if max_date > future_cutoff:
            dates = pd.to_datetime(df[col], errors="coerce")
            future_mask = dates > future_cutoff
            count = future_count = int(future_mask.sum())
            sample_rows = df.index[future_mask][:MAX_SAMPLE_ROWS].tolist()
            issues.append(
                {
                    "table_name": table_name,
                    "column_name": col,
                    "dimension": "timeliness",
                    "source": "rule",
                    "error_type": "type1",
                    "severity": "high",
                    "description": (
                        f"{col} has dates in the future: {count:,} rows are later than "
                        f"{future_cutoff.date()}, the latest being {max_date.date()}."
                    ),
                    "recommendation": "Check these dates for typing mistakes, such as a wrong year.",
                    "fixable": "no",
                    "status": "flagged",
                    "row_reference": {"count": count, "sample_rows": sample_rows},
                    "rule_id": FUTURE_RULE_ID,
                    "rule_key": f"{FUTURE_RULE_ID}:{table_name}:{col}",
                }
            )

        if max_date < stale_cutoff:
            if dates is None:
                dates = pd.to_datetime(df[col], errors="coerce")
            most_recent_mask = dates == max_date
            sample_rows = df.index[most_recent_mask][:MAX_SAMPLE_ROWS].tolist()
            non_null_count = stale_count = profile["row_count"] - stats["null_count"]
            issues.append(
                {
                    "table_name": table_name,
                    "column_name": col,
                    "dimension": "timeliness",
                    "source": "rule",
                    "error_type": "type1",
                    "severity": "medium",
                    "description": (
                        f"The latest {col} is {max_date.date()}, more than {stale_years} years ago, "
                        f"so this field may no longer be filled in, or may hold a placeholder date."
                    ),
                    "recommendation": "Check whether this field is still used. If not, it may be an old field that can be ignored.",
                    "fixable": "no",
                    "status": "flagged",
                    "row_reference": {"count": non_null_count, "sample_rows": sample_rows},
                    "rule_id": STALE_RULE_ID,
                    "rule_key": f"{STALE_RULE_ID}:{table_name}:{col}",
                }
            )

        record_rule(rules, FUTURE_RULE_ID, "timeliness", table_name, col,
                    parameters={"max_days_ahead": future_days}, evidence=evidence,
                    violation_count=future_count)
        record_rule(rules, STALE_RULE_ID, "timeliness", table_name, col,
                    parameters={"max_age_years": stale_years}, evidence=evidence,
                    violation_count=stale_count)
    return issues
