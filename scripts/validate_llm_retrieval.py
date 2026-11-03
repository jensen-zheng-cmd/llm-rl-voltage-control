"""Compare frozen nearest-day retrieval against no retrieval on validation days."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
from statistics import mean

import numpy as np

from run_llm_planning import (
    FORECAST_SEED, MODEL, PROVIDER, Dataset, VoltageControlEnv, closest, feedback, forecast,
    generate, save, score,
)

METRICS = ("reward", "mad_pu", "violation_pct", "min_pu", "max_pu")


def evaluate_arm(key, env, day, prediction, directory, reference=None):
    directory.mkdir()
    payload = {"forecast": prediction}
    if reference is not None:
        payload["similar_day"] = {"forecast": reference["forecast"], **feedback(reference)}
    for retry in range(3):
        attempt = directory / f"attempt_{retry}"
        attempt.mkdir()
        try:
            plan = generate(key, payload, attempt)
            break
        except ValueError as error:
            if retry == 2:
                raise
            payload["format_correction"] = {
                "error": str(error),
                "previous_output": json.loads(
                    (attempt / "response.json").read_text(encoding="utf-8"))["content"],
                "instruction": "Correct the invalid schedule and return the full JSON plan.",
            }
    metrics = score(env, day, plan)
    save(directory / "scored.json", {"plan": plan, "metrics": metrics})
    return {metric: metrics[metric] for metric in METRICS}


def summarize(rows):
    summary = {}
    for group, selected in (
        ("all", rows),
        ("similarity_at_least_0.7", [r for r in rows if r["similarity"] >= .7]),
        ("similarity_below_0.7", [r for r in rows if r["similarity"] < .7]),
    ):
        if not selected:
            continue
        stats = {"days": len(selected)}
        for arm in ("with", "without"):
            stats[arm] = {m: mean(r[f"{arm}_{m}"] for r in selected) for m in METRICS}
            stats[arm]["worst_min_pu"] = min(r[f"{arm}_min_pu"] for r in selected)
            stats[arm]["worst_max_pu"] = max(r[f"{arm}_max_pu"] for r in selected)
        for metric in ("mad_pu", "violation_pct"):
            delta = [r[f"with_{metric}"] - r[f"without_{metric}"] for r in selected]
            improved = sum(d < -1e-10 for d in delta)
            worsened = sum(d > 1e-10 for d in delta)
            stats[f"paired_{metric}"] = {"mean_delta": mean(delta), "improved": improved,
                "worsened": worsened, "tied": len(delta) - improved - worsened}
        summary[group] = stats
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--pool", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    key = args.key_file.read_text(encoding="utf-8-sig").strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError("Key file must contain one API key")
    dataset = Dataset()
    env = VoltageControlEnv(dataset)
    pool_bytes = args.pool.read_bytes()
    pool = json.loads(pool_bytes)
    start, end = dataset.runtime["splits"]["train"]
    if not pool or any(not start <= r["forecast"]["date"] < end for r in pool):
        raise ValueError("Expected a nonempty pool of training dates")
    start, end = map(np.datetime64, dataset.runtime["splits"]["validation"])
    days = np.arange(start, end, dtype="datetime64[D]").astype(str)
    args.output.mkdir(parents=True)
    (args.output / "frozen_pool.json").write_bytes(pool_bytes)
    save(args.output / "config.json", {
        "provider": PROVIDER, "model": MODEL, "forecast_seed": FORECAST_SEED, "dates": days.tolist(),
        "source_pool_sha256": hashlib.sha256(pool_bytes).hexdigest(), "pool_size": len(pool),
        "pv_q_fraction": 0, "reflexion_rounds": 0, "order": "alternate by date",
        "similarity_reporting_threshold": .7, "retrieval": "nearest even below threshold",
    })
    rows = []
    with (args.output / "pairs.csv").open("w", newline="", encoding="utf-8") as table:
        fields = ["date", "reference_date", "similarity"] + [
            f"{arm}_{metric}" for arm in ("with", "without") for metric in METRICS]
        writer = csv.DictWriter(table, fieldnames=fields)
        writer.writeheader()
        for i, day in enumerate(days):
            directory = args.output / day
            directory.mkdir()
            prediction = forecast(dataset, day)
            save(directory / "forecast.json", prediction)
            index, sim = closest(pool, prediction)
            reference = pool[index]
            row = {"date": day, "reference_date": reference["forecast"]["date"], "similarity": sim}
            for arm in (("with", "without") if i % 2 == 0 else ("without", "with")):
                metrics = evaluate_arm(key, env, day, prediction, directory / arm,
                                       reference if arm == "with" else None)
                row.update({f"{arm}_{k}": v for k, v in metrics.items()})
            rows.append(row)
            writer.writerow(row)
            table.flush()
            print(f"{i + 1}/{len(days)} {day} similarity={sim:.3f} "
                  f"MAD without={row['without_mad_pu']:.6f} with={row['with_mad_pu']:.6f}", flush=True)
    save(args.output / "summary.json", summarize(rows))


if __name__ == "__main__":
    main()
