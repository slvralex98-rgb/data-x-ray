from __future__ import annotations

import csv
from pathlib import Path
import random
from datetime import date, timedelta


def demo_rows() -> list[dict]:
    rng = random.Random(42)
    rows = []
    for i in range(1, 121):
        rows.append({"record_id": f"R-{i:04d}", "batch": str((i - 1) // 10 + 1),
            "position": str((i - 1) % 10 + 1), "segment": ["Alpha", "Beta", "Gamma"][i % 3],
            "amount": str(round(rng.uniform(80, 130), 2)),
            "event_date": (date(2025, 1, 1) + timedelta(days=i)).isoformat(),
            "reference": f"REF-{i:05d}", "comment": "ok"})
    rows[3]["amount"] = "89000"
    rows[6]["amount"] = "twelve"
    rows[9]["segment"] = " Alpha "
    rows[13]["segment"] = "alpha"
    rows[19]["event_date"] = "2025-02-30"
    rows[24]["event_date"] = "15.03.2025"
    rows[29]["event_date"] = "2099-01-01"
    rows[34]["comment"] = "\u200bhidden"
    rows[39]["comment"] = "Unexpected long free text " * 15
    rows[44]["amount"] = ""
    rows[49]["segment"] = "NULL"
    rows[54]["reference"] = "???"
    rows[59]["reference"] = "N/A"
    rows[64]["segment"] = "   "
    rows.append(dict(rows[0]))
    return rows


def create_demo(folder: str | Path, all_formats: bool = True) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rows = demo_rows()
    path = folder / "demo.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    if all_formats:
        import polars as pl
        from openpyxl import Workbook
        pl.DataFrame(rows).write_parquet(folder / "demo.parquet")
        book = Workbook()
        ws = book.active
        ws.title = "Demo"
        ws.append(list(rows[0]))
        for row in rows:
            ws.append(list(row.values()))
        book.save(folder / "demo.xlsx")
        book.close()
    return path
