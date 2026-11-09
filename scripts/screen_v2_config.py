"""Screen PV scale and inverter capacity candidates for configuration v2 (power flow only)."""
import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from voltage_control.data import Dataset
from voltage_control.network import Case33Network

PV_SCALES = [1.0, 0.85, 0.75, 0.65]
S_FACTORS = [1.2, 1.5]
PAPER_ORIGINAL = {"mad_pu": 2.50e-2, "violation_pct": 10.7}
SELECTION_RULE = (
    "Fixed before screening, using split-A training days only: "
    "(1) keep candidates whose unavoidable violation floor (over + under, node-step %) is <= 0.01%; "
    "(2) among them choose the smallest |ln(MAD/0.025)| + |ln(VR/10.7)| for Original; "
    "(3) ties within 0.05 prefer pv_scale closer to 1.0, then S factor 1.2. "
    "No control-method result is used."
)


def training_days():
    splits = json.loads((ROOT / "configs/splits_v2.json").read_text(encoding="utf-8"))
    return splits["splits"]["A_half_month"]["train"]


def raw_training_peak(days):
    frame = pd.read_csv(ROOT / "data/raw/mapdn_case33/pv_active.csv")
    starts = pd.to_datetime(frame.pop("DateTime")) - pd.Timedelta(minutes=3)
    mask = starts.dt.strftime("%Y-%m-%d").isin(set(days)).to_numpy()
    return frame.to_numpy(dtype=float)[mask].max(axis=0)


def run(scale, factor, output):
    days = training_days()
    dataset = Dataset()
    peak = raw_training_peak(days) * scale
    capacities = factor * peak
    net = Case33Network(capacities)
    original, over, under = [], [], []
    started = perf_counter()
    for i, date in enumerate(days):
        day = dataset.day(date)
        for t in range(96):
            p, q, pv = day["p_load_mw"][t], day["q_load_mvar"][t], day["p_pv_mw"][t] * scale
            if factor == S_FACTORS[0]:
                original.append(net.solve(p, q, pv).voltage_pu)
            # Lowest tap, SC off and full absorption minimise every voltage on this radial feeder;
            # highest tap, all SC on and full injection maximise them. Violations left are unavoidable.
            over.append(net.solve(p, q, pv, tap=-5, sc_on=(0, 0, 0), q_fraction=-np.ones(6)).voltage_pu)
            under.append(net.solve(p, q, pv, tap=5, sc_on=(1, 1, 1), q_fraction=np.ones(6)).voltage_pu)
        if (i + 1) % 20 == 0:
            print(f"scale={scale} factor={factor} {i + 1}/{len(days)} days {perf_counter() - started:.0f}s", flush=True)
    over, under = np.stack(over).reshape(len(days), 96, 33), np.stack(under).reshape(len(days), 96, 33)
    over_mask, under_mask = over > 1.05, under < .95
    result = {
        "pv_scale": scale, "s_factor": factor, "training_days": len(days),
        "raw_training_peak_mw_scaled": peak.tolist(), "capacities_mva": capacities.tolist(),
        "floor_over_pct": float(100 * over_mask.mean()), "floor_under_pct": float(100 * under_mask.mean()),
        "floor_total_pct": float(100 * (over_mask | under_mask).mean()),
        "floor_days_with_violation": int((over_mask | under_mask).any(axis=(1, 2)).sum()),
        "best_case_max_pu": float(over.max()), "best_case_min_pu": float(under.min()),
        "seconds": perf_counter() - started,
    }
    if original:
        v = np.stack(original)
        result["original"] = {"mad_pu": float(np.abs(v - 1).mean()),
                              "violation_pct": float(100 * ((v < .95) | (v > 1.05)).mean()),
                              "min_pu": float(v.min()), "max_pu": float(v.max())}
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)


def summarize(directory):
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("candidate_*.json"))]
    originals = {r["pv_scale"]: r["original"] for r in rows if "original" in r}
    table = []
    for r in rows:
        o = originals[r["pv_scale"]]
        distance = abs(np.log(o["mad_pu"] / PAPER_ORIGINAL["mad_pu"])) + abs(np.log(o["violation_pct"] / PAPER_ORIGINAL["violation_pct"]))
        table.append({**{k: r[k] for k in ("pv_scale", "s_factor", "floor_over_pct", "floor_under_pct", "floor_total_pct",
                                         "floor_days_with_violation", "best_case_max_pu", "best_case_min_pu")},
                      "original_mad_pu": o["mad_pu"], "original_violation_pct": o["violation_pct"],
                      "original_max_pu": o["max_pu"], "original_min_pu": o["min_pu"],
                      "distance_to_paper_original": float(distance), "feasible": r["floor_total_pct"] <= 0.01})
    feasible = [r for r in table if r["feasible"]]
    selected = None
    if feasible:
        best = min(r["distance_to_paper_original"] for r in feasible)
        tied = [r for r in feasible if r["distance_to_paper_original"] <= best + 0.05]
        selected = min(tied, key=lambda r: (abs(1 - r["pv_scale"]), r["s_factor"]))
    summary = {"selection_rule": SELECTION_RULE, "paper_original": PAPER_ORIGINAL, "candidates": table, "selected": selected}
    (directory / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scale", type=float)
    parser.add_argument("--factor", type=float)
    parser.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.summarize:
        summarize(args.output)
        return
    if args.scale not in PV_SCALES or args.factor not in S_FACTORS:
        raise ValueError("Candidate must come from the fixed screening grid")
    config = args.output / "config.json"
    if not config.exists():
        config.write_text(json.dumps({"pv_scales": PV_SCALES, "s_factors": S_FACTORS, "selection_rule": SELECTION_RULE,
                                      "split_file": "configs/splits_v2.json", "split": "A_half_month/train",
                                      "network_source": "processed v1 15-min data, 2013 days, PV multiplied by pv_scale"},
                                     indent=2), encoding="utf-8")
    run(args.scale, args.factor, args.output / f"candidate_{args.scale}_{args.factor}.json")


if __name__ == "__main__":
    main()
