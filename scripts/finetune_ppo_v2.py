"""v2 stage D: LLM-planned fine-tuning of the pretrained PPO and joint validation (split A).

Subcommands
  plans     150 stratified training days; frozen 0.90 pool, nearest-day retrieval that
            excludes the day itself, one generation per day, no Reflexion.
  train     --arm proposed (critic warm-up, then 150 episodes), nowarmup (150 episodes),
            or nopt (PPO from scratch for 150 episodes on the same plans).
  validate  48 validation days with the C2 frozen-pool plans; all checkpoints plus the
            Original, No-RL and No-LLM baselines.
"""
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
from stable_baselines3.common.logger import configure

from evaluate_ppo_checkpoints import CHECKPOINTS, SaveAfterUpdate, model_file
from finetune_ppo import FixedPlanEnv
from run_llm_planning import (
    MODEL, NEUTRAL, PROVIDER, Dataset, VoltageControlEnv, closest, forecast_v2, parse_plan,
    save, score,
)
from run_llm_planning_v2 import METRICS as LLM_METRICS, build_payload, generate_plan, system_prompt
from validate_ppo_joint import simulate

SEED = 42
EPISODES, WARMUP_EPISODES = 150, 20
FINETUNE = dict(n_steps=960, batch_size=32, n_epochs=4, gamma=.9, gae_lambda=.95, clip_range=.3)
FINETUNE_LR, WARMUP_LR = 1e-5, 1e-4
PRETRAINED = Path(__file__).resolve().parents[1] / "results/models/ppo-pretrain-v2/improved"
METRICS = ("mad_pu", "violation_pct", "min_pu", "max_pu")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def v2_dataset():
    return Dataset(version="v2", split_scheme="A_half_month")


def select_days(dataset):
    """90 ordinary, 30 high-load and 30 high-PV training days (top 20% daily peaks), shuffled."""
    dates = dataset.split_dates("train").astype(str).tolist()
    peaks = np.array([[dataset.day(d)[name].sum(axis=1).max() for name in ("p_load_mw", "p_pv_mw")]
                      for d in dates])
    load, pv = peaks.T
    high_load, high_pv = load >= np.quantile(load, .8), pv >= np.quantile(pv, .8)
    rng = np.random.default_rng(SEED)
    load_ids = rng.choice(np.flatnonzero(high_load), 30, replace=False)
    pv_ids = rng.choice(np.setdiff1d(np.flatnonzero(high_pv), load_ids), 30, replace=False)
    ordinary_ids = rng.choice(np.flatnonzero(~high_load & ~high_pv), 90, replace=False)
    selected = [(dates[i], label) for label, ids in
                (("ordinary", ordinary_ids), ("high_load", load_ids), ("high_pv", pv_ids)) for i in ids]
    return [selected[i] for i in rng.permutation(len(selected))]


def mechanical(spec):
    plan = parse_plan(spec)
    tap, sc = plan.tap[::4], plan.sc_on[::4]
    return {"oltc_changes": int(np.count_nonzero(np.diff(tap))),
            "sc_switching": int(np.abs(np.diff(np.vstack([np.zeros((1, 3), int), sc]), axis=0)).sum())}


def retrieve_excluding(pool, prediction):
    others = [e for e in pool if e["forecast"]["date"] != prediction["date"]]
    index, sim = closest(others, prediction)
    return others[index], sim, len(others) < len(pool)


