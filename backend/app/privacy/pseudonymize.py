"""Pseudonymization of sample rows before they leave the machine.

Mandatory for any cloud LLM endpoint (e.g. Groq): mask person/company
names, addresses, phone numbers, and free-text remarks, while KEEPING
amounts, dates, quantities, and code columns -- those are the audit
subject, not PII. Implemented as one testable, citable function per the
design brief. Local-only endpoints (Ollama) don't require it, but the
step is always available so the privacy guarantee is uniform.

Because the SeefaERP exports are headerless, PII columns can't be found
by name. A hybrid classifier is used (a locked project decision):

  auto-heuristic  - numeric/date columns are never masked (the audit
                    subject); low-distinct varchar is categorical/code
                    (kept); high-distinct varchar with whitespace, or
                    high-distinct pure-alphabetic text, is free-text PII
                    (masked); high-distinct varchar dominated by digits
                    is a structured code such as an invoice number
                    (kept).
  override        - an optional per-client mask_override / keep_override
                    settles the cases the heuristic provably can't:
                    phone-numbers-stored-as-int, national ID codes, and
                    geo-coordinates all look like "codes" to the
                    heuristic and MUST be listed in mask_override to be
                    protected. This is the intended, documented division
                    of labour, not a heuristic failure.

Masking is deterministic pseudonymization, not redaction: equal input
values map to the same token (within a salt), so the LLM can still spot
"the same customer billed twice" -- a Type 2 near-duplicate signal --
without seeing the real value. A fixed default salt keeps runs
reproducible for evaluation; a random per-run salt would harden privacy
further in production (documented trade-off).
"""
from __future__ import annotations

import hashlib

import pandas as pd

from app.profiler.types import TYPE_DATE, TYPE_DECIMAL, TYPE_INT, TYPE_UNKNOWN

DEFAULT_SALT = "dq-audit"
SAMPLE_SIZE = 1000
DISTINCT_RATIO_THRESHOLD = 0.10
SPACE_FRAC_THRESHOLD = 0.15
DIGIT_FRAC_THRESHOLD = 0.50

_KEEP_TYPES = (TYPE_INT, TYPE_DECIMAL, TYPE_DATE, TYPE_UNKNOWN)


def _looks_like_pii(df: pd.DataFrame, col: str, stats: dict) -> bool:
    """Heuristic: is this varchar column free-text PII (mask) or a code (keep)?"""
    non_null = df[col].dropna().astype(str)
    if non_null.empty:
        return False

    distinct_ratio = stats["distinct_count"] / len(non_null)
    if distinct_ratio < DISTINCT_RATIO_THRESHOLD:
        return False  # categorical / status code

    sample = non_null.head(SAMPLE_SIZE)
    frac_space = sample.str.contains(" ").mean()
    frac_digit = sample.str.contains(r"[0-9]").mean()

    if frac_space >= SPACE_FRAC_THRESHOLD:
        return True  # multi-word free text: names, addresses, remarks
    if frac_digit >= DIGIT_FRAC_THRESHOLD:
        return False  # structured code: invoice numbers, paths, refs
    return True  # high-distinct pure-alphabetic: name-like


def classify_pii_columns(
    df: pd.DataFrame,
    profile: dict,
    mask_override: set[str] | None = None,
    keep_override: set[str] | None = None,
) -> dict[str, bool]:
    """Return {column: should_mask}. Overrides win over the heuristic;
    mask_override takes precedence over keep_override (privacy-first)."""
    mask_override = mask_override or set()
    keep_override = keep_override or set()

    decisions: dict[str, bool] = {}
    for col, stats in profile["columns"].items():
        if stats["type"] in _KEEP_TYPES:
            decisions[col] = False
        else:
            decisions[col] = _looks_like_pii(df, col, stats)

    for col in keep_override:
        if col in decisions:
            decisions[col] = False
    for col in mask_override:  # applied last: privacy-first
        if col in decisions:
            decisions[col] = True
    return decisions


def mask_value(value: str, salt: str = DEFAULT_SALT) -> str:
    """Deterministic pseudonym for a single value."""
    digest = hashlib.sha1(f"{salt}:{value}".encode("utf-8")).hexdigest()[:8]
    return f"MASK_{digest}"


def pseudonymize_profile(
    profile: dict,
    pii_columns: dict[str, bool],
    salt: str = DEFAULT_SALT,
) -> dict:
    """Mask PII that leaks through a profile's column statistics.

    A column's min/max and top_values are real cell values, so for a
    masked (PII) column they must be pseudonymized before the profile
    reaches a cloud model -- otherwise a company name still travels as
    `min`/`max`/`top_values`. Numeric/date columns are never masked, so
    their statistics (the ones the LLM actually reasons over) pass
    through untouched. The same salt is used as pseudonymize_sample so a
    masked stat and a masked sample cell share the same token.
    """
    new_columns = {}
    for col, stats in profile["columns"].items():
        if not pii_columns.get(col, False):
            new_columns[col] = stats
            continue
        masked = dict(stats)
        masked["min"] = mask_value(str(stats["min"]), salt) if stats["min"] is not None else None
        masked["max"] = mask_value(str(stats["max"]), salt) if stats["max"] is not None else None
        masked["top_values"] = {mask_value(k, salt): v for k, v in stats["top_values"].items()}
        new_columns[col] = masked
    return {**profile, "columns": new_columns}


def pseudonymize_sample(
    sample: dict,
    pii_columns: dict[str, bool],
    salt: str = DEFAULT_SALT,
) -> dict:
    """Return a masked copy of a stratified sample.

    Non-null values in masked columns become stable pseudonyms; nulls
    stay null; kept columns pass through unchanged. The set of masked
    columns is recorded so the redaction is auditable and citable.
    """
    masked_columns = sorted(col for col, mask in pii_columns.items() if mask)

    masked_rows = []
    for row in sample["sampled_rows"]:
        new_values = {}
        for col, val in row["values"].items():
            if val is not None and pii_columns.get(col, False):
                new_values[col] = mask_value(val, salt)
            else:
                new_values[col] = val
        masked_rows.append({**row, "values": new_values})

    return {**sample, "sampled_rows": masked_rows, "masked_columns": masked_columns}
