"""Check hard voltage-limit feasibility of a v2 candidate on extreme split-A training days with joint MISOCP."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from gurobipy import GRB
from run_misocp_day import solve
from screen_v2_config import raw_training_peak, training_days
from voltage_control.data import Dataset
from voltage_control.environment import DayPlan
from voltage_control.network import Case33Network


def extreme_days(dataset, days, scale):
    stats = []
    for date in days:
        d = dataset.day(date)
        load, pv = d["p_load_mw"].sum(axis=1), d["p_pv_mw"].sum(axis=1) * scale
        stats.append((date, pv.max(), (pv - load).max(), load.max()))
    picks = []
    for column in (1, 2, 3):
        for row in sorted(stats, key=lambda s: -s[column]):
            if row[0] not in picks:
                picks.append(row[0])
                break
        for row in sorted(stats, key=lambda s: -s[column])[1:]:
            if row[0] not in picks:
                picks.append(row[0])
                break
    return picks, {s[0]: dict(pv_peak_mw=s[1], net_export_peak_mw=s[2], load_peak_mw=s[3]) for s in stats if s[0] in picks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", type=float, required=True)
    parser.add_argument("--factor", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--time-limit", type=float, default=120)
    args = parser.parse_args()
    days = training_days()
    dataset = Dataset()
    net = Case33Network(args.factor * args.scale * raw_training_peak(days))
    picks, info = extreme_days(dataset, days, args.scale)
    args.output.mkdir(parents=True, exist_ok=False)
    results = {"pv_scale": args.scale, "s_factor": args.factor, "capacities_mva": net.capacities.tolist(),
               "days": {}, "note": "joint MISOCP with hard 0.95-1.05 limits; AC replay of the optimised actions"}
    for date in picks:
        day = dataset.day(date)
        day["p_pv_mw"] = day["p_pv_mw"] * args.scale
        r = solve(net, day, DayPlan.zero(), args.output / date, fixed=False, time_limit=args.time_limit)
        entry = {**info[date], "status": r["status"], "feasible": r["status"] != GRB.INFEASIBLE and r.get("solutions", 0) > 0}
        for key in ("model_voltage", "ac_voltage", "mip_gap", "max_voltage_difference_pu", "seconds"):
            if key in r:
                entry[key] = r[key]
        results["days"][date] = entry
        print(date, json.dumps(entry), flush=True)
        (args.output / "summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
