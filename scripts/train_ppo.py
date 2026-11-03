"""Pretrain the intraday policy on legal randomized mechanical plans."""
import argparse
import csv
import json
from pathlib import Path
import sys
from time import perf_counter

import gymnasium as gym
import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback, CheckpointCallback
from stable_baselines3.common.logger import configure

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from voltage_control.data import Dataset
from voltage_control.environment import DayPlan, VoltageControlEnv


def sample_plan(rng, kind):
    if kind == 0:
        return DayPlan.zero()
    radius, max_changes = (2, 2) if kind == 1 else (5, 4)
    positions = np.arange(-radius, radius + 1)
    tap = np.full(24, rng.choice(positions))
    changes = rng.choice(np.arange(1, 24), rng.integers(max_changes + 1), replace=False)
    for hour in sorted(changes):
        tap[hour:] = rng.choice(positions[positions != tap[hour - 1]])
    sc = np.zeros((24, 3), dtype=int)
    for device in range(3):
        if rng.random() < 0.5:
            start, end = sorted(rng.choice(25, 2, replace=False))
            sc[start:end, device] = 1
    return DayPlan(tap.repeat(4), sc.repeat(4, axis=0)).validate()


def split_dates(dataset, split):
    return np.arange(*map(np.datetime64, dataset.runtime["splits"][split]), dtype="datetime64[D]")


def flatten(observation):
    return np.concatenate(list(observation.values())).astype(np.float32)


class TrainingEnv(gym.Env):
    observation_space = gym.spaces.Box(-np.inf, np.inf, (126,), np.float32)
    action_space = gym.spaces.Box(-1, 1, (6,), np.float32)

    def __init__(self, dataset, mean, scale, history):
        self.env = VoltageControlEnv(dataset)
        self.dates = split_dates(dataset, "train")
        self.mean, self.scale = mean, scale
        self.history = history

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        date = str(self.np_random.choice(self.dates))
        kind = int(self.np_random.choice(3, p=[0.25, 0.50, 0.25]))
        plan = sample_plan(self.np_random, kind)
        obs, info = self.env.reset(date, plan)
        self.history.write(json.dumps(dict(date=date, kind=kind, tap=plan.tap[::4].tolist(),
                                          sc=plan.sc_on[::4].tolist())) + "\n")
        self.history.flush()
        return (flatten(obs) - self.mean) / self.scale, info

    def step(self, action):
        obs, reward, done, truncated, info = self.env.step(action)
        return (flatten(obs) - self.mean) / self.scale, reward, done, truncated, info


def normalization(dataset, seed):
    # Estimate continuous observation statistics on training days using legal plans
    # and bounded random PV dispatch to cover nonzero observed reactive power.
    rng = np.random.default_rng(seed)
    env = VoltageControlEnv(dataset)
    observations = []
    dates = rng.choice(split_dates(dataset, "train"), 12, replace=False)
    for i, date in enumerate(dates):
        obs, _ = env.reset(str(date), sample_plan(rng, i % 3))
        observations.append(flatten(obs))
        for _ in range(95):
            obs, *_ = env.step(rng.uniform(-1, 1, size=6))
            observations.append(flatten(obs))
    values = np.stack(observations)
    mean = values.mean(axis=0)
    scale = np.maximum(values.std(axis=0), 1e-4)
    mean[-17:], scale[-17:] = 0, 1
    return mean, scale, dates.astype(str).tolist()


def validation_scenarios(dataset, seed):
    rng = np.random.default_rng(seed)
    dates = split_dates(dataset, "validation")
    # Include the highest peak-load and highest peak-PV days, plus a spread
    # through the calendar, with a fixed plan assigned to each date.
    days = [dataset.day(date) for date in dates]
    peak_load = np.argmax([day["p_load_mw"].sum(axis=1).max() for day in days])
    peak_pv = np.argmax([day["p_pv_mw"].sum(axis=1).max() for day in days])
    pilot = set(np.linspace(0, len(dates) - 1, 8, dtype=int)) | {peak_load, peak_pv}
    return [(str(date), sample_plan(rng, i % 3), i in pilot) for i, date in enumerate(dates)]


