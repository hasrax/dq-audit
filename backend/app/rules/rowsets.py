"""Every affected row per rule issue -- collected only for evaluation runs.

An audit keeps a few example rows per issue (row_reference.sample_rows).
The recall check in the reviewer packs must know *every* row an issue
applies to, so ``run_rules(..., collect_rows=True)`` switches collection
on for one run and each row-level issue then carries ``"_rows"`` (row
positions). Column-level findings (a stale column) carry none. Nothing
here is stored in the database, and detection is unchanged.
"""
from __future__ import annotations

from contextlib import contextmanager

_collect = False


@contextmanager
def collecting(on: bool = True):
    global _collect
    previous, _collect = _collect, on
    try:
        yield
    finally:
        _collect = previous


def rows(index) -> dict:
    """``{"_rows": [...]}`` while collecting, else ``{}`` -- spread into an issue dict."""
    return {"_rows": [int(i) for i in index]} if _collect else {}


def carry(found: dict) -> dict:
    """Pass ``_rows`` from a mined relationship on to the issue built from it."""
    return {"_rows": found["_rows"]} if "_rows" in found else {}
