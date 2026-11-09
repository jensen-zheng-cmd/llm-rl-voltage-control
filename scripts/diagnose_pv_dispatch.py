"""Compare pretrained PPO and fixed-plan SOCP reactive dispatch on saved days."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import torch
from stable_baselines3 import PPO

from run_llm_planning import Dataset, VoltageControlEnv, parse_plan, save
from run_misocp_day import voltage_metrics
from train_ppo import flatten
from voltage_control.figures import plt, save as save_figure


def replay_ppo(env, date, plan, model, mean, scale):
    obs, _ = env.reset(date, plan)
    snapshots = []
    for t in range(96):
        action, _ = model.predict((flatten(obs) - mean) / scale, deterministic=True)
        obs, _, done, truncated, info = env.step(action)
        if truncated or done != (t == 95):
            raise RuntimeError(f"Invalid episode boundary at {date}, step {t}")
        snapshots.append(info["snapshot"])
    return (np.stack([s.voltage_pu for s in snapshots]),
            np.stack([s.pv_q_mvar for s in snapshots]),
            np.stack([s.pv_q_limit_mvar for s in snapshots]))


def plot_day(output, date, buses, ppo_q, soc_q, limits, ppo_v, soc_v, focus, soft):
    hours = np.arange(96) / 4
    tag = "SOCP (soft limits)" if soft else "SOCP"
    fig, axes = plt.subplots(3, 2, figsize=(12, 8), sharex=True, sharey=True, constrained_layout=True)
    for i, ax in enumerate(axes.flat):
        ax.fill_between(hours, -limits[:, i], limits[:, i], color="#DDE4ED", label="Available range")
        ax.plot(hours, ppo_q[:, i], color="#2465A4", lw=1.5, label="Pretrained PPO")
        ax.plot(hours, soc_q[:, i], color="#C14F33", lw=1.5, ls="--", label=tag)
        ax.axvline(focus / 4, color=".35", ls=":", lw=1)
        ax.axhline(0, color=".65", lw=.7)
        ax.set(title=f"PV at bus {buses[i]}", xlim=(0, 24), xticks=np.arange(0, 25, 4))
        ax.grid(alpha=.16)
    axes[0, 0].legend(fontsize=8, loc="best")
    fig.supylabel("Reactive power (MVAr), positive = injection")
    fig.supxlabel("Hour of day")
    fig.suptitle(f"{date} | Same LLM plan, different inverter actions")
    save_figure(fig, output, "reactive_actions")

    fig, axes = plt.subplots(3, 1, figsize=(12, 7.5), sharex=True, constrained_layout=True)
    for i, (func, label) in enumerate(((lambda v: np.abs(v-1).mean(axis=1), "Mean absolute deviation"),
                                       (lambda v: v.min(axis=1), "Lowest bus voltage"),
                                       (lambda v: v.max(axis=1), "Highest bus voltage"))):
        ax = axes[i]
        ax.plot(hours, func(ppo_v), color="#2465A4", label="Pretrained PPO")
        ax.plot(hours, func(soc_v), color="#C14F33", ls="--", label=tag)
        ax.axvline(focus / 4, color=".35", ls=":", lw=1, label="Largest PPO deviation excess")
        if i:
            ax.axhline((.95, 1.05)[i-1], color=".45", ls=":", lw=1)
        ax.set(ylabel=f"{label}\n(p.u.)", xlim=(0, 24), xticks=np.arange(0, 25, 4))
        ax.grid(alpha=.16)
    axes[0].legend(ncol=3, fontsize=9)
    axes[-1].set_xlabel("Hour of day")
    fig.suptitle(f"{date} | AC replay under the same mechanical plan")
    save_figure(fig, output, "ac_voltage")


def diagnose(env, day, plan, buses, ppo_q, soc_q, limits, ppo_v, soc_v):
    excess = np.abs(ppo_v - 1).mean(axis=1) - np.abs(soc_v - 1).mean(axis=1)
    focus = int(np.argmax(excess))
    ranked = []
    for t in np.argsort(-excess, kind="stable"):
        if all(abs(t - other) >= 4 for other in ranked):
            ranked.append(int(t))
        if len(ranked) == 3:
            break
    chosen = {t: "largest_deviation_excess" for t in ranked}
    for label, reducer, comparator in (
        ("remaining_overvoltage", np.argmax, lambda v: v > 1.05),
        ("remaining_undervoltage", np.argmin, lambda v: v < .95),
    ):
        t, b = np.unravel_index(reducer(soc_v), soc_v.shape)
        if comparator(soc_v[t, b]):
            chosen[int(t)] = chosen.get(int(t), label)
    cases = []
    for t, label in chosen.items():
        def run(q):
            return env.network.solve(day["p_load_mw"][t], day["q_load_mvar"][t],
                                     day["p_pv_mw"][t], plan.tap[t], plan.sc_on[t], q_mvar=q).voltage_pu
        for actual_q, expected_v in ((ppo_q[t], ppo_v[t]), (soc_q[t], soc_v[t])):
            np.testing.assert_allclose(run(actual_q), expected_v, atol=1e-9, rtol=0)
        swaps = []
        for i, bus in enumerate(buses):
            q = ppo_q[t].copy()
            q[i] = soc_q[t, i]
            swaps.append(dict(bus=int(bus), **voltage_metrics(run(q))))
        cases.append(dict(step=t, hour=t / 4, reason=label, tap=int(plan.tap[t]),
                          sc=plan.sc_on[t].tolist(), pv_q_limit_mvar=limits[t].tolist(),
                          ppo_q_mvar=ppo_q[t].tolist(), socp_q_mvar=soc_q[t].tolist(),
                          ppo=voltage_metrics(ppo_v[t]), socp=voltage_metrics(soc_v[t]),
                          ppo_max_bus=int(ppo_v[t].argmax()+1), ppo_min_bus=int(ppo_v[t].argmin()+1),
                          socp_max_bus=int(soc_v[t].argmax()+1), socp_min_bus=int(soc_v[t].argmin()+1),
                          ppo_to_socp_one_inverter=swaps))
    return focus, cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(1)
    dataset = Dataset()
    env = VoltageControlEnv(dataset)
    buses = env.network.pv_buses + 1
    model_path = args.model / "ppo.zip"
    norm_path = args.model / "normalization.npz"
    model = PPO.load(model_path, device="cpu")
    with np.load(norm_path, allow_pickle=False) as norm:
        mean, scale = norm["mean"], norm["scale"]
    args.output.mkdir(parents=True)
    save(args.output / "config.json", dict(runs=[str(p) for p in args.runs],
         deterministic=True, device_buses=buses.tolist(), saturation_fraction=.99,
         inputs_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (model_path, norm_path)}))
    results = []
    for run in args.runs:
        config = json.loads((run / "config.json").read_bytes())
        original = json.loads((run / "summary.json").read_bytes())
        date = config["date"]
        day = dataset.day(date)
        source = Path(config["source_plans"]) / date / "with" / "scored.json"
        plan = parse_plan(json.loads(source.read_bytes())["plan"])
        fixed = "fixed_llm" if original["fixed_llm"]["solutions"] else "fixed_llm_soft"
        with np.load(run / fixed / "replay.npz", allow_pickle=False) as prior:
            np.testing.assert_array_equal(plan.tap, prior["tap"])
            np.testing.assert_array_equal(plan.sc_on, prior["sc_on"])
            soc_v, soc_q = prior["v_ac"], prior["pv_q_mvar"]
        ppo_v, ppo_q, limits = replay_ppo(env, date, plan, model, mean, scale)
        ppo, socp = voltage_metrics(ppo_v), voltage_metrics(soc_v)
        for m, value in ppo.items():
            np.testing.assert_allclose(value, float(original["llm_comparison"][f"pretrained_{m}"]),
                                       atol=1e-10, rtol=0)
        for m, value in socp.items():
            np.testing.assert_allclose(value, original[fixed]["ac_voltage"][m], atol=1e-10, rtol=0)
        if np.any(abs(soc_q) > limits + 1e-9):
            raise RuntimeError(f"Saved SOCP action exceeds PV limits on {date}")
        focus, cases = diagnose(env, day, plan, buses, ppo_q, soc_q, limits, ppo_v, soc_v)
        directory = args.output / date
        directory.mkdir()
        np.savez_compressed(directory / "traces.npz", ppo_q=ppo_q, socp_q=soc_q, pv_q_limit=limits,
                            ppo_voltage=ppo_v, socp_voltage=soc_v, tap=plan.tap, sc=plan.sc_on)
        with (directory / "dispatch.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["hour", "pv_bus", "limit_mvar", "ppo_q_mvar", "socp_q_mvar",
                             "ppo_local_voltage_pu", "socp_local_voltage_pu"])
            for t in range(96):
                for i, bus in enumerate(buses):
                    writer.writerow([t/4, bus, limits[t, i], ppo_q[t, i], soc_q[t, i],
                                     ppo_v[t, bus-1], soc_v[t, bus-1]])
        plot_day(directory, date, buses, ppo_q, soc_q, limits, ppo_v, soc_v, focus, fixed.endswith("soft"))
        eps = 1e-8
        ppo_sat = (limits > eps) & (abs(ppo_q) >= .99 * limits)
        soc_sat = (limits > eps) & (abs(soc_q) >= .99 * limits)
        results.append(dict(date=date, model=fixed, ppo=ppo, socp=socp,
                            ppo_near_limit=int(ppo_sat.sum()), socp_near_limit=int(soc_sat.sum()),
                            both_near_same_limit=int((ppo_sat & soc_sat & (ppo_q*soc_q > 0)).sum()),
                            opposite_sign=int(((ppo_q*soc_q < 0) &
                                               (abs(ppo_q) >= .01) & (abs(soc_q) >= .01)).sum()),
                            feasible_actions_per_method=96*len(buses),
                            focus=cases))
        print(date, "MAD", ppo["mad_pu"], "->", socp["mad_pu"], "focus", focus/4, flush=True)
    save(args.output / "summary.json", results)


if __name__ == "__main__":
    main()
