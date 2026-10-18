"""Build the monthly demo packs for the demonstration (outside the framework; reads exports only).

Reads data/demo-company/<YYYY-MM>/ (the monthly exports, never modified) and writes
data/demo-company/prepared/:
  history-<month>/   April-June, copied as exported (file names normalised)
  pack1-2026-07/     July with planted issues  (tested before submission)
  pack2-2026-08/     August with planted issues (kept for demo day)
  pack3-2026-09/     September with planted issues (kept for demo day)
Each pack has ANSWER_KEY.md: what was planted, where, and what should find it.
The changes are deterministic (fixed seed), so a pack can be rebuilt identically.

Usage (from the project root):  python tools/build_demo_packs.py
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "demo-company"
OUT = SRC / "prepared"
TABLES = ["InvoiceHeader", "InvoiceDetails", "SalesReturnHeader", "SalesReturnDTL", "ReceiptsHed",
          "CustomerMaster", "ItemMaster", "SalesRepMaster"]
HISTORY = ["2026-04", "2026-05", "2026-06"]
SEED = 20261018


def load_month(month: str) -> dict[str, pd.DataFrame]:
    folder = SRC / month
    out = {}
    for t in TABLES:
        # tolerate small typos in the exported file names (e.g. "CustomerMaste.csv")
        path = folder / f"{t}.csv"
        if not path.exists():
            path = next(p for p in folder.glob("*.csv") if t.lower().startswith(p.stem.lower()))
        out[t] = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    return out


def save(tables: dict[str, pd.DataFrame], folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for t, df in tables.items():
        df.to_csv(folder / f"{t}.csv", index=False, encoding="utf-8")


def num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def fmt(x: float) -> str:
    """Money as the export writes it (two decimals), so the column keeps its number type."""
    return f"{float(x):.2f}"


def set_line_amount(d: pd.DataFrame, i) -> None:
    """Amount = Quantity x Price - Discount (how this system stores a line)."""
    d.loc[i, "Amount"] = fmt(float(d.loc[i, "Quantity"]) * float(d.loc[i, "Price"]) - float(d.loc[i, "Discount"]))


class Pack:
    def __init__(self, month: str, name: str):
        self.month, self.name = month, name
        self.t = load_month(month)
        self.rng = np.random.default_rng(SEED + int(month.replace("-", "")))
        self.key: list[tuple[str, str, str, str]] = []   # (problem, where, rows, expected finder)

    def pick(self, table: str, n: int, where=None) -> list:
        df = self.t[table]
        idx = df.index[where(df)] if where is not None else df.index
        return list(self.rng.choice(idx, size=n, replace=False))

    def note(self, problem, table, column, ids, finder):
        self.key.append((problem, f"{table} · {column}", ", ".join(str(x) for x in ids), finder))

    # --- planted problems -------------------------------------------------------------
    def negative_quantity(self, n):
        d = self.t["InvoiceDetails"]
        rows = self.pick("InvoiceDetails", n, lambda df: num(df, "Quantity") >= 2)
        for i in rows:
            d.loc[i, "Quantity"] = str(-int(float(d.loc[i, "Quantity"])))
            set_line_amount(d, i)
        self.note("Quantity below zero on an invoice line (a return typed as a sale)", "InvoiceDetails", "Quantity",
                  [f"invoice {d.loc[i, 'ReceiptComputerCode']}, item {d.loc[i, 'ItemCode']}" for i in rows],
                  "Rule: values below zero")

    def unknown_customer(self, n):
        h = self.t["InvoiceHeader"]
        rows = self.pick("InvoiceHeader", n)
        for k, i in enumerate(rows):
            h.loc[i, "CustomerCode"] = f"{9900 + k + 1:<5}"   # same padded number format as real codes
        self.note("Invoice for a customer that is not in the customer list", "InvoiceHeader", "CustomerCode",
                  [f"invoice {h.loc[i, 'ReceiptComputerCode']} (customer {h.loc[i, 'CustomerCode']})" for i in rows],
                  "Rule: broken link to CustomerMaster")

    def repeated_invoice_number(self, n):
        h = self.t["InvoiceHeader"]
        rows = self.pick("InvoiceHeader", 2 * n)
        pairs = list(zip(rows[::2], rows[1::2]))
        for a, b in pairs:
            h.loc[b, "ReceiptsNo"] = h.loc[a, "ReceiptsNo"]
        self.note("The same invoice number used on two different invoices", "InvoiceHeader", "ReceiptsNo",
                  [f"{h.loc[a, 'ReceiptsNo']} on invoices {h.loc[a, 'ReceiptComputerCode']} and "
                   f"{h.loc[b, 'ReceiptComputerCode']}" for a, b in pairs], "Rule: repeated values in a unique code")

    def future_date(self, table, col, key_col, label):
        df = self.t[table]
        (i,) = self.pick(table, 1)
        old = df.loc[i, col]
        df.loc[i, col] = old.replace(self.month[:4], str(int(self.month[:4]) + 1), 1)
        self.note(f"{label} dated a year in the future (typing mistake in the year)", table, col,
                  [f"{key_col} {df.loc[i, key_col]}: {df.loc[i, col][:10]}"], "Rule: dates in the future")

    def zero_price(self, n):
        d = self.t["InvoiceDetails"]
        rows = self.pick("InvoiceDetails", n, lambda df: num(df, "Discount") == 0)
        for i in rows:
            d.loc[i, "Price"] = "0.00"
            set_line_amount(d, i)
        self.note("Item sold at a price of zero", "InvoiceDetails", "Price",
                  [f"invoice {d.loc[i, 'ReceiptComputerCode']}, item {d.loc[i, 'ItemCode']}" for i in rows],
                  "AI (contextual)")

    def unknown_item(self, n):
        d = self.t["InvoiceDetails"]
        rows = self.pick("InvoiceDetails", n)
        for k, i in enumerate(rows):
            d.loc[i, "ItemCode"] = f"ZZ{9000 + k + 1}"   # looks like a real item code, but is not in the item list
        self.note("Invoice line for an item that is not in the item list", "InvoiceDetails", "ItemCode",
                  [f"invoice {d.loc[i, 'ReceiptComputerCode']}, item {d.loc[i, 'ItemCode']}" for i in rows],
                  "Rule: broken link to ItemMaster")

    def discount_above_value(self, n):
        d = self.t["InvoiceDetails"]
        rows = self.pick("InvoiceDetails", n, lambda df: num(df, "Discount") > 0)
        for i in rows:
            gross = float(d.loc[i, "Quantity"]) * float(d.loc[i, "Price"])
            d.loc[i, "Discount"] = fmt(round(gross * 1.5, 2))
            d.loc[i, "DisPer"] = "150"   # DisPer is a whole number in this system
            set_line_amount(d, i)
        self.note("Discount larger than the value of the line (150%), so the line amount is negative",
                  "InvoiceDetails", "Discount / DisPer / Amount",
                  [f"invoice {d.loc[i, 'ReceiptComputerCode']}, item {d.loc[i, 'ItemCode']}" for i in rows],
                  "Rule: values below zero (Amount); AI (discount over 100%)")

    def quantity_x100(self, n):
        d = self.t["InvoiceDetails"]
        rows = self.pick("InvoiceDetails", n, lambda df: num(df, "Quantity").between(1, 5))
        for i in rows:
            d.loc[i, "Quantity"] = str(int(float(d.loc[i, "Quantity"])) * 100)
            set_line_amount(d, i)
        self.note("Quantity 100 times too large (two extra zeros typed)", "InvoiceDetails", "Quantity",
                  [f"invoice {d.loc[i, 'ReceiptComputerCode']}, item {d.loc[i, 'ItemCode']}: "
                   f"{d.loc[i, 'Quantity']}" for i in rows], "AI (contextual)")

    def duplicate_rows(self, table, n, key_col, label):
        df = self.t[table]
        rows = self.pick(table, n)
        self.t[table] = pd.concat([df, df.loc[rows]], ignore_index=True)
        self.note(f"{label} entered twice (an exact copy of the row)", table, "whole row",
                  [f"{key_col} {df.loc[i, key_col]}" for i in rows], "Rule: exact duplicate rows")

    def price_x50(self, n):
        d = self.t["InvoiceDetails"]
        rows = self.pick("InvoiceDetails", n, lambda df: num(df, "Discount") == 0)
        for i in rows:
            d.loc[i, "Price"] = fmt(float(d.loc[i, "Price"]) * 50)
            set_line_amount(d, i)
        self.note("Price 50 times the usual price for the item", "InvoiceDetails", "Price",
                  [f"invoice {d.loc[i, 'ReceiptComputerCode']}, item {d.loc[i, 'ItemCode']}: {d.loc[i, 'Price']}"
                   for i in rows], "AI (contextual)")

    def negative_return(self, n):
        s = self.t["SalesReturnDTL"]
        rows = self.pick("SalesReturnDTL", n, lambda df: num(df, "ReturnedQuantity") >= 1)
        for i in rows:
            s.loc[i, "ReturnedQuantity"] = str(-int(float(s.loc[i, "ReturnedQuantity"])))
        self.note("Returned quantity below zero", "SalesReturnDTL", "ReturnedQuantity",
                  [f"return {s.loc[i, 'SRNumber']}, item {s.loc[i, 'ItemCode']}" for i in rows],
                  "Rule: values below zero")

    def write(self):
        folder = OUT / self.name
        if folder.exists():
            shutil.rmtree(folder)
        save(self.t, folder)
        lines = [f"# {self.name}: answer key", "",
                 f"Built from the {self.month} export with `tools/build_demo_packs.py` (seed {SEED}). "
                 "Import it in the app with **Data as of** = the last day of the month.", "",
                 "| # | Planted problem | Where | Rows | Should be found by |", "|---|---|---|---|---|"]
        lines += [f"| {k} | {p} | {w} | {r} | {f} |" for k, (p, w, r, f) in enumerate(self.key, start=1)]
        lines += ["", "Everything else in this month is as exported (the real data was clean on these points)."]
        (folder / "ANSWER_KEY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{self.name}: {len(self.key)} planted problems")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for month in HISTORY:
        folder = OUT / f"history-{month}"
        if folder.exists():
            shutil.rmtree(folder)
        save(load_month(month), folder)
        print(f"history-{month}: copied as exported")

    p = Pack("2026-07", "pack1-2026-07")
    p.negative_quantity(3)
    p.unknown_customer(2)
    p.repeated_invoice_number(2)
    p.future_date("InvoiceHeader", "ReceiptDate", "ReceiptComputerCode", "An invoice")
    p.zero_price(2)
    p.write()

    p = Pack("2026-08", "pack2-2026-08")
    p.unknown_item(3)
    p.discount_above_value(2)
    p.quantity_x100(2)
    p.duplicate_rows("InvoiceDetails", 2, "ReceiptComputerCode", "An invoice line")
    p.future_date("ReceiptsHed", "ReceiptDate", "ReceiptNumber", "A customer payment")
    p.write()

    p = Pack("2026-09", "pack3-2026-09")
    p.price_x50(2)
    p.unknown_customer(1)
    p.negative_return(2)
    p.duplicate_rows("InvoiceHeader", 1, "ReceiptComputerCode", "An invoice")
    p.write()


if __name__ == "__main__":
    main()
