"""v2 stage E: combine the per-seed validation runs of all methods.

Each --seed-runs entry names one training seed and its three validation directories
(finetune_ppo_v2.py validate outputs): LLM plans with proposed/nopt (+ Original, No-RL,
No-LLM), No-Reflexion plans, and Pure-RL plans. Writes per-seed means, the across-seed
mean and sample standard deviation, day-level pairs averaged over seeds, and the ranking
of the methods in each seed.
"""
import argparse
import csv
import json
from pathlib import Path

import numpy as np

METRICS = ("mad_pu", "violation_pct", "min_pu", "max_pu")
# method -> (validation directory index, column prefix)
METHODS = {
    "original": (0, "original"), "no_rl": (0, "no_rl"), "no_pt": (0, "nopt_ep150"), "pure_rl": (2, "purerl_ep150"),
    "no_llm": (0, "no_llm"), "no_reflexion": (1, "norefl_ep150"), "proposed": (0, "proposed_ep150"),
}
DIAGNOSTIC = {"pretrained_unfinetuned": (0, "pretrained_ep000"), "dayahead_no_reflexion": (1, "no_rl"),
              "dayahead_pure_rl": (2, "no_rl")}


def read_pairs(directory):
    with (Path(directory) / "pairs.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def column(rows, prefix, metric):
    return np.array([float(r[f"{prefix}_{metric}"]) for r in rows])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed-runs", nargs=4, action="append", required=True,
                        metavar=("SEED", "LLM_VALIDATE", "NOREFL_VALIDATE", "PURERL_VALIDATE"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    labels = {**METHODS, **DIAGNOSTIC}
    data, dates = {}, None
    for seed, *dirs in args.seed_runs:
        tables = [read_pairs(d) for d in dirs]
        for t in tables:
            days = [r["date"] for r in t]
            if dates is None:
                dates = days
            if days != dates:
                raise ValueError(f"seed {seed}: validation dates differ between runs")
        data[seed] = {m: {k: column(tables[i], prefix, k) for k in METRICS} for m, (i, prefix) in labels.items()}
        # Original must not depend on the plan source; a mismatch means the runs are not comparable.
        for i in (1, 2):
            if not np.array_equal(column(tables[i], "original", "mad_pu"), data[seed]["original"]["mad_pu"]):
                raise ValueError(f"seed {seed}: Original differs between validation runs")
    seeds = list(data)
    per_seed = {s: {m: {"mad_pu": float(v["mad_pu"].mean()), "violation_pct": float(v["violation_pct"].mean()),
                        "violation_free_days": int((v["violation_pct"] == 0).sum()),
                        "min_pu": float(v["min_pu"].min()), "max_pu": float(v["max_pu"].max())}
                    for m, v in data[s].items()} for s in seeds}
    across = {}
    for m in labels:
        entry = {}
        for k in ("mad_pu", "violation_pct", "violation_free_days"):
            values = np.array([per_seed[s][m][k] for s in seeds], float)
            entry[k] = {"mean": float(values.mean()), "std": float(values.std(ddof=1)) if len(values) > 1 else None,
                        "per_seed": values.tolist()}
        across[m] = entry
    day_mean = {m: {k: np.mean([data[s][m][k] for s in seeds], axis=0) for k in ("mad_pu", "violation_pct")}
                for m in labels}
    paired = {}
    for other in [m for m in labels if m != "proposed"]:
        stats = {}
        for k in ("mad_pu", "violation_pct"):
            d = day_mean["proposed"][k] - day_mean[other][k]
            stats[k] = {"mean_delta": float(d.mean()), "standard_error": float(d.std(ddof=1) / np.sqrt(len(d))),
                        "lower": int((d < -1e-10).sum()), "higher": int((d > 1e-10).sum()),
                        "tied": int((np.abs(d) <= 1e-10).sum())}
        stats["seeds_proposed_lower_mad"] = int(sum(per_seed[s]["proposed"]["mad_pu"] < per_seed[s][other]["mad_pu"]
                                                    for s in seeds))
        paired[f"proposed_vs_{other}"] = stats
    ranking = {s: sorted(METHODS, key=lambda m: per_seed[s][m]["mad_pu"]) for s in seeds}
    args.output.mkdir(parents=True, exist_ok=True)
    summary = {"seeds": seeds, "days": len(dates), "sources": {s: d for s, *d in args.seed_runs},
               "methods": list(METHODS), "diagnostics": list(DIAGNOSTIC), "per_seed": per_seed,
               "across_seeds": across, "paired_day_means": paired, "ranking_by_mad": ranking,
               "ranking_identical": len({tuple(r) for r in ranking.values()}) == 1}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    with (args.output / "per_seed.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["seed", "method", "mad_pu", "violation_pct", "violation_free_days", "min_pu", "max_pu"])
        for s in seeds:
            for m in labels:
                r = per_seed[s][m]
                writer.writerow([s, m, r["mad_pu"], r["violation_pct"], r["violation_free_days"], r["min_pu"], r["max_pu"]])
    with (args.output / "day_means.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date"] + [f"{m}_{k}" for m in labels for k in ("mad_pu", "violation_pct")])
        for i, d in enumerate(dates):
            writer.writerow([d] + [day_mean[m][k][i] for m in labels for k in ("mad_pu", "violation_pct")])
    for m in labels:
        a = across[m]
        print(f"{m:24} MAD {a['mad_pu']['mean']:.6f} ± {a['mad_pu']['std'] or 0:.6f}  "
              f"viol {a['violation_pct']['mean']:.3f}% ± {a['violation_pct']['std'] or 0:.3f}  "
              f"per seed {' '.join(f'{x:.6f}' for x in a['mad_pu']['per_seed'])}")
    for s, r in ranking.items():
        print(f"seed {s}: {' < '.join(r)}")


if __name__ == "__main__":
    main()
