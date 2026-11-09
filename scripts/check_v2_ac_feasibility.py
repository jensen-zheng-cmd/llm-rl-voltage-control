"""Constructive AC check: search an hourly OLTC schedule (<=4 changes), fixed SC state and per-step
uniform PV reactive fraction that keep every node within 0.95-1.05 p.u. under full AC power flow."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from screen_v2_config import raw_training_peak, training_days
from voltage_control.data import Dataset
from voltage_control.network import Case33Network

TAPS = np.arange(-5, 6)
FRACTIONS = np.linspace(-1, 1, 9)


def hourly_feasible(net, day, scale, sc):
    ok = np.zeros((96, len(TAPS)), dtype=bool)
    choice = np.full((96, len(TAPS)), np.nan)
    for t in range(96):
        p, q, pv = day["p_load_mw"][t], day["q_load_mvar"][t], day["p_pv_mw"][t] * scale
        for k, tap in enumerate(TAPS):
            best = None
            for f in FRACTIONS:
                v = net.solve(p, q, pv, int(tap), sc, q_fraction=np.full(6, f)).voltage_pu
                if v.min() >= .95 and v.max() <= 1.05:
                    dev = np.abs(v - 1).mean()
                    if best is None or dev < best[0]:
                        best = (dev, f)
            if best:
                ok[t, k], choice[t, k] = True, best[1]
    return ok.reshape(24, 4, -1).all(axis=1), choice


def schedule(hour_ok, max_changes=4):
    # DP over hours: state = (tap index, changes used); returns a feasible tap path or None.
    INF = 10**9
    cost = np.full((24, len(TAPS)), INF)
    back = np.zeros((24, len(TAPS)), dtype=int)
    cost[0, hour_ok[0]] = 0
    for h in range(1, 24):
        for k in np.flatnonzero(hour_ok[h]):
            prev = cost[h - 1] + (np.arange(len(TAPS)) != k)
            back[h, k] = int(np.argmin(prev))
            cost[h, k] = prev[back[h, k]]
    end = int(np.argmin(cost[-1]))
    if cost[-1, end] > max_changes:
        return None, int(min(cost[-1].min(), INF))
    path = [end]
    for h in range(23, 0, -1):
        path.append(back[h, path[-1]])
    return [int(TAPS[k]) for k in path[::-1]], int(cost[-1, end])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--factor", type=float, default=1.2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dataset = Dataset()
    net = Case33Network(args.factor * args.scale * raw_training_peak(training_days()))
    day = dataset.day(args.date)
    result = {"date": args.date, "pv_scale": args.scale, "s_factor": args.factor, "attempts": []}
    for sc in ((0, 0, 0), (1, 1, 1)):
        hour_ok, _ = hourly_feasible(net, day, args.scale, sc)
        taps, changes = schedule(hour_ok)
        attempt = {"sc_on_all_day": sc, "hours_without_any_feasible_tap": int((~hour_ok.any(axis=1)).sum()),
                   "tap_schedule": taps, "changes": changes, "feasible": taps is not None}
        result["attempts"].append(attempt)
        if taps is not None:
            break
    result["feasible"] = any(a["feasible"] for a in result["attempts"])
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / f"{args.date}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
