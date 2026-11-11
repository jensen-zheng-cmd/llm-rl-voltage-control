"""Combine the v2 validation runs into a Table III style comparison (48 validation days).

Proposed, No-RL, No-LLM, No-PT and Original come from the stage D validation;
Pure-RL and No-Reflexion from their own validation runs (same days, same scorer).
Paper values are Table III of the target paper (33-bus test set, mean of 3 seeds).
"""
import argparse
import csv
import json
from pathlib import Path

import numpy as np

from finetune_ppo_v2 import mechanical, paired
from run_llm_planning import save

PAPER = {"Original": (2.50e-2, 10.7), "No-LLM": (1.14e-2, 3.65e-2), "No-RL": (1.69e-2, 1.21),
         "Proposed": (8.22e-3, 2.60e-3), "Pure-RL": (2.06e-2, 3.24), "No-PT": (1.02e-2, 1.26),
         "No-Reflexion": (8.60e-3, 6.08e-3), "MISOCP": (2.73e-3, 0.0)}


def rows(path, column):
    with (Path(path) / "pairs.csv").open(encoding="utf-8", newline="") as f:
        data = list(csv.DictReader(f))
    return [{"date": r["date"], **{m: float(r[f"{column}_{m}"]) for m in ("mad_pu", "violation_pct", "min_pu", "max_pu")}}
            for r in data]


def plans_mechanical(directory, arm):
    counts = [mechanical(json.loads((p / arm / "scored.json").read_text(encoding="utf-8"))["plan"])
              for p in sorted(Path(directory).iterdir()) if (p / arm / "scored.json").exists()]
    return {"days": len(counts), "mean_oltc_changes": float(np.mean([c["oltc_changes"] for c in counts])),
            "mean_sc_switching": float(np.mean([c["sc_switching"] for c in counts]))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-d", type=Path, required=True)
    parser.add_argument("--purerl", type=Path, required=True)
    parser.add_argument("--purerl-plans", type=Path, required=True)
    parser.add_argument("--norefl", type=Path, required=True)
    parser.add_argument("--norefl-plans", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    arms = {"Original": rows(args.stage_d, "original"), "No-LLM": rows(args.stage_d, "no_llm"),
            "No-RL": rows(args.stage_d, "no_rl"), "Proposed": rows(args.stage_d, "proposed_ep150"),
            "Pure-RL": rows(args.purerl, "purerl_ep150"), "No-PT": rows(args.stage_d, "nopt_ep150"),
            "No-Reflexion": rows(args.norefl, "norefl_ep150"),
            "Pure-RL day-ahead only (PV Q=0)": rows(args.purerl, "no_rl"),
            "No-Reflexion day-ahead only (PV Q=0)": rows(args.norefl, "no_rl")}
    dates = [r["date"] for r in arms["Proposed"]]
    if any([r["date"] for r in v] != dates for v in arms.values()):
        raise ValueError("All arms must cover the same validation days in order")
    summary = {}
    for name, data in arms.items():
        summary[name] = {"mad_pu": float(np.mean([r["mad_pu"] for r in data])),
                         "violation_pct": float(np.mean([r["violation_pct"] for r in data])),
                         "violation_free_days": int(sum(r["violation_pct"] == 0 for r in data)),
                         "min_pu": min(r["min_pu"] for r in data), "max_pu": max(r["max_pu"] for r in data),
                         "worse_than_original_days": int(sum(r["mad_pu"] > o["mad_pu"] + 1e-10
                                                             for r, o in zip(data, arms["Original"]))),
                         "paper_table3": dict(zip(("mad_pu", "violation_pct"), PAPER[name])) if name in PAPER else None}
    comparisons = {f"Proposed_vs_{b}": paired(arms["Proposed"], arms[b])
                   for b in ("Pure-RL", "No-Reflexion", "No-PT", "No-LLM", "No-RL")}
    comparisons["No-Reflexion_vs_Pure-RL"] = paired(arms["No-Reflexion"], arms["Pure-RL"])
    comparisons["Pure-RL day-ahead_vs_No-RL"] = paired(arms["Pure-RL day-ahead only (PV Q=0)"], arms["No-RL"])
    comparisons["No-Reflexion day-ahead_vs_No-RL"] = paired(arms["No-Reflexion day-ahead only (PV Q=0)"], arms["No-RL"])
    mech = {"Pure-RL": plans_mechanical(args.purerl_plans, "purerl"),
            "No-Reflexion": plans_mechanical(args.norefl_plans, "nr")}
    args.output.mkdir(parents=True, exist_ok=True)
    save(args.output / "summary.json", {"days": len(dates), "arms": summary, "paired": comparisons,
                                        "mechanical_validation_plans": mech})
    with (args.output / "validation_pairs.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date"] + [f"{a}_{m}" for a in arms for m in ("mad_pu", "violation_pct")])
        for i, d in enumerate(dates):
            writer.writerow([d] + [arms[a][i][m] for a in arms for m in ("mad_pu", "violation_pct")])
    for name, s in summary.items():
        print(f"{name:40s} {s['mad_pu']:.6f} {s['violation_pct']:.3f}% free={s['violation_free_days']} "
              f"max={s['max_pu']:.4f} worse_than_original={s['worse_than_original_days']}")
    print(json.dumps(comparisons, indent=1))
    print(mech)


if __name__ == "__main__":
    main()
