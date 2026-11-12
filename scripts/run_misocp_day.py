"""Single validation-day DistFlow conic reference with AC replay."""
import argparse
import csv
import json
from pathlib import Path

import gurobipy as gp
from gurobipy import GRB
import numpy as np

from run_llm_planning import Dataset, parse_plan, save
from voltage_control.environment import DayPlan
from voltage_control.network import Case33Network


def voltage_metrics(v):
    return dict(mad_pu=float(np.abs(v - 1).mean()),
                violation_pct=float(100 * ((v < .95) | (v > 1.05)).mean()),
                min_pu=float(v.min()), max_pu=float(v.max()))


def ac_replay(net, day, plan, reactive):
    voltage, loss, residual = [], [], []
    for t in range(96):
        snapshot = net.solve(day["p_load_mw"][t], day["q_load_mvar"][t], day["p_pv_mw"][t],
                             plan.tap[t], plan.sc_on[t], q_mvar=reactive[t])
        voltage.append(snapshot.voltage_pu)
        loss.append(snapshot.p_loss_mw)
        residual.append(snapshot.max_nodal_mismatch_mva)
    return np.stack(voltage), np.array(loss), max(residual)


def solve(net, day, llm_plan, output, fixed=False, soft=False, time_limit=90, loss_weight=0.0, threads=4,
          safe_upper=False):
    output.mkdir()
    units = net.base_mva
    settings = net.system["paper_devices"]
    src, dst = net.branches[:, :2].astype(int).T - 1
    zbase = net.system["network"]["base_kv"] ** 2 / units
    r, x = net.branches[:, 2:4].T / zbase
    children = [[e for e, a in enumerate(src) if a == bus] for bus in range(33)]
    parents = {int(b): e for e, b in enumerate(dst)}
    if len(parents) != 32 or 0 in parents or any(a >= b for a, b in zip(src, dst)):
        raise ValueError("Expected the frozen case33 branches directed from root to leaves")
    pv_pos = {int(bus): i for i, bus in enumerate(net.pv_buses)}
    sc_pos = {int(bus): i for i, bus in enumerate(net.sc_buses)}
    demand_p = np.column_stack([np.zeros(96), day["p_load_mw"]]) / units
    demand_q = np.column_stack([np.zeros(96), day["q_load_mvar"]]) / units
    demand_p[:, net.pv_buses] -= day["p_pv_mw"] / units
    q_limit = np.stack([net.q_limits(p) for p in day["p_pv_mw"]])
    sc_capacity = settings["sc_capacity_each_mvar"] / units
    vmin, vmax = (.8, 1.2) if soft else (.95, 1.05)
    # A branch current is the phasor sum of downstream nodal currents.
    # Bound its magnitude using the triangle inequality and voltage floor.
    q_abs = demand_q.copy()
    q_abs[:, net.pv_buses] += q_limit / units
    q_abs[:, net.sc_buses] = np.maximum(
        q_abs[:, net.sc_buses], abs(demand_q[:, net.sc_buses] - sc_capacity))
    downstream = np.hypot(demand_p, q_abs) / vmin
    for e in np.argsort(dst)[::-1]:
        downstream[:, src[e]] += downstream[:, dst[e]]
    current_squared_bound = downstream[:, dst] ** 2

    with gp.Model("case33_distflow") as m:
        m.Params.LogFile = str(output / "solver.log")
        m.Params.NonConvex = 0
        m.Params.Threads = threads
        m.Params.Seed = 42
        m.Params.TimeLimit = time_limit
        m.Params.MIPGap = .005
        m.Params.FeasibilityTol = 1e-8
        m.Params.IntFeasTol = 1e-8
        v = m.addMVar((96, 33), lb=vmin**2, ub=vmax**2, name="v")
        p = m.addMVar((96, 32), lb=-GRB.INFINITY, name="p")
        q = m.addMVar((96, 32), lb=-GRB.INFINITY, name="q")
        ell = m.addMVar((96, 32), lb=0, ub=current_squared_bound, name="ell")
        pv_q = m.addMVar((96, 6), lb=-q_limit / units, ub=q_limit / units, name="pv_q")
        deviation = m.addMVar((96, 33), name="deviation")
        m.addConstr(deviation >= v - 1)
        m.addConstr(deviation >= 1 - v)
        objective = deviation.sum()
        if soft:
            over = m.addMVar((96, 33), name="over")
            under = m.addMVar((96, 33), name="under")
            m.addConstr(over >= v - 1.05**2)
            m.addConstr(under >= .95**2 - v)
            objective += 100 * (over.sum() + under.sum())
        if loss_weight:
            # Penalising modelled losses discourages fictitious currents that make the cone relaxation inexact.
            objective += loss_weight * (ell * r).sum()
        step = settings["oltc_voltage_step_pu"]
        tap_values = np.arange(-5, 6)
        if fixed:
            root = (1 + step * llm_plan.tap[::4]) ** 2
            sc = llm_plan.sc_on[::4]
        else:
            onehot = m.addMVar((24, 11), vtype=GRB.BINARY, name="tap")
            sc = m.addMVar((24, 3), vtype=GRB.BINARY, name="sc")
            change = m.addMVar(23, ub=1, name="change")
            sc_start = m.addMVar((23, 3), ub=1, name="sc_start")
            m.addConstr(onehot.sum(axis=1) == 1)
            for h in range(1, 24):
                m.addConstr(onehot[h] - onehot[h-1] <= change[h-1])
            m.addConstr(change.sum() <= settings["oltc_max_changes_after_initial_setting"])
            m.addConstr(sc_start >= sc[1:] - sc[:-1])
            m.addConstr(sc_start.sum(axis=0) + sc[0] <= 1)
            onehot.Start = np.eye(11)[llm_plan.tap[::4] + 5]
            sc.Start = llm_plan.sc_on[::4]
            root = onehot @ ((1 + step * tap_values) ** 2)
        for h in range(24):
            m.addConstr(v[h*4:h*4+4, 0] == root[h])
        for bus in range(1, 33):
            edge = parents[bus]
            out = children[bus]
            p_out = p[:, out].sum(axis=1) if out else 0
            q_out = q[:, out].sum(axis=1) if out else 0
            q_local = demand_q[:, bus]
            if bus in pv_pos:
                q_local = q_local - pv_q[:, pv_pos[bus]]
            if bus in sc_pos:
                q_local = q_local - sc[np.arange(96)//4, sc_pos[bus]] * sc_capacity
            m.addConstr(p[:, edge] == p_out + demand_p[:, bus] + r[edge] * ell[:, edge])
            m.addConstr(q[:, edge] == q_out + q_local + x[edge] * ell[:, edge])
        m.addConstr(v[:, dst] == v[:, src] - 2*(p*r + q*x) + ell*(r*r+x*x))
        if safe_upper:
            # Lossless (LinDistFlow) voltages bound every branch-flow solution from above, the exact AC one
            # included, so limiting them keeps the AC replay below the upper limit despite an inexact cone.
            p_hat = m.addMVar((96, 32), lb=-GRB.INFINITY, name="p_hat")
            q_hat = m.addMVar((96, 32), lb=-GRB.INFINITY, name="q_hat")
            v_hat = m.addMVar((96, 33), lb=-GRB.INFINITY, ub=GRB.INFINITY if soft else vmax**2, name="v_hat")
            for bus in range(1, 33):
                edge, out = parents[bus], children[bus]
                q_local = demand_q[:, bus]
                if bus in pv_pos:
                    q_local = q_local - pv_q[:, pv_pos[bus]]
                if bus in sc_pos:
                    q_local = q_local - sc[np.arange(96)//4, sc_pos[bus]] * sc_capacity
                m.addConstr(p_hat[:, edge] == (p_hat[:, out].sum(axis=1) if out else 0) + demand_p[:, bus])
                m.addConstr(q_hat[:, edge] == (q_hat[:, out].sum(axis=1) if out else 0) + q_local)
            m.addConstr(v_hat[:, 0] == v[:, 0])
            m.addConstr(v_hat[:, dst] == v_hat[:, src] - 2*(p_hat*r + q_hat*x))
            if soft:
                m.addConstr(over >= v_hat - 1.05**2)
        # Scalar Q constraints preserve the recognized rotated-cone form.
        pv, qv, iv, vv = [a.tolist() for a in (p, q, ell, v)]
        for t in range(96):
            for e, a in enumerate(src):
                m.addQConstr(pv[t][e]**2 + qv[t][e]**2 <= vv[t][a]*iv[t][e])
        m.setObjective(objective, GRB.MINIMIZE)
        m.optimize()
        result = dict(status=int(m.Status), solutions=int(m.SolCount), seconds=float(m.Runtime),
                      variables=m.NumVars, binary_variables=m.NumBinVars,
                      cone_constraints=m.NumQConstrs, soft_voltage=soft, loss_weight=loss_weight,
                      safe_upper=safe_upper,
                      objective="sum_abs_squared_voltage_deviation" if not soft else
                      "sum_abs_squared_voltage_deviation_plus_100_times_voltage_slack")
        if not m.SolCount:
            save(output / "summary.json", result)
            return result
        result["objective_value"] = float(m.ObjVal)
        if m.IsMIP:
            result["mip_gap"] = float(m.MIPGap)
            result["objective_bound"] = float(m.ObjBound)
        if fixed:
            plan = llm_plan
        else:
            plan = DayPlan(tap_values[onehot.X.argmax(axis=1)].repeat(4),
                           np.rint(sc.X).astype(int).repeat(4, axis=0)).validate()
        raw_reactive = pv_q.X * units
        correction = np.max(abs(raw_reactive - np.clip(raw_reactive, -q_limit, q_limit)))
        if correction > 1e-6:
            raise RuntimeError("Solver exceeded PV limits beyond tolerance")
        reactive = np.clip(raw_reactive, -q_limit, q_limit)
        v_model = np.sqrt(v.X)
        cone_slack = v.X[:, src] * ell.X - p.X**2 - q.X**2
        v_ac, loss, residual = ac_replay(net, day, plan, reactive)
        result.update(model_voltage=voltage_metrics(v_model), ac_voltage=voltage_metrics(v_ac),
                      max_voltage_difference_pu=float(abs(v_model-v_ac).max()),
                      max_cone_slack_pu_squared=float(cone_slack.max()),
                      min_cone_slack_pu_squared=float(cone_slack.min()),
                      model_loss_mwh=float((ell.X*r).sum()*units*.25),
                      ac_loss_mwh=float(loss.sum()*.25),
                      max_ac_balance_residual_mva=float(residual),
                      q_bound_roundoff_correction_mvar=float(correction),
                      oltc_changes=int(np.count_nonzero(np.diff(plan.tap))))
        np.savez_compressed(output / "replay.npz", tap=plan.tap, sc_on=plan.sc_on, pv_q_mvar=reactive,
                            v_model=v_model, v_ac=v_ac, cone_slack=cone_slack)
        save(output / "actions.json", dict(tap_hourly=plan.tap[::4].tolist(),
             sc_hourly=plan.sc_on[::4].tolist(), pv_q_mvar=reactive.tolist()))
        save(output / "summary.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--plans", required=True, type=Path)
    parser.add_argument("--date")
    parser.add_argument("--time-limit", type=float, default=90)
    args = parser.parse_args()
    dataset = Dataset()
    net = Case33Network(dataset.capacities)
    with Path("results/ppo-joint-v1/pairs.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if args.date:
        date = args.date
    else:
        dates = [row["date"] for row in rows]
        features = np.array([[dataset.day(d)[k].sum(axis=1).mean()
                              for k in ("p_load_mw", "p_pv_mw")] for d in dates])
        date = dates[np.linalg.norm((features-np.median(features, axis=0))/features.std(axis=0),
                                    axis=1).argmin()]
    comparison = next(row for row in rows if row["date"] == date)
    day = dataset.day(date)
    plan = parse_plan(json.loads((args.plans / date / "with/scored.json").read_bytes())["plan"])
    args.output.mkdir(parents=True)
    save(args.output / "config.json", dict(date=date, time_limit=args.time_limit,
         source_plans=str(args.plans), solver_version=list(gp.gurobi.version()),
         voltage_hard_limits=[.95, 1.05], objective="sum_abs_squared_voltage_deviation"))
    result = {"date": date, "llm_comparison": comparison}
    for name, fixed in (("joint", False), ("fixed_llm", True)):
        hard = solve(net, day, plan, args.output / name, fixed=fixed, time_limit=args.time_limit)
        result[name] = hard
        if hard["status"] == GRB.INFEASIBLE:
            result[name + "_soft"] = solve(net, day, plan, args.output / (name + "_soft"),
                                           fixed=fixed, soft=True, time_limit=args.time_limit)
        save(args.output / "summary.json", result)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
