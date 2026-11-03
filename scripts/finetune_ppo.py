"""Generate fixed day plans and fine-tune PPO on 150 training days."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
from time import perf_counter

import gymnasium as gym
import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.logger import configure

from run_llm_planning import MODEL, PROVIDER, Dataset, VoltageControlEnv, closest, forecast, parse_plan, save
from train_ppo import flatten
from validate_llm_retrieval import evaluate_arm

SEED = 42
SETTINGS = {"learning_rate": 1e-5, "n_steps": 192, "batch_size": 32,
            "n_epochs": 4, "gamma": .9, "gae_lambda": .95, "clip_range": .3}


def select_days(dataset, excluded):
    start, end = map(np.datetime64, dataset.runtime["splits"]["train"])
    dates = [str(day) for day in np.arange(start, end, dtype="datetime64[D]")
             if str(day) not in excluded]
    peaks = []
    for day in dates:
        data = dataset.day(day)
        peaks.append([data[name].sum(axis=1).max() for name in ("p_load_mw", "p_pv_mw")])
    load, pv = np.array(peaks).T
    high_load = load >= np.quantile(load, .8)
    high_pv = pv >= np.quantile(pv, .8)
    rng = np.random.default_rng(SEED)
    # Draw without replacement. A day qualifying for both tails can be assigned only once.
    load_ids = rng.choice(np.flatnonzero(high_load), 30, replace=False)
    pv_ids = rng.choice(np.setdiff1d(np.flatnonzero(high_pv), load_ids), 30, replace=False)
    ordinary_ids = rng.choice(np.flatnonzero(~high_load & ~high_pv), 90, replace=False)
    selected = [(dates[i], label) for label, indices in
                (("ordinary", ordinary_ids), ("high_load", load_ids), ("high_pv", pv_ids))
                for i in indices]
    return [selected[i] for i in rng.permutation(len(selected))]


class FixedPlanEnv(gym.Env):
    observation_space = gym.spaces.Box(-np.inf, np.inf, (126,), np.float32)
    action_space = gym.spaces.Box(-1, 1, (6,), np.float32)

    def __init__(self, dataset, plans, mean, scale):
        self.env = VoltageControlEnv(dataset)
        self.plans = plans
        self.mean, self.scale = mean, scale
        self.next_day = 0
        self.completed = 0
        self.reward_sum = 0.0
        self.episode_rewards = []

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        date, plan = self.plans[self.next_day % len(self.plans)]
        self.next_day += 1
        self.reward_sum = 0.0
        obs, info = self.env.reset(date, plan)
        return (flatten(obs) - self.mean) / self.scale, info

    def step(self, action):
        obs, reward, done, truncated, info = self.env.step(action)
        self.reward_sum += reward
        if done:
            self.completed += 1
            self.episode_rewards.append(self.reward_sum)
        return (flatten(obs) - self.mean) / self.scale, reward, done, truncated, info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--pool", required=True, type=Path)
    parser.add_argument("--exclude-config", required=True, type=Path, nargs="+")
    parser.add_argument("--pretrained", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    torch.set_num_threads(1)
    dataset = Dataset()
    key = args.key_file.read_text(encoding="utf-8-sig").strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError("Key file must contain one API key")
    pool = json.loads(args.pool.read_bytes())
    excluded = {day for cfg in args.exclude_config for day in json.loads(cfg.read_bytes())["dates"]}
    excluded.update(e["forecast"]["date"] for e in pool)
    selection = select_days(dataset, excluded)
    config = {"provider": PROVIDER, "model": MODEL, "seed": SEED, "selection": selection, "excluded_training_dates": len(excluded),
              "settings": SETTINGS, "episodes": 150, "steps": 14400,
              "input_sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in (
                  ("pool", args.pool), ("ppo", args.pretrained / "ppo.zip"),
                  ("normalization", args.pretrained / "normalization.npz"))},
              "optimizer": "reset Adam state; retain pretrained policy and value weights"}
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = args.output / "config.json"
    if manifest.exists():
        if json.loads(manifest.read_bytes()) != json.loads(json.dumps(config)):
            raise ValueError("Run configuration changed; choose a new output directory")
        if (args.output / "summary.json").exists():
            raise ValueError("Run is already complete")
    else:
        save(manifest, config)
        (args.output / "frozen_pool.json").write_bytes(args.pool.read_bytes())

    env = VoltageControlEnv(dataset)
    records = []
    for i, (day, group) in enumerate(selection):
        directory = args.output / day
        directory.mkdir(exist_ok=True)
        record_path = directory / "plan.json"
        if record_path.exists():
            record = json.loads(record_path.read_bytes())
        else:
            prediction = forecast(dataset, day)
            save(directory / "forecast.json", prediction)
            index, similarity = closest(pool, prediction)
            trial = len(list(directory.glob("generation_*")))
            evaluate_arm(key, env, day, prediction, directory / f"generation_{trial}", pool[index])
            evaluated = json.loads((directory / f"generation_{trial}" / "scored.json").read_bytes())
            record = {"date": day, "group": group, "plan": evaluated["plan"],
                      "reference_date": pool[index]["forecast"]["date"], "similarity": similarity}
            save(record_path, record)
        records.append(record)
        print(f"Plans {i + 1}/150 {day} {group}", flush=True)
    save(args.output / "plans.json", records)

    norm_path = args.pretrained / "normalization.npz"
    with np.load(norm_path, allow_pickle=False) as norm:
        train_env = FixedPlanEnv(dataset, [(r["date"], parse_plan(r["plan"])) for r in records],
                                norm["mean"], norm["scale"])
    model = PPO.load(args.pretrained / "ppo.zip", env=train_env, device="cpu", seed=SEED, **SETTINGS)
    model.policy.optimizer.state.clear()
    model.set_logger(configure(str(args.output), ["csv"]))
    print("Starting PPO fine-tuning: 14400 steps", flush=True)
    started = perf_counter()
    model.learn(total_timesteps=14400)
    if model.num_timesteps != 14400 or train_env.completed != 150:
        raise RuntimeError("Fine-tuning budget mismatch")
    model.save(args.output / "ppo")
    (args.output / "normalization.npz").write_bytes(norm_path.read_bytes())
    reloaded = PPO.load(args.output / "ppo", device="cpu")
    probe = np.zeros(126, dtype=np.float32)
    np.testing.assert_array_equal(model.predict(probe, deterministic=True)[0],
                                  reloaded.predict(probe, deterministic=True)[0])
    with (args.output / "episode_rewards.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "group", "reward"])
        writer.writerows((r["date"], r["group"], reward)
                         for r, reward in zip(records, train_env.episode_rewards, strict=True))
    summary = {"steps": model.num_timesteps, "episodes": train_env.completed,
               "training_seconds": perf_counter() - started, "reload_check": "passed",
               "validation": "deferred"}
    save(args.output / "summary.json", summary)
    print(summary, flush=True)


if __name__ == "__main__":
    main()
