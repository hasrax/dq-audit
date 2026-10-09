"""Database backup -> CSV exports (data preparation, outside the framework).

The audit framework stays export-based: it only ever reads CSV/Excel. For
organisations that gave a database backup instead of exports, restore the
backup on a local database server and use this tool to produce exports.
It connects with a read-only intent and only runs SELECT statements, so
the restored database is never modified.

Two steps:
  1. Inventory (nothing is exported): every table with its row and column
     counts, its newest date, and a guess of its role, written to
     inventory.csv for the researcher to confirm.
         python tools/backup_to_csv.py --server . --database RMS --inventory --out data/company_d
  2. Export the confirmed tables (UTF-8 CSV with real column names):
         python tools/backup_to_csv.py --server . --database RMS --out data/company_d/2026-10-06 \\
             --tables Orders,OrderItems,...

Requires pyodbc and "ODBC Driver 17 for SQL Server" (see tools/requirements-tools.txt).
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import pyodbc

# Name patterns for the role guess (the researcher confirms the final list).
SKIP_PATTERNS = re.compile(r"(^sys|log$|_log|logs?_|audit|^temp|_temp|^tmp|_tmp|backup|_bak|_old$|old$|"
                           r"^z_|error|session|migration|sysdiagram|_deleted)", re.I)
MASTER_PATTERNS = re.compile(r"(master|customer|supplier|vendor|employee|staff|item|product|menu|category|"
                             r"unit|type|location|branch|table|user|setting|config|tax|bank|account$)", re.I)
TXN_PATTERNS = re.compile(r"(invoice|order|bill|sale|payment|receipt|transaction|txn|kot|grn|purchase|stock|"
                          r"detail|header|line|movement|return|journal|ledger)", re.I)


def connect(server: str, database: str) -> pyodbc.Connection:
    conn = pyodbc.connect(
        f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={server};DATABASE={database};"
        "Trusted_Connection=yes;ApplicationIntent=ReadOnly;TrustServerCertificate=yes",
        autocommit=True, readonly=True,
    )
    return conn


def guess_role(table: str, rows: int) -> str:
    if rows == 0:
        return "empty"
    if SKIP_PATTERNS.search(table):
        return "system/log"
    if TXN_PATTERNS.search(table):
        return "transaction"
    if MASTER_PATTERNS.search(table):
        return "master"
    return "other"


def inventory(conn: pyodbc.Connection) -> list[dict]:
    cur = conn.cursor()
    tables = cur.execute(
        "SELECT s.name, t.name, SUM(p.rows), "
        "  (SELECT COUNT(*) FROM sys.columns c WHERE c.object_id = t.object_id) "
        "FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id "
        "JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1) "
        "WHERE t.is_ms_shipped = 0 GROUP BY s.name, t.name, t.object_id ORDER BY SUM(p.rows) DESC"
    ).fetchall()
    out = []
    for schema, table, rows, cols in tables:
        date_cols = [r[0] for r in cur.execute(
            "SELECT c.name FROM sys.columns c JOIN sys.types ty ON ty.user_type_id = c.user_type_id "
            "WHERE c.object_id = OBJECT_ID(?) AND ty.name IN ('date','datetime','datetime2','smalldatetime')",
            f"[{schema}].[{table}]").fetchall()]
        newest = None
        if date_cols and rows:
            col = date_cols[0]
            newest = cur.execute(f"SELECT MAX([{col}]) FROM [{schema}].[{table}]").fetchone()[0]
        out.append({"schema": schema, "table": table, "rows": int(rows or 0), "columns": int(cols),
                    "date_columns": ";".join(date_cols), "newest_date": newest and str(newest)[:10],
                    "role_guess": guess_role(table, int(rows or 0))})
    return out


def export_table(conn: pyodbc.Connection, schema: str, table: str, path: Path, batch: int = 20000) -> int:
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM [{schema}].[{table}]")
    header = [d[0] for d in cur.description]
    n = 0
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                break
            writer.writerows(["" if v is None else v for v in r] for r in rows)
            n += len(rows)
    return n


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default=".")
    ap.add_argument("--database", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--inventory", action="store_true", help="list tables only; export nothing")
    ap.add_argument("--tables", default="", help="comma-separated tables to export (schema.table or table)")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    conn = connect(args.server, args.database)
    inv = inventory(conn)

    if args.inventory or not args.tables:
        with (out / "inventory.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(inv[0]))
            writer.writeheader()
            writer.writerows(inv)
        print(f"{'table':38} {'rows':>9} {'cols':>5}  {'newest':10}  role guess")
        for r in inv:
            print(f"{(r['schema'] + '.' + r['table'])[:38]:38} {r['rows']:>9,} {r['columns']:>5}  "
                  f"{r['newest_date'] or '':10}  {r['role_guess']}")
        print(f"\n{len(inv)} tables, {sum(r['rows'] for r in inv):,} rows -> {out / 'inventory.csv'}")
        return

    wanted = [t.strip() for t in args.tables.split(",") if t.strip()]
    by_name = {r["table"].lower(): r for r in inv} | {f"{r['schema']}.{r['table']}".lower(): r for r in inv}
    missing = [t for t in wanted if t.lower() not in by_name]
    if missing:
        sys.exit(f"not found: {', '.join(missing)}")
    for t in wanted:
        r = by_name[t.lower()]
        n = export_table(conn, r["schema"], r["table"], out / f"{r['table']}.csv")
        print(f"exported {r['table']}: {n:,} rows")


if __name__ == "__main__":
    main()
