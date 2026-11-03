"""Compare fixed LLM plans with zero, pretrained and fine-tuned PV dispatch."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np
import torch
from stable_baselines3 import PPO

from run_llm_planning import Dataset, VoltageControlEnv, parse_plan, save
from train_ppo import flatten

ARMS = ("zero", "pretrained", "finetuned")
METRICS = ("mad_pu", "violation_pct", "min_pu", "max_pu")


def simulate(env, date, plan, model, mean, scale):
    obs, _ = env.reset(date, plan)
    voltages = []
    for step in range(96):
        action, _ = model.predict((flatten(obs) - mean) / scale, deterministic=True)
        obs, _, done, truncated, info = env.step(action)
        if truncated or done != (step == 95):
            raise RuntimeError(f"Unexpected episode boundary on {date} at step {step}")
        voltages.append(info["snapshot"].voltage_pu)
    v = np.stack(voltages)
    return dict(mad_pu=float(np.abs(v - 1).mean()),
                violation_pct=float(100 * ((v < .95) | (v > 1.05)).mean()),
                min_pu=float(v.min()), max_pu=float(v.max()))


def summarize(rows):
    summary = {}
    for arm in ARMS:
        metrics = {m: np.array([r[f"{arm}_{m}"] for r in rows]) for m in METRICS}
        summary[arm] = dict(mad_pu=float(metrics["mad_pu"].mean()),
                            violation_pct=float(metrics["violation_pct"].mean()),
                            min_pu=float(metrics["min_pu"].min()),
                            max_pu=float(metrics["max_pu"].max()))
    for before, after in (("zero", "pretrained"), ("pretrained", "finetuned")):
        comparison = {}
        for metric in ("mad_pu", "violation_pct"):
            delta = np.array([r[f"{after}_{metric}"] - r[f"{before}_{metric}"] for r in rows])
            comparison[metric] = dict(mean_delta=float(delta.mean()),
                                      improved=int((delta < -1e-10).sum()),
                                      worsened=int((delta > 1e-10).sum()),
                                      tied=int((np.abs(delta) <= 1e-10).sum()))
        summary[f"{before}_to_{after}"] = comparison
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plans", type=Path, required=True)
    parser.add_argument("--pretrained", type=Path, required=True)
    parser.add_argument("--finetuned", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(1)
    dataset = Dataset()
    env = VoltageControlEnv(dataset)
    start, end = map(np.datetime64, dataset.runtime["splits"]["validation"])
    dates = np.arange(start, end, dtype="datetime64[D]").astype(str)
    with (args.plans / "pairs.csv").open(newline="", encoding="utf-8") as f:
        source_dates = [r["date"] for r in csv.DictReader(f)]
    if source_dates != dates.tolist():
        raise ValueError("Saved plans must cover exactly the validation dates in order")
    inputs = {}
    frozen = {}
    for date in dates:
        path = args.plans / date / "with" / "scored.json"
        raw = path.read_bytes()
        saved = json.loads(raw)
        parse_plan(saved["plan"])
        frozen[date] = saved
        inputs[date] = hashlib.sha256(raw).hexdigest()
    models = {}
    model_hashes = {}
    for arm in ARMS[1:]:
        directory = getattr(args, arm)
        model_path, norm_path = directory / "ppo.zip", directory / "normalization.npz"
        models[arm] = PPO.load(model_path, device="cpu")
        model_hashes[arm] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in (model_path, norm_path)}
    if model_hashes["pretrained"]["normalization.npz"] != model_hashes["finetuned"]["normalization.npz"]:
        raise ValueError("Expected identical pretrained and fine-tuned normalization")
    with np.load(args.pretrained / "normalization.npz", allow_pickle=False) as norm:
        mean, scale = norm["mean"], norm["scale"]
    args.output.mkdir(parents=True)
    save(args.output / "config.json", dict(dates=dates.tolist(), plans_source=str(args.plans),
         plan_record_sha256=inputs, model_sha256=model_hashes, deterministic=True,
         zero_arm="reused from original LLM retrieval validation", expected_new_steps=len(dates)*192))
    fields = ["date"] + [f"{arm}_{metric}" for arm in ARMS for metric in METRICS]
    rows = []
    started = perf_counter()
    with (args.output / "pairs.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for i, date in enumerate(dates, 1):
            saved = frozen[date]
            plan = parse_plan(saved["plan"])
            row = {"date": date}
            results = {"zero": saved["metrics"]}
            for arm, model in models.items():
                results[arm] = simulate(env, date, plan, model, mean, scale)
            for arm, metrics in results.items():
                row.update({f"{arm}_{m}": metrics[m] for m in METRICS})
            rows.append(row)
            writer.writerow(row)
            f.flush()
            elapsed = perf_counter() - started
            print(f"{i}/{len(dates)} {date} elapsed={elapsed:.1f}s "
                  f"MAD {row['zero_mad_pu']:.6f} / {row['pretrained_mad_pu']:.6f} / "
                  f"{row['finetuned_mad_pu']:.6f}", flush=True)
    save(args.output / "summary.json", dict(days=len(rows), new_steps=len(rows)*192,
         elapsed_seconds=perf_counter() - started, **summarize(rows)))


if __name__ == "__main__":
    main()