def evaluate(dataset, model, mean, scale, scenarios, output):
    env = VoltageControlEnv(dataset)
    fields = ["date", "policy", "mad_pu", "violation_pct", "min_pu", "max_pu"]
    rows = []
    with output.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for date, plan, _ in scenarios:
            for policy in ("zero", "ppo"):
                obs, _ = env.reset(date, plan)
                voltages = []
                for _ in range(96):
                    action = np.zeros(6) if policy == "zero" else model.predict(
                        (flatten(obs) - mean) / scale, deterministic=True)[0]
                    obs, _, _, _, info = env.step(action)
                    voltages.append(info["snapshot"].voltage_pu)
                v = np.stack(voltages)
                row = dict(date=date, policy=policy, mad_pu=np.abs(v - 1).mean(),
                           violation_pct=100 * ((v < .95) | (v > 1.05)).mean(),
                           min_pu=v.min(), max_pu=v.max())
                writer.writerow(row)
                rows.append(row)
            file.flush()
    return {policy: {
        key: float(reducer([r[key] for r in rows if r["policy"] == policy]))
        for key, reducer in zip(fields[2:], [np.mean, np.mean, np.min, np.max])
    } for policy in ("zero", "ppo")}


class ValidationCallback(BaseCallback):
    def __init__(self, dataset, mean, scale, scenarios, directory):
        super().__init__()
        self.dataset = dataset
        self.mean, self.scale = mean, scale
        self.scenarios = scenarios
        self.directory = directory

    def _on_step(self):
        if self.num_timesteps % (300 * 96) == 0:
            score = evaluate(self.dataset, self.model, self.mean, self.scale, self.scenarios,
                             self.directory / f"validation_{self.num_timesteps}.csv")
            print(f"Validation at {self.num_timesteps}: {score}", flush=True)
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episodes", choices=[20, 1500], type=int, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(1)
    dataset = Dataset()
    mean, scale, norm_dates = normalization(dataset, args.seed + 1)
    np.savez(args.output / "normalization.npz", mean=mean, scale=scale)
    scenarios = validation_scenarios(dataset, 2026)
    pilot = [row for row in scenarios if row[2]]
    settings = dict(n_steps=384, batch_size=32, n_epochs=4, gamma=.9, gae_lambda=.95, clip_range=.3)
    (args.output / "config.json").write_text(json.dumps(dict(
        seed=args.seed, episodes=args.episodes, splits=dataset.runtime["splits"],
        dataset=str(dataset.directory), dataset_manifest=dataset.info, environment=dataset.runtime,
        normalization_dates=norm_dates, plan_probabilities=[.25, .50, .25],
        learning_rates=[1e-4, 1e-5], hidden_layers=[256, 256], activation="Tanh",
        torch=torch.__version__, settings=settings,
        validation_plans=[dict(date=date, tap=plan.tap[::4].tolist(),
                               sc=plan.sc_on[::4].tolist(), pilot=small)
                          for date, plan, small in scenarios]), indent=2), encoding="utf-8")
    started = perf_counter()
    with (args.output / "training_scenarios.jsonl").open("w") as history:
        env = TrainingEnv(dataset, mean, scale, history)
        model = PPO("MlpPolicy", env, **settings, seed=args.seed, device="cpu",
                    learning_rate=lambda remaining: 1e-5 + 9e-5 * remaining,
                    policy_kwargs=dict(net_arch=dict(pi=[256, 256], vf=[256, 256]),
                                       activation_fn=torch.nn.Tanh))
        model.set_logger(configure(str(args.output), ["csv"]))
        callbacks = [
            CheckpointCallback(300 * 96, str(args.output), name_prefix="ppo"),
            ValidationCallback(dataset, mean, scale, pilot, args.output),
        ] if args.episodes == 1500 else []
        model.learn(args.episodes * 96, callback=callbacks)
    elapsed = perf_counter() - started
    model.save(args.output / "ppo")
    loaded = PPO.load(args.output / "ppo", device="cpu")
    observation = np.zeros(126, np.float32)
    np.testing.assert_array_equal(model.predict(observation, deterministic=True)[0],
                                  loaded.predict(observation, deterministic=True)[0])
    print(f"Training completed: {model.num_timesteps} steps in {elapsed:.1f}s", flush=True)
    score = evaluate(dataset, loaded, mean, scale, scenarios if args.episodes == 1500 else pilot,
                     args.output / "validation.csv")
    summary = dict(steps=model.num_timesteps, episodes=args.episodes,
                   training_seconds=elapsed, validation=score)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(summary, flush=True)


if __name__ == "__main__":
    main()
