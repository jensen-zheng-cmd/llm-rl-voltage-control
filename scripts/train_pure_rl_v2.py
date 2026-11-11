"""v2 Pure-RL baseline: a day-ahead PPO agent schedules the OLTC and capacitors.

The paper replaces the day-ahead LLM by a separate RL agent with a soft
constraint (penalty for grid-code-violating actions) and a hard constraint
(device states frozen once the switching allowance is used up). The penalty is
excluded from all reported rewards. The intraday agent is the same pretrained
PPO, fine-tuned on Pure-RL plans with `finetune_ppo_v2.py train --arm proposed`.

Subcommands
  train   200 episodes on the C2 formal training days in the same order as the
          LLM pool build; information equals the LLM input (noisy regional
          hourly net load forecasts) and scoring uses zero PV reactive power.
  plans   Deterministic plans for the 150 fine-tune days (stage D layout) and the
          48 validation days (C2 validation layout).
"""
import argparse
import csv
import json
from pathlib import Path
from time import perf_counter

import gymnasium as gym
import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.logger import configure

from finetune_ppo_v2 import digest, mechanical, select_days, v2_dataset
from run_llm_planning import NEUTRAL, VoltageControlEnv, forecast_v2, parse_plan, save, score
from run_llm_planning_v2 import METRICS, day_groups

SEED = 42
EPISODES = 200
PENALTY = .05
SETTINGS = dict(n_steps=240, batch_size=60, n_epochs=10, learning_rate=3e-4, gamma=.99, gae_lambda=.95,
                clip_range=.2, ent_coef=.01)
MAX_TAP_CHANGES = 4
OBS_SIZE = 4 * 24 + 24 + 1 + 3 + 1 + 3


def forecast_scale(dataset):
    """Largest absolute hourly total net-load forecast over all training days."""
    return float(max(np.abs(forecast_v2(dataset, d)["net_load_mw"]).max()
                     for d in dataset.split_dates("train").astype(str)))


def to_spec(tap, sc):
    changes = [[h, int(tap[h])] for h in range(1, 24) if tap[h] != tap[h - 1]]
    intervals = {}
    for i, bus in enumerate(("4", "9", "26")):
        on = np.flatnonzero(sc[:, i])
        intervals[bus] = None if not len(on) else [int(on[0]), int(on[-1]) + 1]
    spec = {"initial_tap": int(tap[0]), "tap_changes": changes, "sc_intervals": intervals}
    parse_plan(spec)
    return spec


class DayAheadEnv(gym.Env):
    """One day = 24 hourly decisions of the OLTC target position and three capacitor states."""
    observation_space = gym.spaces.Box(-np.inf, np.inf, (OBS_SIZE,), np.float32)
    action_space = gym.spaces.MultiDiscrete([11, 2, 2, 2])

    def __init__(self, dataset, dates, scale, penalty=PENALTY):
        self.dataset, self.dates, self.scale, self.penalty = dataset, list(dates), scale, penalty
        self.grid = VoltageControlEnv(dataset).network
        self.regions = list(dataset.system["regions_from_paper_topology"])
        self.next_day = 0
        self.episodes = []

    def _obs(self):
        hour = np.zeros(24)
        if self.hour < 24:
            hour[self.hour] = 1
        return np.concatenate([self.profile, hour, [self.current_tap / 5], self.current_sc,
                               [self.changes_left / MAX_TAP_CHANGES], self.sc_done]).astype(np.float32)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.date = self.dates[self.next_day % len(self.dates)]
        self.next_day += 1
        prediction = forecast_v2(self.dataset, self.date)
        self.profile = np.concatenate([*(prediction["regions"][r]["net_load_mw"] for r in self.regions),
                                       prediction["net_load_mw"]]) / self.scale
        self.data = self.dataset.day(self.date)
        self.hour, self.current_tap, self.changes_left = 0, 0, MAX_TAP_CHANGES
        self.current_sc, self.sc_done = np.zeros(3), np.zeros(3)
        self.tap, self.sc = np.zeros(24, dtype=int), np.zeros((24, 3), dtype=int)
        self.physical, self.violations = 0.0, 0
        return self._obs(), {"date": self.date}

    def step(self, action):
        target, requested = int(action[0]) - 5, np.asarray(action[1:], dtype=int)
        illegal = 0
        if self.hour == 0:
            self.current_tap = target
        elif target != self.current_tap:
            if self.changes_left:
                self.current_tap, self.changes_left = target, self.changes_left - 1
            else:
                illegal += 1
        for i in range(3):
            if requested[i] and not self.current_sc[i]:
                if self.sc_done[i]:
                    illegal += 1
                else:
                    self.current_sc[i] = 1
            elif not requested[i] and self.current_sc[i]:
                self.current_sc[i], self.sc_done[i] = 0, 1
        self.tap[self.hour], self.sc[self.hour] = self.current_tap, self.current_sc
        reward = 0.0
        for k in range(4 * self.hour, 4 * self.hour + 4):
            v = self.grid.solve(self.data["p_load_mw"][k], self.data["q_load_mvar"][k], self.data["p_pv_mw"][k],
                                self.current_tap, self.current_sc.astype(int), q_fraction=np.zeros(6)).voltage_pu
            reward -= float(np.mean(np.abs(v - 1)))
        self.physical += reward
        self.violations += illegal
        self.hour += 1
        done = self.hour == 24
        if done:
            self.episodes.append({"date": self.date, "reward": self.physical, "illegal_actions": self.violations,
                                  "plan": to_spec(self.tap, self.sc)})
        return self._obs(), reward - self.penalty * illegal, done, False, {}


