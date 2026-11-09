"""Repeat the frozen-plan fine-tune and compare its checkpoints on validation dates."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import csv
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.logger import configure

from finetune_ppo import FixedPlanEnv
from run_llm_planning import Dataset, VoltageControlEnv, parse_plan, save
from validate_ppo_joint import METRICS, simulate

CHECKPOINTS = (30, 60, 100, 150)
RESULT_FIELDS = ["date", *METRICS]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model_file(directory, episode):
    return directory / f"ppo_{episode:03}.zip"


class SaveAfterUpdate(BaseCallback):
    def __init__(self, output, n_steps, n_epochs, initial_updates):
        super().__init__()
        self.output = output
        self.n_steps = n_steps
        self.n_epochs = n_epochs
        self.initial_updates = initial_updates
        self.saved = []

    def _save_if_due(self):
        episode = self.num_timesteps // 96
        if episode in CHECKPOINTS and episode not in self.saved:
            if self.num_timesteps != episode * 96:
                raise RuntimeError("Checkpoint does not fall on an episode boundary")
            expected = self.initial_updates + self.num_timesteps // self.n_steps * self.n_epochs
            if self.model._n_updates != expected:
                raise RuntimeError("Checkpoint taken before the final update for this batch")
            self.model.save(model_file(self.output, episode))
            self.saved.append(episode)
            print(f"Checkpoint saved after {episode} episodes ({self.num_timesteps} steps)", flush=True)

    def _on_step(self):
        return True

    def _on_rollout_start(self):
        self._save_if_due()

    def _on_training_end(self):
        self._save_if_due()


def training_replay(dataset, args, config, training_rows, mean, scale):
    env = FixedPlanEnv(dataset, [(r["date"], parse_plan(r["plan"])) for r in training_rows], mean, scale)
    settings = config["settings"]
    model = PPO.load(args.pretrained / "ppo.zip", env=env, device="cpu", seed=config["seed"], **settings)
    model.policy.optimizer.state.clear()
    model.set_logger(configure(str(args.output), ["csv"]))
    callback = SaveAfterUpdate(args.output, settings["n_steps"], settings["n_epochs"], model._n_updates)
    started = perf_counter()
    model.learn(total_timesteps=config["steps"], callback=callback)
    elapsed = perf_counter() - started
    if env.completed != config["episodes"] or model.num_timesteps != config["steps"]:
        raise RuntimeError("Fine-tuning budget mismatch")
    if callback.saved != list(CHECKPOINTS):
        raise RuntimeError("Missing checkpoint")
    previous = PPO.load(args.previous_final / "ppo.zip", device="cpu")
    old, new = previous.policy.state_dict(), model.policy.state_dict()
    identical = old.keys() == new.keys() and all(torch.equal(old[k], new[k]) for k in old)
    print(f"Training finished in {elapsed:.1f}s, final policy identical to prior run: {identical}", flush=True)
    return elapsed, identical


def evaluate_checkpoint(episode, output, plans):
    torch.set_num_threads(1)
    env = VoltageControlEnv(Dataset())
    model = PPO.load(model_file(output, episode), device="cpu")
    with np.load(output / "normalization.npz", allow_pickle=False) as norm:
        mean, scale = norm["mean"], norm["scale"]
    rows = []
    with (output / f"validation_{episode:03}.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        for i, (date, spec) in enumerate(plans.items(), 1):
            row = {"date": date, **simulate(env, date, parse_plan(spec), model, mean, scale)}
            rows.append(row)
            writer.writerow(row)
            f.flush()
            if i % 20 == 0 or i == len(plans):
                print(f"Checkpoint {episode}: validated {i}/{len(plans)} days", flush=True)
    return rows


def read_validation(args, dataset):
    config = json.loads((args.baseline / "config.json").read_bytes())
    with (args.baseline / "pairs.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    start, end = map(np.datetime64, dataset.runtime["splits"]["validation"])
    dates = np.arange(start, end, dtype="datetime64[D]").astype(str).tolist()
    if dates != [r["date"] for r in rows]:
        raise ValueError("Prior comparison must contain every validation date, in order")
    plans = {}
    for date in dates:
        source = Path(config["plans_source"]) / date / "with" / "scored.json"
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != config["plan_record_sha256"][date]:
            raise ValueError(f"Validation plan changed for {date}")
        plans[date] = json.loads(raw)["plan"]
    for arm, directory in (("pretrained", args.pretrained), ("finetuned", args.previous_final)):
        for name in ("ppo.zip", "normalization.npz"):
            if digest(directory / name) != config["model_sha256"][arm][name]:
                raise ValueError(f"Prior validation model input changed: {arm}/{name}")
    return rows, plans


def write_report(output, baseline_rows, evaluations, identical, train_seconds):
    def prior_rows(arm):
        return [{"date": r["date"], **{m: float(r[f"{arm}_{m}"]) for m in METRICS}}
                for r in baseline_rows]

    base = prior_rows("pretrained")
    stages = {0: base, **evaluations}
    if identical:
        stages[150] = prior_rows("finetuned")
    days = [r["date"] for r in base]
    summary = {}
    with (output / "pairs.csv").open("w", encoding="utf-8", newline="") as f:
        fields = ["date"] + [f"ep{ep}_{m}" for ep in stages for m in METRICS]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for i, date in enumerate(days):
            writer.writerow({"date": date, **{f"ep{ep}_{m}": values[i][m]
                             for ep, values in stages.items() for m in METRICS}})
    for ep, values in stages.items():
        if [r["date"] for r in values] != days:
            raise ValueError(f"Mismatched validation dates at checkpoint {ep}")
        columns = {m: np.array([r[m] for r in values]) for m in METRICS}
        stats = {m: float(reducer(columns[m])) for m, reducer in
                 zip(METRICS, (np.mean, np.mean, np.min, np.max))}
        for m in ("mad_pu", "violation_pct"):
            delta = columns[m] - np.array([r[m] for r in base])
            improved = int((delta < -1e-10).sum())
            worsened = int((delta > 1e-10).sum())
            stats[f"{m}_paired"] = dict(improved=improved, worsened=worsened,
                                       tied=len(delta)-improved-worsened)
        summary[ep] = stats
    save(output / "summary.json", dict(days=len(days), training_seconds=train_seconds,
         final_policy_identical_to_previous=identical, new_validation_steps=len(evaluations)*len(days)*96,
         checkpoints=summary))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training", type=Path, default=Path("results/ppo-finetune-v1"))
    parser.add_argument("--baseline", type=Path, default=Path("results/ppo-joint-v1"))
    parser.add_argument("--pretrained", type=Path, default=Path("results/models/ppo-v1"))
    parser.add_argument("--previous-final", type=Path, default=Path("results/models/ppo-finetune-v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    torch.set_num_threads(1)
    dataset = Dataset()
    config = json.loads((args.training / "config.json").read_bytes())
    training_rows = json.loads((args.training / "plans.json").read_bytes())
    if [[r["date"], r["group"]] for r in training_rows] != config["selection"]:
        raise ValueError("Training dates or order changed")
    norm_path = args.pretrained / "normalization.npz"
    for name, path in (("ppo", args.pretrained / "ppo.zip"), ("normalization", norm_path)):
        if digest(path) != config["input_sha256"][name]:
            raise ValueError(f"Training input changed: {name}")
    baseline_rows, plans = read_validation(args, dataset)
    args.output.mkdir(parents=True)
    (args.output / "normalization.npz").write_bytes(norm_path.read_bytes())
    save(args.output / "config.json", dict(original_training=config, checkpoints=CHECKPOINTS,
         plans_sha256=digest(args.training / "plans.json"),
         baseline_config_sha256=digest(args.baseline / "config.json"), workers=args.workers))
    with np.load(norm_path, allow_pickle=False) as norm:
        mean, scale = norm["mean"], norm["scale"]
    elapsed, identical = training_replay(dataset, args, config, training_rows, mean, scale)
    to_evaluate = CHECKPOINTS[:-1] if identical else CHECKPOINTS
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        jobs = {ep: pool.submit(evaluate_checkpoint, ep, args.output, plans) for ep in to_evaluate}
        evaluations = {ep: job.result() for ep, job in jobs.items()}
    write_report(args.output, baseline_rows, evaluations, identical, elapsed)
    print("All checkpoint comparisons finished", flush=True)


if __name__ == "__main__":
    main()