def plans(args):
    dataset = v2_dataset()
    env = VoltageControlEnv(dataset)
    regions = dataset.system["regions_from_paper_topology"]
    system = system_prompt(dataset.system)
    key = args.key_file.read_text(encoding="utf-8-sig").strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError("Key file must contain one API key")
    pool_bytes = args.pool.read_bytes()
    pool = json.loads(pool_bytes)
    train = set(dataset.split_dates("train").astype(str))
    if not pool or any(e["forecast"]["date"] not in train for e in pool):
        raise ValueError("Pool must contain training dates only")
    selection = select_days(dataset)[:args.limit] if args.limit else select_days(dataset)
    config = {"stage": "plans", "seed": SEED, "selection": selection, "pool": str(args.pool),
              "pool_sha256": hashlib.sha256(pool_bytes).hexdigest(), "provider": PROVIDER, "model": MODEL,
              "retrieval": "nearest pool day excluding the same date", "reflexion_rounds": 0,
              "generations_per_day": 1, "system_prompt_sha256": hashlib.sha256(system.encode()).hexdigest()}
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = args.output / "config.json"
    if manifest.exists():
        if json.loads(manifest.read_text(encoding="utf-8")) != json.loads(json.dumps(config)):
            raise ValueError("Plan configuration changed; choose a new output directory")
    else:
        save(manifest, config)
    records = []
    for i, (day, group) in enumerate(selection, 1):
        directory = args.output / day
        path = directory / "plan.json"
        if path.exists():
            records.append(json.loads(path.read_text(encoding="utf-8")))
            continue
        if directory.exists():
            n = 1
            while (args.output / f"{day}_incomplete_{n}").exists():
                n += 1
            directory.rename(args.output / f"{day}_incomplete_{n}")
        directory.mkdir()
        prediction = forecast_v2(dataset, day)
        save(directory / "forecast.json", prediction)
        reference, sim, self_in_pool = retrieve_excluding(pool, prediction)
        spec, attempt = generate_plan(key, build_payload(prediction, regions, reference), directory / "generation", system)
        metrics = score(env, day, spec)
        record = {"date": day, "group": group, "plan": spec, "reference_date": reference["forecast"]["date"],
                  "similarity": sim, "self_in_pool": self_in_pool,
                  "no_rl": {m: metrics[m] for m in LLM_METRICS}, **mechanical(spec)}
        save(path, record)
        records.append(record)
        print(f"Plans {i}/{len(selection)} {day} {group} sim={sim:.3f} "
              f"MAD(PV Q=0)={metrics['mad_pu']:.5f}", flush=True)
    save(args.output / "plans.json", records)


def actor_parameters(model):
    policy = model.policy
    return [*policy.mlp_extractor.policy_net.parameters(), *policy.action_net.parameters(), policy.log_std]


