"""Moderate reproducible CPU smoke check: python -m tools.benchmark_rule_miner."""
from datetime import date, timedelta
import json
import time

import polars as pl

from data_xray.loaders import Dataset
from data_xray.rules import MiningOptions, RuleMiner, evaluate_rules


def main():
    rows = 20_000
    start = date(2026, 1, 1)
    frame = pl.DataFrame({
        "id": [f"{i:06d}" for i in range(rows)],
        "category": [f"cat{i % 8}" for i in range(rows)],
        "region": [f"region{i % 4}" for i in range(rows)],
        "amount": [100 + i % 50 for i in range(rows)],
        "limit": [200 + i % 50 for i in range(rows)],
        "status": ["closed" if i % 2 else "open" for i in range(rows)],
        "start": [(start + timedelta(days=i % 30)).isoformat() for i in range(rows)],
        "end": [(start + timedelta(days=i % 30 + 3)).isoformat() for i in range(rows)],
    })
    dataset = Dataset(frame, "benchmark:20000x8", "parquet", False, [])
    before = time.perf_counter()
    mined = RuleMiner().mine(dataset, options=MiningOptions(max_pairs=12))
    discovery = time.perf_counter() - before
    before = time.perf_counter()
    checked = evaluate_rules(dataset, mined.rules)
    elapsed = time.perf_counter() - before
    print(json.dumps({"rows": rows, "columns": frame.width, "pair_budget": 12,
                      "rules": len(mined.rules), "discovery_seconds": round(discovery, 3),
                      "checking_seconds": round(elapsed, 3),
                      "not_applicable": sum(ev.status == "not_applicable" for ev in checked.evaluations)}))


if __name__ == "__main__":
    main()
