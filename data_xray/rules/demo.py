"""Reproducible training and changed-load examples: python -m data_xray.rules.demo."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl


def create_rule_demo(directory: str | Path = "examples") -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    records = []
    regions = {"hardware": "north", "software": "west", "service": "south"}
    for i in range(150):
        category = list(regions)[i % 3]
        status = "closed" if i % 2 == 0 else "open"
        start = date(2026, 1, 1) + timedelta(days=i % 30)
        end = start + timedelta(days=2 + i % 5)
        records.append({"order_id": f"{i+1:06d}", "category": category, "region": regions[category],
                        "status": status, "closed_at": end.isoformat() if status == "closed" else "",
                        "start_date": start.isoformat(), "end_date": end.isoformat(),
                        "amount": str(100 + (i % 21) * 5), "contact": f"person{i:03d}@example.org"})
    records[6]["region"] = "east"                 # 1-based record 7: FD violation
    records[10]["closed_at"] = ""                # record 11: conditional required
    records[14]["end_date"] = "2025-12-31"        # record 15: inverted dates
    records[18]["amount"] = "9900"               # record 19: robust range
    records[22]["contact"] = "invalid-contact"    # record 23: email syntax
    records[26]["category"] = "special"           # record 27: rare determinant, no repeat support
    frame = pl.DataFrame(records)
    target = directory / "rule_miner_demo.csv"
    frame.write_csv(target)
    frame.write_parquet(directory / "rule_miner_demo.parquet")
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Orders"
    sheet.append(frame.columns)
    for row in frame.iter_rows():
        sheet.append(row)
    workbook.save(directory / "rule_miner_demo.xlsx")
    changed = [dict(row) for row in records]
    changed[0].update(category="new_category", region="unknown", amount="8000", contact="bad")
    changed[2]["closed_at"] = "NULL"
    changed[4]["start_date"] = "invalid-date"
    changed[8]["order_id"] = ""
    changed[12]["end_date"] = "2025-01-01"
    pl.DataFrame(changed).write_csv(directory / "rule_miner_changed.csv")
    return target


if __name__ == "__main__":
    print(create_rule_demo().resolve())
