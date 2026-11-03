"""Replay and plot the single-day AC voltage comparison."""
import argparse
import json
from pathlib import Path

import numpy as np
import torch
from stable_baselines3 import PPO

from run_misocp_day import ac_replay, voltage_metrics
from run_llm_planning import Dataset, VoltageControlEnv, parse_plan, save as save_json
from train_ppo import flatten
from voltage_control.environment import DayPlan
from voltage_control.figures import plt, save


def ppo_voltage(env, day, plan, directory):
    model = PPO.load(directory / "ppo.zip", device="cpu")
    with np.load(directory / "normalization.npz", allow_pickle=False) as norm:
        mean, scale = norm["mean"], norm["scale"]
    obs, _ = env.reset(day, plan)
    voltages = []
    for t in range(96):
        action, _ = model.predict((flatten(obs) - mean) / scale, deterministic=True)
        obs, _, done, truncated, info = env.step(action)
        if truncated or done != (t == 95):
            raise RuntimeError("Unexpected episode boundary")
        voltages.append(info["snapshot"].voltage_pu)
    return np.stack(voltages)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(1)
    config = json.loads((args.run / "config.json").read_bytes())
    date = config["date"]
    dataset = Dataset()
    env = VoltageControlEnv(dataset)
    day = dataset.day(date)
    source = Path(config["source_plans"]) / date / "with" / "scored.json"
    llm = parse_plan(json.loads(source.read_bytes())["plan"])
    neutral = DayPlan.zero()
    model_dir = Path("results/models")
    zero = np.zeros((96, 6))
    traces = {
        "Original": ac_replay(env.network, day, neutral, zero)[0],
        "No-LLM diagnostic": ppo_voltage(env, date, neutral, model_dir / "ppo-v1"),
        "No-RL": ac_replay(env.network, day, llm, zero)[0],
        "LLM + pretrained PPO": ppo_voltage(env, date, llm, model_dir / "ppo-v1"),
        "LLM + fine-tuned PPO": ppo_voltage(env, date, llm, model_dir / "ppo-finetune-v1"),
    }
    baseline = json.loads((args.run / "summary.json").read_bytes())
    expected = [float(baseline["llm_comparison"][f"{arm}_mad_pu"])
                for arm in ("zero", "pretrained", "finetuned")]
    for name, directory in (("Fixed LLM + SOCP", "fixed_llm"), ("Joint MISOCP", "joint")):
        if not baseline[directory]["solutions"]:
            directory += "_soft"
            name += " (soft limits)"
        if directory not in baseline or not baseline[directory]["solutions"]:
            continue
        with np.load(args.run / directory / "replay.npz", allow_pickle=False) as data:
            traces[name] = data["v_ac"]
        expected.append(baseline[directory]["ac_voltage"]["mad_pu"])
    metrics = {name: voltage_metrics(v) for name, v in traces.items()}
    np.testing.assert_allclose([m["mad_pu"] for m in list(metrics.values())[2:]],
                               expected, atol=1e-12, rtol=0)
    for name, v in traces.items():
        metrics[name].update(bus29_min_pu=float(v[:, 28].min()),
                             bus29_max_pu=float(v[:, 28].max()))
    args.output.mkdir(parents=True, exist_ok=True)
    save_json(args.output / "voltage_comparison_metrics.json", {"date": date, "schemes": metrics})

    hours = np.arange(96)/4
    colors = ["#555555", "#9467BD", "#D55E00", "#0072B2", "#56A0CD", "#009E73", "#B23A6F"]
    values = np.stack(list(traces.values()))
    node = values[:, :, 28]
    ymin = min(.945, node.min() - .005)
    ymax = max(1.055, node.max() + .005)
    fig, axes = plt.subplots(4, 2, figsize=(12, 10), sharex=True, sharey=True, constrained_layout=True)
    for i, ((name, _), ax) in enumerate(zip(traces.items(), axes.flat)):
        ax.plot(hours, node[i], color=colors[i], lw=1.65)
        for limit in (.95, 1.05):
            ax.axhline(limit, color="#AB4B4B", lw=.8, ls=":")
        ax.axhline(1, color=".5", lw=.8, ls="--")
        ax.set(title=name, xlim=(0, 24), ylim=(ymin, ymax), xticks=np.arange(0, 25, 6))
        ax.grid(alpha=.15)
        ax.label_outer()
    axes.flat[-1].axis("off")
    axes.flat[-1].text(.04, .7, "Dashed: 1.00 p.u. target\nDotted: 0.95 / 1.05 p.u. limits\n"
                      "No-LLM diagnostic uses frozen pretrained PPO.\nAll curves are AC power-flow results.",
                      transform=axes.flat[-1].transAxes, va="center")
    for ax in axes[-2]:
        ax.tick_params(labelbottom=True)
    fig.supxlabel("Hour of day")
    fig.supylabel("Voltage magnitude (p.u.)")
    fig.suptitle(f"{date} | Bus 29 voltage")
    save(fig, args.output, "bus29_voltage")

    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, constrained_layout=True)
    for i, (name, v) in enumerate(traces.items()):
        style = "--" if name == "LLM + fine-tuned PPO" else "-"
        axes[0].plot(hours, v.min(axis=1), label=name, color=colors[i], ls=style, lw=1.35)
        axes[1].plot(hours, v.max(axis=1), color=colors[i], ls=style, lw=1.35)
    for ax, limit, label in zip(axes, (.95, 1.05), ("Lowest bus voltage", "Highest bus voltage")):
        ax.axhline(limit, color="#AB4B4B", ls=":", lw=1.2, label="Voltage limit")
        ax.axhline(1, color=".5", ls="--", lw=.8)
        ax.set(title=label, ylabel="Voltage (p.u.)", xlim=(0, 24), xticks=np.arange(0, 25, 3))
        ax.grid(alpha=.18)
    axes[0].legend(ncol=4, fontsize=8, loc="best")
    axes[1].set_xlabel("Hour of day")
    fig.suptitle(f"{date} | Network voltage envelope")
    save(fig, args.output, "network_voltage_envelope")


if __name__ == "__main__":
    main()
