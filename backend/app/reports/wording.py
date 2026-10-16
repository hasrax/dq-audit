"""Display wording (after the usability study).

Issue and rule texts are stored exactly as the detectors wrote them (older
audits use older wording); this rewrites them for people at display time:
company column names instead of col_N, plain words instead of "null" and
"<=", one decimal place, thousands separators and dates as 06 Oct 2026.
Detection, scoring and the stored text are unchanged.
"""
from __future__ import annotations

import re
from datetime import date

from app.aliases import named

MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
DATE_FORMAT = "%d %b %Y"     # one date style everywhere: 06 Oct 2026

_OLD_NULL = re.compile(r"(?P<col>[^.]+?) is (?P<pct>[\d.]+)% null \((?P<a>\d+) of (?P<b>\d+) rows\)")
_EVERY_ROW = re.compile(r"is empty in (?P<a>[\d,]+) of (?P<b>[\d,]+) rows \(100(?:\.0+)?%\)")
_BREAK = re.compile(r"but ([\d,]+) rows? break that (?:identity|order)")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_PCT = re.compile(r"(?<![\d.])(\d+\.\d+)%")
_WHOLE_DECIMAL = re.compile(r"(?<![\d.,])(-?\d+)\.0+\b(?!%)")
_BIG_INT = re.compile(rf"(?<![\w.,#/-])(?<!(?:{MONTHS}) )(-?\d{{4,}})(?![\d]|[.,]\d)")
_WORDS = [
    (re.compile(r"\bnon-null\b"), "filled"),
    (re.compile(r"\bnull\b"), "empty"),
    (re.compile(r"\s<=\s"), " not more than "),
    (re.compile(r"\s>=\s"), " not less than "),
    (re.compile(r"\s!=\s"), " is not "),
    (re.compile(r"(?<=\w) > (?=\w)"), " is more than "),
    (re.compile(r"(?<=\w) < (?=\w)"), " is less than "),
    (re.compile(r"(?<=\w) x (?=\w)"), " × "),
    (re.compile(r"\s--\s"), " — "),
]


def _pct(m: re.Match) -> str:
    v = float(m.group(1))
    if 99.95 <= v < 100:
        return "over 99.9%"
    if 0 < v < 0.05:
        return "under 0.1%"
    return f"{v:.1f}".removesuffix(".0") + "%"


def _iso(m: re.Match) -> str:
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime(DATE_FORMAT)
    except ValueError:
        return m.group(0)


def _big_int(m: re.Match) -> str:
    n = int(m.group(1))
    return m.group(1) if len(m.group(1)) == 4 and 1900 <= n <= 2100 else f"{n:,}"   # a bare year stays a year


def _every_row(m: re.Match) -> str:
    a, b = m.group("a"), m.group("b")
    return f"is empty in every row ({a} of {b})" if a == b else m.group(0)


def plain(text: str | None) -> str:
    """Rewrite one stored issue / rule text in plain English (see module docstring)."""
    if not text:
        return text or ""
    text = _OLD_NULL.sub(lambda m: f"{m.group('col')} is empty in {int(m.group('a')):,} of {int(m.group('b')):,} "
                                   f"rows ({m.group('pct')}%)", text)
    text = _BREAK.sub(lambda m: f"but in {m.group(1)} rows it does not", text)
    for rx, rep in _WORDS:
        text = rx.sub(rep, text)
    text = _ISO_DATE.sub(_iso, text)
    text = _WHOLE_DECIMAL.sub(lambda m: m.group(1), text)
    text = _BIG_INT.sub(_big_int, text)
    text = _EVERY_ROW.sub(_every_row, text)
    return _PCT.sub(_pct, text)


def show(text: str | None, table: str, aliases: dict) -> str:
    """Company column names (col_N -> name), then plain wording."""
    return plain(named(text, table, aliases))
