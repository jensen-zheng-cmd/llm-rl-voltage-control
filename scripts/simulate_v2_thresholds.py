"""Offline v2 experience-pool threshold simulation; no API, PPO, or voltage scoring."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

from run_llm_planning import Dataset, FORECAST_SEED, forecast_v2, save, similarity
from run_llm_planning_v2 import ORDER_SEED, day_groups
from voltage_control.common import PROJECT_ROOT

THRESHOLDS = (0.70, 0.80, 0.85, 0.90)


def nearest(pool, prediction):
    if not pool:
        return None, 0.0
    values = [similarity(entry, prediction) for entry in pool]
    index = int(np.argmax(values))
    return index, float(values[index])


def distribution(values):
    values = np.asarray(values, dtype=float)
    return {"days": len(values), "mean": float(values.mean()),
            "median": float(np.median(values)), "p05": float(np.percentile(values, 5)),
            "min": float(values.min()), "max": float(values.max()),
            "coverage_at_0_70": float((values >= .70).mean()),
            "coverage_at_0_80": float((values >= .80).mean())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--order", choices=("chronological", "c2"), default="chronological",
                        help="c2: six seed days always added, then the 200 C2 formal days in their fixed order")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    dataset = Dataset(version="v2", split_scheme="A_half_month")
    dates = dataset.split_dates("train").astype(str).tolist()
    validation = set(dataset.split_dates("validation").astype(str))
    test = set(dataset.split_dates("test").astype(str))
    if len(dates) != 221 or set(dates) & (validation | test):
        raise ValueError("C1 must use exactly the 221 split-A training days")

    predictions = []
    regional, totals = [], []
    for i, day in enumerate(dates, 1):
        prediction = forecast_v2(dataset, day)
        predictions.append(prediction)
        regional.append([prediction["regions"][name]["net_load_mw"]
                         for name in dataset.system["regions_from_paper_topology"]])
        totals.append(prediction["net_load_mw"])
        if i % 50 == 0 or i == len(dates):
            print(f"Forecasts {i}/{len(dates)}", flush=True)

    # Training-only realized values identify the high-PV stratum. This definition is
    # the same daily-peak upper quintile used by the earlier fine-tuning day selection.
    peak_pv = np.asarray([dataset.day(day)["p_pv_mw"].sum(axis=1).max() for day in dates])
    high_pv = peak_pv >= np.quantile(peak_pv, .8)
    split_bytes = (PROJECT_ROOT / "configs/splits_v2.json").read_bytes()
    system_bytes = (PROJECT_ROOT / "configs/system_v2.json").read_bytes()
    sequence, forced = list(range(len(dates))), 0
    extra = {}
    if args.order == "c2":
        groups = day_groups(dataset)
        position = {day: i for i, day in enumerate(dates)}
        sequence = [position[day] for day in groups["seed"] + groups["formal"]]
        forced = len(groups["seed"])
        extra = {"order": "c2", "order_seed": ORDER_SEED,
                 "simulation": "six C2 seed days always added, then the 200 C2 formal days in fixed random order; "
                               "sim < threshold adds a record; otherwise no size change",
                 "sequence_days": len(sequence), "coverage_days": len(dates)}
    save(args.output / "config.json", {
        "version": "v2_c1", "split_scheme": "A_half_month", "scope": "train_only",
        "training_days": len(dates), "validation_days_used": 0, "test_days_used": 0,
        "thresholds": THRESHOLDS, "forecast_seed": FORECAST_SEED,
        "forecast": {
            "quantity": "hourly regional P_load minus P_PV and consistent sum over regions",
            "aggregation_before_noise": True,
            "noise": "independent multiplicative Gaussian, relative standard deviation 0.05",
            "signed_reverse_flow": True, "nodal_profiles_supplied_to_strategy": False,
        },
        "high_pv_definition": "true daily maximum of summed six-PV active power >= split-A training upper quintile",
        "high_pv_days": int(high_pv.sum()),
        "simulation": "chronological first-representative clustering; sim < threshold adds a record; otherwise no size change",
        "not_simulated": "reward-based record replacement, LLM calls, voltage effect, C2 final 200-day selection",
        "coverage": "retrospective nearest-neighbor similarity of each training forecast to the final proxy pool",
        "splits_sha256": hashlib.sha256(split_bytes).hexdigest(),
        "system_v2_sha256": hashlib.sha256(system_bytes).hexdigest(),
        **extra,
    })
    np.savez_compressed(args.output / "forecasts.npz", dates=np.asarray(dates),
                        regional_net_load_mw=np.asarray(regional), total_net_load_mw=np.asarray(totals),
                        high_pv=high_pv, daily_peak_pv_mw=peak_pv)

    trajectory, coverage_rows, summaries = [], [], []
    for threshold in THRESHOLDS:
        pool, pool_indices = [], []
        for step, position in enumerate(sequence, 1):
            day, prediction = dates[position], predictions[position]
            index, score = nearest(pool, prediction)
            action = "matched_existing" if index is not None and score >= threshold else "added"
            if step <= forced:
                action = "added"
            if action == "added":
                pool.append(prediction)
                pool_indices.append(position)
            trajectory.append({"threshold": threshold, "step": step, "date": day,
                               "nearest_similarity_before": score if index is not None else "",
                               "nearest_date_before": dates[pool_indices[index]] if index is not None else "",
                               "action": action, "pool_size_after": len(pool)})
        similarities, references = [], []
        for day, prediction in zip(dates, predictions):
            index, score = nearest(pool, prediction)
            similarities.append(score)
            references.append(pool[index]["date"])
            coverage_rows.append({"threshold": threshold, "date": day,
                                  "is_high_pv": bool(high_pv[len(similarities) - 1]),
                                  "nearest_final_pool_date": pool[index]["date"],
                                  "nearest_final_pool_similarity": score})
        similarities = np.asarray(similarities)
        entry = {"threshold": threshold, "final_pool_size": len(pool),
                 "matched_existing_days": len(sequence) - len(pool),
                 "high_pv_pool_entries": int(high_pv[pool_indices].sum()),
                 "all_training": distribution(similarities),
                 "high_pv_training": distribution(similarities[high_pv])}
        summaries.append(entry)
        save(args.output / f"threshold_{threshold:.2f}.json",
             {**entry, "pool_dates": [pool[i]["date"] for i in range(len(pool))]})
        print(f"threshold={threshold:.2f} pool={len(pool)} "
              f"high-PV coverage@0.70={entry['high_pv_training']['coverage_at_0_70']:.3f} "
              f"high-PV min similarity={entry['high_pv_training']['min']:.3f}", flush=True)

    for name, rows in (("update_trajectory.csv", trajectory), ("final_pool_coverage.csv", coverage_rows)):
        with (args.output / name).open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    save(args.output / "summary.json", {
        "training_days": len(dates), "high_pv_days": int(high_pv.sum()), "thresholds": summaries,
        "selection": "Report all four candidates; select two for C2 without using control outcomes or held-out dates",
    })
    print(json.dumps(summaries, indent=2), flush=True)


if __name__ == "__main__":
    main()
