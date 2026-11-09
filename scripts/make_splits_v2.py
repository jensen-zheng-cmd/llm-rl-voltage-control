"""Write the two 2013 date splits used by configuration v2."""
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
YEAR = 2013


def half_month_cycles():
    days = np.arange(np.datetime64(f"{YEAR}-01-01"), np.datetime64(f"{YEAR + 1}-01-01"), dtype="datetime64[D]")
    cycles = {}
    for day in days:
        month = str(day)[:7]
        half = 0 if int(str(day)[8:10]) <= 15 else 1
        cycles.setdefault((month, half), []).append(str(day))
    return list(cycles.values())


def split_a():
    # Each half-month cycle: train, 1 buffer, 2 validation, 2 test, 1 buffer.
    # Buffers separate held-out days from adjacent training days.
    groups = {"train": [], "validation": [], "test": [], "buffer": []}
    for cycle in half_month_cycles():
        n = len(cycle) - 6
        groups["train"] += cycle[:n]
        groups["buffer"].append(cycle[n])
        groups["validation"] += cycle[n + 1:n + 3]
        groups["test"] += cycle[n + 3:n + 5]
        groups["buffer"].append(cycle[n + 5])
    return groups


def split_b():
    days = np.arange(np.datetime64(f"{YEAR}-01-01"), np.datetime64(f"{YEAR + 1}-01-01"), dtype="datetime64[D]")
    groups = {"train": [], "validation": [], "test": [], "buffer": []}
    for i, day in enumerate(days):
        groups[("train", "train", "train", "train", "validation", "test")[i % 6]].append(str(day))
    return groups


def main():
    splits = {"A_half_month": split_a(), "B_daily_4_1_1": split_b()}
    for name, groups in splits.items():
        dates = sum(groups.values(), [])
        if len(dates) != 365 or len(set(dates)) != 365:
            raise ValueError(f"{name} must partition all 365 days exactly once")
    record = {
        "status": "frozen_v2",
        "year": YEAR,
        "rules": {
            "A_half_month": "cycles 1-15 and 16-end of each month; first len-6 days train, then 1 buffer, 2 validation, 2 test, 1 buffer",
            "B_daily_4_1_1": "6-day cycle from 2013-01-01: 4 train, 1 validation, 1 test; no buffer; used only to measure adjacent-day leakage in LLM retrieval",
        },
        "counts": {name: {k: len(v) for k, v in g.items()} for name, g in splits.items()},
        "splits": splits,
    }
    path = ROOT / "configs/splits_v2.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(record["counts"]))


if __name__ == "__main__":
    main()