def train(args):
    torch.set_num_threads(1)
    dataset = v2_dataset()
    plan_config = json.loads((args.plans / "config.json").read_text(encoding="utf-8"))
    records = json.loads((args.plans / "plans.json").read_text(encoding="utf-8"))
    if [[r["date"], r["group"]] for r in records] != plan_config["selection"] or len(records) != EPISODES:
        raise ValueError("Expected the full 150-day plan set in selection order")
    norm_path = args.pretrained / "normalization.npz"
    with np.load(norm_path, allow_pickle=False) as norm:
        mean, scale = norm["mean"], norm["scale"]
    plan_list = [(r["date"], parse_plan(r["plan"])) for r in records]
    args.output.mkdir(parents=True)
    (args.output / "normalization.npz").write_bytes(norm_path.read_bytes())
    config = {"stage": "train", "arm": args.arm, "seed": SEED, "episodes": EPISODES, "steps": EPISODES * 96,
              "plans_sha256": digest(args.plans / "plans.json"), "plans": str(args.plans),
              "settings": FINETUNE, "learning_rate": FINETUNE_LR,
              "warmup": {"episodes": WARMUP_EPISODES, "days": "first 20 fine-tune days in order",
                         "learning_rate": WARMUP_LR, "trained": "value network only; policy and log std frozen"}
              if args.arm == "proposed" else None,
              "initial_model": None if args.arm == "nopt" else str(args.pretrained),
              "input_sha256": {"normalization": digest(norm_path),
                               **({} if args.arm == "nopt" else {"ppo": digest(args.pretrained / "ppo.zip")})},
              "nopt_settings": "pretraining schedule (1e-4 -> 1e-5 linear), initial std 0.37, same network"
              if args.arm == "nopt" else None}
    save(args.output / "config.json", config)
    started = perf_counter()
    warm = {}
    if args.arm == "nopt":
        env = FixedPlanEnv(dataset, plan_list, mean, scale)
        model = PPO("MlpPolicy", env, **FINETUNE, seed=SEED, device="cpu",
                    learning_rate=lambda remaining: 1e-5 + 9e-5 * remaining,
                    policy_kwargs=dict(net_arch=dict(pi=[256, 256], vf=[256, 256]),
                                       activation_fn=torch.nn.Tanh, log_std_init=float(np.log(.37))))
    else:
        lr = WARMUP_LR if args.arm == "proposed" else FINETUNE_LR
        first = FixedPlanEnv(dataset, plan_list[:WARMUP_EPISODES] if args.arm == "proposed" else plan_list, mean, scale)
        model = PPO.load(args.pretrained / "ppo.zip", env=first, device="cpu", seed=SEED,
                         learning_rate=lr, **FINETUNE)
        model.policy.optimizer.state.clear()
        env = first
        if args.arm == "proposed":
            before = [p.detach().clone() for p in actor_parameters(model)]
            for p in actor_parameters(model):
                p.requires_grad_(False)
            (args.output / "warmup").mkdir()
            model.set_logger(configure(str(args.output / "warmup"), ["csv"]))
            model.learn(total_timesteps=WARMUP_EPISODES * 96)
            if first.completed != WARMUP_EPISODES:
                raise RuntimeError("Warm-up budget mismatch")
            for p, old in zip(actor_parameters(model), before):
                if not torch.equal(p, old):
                    raise RuntimeError("Actor changed during critic warm-up")
                p.requires_grad_(True)
            warm = {"episode_rewards": first.episode_rewards, "updates": model._n_updates}
            model.learning_rate = FINETUNE_LR
            model._setup_lr_schedule()
            env = FixedPlanEnv(dataset, plan_list, mean, scale)
            model.set_env(env)
            model.save(model_file(args.output, 0))
    model.set_logger(configure(str(args.output), ["csv"]))
    callback = SaveAfterUpdate(args.output, FINETUNE["n_steps"], FINETUNE["n_epochs"], model._n_updates)
    model.learn(total_timesteps=EPISODES * 96, callback=callback, reset_num_timesteps=True)
    elapsed = perf_counter() - started
    if env.completed != EPISODES or model.num_timesteps != EPISODES * 96 or callback.saved != list(CHECKPOINTS):
        raise RuntimeError("Fine-tuning budget or checkpoint mismatch")
    reloaded = PPO.load(model_file(args.output, 150), device="cpu")
    probe = np.zeros(126, dtype=np.float32)
    np.testing.assert_array_equal(model.predict(probe, deterministic=True)[0],
                                  reloaded.predict(probe, deterministic=True)[0])
    with (args.output / "episode_rewards.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["episode", "date", "group", "reward"])
        writer.writerows((i, r["date"], r["group"], reward)
                         for i, (r, reward) in enumerate(zip(records, env.episode_rewards, strict=True), 1))
    save(args.output / "summary.json", {"arm": args.arm, "steps": model.num_timesteps, "episodes": env.completed,
         "training_seconds": elapsed, "reload_check": "passed", "warmup": warm or None,
         "checkpoints": list(CHECKPOINTS)})
    print(f"{args.arm}: trained {EPISODES} episodes in {elapsed:.1f}s", flush=True)


def evaluate_job(label, model_path, norm_path, plans_by_date, neutral):
    torch.set_num_threads(1)
    env = VoltageControlEnv(v2_dataset())
    model = PPO.load(model_path, device="cpu")
    with np.load(norm_path, allow_pickle=False) as norm:
        mean, scale = norm["mean"], norm["scale"]
    rows = []
    for date, spec in plans_by_date.items():
        rows.append({"date": date, **simulate(env, date, parse_plan(NEUTRAL if neutral else spec), model, mean, scale)})
    print(f"Validated {label}", flush=True)
    return label, rows


def paired(a, b):
    stats = {}
    for m in ("mad_pu", "violation_pct"):
        d = np.array([x[m] - y[m] for x, y in zip(a, b)])
        stats[m] = {"mean_delta": float(d.mean()), "standard_error": float(d.std(ddof=1) / np.sqrt(len(d))),
                    "lower": int((d < -1e-10).sum()), "higher": int((d > 1e-10).sum()),
                    "tied": int((np.abs(d) <= 1e-10).sum())}
    return stats


def validate(args):
    dataset = v2_dataset()
    dates = dataset.split_dates("validation").astype(str).tolist()
    with (args.c2 / "pairs.csv").open(encoding="utf-8", newline="") as f:
        c2 = {r["date"]: r for r in csv.DictReader(f)}
    if list(c2) != dates:
        raise ValueError("C2 validation must cover the 48 validation days in order")
    plan_records, plan_hashes = {}, {}
    for date in dates:
        path = args.c2 / date / args.c2_arm / "scored.json"
        plan_hashes[date] = digest(path)
        plan_records[date] = json.loads(path.read_text(encoding="utf-8"))
    llm_plans = {d: r["plan"] for d, r in plan_records.items()}
    runs = dict(spec.split("=", 1) for spec in args.run)
    norm_path = args.pretrained / "normalization.npz"
    norm_hash = digest(norm_path)
    jobs = [("pretrained_ep000", args.pretrained / "ppo.zip", False)]
    for arm, directory in runs.items():
        directory = Path(directory)
        if digest(directory / "normalization.npz") != norm_hash:
            raise ValueError(f"{arm} normalization differs from the pretrained model")
        jobs += [(f"{arm}_ep{ep:03}", model_file(directory, ep), False) for ep in CHECKPOINTS]
    if not args.skip_no_llm:
        jobs.append(("no_llm", model_file(Path(runs["proposed"]), 150), True))
    args.output.mkdir(parents=True)
    save(args.output / "config.json", {
        "stage": "validate", "dates": dates, "c2_source": str(args.c2), "c2_arm": args.c2_arm,
        "plan_record_sha256": plan_hashes, "runs": runs, "pretrained": str(args.pretrained),
        "models_sha256": {label: digest(path) for label, path, _ in jobs}, "normalization_sha256": norm_hash,
        "deterministic_policy": True, "final_proposed": "proposed_ep150 (fixed before validation)",
        "original_and_no_rl": "reused from the C2 validation records (PV reactive power zero)",
        "no_llm": None if args.skip_no_llm else "neutral plan (tap 0, capacitors off) with proposed_ep150"})
    started = perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(evaluate_job, label, path, norm_path, llm_plans, neutral) for label, path, neutral in jobs]
        results = dict(f.result() for f in futures)
    results["original"] = [{"date": d, **{m: float(c2[d][f"neutral_{m}"]) for m in METRICS}} for d in dates]
    results["no_rl"] = [{"date": d, **{m: plan_records[d]["metrics"][m] for m in METRICS}} for d in dates]
    order = ["original", "no_rl", *([] if args.skip_no_llm else ["no_llm"]), "pretrained_ep000",
             *[f"{arm}_ep{ep:03}" for arm in runs for ep in CHECKPOINTS]]
    with (args.output / "pairs.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date"] + [f"{a}_{m}" for a in order for m in METRICS])
        writer.writeheader()
        for i, d in enumerate(dates):
            writer.writerow({"date": d, **{f"{a}_{m}": results[a][i][m] for a in order for m in METRICS}})
    arms = {}
    for a in order:
        rows = results[a]
        arms[a] = {"mad_pu": float(np.mean([r["mad_pu"] for r in rows])),
                   "violation_pct": float(np.mean([r["violation_pct"] for r in rows])),
                   "violation_free_days": int(sum(r["violation_pct"] == 0 for r in rows)),
                   "min_pu": float(min(r["min_pu"] for r in rows)), "max_pu": float(max(r["max_pu"] for r in rows))}
    llm_mech = [mechanical(p) for p in llm_plans.values()]
    mech = {"llm_plans": {"grid_code_compliant_days": len(llm_mech),
                          "mean_oltc_changes": float(np.mean([m["oltc_changes"] for m in llm_mech])),
                          "mean_sc_switching": float(np.mean([m["sc_switching"] for m in llm_mech]))},
            "neutral_plan": mechanical(NEUTRAL)}
    pairs = [("proposed_ep150", "pretrained_ep000"), ("proposed_ep150", "nowarmup_ep150"),
             ("proposed_ep150", "nopt_ep150"), ("proposed_ep150", "no_rl"), ("proposed_ep150", "no_llm"),
             ("proposed_ep150", "original"), ("no_llm", "original"), ("nowarmup_ep150", "pretrained_ep000")]
    comparisons = {f"{a}_vs_{b}": paired(results[a], results[b]) for a, b in pairs if a in results and b in results}
    save(args.output / "summary.json", {"days": len(dates), "elapsed_seconds": perf_counter() - started,
         "arms": arms, "mechanical": mech, "paired": comparisons})
    print(json.dumps(arms, indent=1), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plans")
    p.add_argument("--key-file", type=Path, required=True)
    p.add_argument("--pool", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--limit", type=int, help="Smoke test: only the first N selected days")
    t = sub.add_parser("train")
    t.add_argument("--arm", choices=("proposed", "nowarmup", "nopt"), required=True)
    t.add_argument("--plans", type=Path, required=True)
    t.add_argument("--pretrained", type=Path, default=PRETRAINED)
    t.add_argument("--output", type=Path, required=True)
    v = sub.add_parser("validate")
    v.add_argument("--c2", type=Path, required=True, help="C2 validation run directory")
    v.add_argument("--c2-arm", default="t090")
    v.add_argument("--run", action="append", required=True, help="arm=training output directory")
    v.add_argument("--pretrained", type=Path, default=PRETRAINED)
    v.add_argument("--output", type=Path, required=True)
    v.add_argument("--workers", type=int, default=7)
    v.add_argument("--skip-no-llm", action="store_true", help="Omit the neutral-plan No-LLM arm")
    args = parser.parse_args()
    {"plans": plans, "train": train, "validate": validate}[args.command](args)


if __name__ == "__main__":
    main()
