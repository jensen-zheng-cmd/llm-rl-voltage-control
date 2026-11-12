"""C3: compare the retrieval gain of a frozen experience pool between split A and split B.

Split B (4 train, 1 validation, 1 test days in a 6-day cycle, no buffer) puts training days
right next to each validation day, so a retrieved similar day can be the day before or after.
Each run is a run_llm_planning_v2.py validate directory with the pool arm and the
no-experience arm ("none"). Gain = none minus pool, per day, for MAD and violation rate.
"""
import argparse
import csv
import hashlib
import json
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def load(directory, arm):
    directory = Path(directory)
    with (directory / "pairs.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    pool_bytes = (directory / f"frozen_pool_{arm}.json").read_bytes()
    pool = json.loads(pool_bytes)
    splits = json.loads((ROOT / "configs/splits_v2.json").read_text(encoding="utf-8"))["splits"]
    split = splits[config["split_scheme"]]
    dates = [r["date"] for r in rows]
    # C2 predates the --split option; its full date list still proves the split.
    if (config.get("split", "validation") != "validation" or config.get("reflexion_rounds") != 0
            or len(dates) != len(set(dates)) or dates != config["dates"]
            or set(dates) != set(split["validation"])):
        raise ValueError(f"{directory}: expected complete frozen-pool validation")
    pool_dates = {e["forecast"]["date"] for e in pool}
    if not pool_dates or not pool_dates <= set(split["train"]):
        raise ValueError(f"{directory}: pool contains non-training dates or is empty")
    if hashlib.sha256(pool_bytes).hexdigest() != config["pool_sha256"][arm]:
        raise ValueError(f"{directory}: frozen pool hash differs from the run configuration")
    if any(r[f"{arm}_reference_date"] not in pool_dates for r in rows):
        raise ValueError(f"{directory}: retrieved reference is absent from the frozen pool")
    return rows, config, pool


def gap_days(a, b):
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def mean_se(x):
    x = np.asarray(x, float)
    return {"mean": float(x.mean()), "standard_error": float(x.std(ddof=1) / np.sqrt(len(x))), "n": int(len(x))}


def analyse(directory, arm):
    rows, config, pool = load(directory, arm)
    f = lambda key: np.array([float(r[key]) for r in rows])
    none, pooled, neutral = f("none_mad_pu"), f(f"{arm}_mad_pu"), f("neutral_mad_pu")
    gain = none - pooled
    viol_gain = f("none_violation_pct") - f(f"{arm}_violation_pct")
    sim = f(f"{arm}_similarity")
    gaps = np.array([gap_days(r["date"], r[f"{arm}_reference_date"]) for r in rows])
    pool_dates = [e["forecast"]["date"] for e in pool]
    nearest_pool = np.array([min(gap_days(r["date"], d) for d in pool_dates) for r in rows])
    by_gap = {}
    for name, mask in (("reference_within_1_day", gaps <= 1), ("reference_2_to_7_days", (gaps > 1) & (gaps <= 7)),
                       ("reference_over_7_days", gaps > 7)):
        by_gap[name] = {"days": int(mask.sum()),
                        "mad_gain": mean_se(gain[mask]) if mask.sum() > 1 else None,
                        "relative_gain_pct": float(100 * gain[mask].sum() / none[mask].sum()) if mask.any() else None}
    return {
        "run": str(directory), "split_scheme": config["split_scheme"], "split": "validation",
        "days": len(rows), "pool_entries": len(pool),
        "mad": {"neutral": float(neutral.mean()), "none": float(none.mean()), "pool": float(pooled.mean())},
        "violation_pct": {"none": float(f("none_violation_pct").mean()), "pool": float(f(f"{arm}_violation_pct").mean())},
        "mad_gain": {**mean_se(gain), "pool_better_days": int((gain > 1e-10).sum()), "pool_worse_days": int((gain < -1e-10).sum())},
        "relative_mad_gain_pct": float(100 * gain.mean() / none.mean()),
        "violation_gain_pct_points": mean_se(viol_gain),
        "similarity": {"mean": float(sim.mean()), "median": float(np.median(sim)), "min": float(sim.min()),
                       "share_at_least_0.9": float((sim >= .9).mean()), "share_at_least_0.95": float((sim >= .95).mean())},
        "reference_gap_days": {"median": float(np.median(gaps)), "share_within_1": float((gaps <= 1).mean()),
                               "share_within_3": float((gaps <= 3).mean()), "share_within_7": float((gaps <= 7).mean())},
        "nearest_pool_date_gap_days": {"median": float(np.median(nearest_pool)),
                                       "share_within_1": float((nearest_pool <= 1).mean())},
        "gain_by_reference_gap": by_gap,
        "corr_similarity_gain": float(np.corrcoef(sim, gain)[0, 1]),
    }, rows, gain, gaps, sim


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--a-run", type=Path, required=True, help="Split A validate run (C2)")
    parser.add_argument("--b-run", type=Path, required=True, help="Split B validate run (C3)")
    parser.add_argument("--arm", default="t090")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    a_result, b_result = analyse(args.a_run, args.arm), analyse(args.b_run, args.arm)
    a, a_rows, *_ = a_result
    b, b_rows, *_ = b_result
    if a["split_scheme"] != "A_half_month" or b["split_scheme"] != "B_daily_4_1_1":
        raise ValueError("expected a split A run and a split B run")
    configs = [json.loads((d / "config.json").read_text(encoding="utf-8"))
               for d in (args.a_run, args.b_run)]
    settings = ("provider", "model", "temperature", "top_p", "pv_q_fraction", "forecast_seed",
                "system_prompt_sha256", "generations_per_arm", "reflexion_rounds", "retrieval")
    for key in settings:
        if configs[0][key] != configs[1][key]:
            raise ValueError(f"A and B differ in {key}")
    diff = b["mad_gain"]["mean"] - a["mad_gain"]["mean"]
    se = float(np.hypot(a["mad_gain"]["standard_error"], b["mad_gain"]["standard_error"]))
    summary = {"arm": args.arm, "A": a, "B": b,
               "shared_validation_settings": {k: configs[0][k] for k in settings},
               "overlapping_validation_dates": len({r["date"] for r in a_rows} & {r["date"] for r in b_rows}),
               "uncertainty_note": "Within-split standard errors describe day-to-day variation only. "
                   "The independent standard error below is an assumption-based reference, not a significance test: "
                   "validation dates overlap and temporal dependence is not modelled. One pool and one LLM draw "
                   "per day/arm do not quantify pool-building or LLM sampling uncertainty.",
               "analysis_scope": "Split B construction and retrieval-gain comparison were planned. "
                   "Reference-date gaps, gap subgroups and similarity correlations are post-hoc diagnostics.",
               "B_minus_A": {"mad_gain": diff, "standard_error_independent": se,
                             "relative_gain_pct_points": b["relative_mad_gain_pct"] - a["relative_mad_gain_pct"]}}
    summary["source_sha256"] = {
        name: {filename: hashlib.sha256((directory / filename).read_bytes()).hexdigest()
               for filename in ("config.json", "pairs.csv", f"frozen_pool_{args.arm}.json")}
        for name, directory in (("A", args.a_run), ("B", args.b_run))}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    for name, (_, rows, gain, gaps, sim) in (("a", a_result), ("b", b_result)):
        with (args.output / f"split_{name}_days.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "reference_date", "reference_gap_days", "similarity", "none_mad_pu",
                             f"{args.arm}_mad_pu", "mad_gain"])
            for r, g, d, s in zip(rows, gain, gaps, sim):
                writer.writerow([r["date"], r[f"{args.arm}_reference_date"], int(d), s, r["none_mad_pu"],
                                 r[f"{args.arm}_mad_pu"], g])
    for name, s in (("A", a), ("B", b)):
        print(f"{name}: days {s['days']} pool {s['pool_entries']} MAD none {s['mad']['none']:.6f} pool {s['mad']['pool']:.6f} "
              f"gain {s['mad_gain']['mean']:.6f} ± {s['mad_gain']['standard_error']:.6f} ({s['relative_mad_gain_pct']:.1f}%) "
              f"sim median {s['similarity']['median']:.3f} ref within 1 day {s['reference_gap_days']['share_within_1']:.2f}")
    print(f"B - A gain {diff:.6f} (independence-assumption SE {se:.6f}; see uncertainty_note)")


if __name__ == "__main__":
    main()
