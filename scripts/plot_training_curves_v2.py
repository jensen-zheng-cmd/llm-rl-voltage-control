"""Training curves in the style of the paper's Fig. 7 (v2, split A).

Phase 1 (episodes 1-206) is day-ahead improvement with PV reactive power zero:
the LLM arms show the 6 seed days and then the 200 formal pool-build days; the
Pure-RL day-ahead agent was trained on the same 200 formal days in the same
order, so it is drawn at episodes 7-206. Phase 2 (150 episodes) is intraday PPO
fine-tuning on the frozen day-ahead policy, same 150 days in the same order.

The y axis follows the paper's Fig. 7 scale, which matches Eq. (1): the daily sum
over nodes and 15-minute steps of |V - 1|, negated, i.e. our Eq. (11) daily
reward times the node count (33). As in the paper, a trailing 25-episode moving
average runs continuously across the phase boundary, so a step change at the
boundary appears as a 25-episode ramp. No-Reflexion is shown only in phase 1;
No-PT only in phase 2, its average starting from the Proposed phase-1 history
because both share the same LLM agent.

The second figure subtracts, for each episode, the reward of the Original plan
(tap 0, capacitors off, PV reactive power zero) on the same date, removing the
day-to-day difficulty that the moving average alone does not remove.
"""
import argparse
import csv
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from voltage_control.figures import plt
from finetune_ppo_v2 import v2_dataset
from run_llm_planning import NEUTRAL, VoltageControlEnv, score

SEED_DAYS, FORMAL, PHASE2, WINDOW, NODES = 6, 200, 150, 25, 33
PHASE1 = SEED_DAYS + FORMAL
COLORS = {"Proposed": "#d62728", "Pure-RL": "#1f77b4", "No-Reflexion": "#2ca02c", "No-PT": "#9467bd"}


def table(path):
    with Path(path).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def build(seed, formal):
    rows = table(seed) + table(formal)
    if len(rows) != PHASE1:
        raise ValueError(f"{formal}: expected {SEED_DAYS} seed + {FORMAL} formal days")
    return [r["date"] for r in rows], np.array([float(r["selected_reward"]) for r in rows]), \
        {r["date"]: float(r["neutral_reward"]) for r in rows}


def trailing(values):
    values = np.asarray(values, dtype=float)
    out = np.full(len(values), np.nan)
    for i in range(len(values)):
        window = values[max(0, i - WINDOW + 1):i + 1]
        window = window[~np.isnan(window)]
        if len(window):
            out[i] = window.mean()
    return out


def draw(curves, path, ylabel, title):
    fig, ax = plt.subplots(figsize=(8, 4.2))
    x = np.arange(1, PHASE1 + PHASE2 + 1)
    for name, (series, shown) in curves.items():
        y = trailing(series)
        y[~shown] = np.nan
        ax.plot(x, y, color=COLORS[name], lw=1.8, label=name)
    ax.axvline(PHASE1 + .5, color="0.4", ls="--", lw=1)
    top = ax.get_ylim()[1]
    ax.text(PHASE1 / 2, top, "日前策略改进（PV 无功为零）", ha="center", va="top", fontsize=9)
    ax.text(PHASE1 + PHASE2 / 2, top, "冻结日前策略，日内 PPO 微调", ha="center", va="top", fontsize=9)
    ax.set_xlabel("回合（天）；前 6 回合为 LLM 起步日")
    ax.set_ylabel(ylabel)
    ax.set_xlim(0, PHASE1 + PHASE2 + 1)
    ax.grid(alpha=.2)
    ax.legend(loc="lower right", fontsize=9)
    ax.set_title(title, fontsize=10, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--proposed-seed", type=Path, required=True, help="C2 seed run days.csv")
    parser.add_argument("--proposed-build", type=Path, required=True, help="C2 formal run days.csv")
    parser.add_argument("--norefl-seed", type=Path, required=True)
    parser.add_argument("--norefl-build", type=Path, required=True)
    parser.add_argument("--purerl-dayahead", type=Path, required=True, help="Pure-RL episode_rewards.csv")
    parser.add_argument("--finetune", action="append", required=True,
                        help="arm=episode_rewards.csv for proposed, purerl and nopt")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    p_dates, p_reward, neutral = build(args.proposed_seed, args.proposed_build)
    n_dates, n_reward, _ = build(args.norefl_seed, args.norefl_build)
    pure = table(args.purerl_dayahead)
    if [r["date"] for r in pure] != p_dates[SEED_DAYS:] or n_dates != p_dates:
        raise ValueError("Phase-1 dates must match the C2 seed and formal order")
    tune = {k: table(v) for k, v in (s.split("=", 1) for s in args.finetune)}
    t_dates = [r["date"] for r in tune["proposed"]]
    if any([r["date"] for r in rows] != t_dates for rows in tune.values()) or len(t_dates) != PHASE2:
        raise ValueError("Fine-tuning arms must share the same 150 dates in order")
    env = VoltageControlEnv(v2_dataset())
    neutral.update({d: score(env, d, NEUTRAL)["reward"] for d in t_dates if d not in neutral})
    dates = p_dates + t_dates
    gap = np.full(SEED_DAYS, np.nan)
    phase2 = {k: np.array([float(r["reward"]) for r in rows]) for k, rows in tune.items()}
    series = {
        "Proposed": np.r_[p_reward, phase2["proposed"]],
        "Pure-RL": np.r_[gap, [float(r["reward"]) for r in pure], phase2["purerl"]],
        "No-Reflexion": np.r_[n_reward, np.full(PHASE2, np.nan)],
        "No-PT": np.r_[p_reward, phase2["nopt"]],
    }
    shown = {name: ~np.isnan(s) for name, s in series.items()}
    shown["No-PT"][:PHASE1] = False
    original = np.array([neutral[d] for d in dates])
    args.output.mkdir(parents=True, exist_ok=True)
    draw({k: (v * NODES, shown[k]) for k, v in series.items()}, args.output / "training_curves.png",
         "日总偏差取负（Σ节点 Σ时段，论文图 7 量纲）",
         "训练曲线（33 节点 v2，单种子，25 回合滑动平均跨阶段连续计算）")
    draw({k: ((v - original) * NODES, shown[k]) for k, v in series.items()},
         args.output / "training_curves_vs_original.png", "相对同日 Original 的改进（同一量纲）",
         "相对同日 Original 的改进（Original：档位 0、电容器全关、PV 无功为零）")
    with (args.output / "training_curves.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["method", "episode", "date", "daily_reward_eq11", "paper_scale", "moving_average_25",
                         "original_daily_reward", "improvement_vs_original_paper_scale",
                         "improvement_moving_average_25"])
        for name, s in series.items():
            ma, rel = trailing(s * NODES), trailing((s - original) * NODES)
            for i in np.flatnonzero(shown[name]):
                writer.writerow([name, i + 1, dates[i], s[i], s[i] * NODES, ma[i], original[i],
                                 (s[i] - original[i]) * NODES, rel[i]])


if __name__ == "__main__":
    main()