def plan_for(model, env, day):
    env.dates, env.next_day = [day], 0
    obs, _ = env.reset()
    done = False
    while not done:
        action, _ = model.predict(obs, deterministic=True)
        obs, _, done, _, _ = env.step(action)
    return env.episodes[-1]


def train(args):
    torch.set_num_threads(1)
    dataset = v2_dataset()
    days = day_groups(dataset)["formal"]
    scale = forecast_scale(dataset)
    env = DayAheadEnv(dataset, days, scale)
    args.output.mkdir(parents=True)
    save(args.output / "config.json", {
        "stage": "pure_rl_train", "seed": SEED, "episodes": EPISODES, "days": days,
        "day_order": "C2 formal training days in the LLM pool-build order", "settings": SETTINGS,
        "policy": "MlpPolicy 256-256 Tanh, MultiDiscrete [11 tap targets, 3 x on/off]",
        "observation": "3 regional + total hourly net-load forecasts (forecast_v2) / scale, hour one-hot, "
                       "current tap / 5, capacitor states, tap changes left / 4, capacitor finished flags",
        "forecast_scale_mw": scale, "penalty_per_illegal_action": PENALTY,
        "hard_constraint": "tap kept after four changes; a capacitor switched off stays off",
        "reward": "hourly sum of node-mean absolute deviation with PV reactive power zero, minus penalty",
        "reported_reward": "physical reward only (penalty excluded)"})
    model = PPO("MlpPolicy", env, **SETTINGS, seed=SEED, device="cpu",
                policy_kwargs=dict(net_arch=dict(pi=[256, 256], vf=[256, 256]), activation_fn=torch.nn.Tanh))
    model.set_logger(configure(str(args.output), ["csv"]))
    started = perf_counter()
    model.learn(total_timesteps=EPISODES * 24)
    elapsed = perf_counter() - started
    if len(env.episodes) != EPISODES:
        raise RuntimeError("Episode budget mismatch")
    model.save(args.output / "dayahead.zip")
    with (args.output / "episode_rewards.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["episode", "date", "reward", "illegal_actions", "oltc_changes", "sc_switching"])
        for i, e in enumerate(env.episodes, 1):
            m = mechanical(e["plan"])
            writer.writerow([i, e["date"], e["reward"], e["illegal_actions"], m["oltc_changes"], m["sc_switching"]])
    rewards = [e["reward"] for e in env.episodes]
    save(args.output / "summary.json", {"episodes": EPISODES, "steps": model.num_timesteps, "training_seconds": elapsed,
         "first_25_mean_reward": float(np.mean(rewards[:25])), "last_25_mean_reward": float(np.mean(rewards[-25:])),
         "illegal_actions_total": int(sum(e["illegal_actions"] for e in env.episodes))})
    print(f"Pure-RL day-ahead: {EPISODES} episodes in {elapsed:.1f}s; reward first 25 "
          f"{np.mean(rewards[:25]):.4f}, last 25 {np.mean(rewards[-25:]):.4f}", flush=True)


def plans(args):
    torch.set_num_threads(1)
    dataset = v2_dataset()
    config = json.loads((args.model / "config.json").read_text(encoding="utf-8"))
    model = PPO.load(args.model / "dayahead.zip", device="cpu")
    env = DayAheadEnv(dataset, [], config["forecast_scale_mw"])
    scorer = VoltageControlEnv(dataset)
    selection = select_days(dataset)
    finetune, validation = args.output / "finetune_plans", args.output / "validation"
    finetune.mkdir(parents=True)
    validation.mkdir()
    source = {"model": str(args.model / "dayahead.zip"), "model_sha256": digest(args.model / "dayahead.zip"),
              "deterministic_policy": True}
    save(finetune / "config.json", {"stage": "plans", "seed": SEED, "selection": selection,
                                    "planner": "pure_rl_dayahead", **source})
    records = []
    for day, group in selection:
        episode = plan_for(model, env, day)
        metrics = score(scorer, day, episode["plan"])
        if not np.isclose(metrics["reward"], episode["reward"], rtol=0, atol=1e-9):
            raise RuntimeError(f"Day-ahead reward differs from the common scorer on {day}")
        records.append({"date": day, "group": group, "plan": episode["plan"],
                        "illegal_actions": episode["illegal_actions"],
                        "no_rl": {m: metrics[m] for m in METRICS}, **mechanical(episode["plan"])})
    save(finetune / "plans.json", records)
    rows = []
    for day in dataset.split_dates("validation").astype(str):
        episode = plan_for(model, env, day)
        metrics, neutral = score(scorer, day, episode["plan"]), score(scorer, day, NEUTRAL)
        (validation / day / "purerl").mkdir(parents=True)
        save(validation / day / "purerl" / "scored.json", {"plan": episode["plan"], "metrics": metrics,
                                                           "illegal_actions": episode["illegal_actions"]})
        rows.append({"date": day, **{f"neutral_{m}": neutral[m] for m in METRICS},
                     **{f"purerl_{m}": metrics[m] for m in METRICS}})
    with (validation / "pairs.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    save(validation / "config.json", {"stage": "pure_rl_validation_plans", **source})
    print(f"Pure-RL plans: fine-tune mean MAD(PV Q=0) {np.mean([r['no_rl']['mad_pu'] for r in records]):.5f}; "
          f"validation {np.mean([r['purerl_mad_pu'] for r in rows]):.5f} vs neutral "
          f"{np.mean([r['neutral_mad_pu'] for r in rows]):.5f}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    t = sub.add_parser("train")
    t.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("plans")
    p.add_argument("--model", type=Path, required=True, help="Pure-RL training output directory")
    p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    {"train": train, "plans": plans}[args.command](args)


if __name__ == "__main__":
    main()
