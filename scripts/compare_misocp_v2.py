"""Compare the v2 MISOCP reference (perfect information) with the methods on the 48 test days.

Inputs: a run_misocp_v2.py --days test directory and the seed-averaged day means of
summarize_seeds_v2.py --split test. Optional diagnostic runs (non-test days) are tabulated
alongside so that the frozen variant's choice stays traceable.
"""
import argparse
import csv
import json
from pathlib import Path

import numpy as np

METHODS = ("original", "no_rl", "no_pt", "pure_rl", "no_llm", "no_reflexion", "proposed")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--misocp-run", type=Path, required=True)
    parser.add_argument("--day-means", type=Path, required=True, help="results/test-v2/day_means.csv")
    parser.add_argument("--diagnostic", nargs=2, action="append", default=[], metavar=("NAME", "RUN"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with (args.misocp_run / "days.csv").open(encoding="utf-8", newline="") as f:
        misocp = {r["date"]: r for r in csv.DictReader(f)}
    with args.day_means.open(encoding="utf-8", newline="") as f:
        methods = list(csv.DictReader(f))
    dates = [r["date"] for r in methods]
    if sorted(dates) != sorted(misocp):
        raise ValueError("MISOCP and method runs cover different days")
    ref = np.array([float(misocp[d]["stage2_ac_mad"]) for d in dates])
    ref_viol = np.array([float(misocp[d]["stage2_ac_violation_pct"]) for d in dates])
    run_summary = json.loads((args.misocp_run / "summary.json").read_text(encoding="utf-8"))
    table = {}
    original = np.array([float(r["original_mad_pu"]) for r in methods])
    for m in METHODS:
        mad = np.array([float(r[f"{m}_mad_pu"]) for r in methods])
        excess = mad - ref
        table[m] = {"mad_pu": float(mad.mean()), "violation_pct": float(np.mean([float(r[f"{m}_violation_pct"]) for r in methods])),
                    "excess_over_misocp": float(excess.mean()),
                    "excess_standard_error": float(excess.std(ddof=1) / np.sqrt(len(excess))),
                    "days_below_misocp": int((excess < -1e-10).sum()),
                    "ratio_to_misocp": float(mad.mean() / ref.mean()),
                    "share_of_original_gap_closed_pct": float(100 * (original.mean() - mad.mean()) / (original.mean() - ref.mean()))}
    diagnostics = {}
    for name, run in args.diagnostic:
        diagnostics[name] = json.loads((Path(run) / "summary.json").read_text(encoding="utf-8"))
    summary = {"misocp_run": str(args.misocp_run), "days": len(dates),
               "misocp": {"stage2_ac_mad_pu": float(ref.mean()), "stage2_ac_violation_pct": float(ref_viol.mean()),
                          "stage1_ac_mad_pu": run_summary["stage1_ac"]["mad_pu"],
                          "violation_free_days": int((ref_viol == 0).sum()), **{k: run_summary[k] for k in (
                              "soft_stage1_days", "stage1_time_limit_days", "max_stage1_gap", "max_cone_slack",
                              "stage2_steps_not_optimal", "mean_seconds")},
                          "min_pu": run_summary["stage2_ac"]["min_pu"], "max_pu": run_summary["stage2_ac"]["max_pu"],
                          "mean_oltc_changes": float(np.mean([int(misocp[d]["oltc_changes"]) for d in dates])),
                          "mean_sc_switching": float(np.mean([int(misocp[d]["sc_switching"]) for d in dates]))},
               "methods_seed_mean": table, "diagnostic_runs": diagnostics}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    with (args.output / "days.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "misocp_mad_pu", "misocp_violation_pct", "soft_stage1", "stage1_seconds", "stage1_mip_gap"]
                   + [f"{m}_mad_pu" for m in METHODS])
        for r, d in zip(methods, dates):
            x = misocp[d]
            w.writerow([d, x["stage2_ac_mad"], x["stage2_ac_violation_pct"], x["soft_stage1"], x["stage1_seconds"],
                        x["stage1_mip_gap"]] + [r[f"{m}_mad_pu"] for m in METHODS])
    print(f"MISOCP {ref.mean():.6f} / {ref_viol.mean():.3f}%")
    for m, t in table.items():
        print(f"{m:14} {t['mad_pu']:.6f}  x{t['ratio_to_misocp']:.2f}  gap closed {t['share_of_original_gap_closed_pct']:.1f}%")


if __name__ == "__main__":
    main()
