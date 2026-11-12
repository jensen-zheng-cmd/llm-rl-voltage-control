"""v2 MISOCP reference: perfect-information joint optimisation, then exact AC reactive dispatch.

Stage 1  joint MISOCP of OLTC, capacitors and PV reactive power (run_misocp_day.solve) on the
         true 15-minute loads and PV of the day, 0.95-1.05 p.u. hard limits (soft limits only
         if the hard model is infeasible), optional penalty on modelled losses.
Stage 2  OLTC and capacitor schedule of stage 1 fixed; every 15-minute step solves the exact
         branch-flow AC model (non-convex, Gurobi global) for the six PV reactive powers,
         minimising the evaluation metric sum |V - 1| with a large penalty on voltages outside
         0.95-1.05 p.u. Stage 1 reactive powers are the start point and the fallback.
Both stages are replayed in the common pandapower AC power flow and scored there.

The perfect-information reference does not use forecasts and takes no part in selecting any method.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import csv
import json
import os
from pathlib import Path
import sys
from time import perf_counter

import gurobipy as gp
from gurobipy import GRB
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from run_misocp_day import ac_replay, solve, voltage_metrics
from voltage_control.data import Dataset
from voltage_control.environment import DayPlan
from voltage_control.network import Case33Network

VIOLATION_WEIGHT = 100.0


def v2_dataset():
    return Dataset(version="v2", split_scheme="A_half_month")


def diagnostic_days(dataset):
    """Ten non-test days: the six A2 extreme training days and four validation days
    (highest PV peak, highest net export, highest load peak, most ordinary)."""
    a2 = ["2013-09-09", "2013-09-20", "2013-04-20", "2013-06-01", "2013-01-23", "2013-12-04"]
    days = dataset.split_dates("validation").astype(str).tolist()
    stats = []
    for d in days:
        x = dataset.day(d)
        load, pv = x["p_load_mw"].sum(axis=1), x["p_pv_mw"].sum(axis=1)
        stats.append((pv.max(), (pv - load).max(), load.max(), load.mean(), pv.mean()))
    stats = np.asarray(stats)
    feature = stats[:, 3:]
    ordinary = np.linalg.norm((feature - np.median(feature, axis=0)) / feature.std(axis=0), axis=1)
    picks = []
    for score in (-stats[:, 0], -stats[:, 1], -stats[:, 2], ordinary):
        picks.append(next(days[i] for i in np.argsort(score) if days[i] not in picks))
    return a2 + picks


def topology(net):
    src, dst = net.branches[:, :2].astype(int).T - 1
    zbase = net.system["network"]["base_kv"] ** 2 / net.base_mva
    r, x = net.branches[:, 2:4].T / zbase
    children = [[e for e, a in enumerate(src) if a == bus] for bus in range(33)]
    parents = {int(b): e for e, b in enumerate(dst)}
    return src, dst, r, x, children, parents


def exact_dispatch(net, day, plan, q_start, step_limit):
    """Per-step global optimum of PV reactive power on the exact radial branch-flow model."""
    units = net.base_mva
    settings = net.system["paper_devices"]
    src, dst, r, x, children, parents = topology(net)
    pv_pos = {int(b): i for i, b in enumerate(net.pv_buses)}
    sc_pos = {int(b): i for i, b in enumerate(net.sc_buses)}
    reactive, statuses, gaps = np.array(q_start, dtype=float), [], []
    for t in range(96):
        demand_p = np.r_[0, day["p_load_mw"][t]] / units
        demand_q = np.r_[0, day["q_load_mvar"][t]] / units
        demand_p[net.pv_buses] -= day["p_pv_mw"][t] / units
        limit = net.q_limits(day["p_pv_mw"][t])
        root = 1 + settings["oltc_voltage_step_pu"] * plan.tap[t]
        with gp.Env(params={"OutputFlag": 0}) as env, gp.Model(env=env) as m:
            m.Params.NonConvex, m.Params.TimeLimit, m.Params.Threads = 2, step_limit, 1
            m.Params.MIPGap, m.Params.FeasibilityTol, m.Params.Seed = 1e-6, 1e-8, 42
            V = m.addMVar(33, lb=.8, ub=1.2)
            v = m.addMVar(33, lb=.64, ub=1.44)
            p = m.addMVar(32, lb=-2, ub=2)
            q = m.addMVar(32, lb=-2, ub=2)
            ell = m.addMVar(32, lb=0, ub=8)
            pv_q = m.addMVar(6, lb=-limit / units, ub=limit / units)
            dev, over, under = m.addMVar(33), m.addMVar(33), m.addMVar(33)
            m.addConstr(V[0] == root)
            for i in range(33):
                m.addConstr(v[i] == V[i] * V[i])
            for bus in range(1, 33):
                e, out = parents[bus], children[bus]
                q_local = demand_q[bus]
                if bus in pv_pos:
                    q_local = q_local - pv_q[pv_pos[bus]]
                if bus in sc_pos:
                    q_local = q_local - plan.sc_on[t][sc_pos[bus]] * settings["sc_capacity_each_mvar"] / units
                m.addConstr(p[e] == (p[out].sum() if out else 0) + demand_p[bus] + r[e] * ell[e])
                m.addConstr(q[e] == (q[out].sum() if out else 0) + q_local + x[e] * ell[e])
            for e in range(32):
                a, b = src[e], dst[e]
                m.addConstr(v[b] == v[a] - 2 * (p[e] * r[e] + q[e] * x[e]) + ell[e] * (r[e] ** 2 + x[e] ** 2))
                m.addConstr(p[e] * p[e] + q[e] * q[e] == v[a] * ell[e])
            m.addConstr(dev >= V - 1)
            m.addConstr(dev >= 1 - V)
            m.addConstr(over >= V - 1.05)
            m.addConstr(under >= .95 - V)
            m.setObjective(dev.sum() + VIOLATION_WEIGHT * (over.sum() + under.sum()), GRB.MINIMIZE)
            pv_q.Start = np.clip(q_start[t], -limit, limit) / units
            m.optimize()
            statuses.append(int(m.Status))
            if m.SolCount:
                reactive[t] = np.clip(pv_q.X * units, -limit, limit)
                gaps.append(float(m.MIPGap))
    return reactive, statuses, gaps


def mechanical(plan):
    tap, sc = plan.tap[::4], plan.sc_on[::4]
    return {"oltc_changes": int(np.count_nonzero(np.diff(tap))),
            "sc_switching": int(np.abs(np.diff(np.vstack([np.zeros((1, 3), int), sc]), axis=0)).sum())}


def run_day(date, output, loss_weight, time_limit, step_limit, threads, safe_upper=False):
    dataset = v2_dataset()
    net = Case33Network(dataset.capacities)
    day = dataset.day(date)
    directory = output / date
    directory.mkdir(parents=True)
    started = perf_counter()
    stage1 = solve(net, day, DayPlan.zero(), directory / "stage1", time_limit=time_limit,
                   loss_weight=loss_weight, threads=threads, safe_upper=safe_upper)
    soft = False
    if not stage1.get("solutions"):
        soft = True
        stage1 = solve(net, day, DayPlan.zero(), directory / "stage1_soft", soft=True, time_limit=time_limit,
                       loss_weight=loss_weight, threads=threads, safe_upper=safe_upper)
    if not stage1.get("solutions"):
        raise RuntimeError(f"{date}: no stage-1 solution")
    replay = np.load(directory / ("stage1_soft" if soft else "stage1") / "replay.npz")
    plan = DayPlan(replay["tap"], replay["sc_on"]).validate()
    t2 = perf_counter()
    reactive, statuses, gaps = exact_dispatch(net, day, plan, replay["pv_q_mvar"], step_limit)
    v_ac, loss, residual = ac_replay(net, day, plan, reactive)
    stage2 = {"ac_voltage": voltage_metrics(v_ac), "seconds": perf_counter() - t2,
              "steps_optimal": sum(s == GRB.OPTIMAL for s in statuses), "steps_time_limit": sum(s == GRB.TIME_LIMIT for s in statuses),
              "steps_without_solution": 96 - len(gaps), "max_step_gap": max(gaps) if gaps else None,
              "max_ac_balance_residual_mva": float(residual), "ac_loss_mwh": float(loss.sum() * .25)}
    np.savez_compressed(directory / "stage2.npz", tap=plan.tap, sc_on=plan.sc_on, pv_q_mvar=reactive, v_ac=v_ac)
    result = {"date": date, "loss_weight": loss_weight, "safe_upper": safe_upper, "soft_stage1": soft,
              "stage1": {k: stage1.get(k) for k in ("status", "seconds", "mip_gap", "objective_value", "model_voltage",
                                                     "ac_voltage", "max_voltage_difference_pu",
                                                     "max_cone_slack_pu_squared", "model_loss_mwh", "ac_loss_mwh")},
              "stage2": stage2, **mechanical(plan), "total_seconds": perf_counter() - started}
    (directory / "summary.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"{date} w={loss_weight} safe={safe_upper} stage1 AC {stage1['ac_voltage']['mad_pu']:.5f}/{stage1['ac_voltage']['violation_pct']:.3f}% "
          f"stage2 AC {stage2['ac_voltage']['mad_pu']:.5f}/{stage2['ac_voltage']['violation_pct']:.3f}% "
          f"({result['total_seconds']:.0f}s)", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", choices=("diagnostic", "test"), required=True)
    parser.add_argument("--loss-weight", type=float, default=0.0)
    parser.add_argument("--time-limit", type=float, default=600, help="Stage-1 MISOCP limit per day (s)")
    parser.add_argument("--step-limit", type=float, default=20, help="Stage-2 limit per 15-minute step (s)")
    parser.add_argument("--safe-upper", action="store_true",
                        help="Stage 1 limits lossless (LinDistFlow) voltages, an upper bound of the AC voltages")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if "GRB_LICENSE_FILE" not in os.environ:
        parser.error("Set GRB_LICENSE_FILE to the full Gurobi licence (the bundled one is size-limited)")
    dataset = v2_dataset()
    dates = diagnostic_days(dataset) if args.days == "diagnostic" else dataset.split_dates("test").astype(str).tolist()
    args.output.mkdir(parents=True, exist_ok=True)
    config = {"days": args.days, "dates": dates, "loss_weight": args.loss_weight, "safe_upper": args.safe_upper,
              "time_limit": args.time_limit,
              "step_limit": args.step_limit, "threads": args.threads, "violation_weight_stage2": VIOLATION_WEIGHT,
              "information": "true 15-minute loads and PV (perfect information)", "solver": list(gp.gurobi.version())}
    path = args.output / "config.json"
    if path.exists() and json.loads(path.read_text(encoding="utf-8")) != config:
        raise ValueError("Configuration differs from the existing run")
    path.write_text(json.dumps(config, indent=1), encoding="utf-8")
    todo = [d for d in dates if not (args.output / d / "summary.json").exists()]
    for d in todo:
        if (args.output / d).exists():
            n = 1
            while (args.output / f"{d}_incomplete_{n}").exists():
                n += 1
            (args.output / d).rename(args.output / f"{d}_incomplete_{n}")
    with ProcessPoolExecutor(args.workers) as pool:
        list(pool.map(run_day, todo, *zip(*[(args.output, args.loss_weight, args.time_limit, args.step_limit,
                                               args.threads, args.safe_upper)] * len(todo))))
    rows = [json.loads((args.output / d / "summary.json").read_text(encoding="utf-8")) for d in dates]
    with (args.output / "days.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "soft_stage1", "stage1_status", "stage1_seconds", "stage1_mip_gap", "max_cone_slack",
                    "model_mad", "model_max", "stage1_ac_mad", "stage1_ac_violation_pct", "stage1_ac_max",
                    "stage2_ac_mad", "stage2_ac_violation_pct", "stage2_ac_min", "stage2_ac_max",
                    "stage2_steps_optimal", "stage2_seconds", "oltc_changes", "sc_switching"])
        for r in rows:
            s1, s2 = r["stage1"], r["stage2"]
            w.writerow([r["date"], r["soft_stage1"], s1["status"], s1["seconds"], s1["mip_gap"],
                        s1["max_cone_slack_pu_squared"], s1["model_voltage"]["mad_pu"], s1["model_voltage"]["max_pu"],
                        s1["ac_voltage"]["mad_pu"], s1["ac_voltage"]["violation_pct"], s1["ac_voltage"]["max_pu"],
                        s2["ac_voltage"]["mad_pu"], s2["ac_voltage"]["violation_pct"], s2["ac_voltage"]["min_pu"],
                        s2["ac_voltage"]["max_pu"], s2["steps_optimal"], s2["seconds"], r["oltc_changes"], r["sc_switching"]])
    mean = lambda f: float(np.mean([f(r) for r in rows]))
    summary = {"days": len(rows), "loss_weight": args.loss_weight, "safe_upper": args.safe_upper,
               "stage1_ac": {"mad_pu": mean(lambda r: r["stage1"]["ac_voltage"]["mad_pu"]),
                             "violation_pct": mean(lambda r: r["stage1"]["ac_voltage"]["violation_pct"])},
               "stage2_ac": {"mad_pu": mean(lambda r: r["stage2"]["ac_voltage"]["mad_pu"]),
                             "violation_pct": mean(lambda r: r["stage2"]["ac_voltage"]["violation_pct"]),
                             "violation_free_days": sum(r["stage2"]["ac_voltage"]["violation_pct"] == 0 for r in rows),
                             "min_pu": min(r["stage2"]["ac_voltage"]["min_pu"] for r in rows),
                             "max_pu": max(r["stage2"]["ac_voltage"]["max_pu"] for r in rows)},
               "soft_stage1_days": sum(r["soft_stage1"] for r in rows),
               "stage1_time_limit_days": sum(r["stage1"]["status"] == GRB.TIME_LIMIT for r in rows),
               "max_stage1_gap": max(r["stage1"]["mip_gap"] or 0 for r in rows),
               "max_cone_slack": max(r["stage1"]["max_cone_slack_pu_squared"] for r in rows),
               "stage2_steps_not_optimal": sum(96 - r["stage2"]["steps_optimal"] for r in rows),
               "mean_seconds": mean(lambda r: r["total_seconds"])}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
