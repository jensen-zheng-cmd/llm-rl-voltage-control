"""v2 day-ahead LLM experience-pool building and frozen-pool validation (split A).

Information follows the paper: the LLM sees noisy hourly regional and total net
load forecasts, and feedback is limited to the daily reward plus hourly region
and total average voltages. Scoring uses the true 15-minute data with zero PV
reactive power. v1 entry points and results are left unchanged.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

import numpy as np

from run_llm_planning import (
    FORECAST_SEED, MODEL, NEUTRAL, PROVIDER, Dataset, VoltageControlEnv, closest, feedback_v2,
    forecast_v2, generate, save, score,
)

ORDER_SEED = 2026
FORMAL_DAYS, PILOT_DAYS = 200, 3
REFLEXION_ROUNDS = 3
METRICS = ("reward", "mad_pu", "violation_pct", "min_pu", "max_pu")


def system_prompt(system):
    devices = system["paper_devices"]
    lines = []
    for name, buses in system["regions_from_paper_topology"].items():
        sc = [b for b in devices["sc_buses"] if b in buses]
        pv = [b for b in devices["pv_buses"] if b in buses]
        lines.append(f"- {name}: buses {buses}; capacitor at bus {sc}; PV at buses {pv}")
    regions = "\n".join(lines)
    return f"""You schedule an OLTC and three switched capacitors in the balanced
Baran-Wu 33-bus radial feeder. Bus 1 is the substation with the OLTC. The
feeder is divided into three regions:
{regions}

Input: day-ahead hourly forecasts of net active load (load minus PV, MW) for
each region and for the whole feeder. Negative values mean reverse power flow.
Forecasts contain errors. When feedback is provided, it gives the total daily
reward (higher is better, 0 is ideal) and the hourly average voltage of each
region and of the whole feeder. Aim to minimize summed absolute node voltage
deviations from 1.0 pu and keep every node within 0.95-1.05 pu; node voltages
spread around the regional averages, and remote buses deviate the most. High
net import tends to cause undervoltage, reverse flow can cause overvoltage.
Higher taps and energized capacitors usually raise voltage. Each capacitor
injects {devices["sc_capacity_each_mvar"]} MVAr. PV reactive power is zero.

Return only a JSON object with this exact schema, for example:
{{"initial_tap":0,"tap_changes":[[10,-1],[17,1]],"sc_intervals":{{"4":null,"9":[17,22],"26":null}}}}
Use integer tap positions -5 through 5 (each step is {devices["oltc_voltage_step_pu"]} pu), no more than
four changes at strictly increasing integer hours 1 through 23, and avoid
redundant changes. Capacitor intervals are [start, end) with integer hours
0 <= start < end <= 24; null means off all day. Use only buses 4, 9 and 26.
Each capacitor may have at most one interval. Output the full schedule.
Forecast arrays start at hour 0."""


def representative_days(dataset, dates):
    """Two ordinary, two peak-load and two peak-PV training days (v1 rule on v2 dates)."""
    days = [dataset.day(day) for day in dates]
    load = np.asarray([d["p_load_mw"].sum(axis=1) for d in days])
    pv = np.asarray([d["p_pv_mw"].sum(axis=1) for d in days])
    features = np.column_stack([load.mean(axis=1), pv.mean(axis=1)])
    ordinary = np.linalg.norm((features - np.median(features, axis=0)) / features.std(axis=0), axis=1)
    picks = []
    for order in (np.argsort(ordinary), np.argsort(-load.max(axis=1)), np.argsort(-pv.max(axis=1))):
        picks.extend([dates[i] for i in order if dates[i] not in picks][:2])
    return picks


def day_groups(dataset):
    train = dataset.split_dates("train").astype(str).tolist()
    seeds = representative_days(dataset, train)
    rest = sorted(set(train) - set(seeds))
    order = np.random.default_rng(ORDER_SEED).permutation(rest).tolist()
    return {"seed": seeds, "formal": order[:FORMAL_DAYS],
            "pilot": order[FORMAL_DAYS:FORMAL_DAYS + PILOT_DAYS],
            "unused": order[FORMAL_DAYS + PILOT_DAYS:]}


def build_payload(prediction, regions, reference=None, attempts=()):
    payload = {"forecast": prediction}
    if reference is not None:
        payload["similar_day"] = {"forecast": reference["forecast"], **feedback_v2(reference, regions)}
    if attempts:
        payload["previous_attempts"] = [feedback_v2(entry, regions) for entry in attempts]
        payload["task"] = "Revise the schedule using the same-day simulated voltage feedback."
    return payload


def generate_plan(key, payload, directory, system):
    """One plan with up to two format repairs and backoff on transient API failures."""
    payload = dict(payload)
    attempt = directory
    for repair in range(3):
        attempt.mkdir()
        for wait in (15, 30, 60, 120, 240, None):
            try:
                return generate(key, payload, attempt, system), attempt
            except RuntimeError as error:
                if wait is None:
                    raise
                with (attempt / "api_errors.log").open("a", encoding="utf-8") as log:
                    log.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {error}; retry in {wait}s\n")
                time.sleep(wait)
            except ValueError as error:
                if repair == 2:
                    raise
                payload["format_correction"] = {
                    "error": str(error),
                    "previous_output": json.loads(
                        (attempt / "response.json").read_text(encoding="utf-8"))["content"]
                    if (attempt / "response.json").exists() else None,
                    "instruction": "Correct the invalid schedule and return the full JSON plan.",
                }
                attempt = directory.with_name(f"{directory.name}_repair_{repair + 1}")
                break


def brief(entry):
    return {"plan": entry["plan"], **{m: entry["metrics"][m] for m in METRICS}}


def save_state(output, state):
    temporary = output / "state.json.tmp"
    temporary.write_text(json.dumps(state, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(temporary, output / "state.json")
    save(output / "pool.json", state["pool"])
    save(output / "summary.json", state["days"])


def load_state(output, initial):
    path = output / "state.json"
    if not path.exists():
        return initial
    state = json.loads(path.read_text(encoding="utf-8"))
    done = {row["date"] for row in state["days"]}
    for child in output.iterdir():
        if child.is_dir() and child.name[:4].isdigit() and len(child.name) == 10 and child.name not in done:
            n = 1
            while (output / f"{child.name}_incomplete_{n}").exists():
                n += 1
            child.rename(output / f"{child.name}_incomplete_{n}")
    return state


def prepare_output(output, resume, config):
    if resume:
        stored = json.loads((output / "config.json").read_text(encoding="utf-8"))
        if stored != config:
            raise ValueError("Resume configuration differs from the stored run")
    else:
        output.mkdir(parents=True)
        save(output / "config.json", config)


def check_pool(pool, train):
    if not pool or any(entry["forecast"]["date"] not in train for entry in pool):
        raise ValueError("Experience pool must be nonempty and contain training dates only")


def build(args, key, dataset, env, system, groups):
    regions = dataset.system["regions_from_paper_topology"]
    train = set(dataset.split_dates("train").astype(str))
    days = groups[args.stage]
    initial_pool, pool_hash = [], None
    if args.stage != "seed":
        pool_bytes = args.pool.read_bytes()
        initial_pool, pool_hash = json.loads(pool_bytes), hashlib.sha256(pool_bytes).hexdigest()
        check_pool(initial_pool, train)
    config = {
        "version": "v2_c2", "stage": args.stage, "split_scheme": dataset.split_scheme, "dates": days,
        "threshold": args.threshold, "reflexion_rounds": REFLEXION_ROUNDS, "order_seed": ORDER_SEED,
        "forecast": "forecast_v2: regional hourly net load, aggregated then multiplicative Gaussian sd 0.05",
        "forecast_seed": FORECAST_SEED, "feedback": "feedback_v2: reward and hourly region/total voltages",
        "provider": PROVIDER, "model": MODEL, "temperature": .7, "top_p": .8, "pv_q_fraction": 0,
        "system_prompt_sha256": hashlib.sha256(system.encode()).hexdigest(),
        "initial_pool": str(args.pool) if args.pool else None, "initial_pool_sha256": pool_hash,
        "seed_rule": "all six representative days are added; neutral plan kept when better" if args.stage == "seed" else None,
    }
    prepare_output(args.output, args.resume, config)
    state = load_state(args.output, {"pool": initial_pool, "days": []})
    pool = state["pool"]
    for day in days[len(state["days"]):]:
        started = time.time()
        directory = args.output / day
        directory.mkdir()
        prediction = forecast_v2(dataset, day)
        save(directory / "forecast.json", prediction)
        baseline = {"forecast": prediction, "plan": NEUTRAL, "metrics": score(env, day, NEUTRAL)}
        # Seed days form the cold-start pool independently, without retrieval.
        index, sim = closest(pool, prediction) if args.stage != "seed" else (None, 0.0)
        reference = pool[index] if index is not None else None
        candidates = []
        for round_id in range(REFLEXION_ROUNDS + 1):
            payload = build_payload(prediction, regions, reference, candidates)
            plan, attempt = generate_plan(key, payload, directory / f"round_{round_id}", system)
            entry = {"forecast": prediction, "plan": plan, "metrics": score(env, day, plan)}
            save(attempt / "scored.json", entry)
            candidates.append(entry)
        best = max(candidates, key=lambda e: e["metrics"]["reward"])
        if args.stage == "seed":
            if baseline["metrics"]["reward"] > best["metrics"]["reward"]:
                best = baseline
            pool.append(best)
            update = "added"
        elif sim < args.threshold:
            pool.append(best)
            update = "added"
        elif best["metrics"]["reward"] > pool[index]["metrics"]["reward"]:
            pool[index] = best
            update = "replaced"
        else:
            update = "retained"
        row = {"date": day, "reference_date": reference["forecast"]["date"] if reference else None,
               "similarity": sim, "pool_update": update, "pool_size_after": len(pool),
               "neutral": brief(baseline), "rounds": [brief(e) for e in candidates],
               "selected_is_neutral": best is baseline, "selected": brief(best),
               "seconds": round(time.time() - started, 1)}
        save(directory / "summary.json", row)
        state["days"].append(row)
        save_state(args.output, state)
        print(f"{len(state['days'])}/{len(days)} {day} sim={sim:.3f} neutral={baseline['metrics']['reward']:.4f} "
              f"rounds={[round(e['metrics']['reward'], 4) for e in candidates]} {update} "
              f"pool={len(pool)} {row['seconds']}s", flush=True)
    write_build_tables(args.output, state)


def write_build_tables(output, state):
    rows = []
    for row in state["days"]:
        flat = {"date": row["date"], "reference_date": row["reference_date"], "similarity": row["similarity"],
                "pool_update": row["pool_update"], "pool_size_after": row["pool_size_after"],
                "neutral_reward": row["neutral"]["reward"], "selected_is_neutral": row["selected_is_neutral"],
                "seconds": row["seconds"]}
        for i, r in enumerate(row["rounds"]):
            flat[f"round{i}_reward"] = r["reward"]
            flat[f"round{i}_violation_pct"] = r["violation_pct"]
        flat["selected_reward"] = row["selected"]["reward"]
        flat["selected_mad_pu"] = row["selected"]["mad_pu"]
        flat["selected_violation_pct"] = row["selected"]["violation_pct"]
        rows.append(flat)
    with (output / "days.csv").open("w", newline="", encoding="utf-8") as table:
        writer = csv.DictWriter(table, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def paired(rows, a, b):
    stats = {}
    for metric in ("mad_pu", "violation_pct", "reward"):
        delta = np.asarray([r[f"{a}_{metric}"] - r[f"{b}_{metric}"] for r in rows])
        stats[metric] = {"mean_delta": float(delta.mean()),
                         "standard_error": float(delta.std(ddof=1) / np.sqrt(len(delta))) if len(delta) > 1 else None,
                         "lower": int((delta < -1e-10).sum()), "higher": int((delta > 1e-10).sum()),
                         "tied": int((np.abs(delta) <= 1e-10).sum())}
    return stats


def validate(args, key, dataset, env, system):
    regions = dataset.system["regions_from_paper_topology"]
    train = set(dataset.split_dates("train").astype(str))
    days = dataset.split_dates("validation").astype(str).tolist()
    pools, hashes = {}, {}
    for spec in args.pool_arm:
        name, path = spec.split("=", 1)
        data = Path(path).read_bytes()
        pools[name], hashes[name] = json.loads(data), hashlib.sha256(data).hexdigest()
        check_pool(pools[name], train)
    arms = [*pools, "none"]
    config = {"version": "v2_c2", "stage": "validate", "split_scheme": dataset.split_scheme, "dates": days,
              "arms": arms, "pool_sha256": hashes, "reflexion_rounds": 0, "generations_per_arm": 1,
              "retrieval": "nearest pool day even below threshold", "order": "arms rotate by date index",
              "provider": PROVIDER, "model": MODEL, "temperature": .7, "top_p": .8, "pv_q_fraction": 0,
              "forecast_seed": FORECAST_SEED, "system_prompt_sha256": hashlib.sha256(system.encode()).hexdigest()}
    prepare_output(args.output, args.resume, config)
    if not args.resume:
        for name, spec in zip(pools, args.pool_arm):
            shutil.copyfile(spec.split("=", 1)[1], args.output / f"frozen_pool_{name}.json")
    state = load_state(args.output, {"pool": [], "days": []})
    for i, day in enumerate(days[len(state["days"]):], len(state["days"])):
        directory = args.output / day
        directory.mkdir()
        prediction = forecast_v2(dataset, day)
        save(directory / "forecast.json", prediction)
        neutral = score(env, day, NEUTRAL)
        row = {"date": day, **{f"neutral_{m}": neutral[m] for m in METRICS}}
        rotation = arms[i % len(arms):] + arms[:i % len(arms)]
        for arm in rotation:
            reference = None
            if arm != "none":
                index, sim = closest(pools[arm], prediction)
                reference = pools[arm][index]
                row[f"{arm}_reference_date"], row[f"{arm}_similarity"] = reference["forecast"]["date"], sim
            plan, attempt = generate_plan(key, build_payload(prediction, regions, reference), directory / arm, system)
            metrics = score(env, day, plan)
            save(attempt / "scored.json", {"plan": plan, "metrics": metrics})
            row.update({f"{arm}_{m}": metrics[m] for m in METRICS})
        state["days"].append(row)
        save_state(args.output, state)
        print(f"{i + 1}/{len(days)} {day} " + " ".join(f"{a}={row[f'{a}_mad_pu']:.5f}" for a in arms), flush=True)
    rows = state["days"]
    with (args.output / "pairs.csv").open("w", newline="", encoding="utf-8") as table:
        writer = csv.DictWriter(table, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {"days": len(rows), "means": {a: {m: float(np.mean([r[f"{a}_{m}"] for r in rows])) for m in METRICS}
                                            for a in ["neutral", *arms]},
               "worst_min_pu": {a: min(r[f"{a}_min_pu"] for r in rows) for a in ["neutral", *arms]},
               "worst_max_pu": {a: max(r[f"{a}_max_pu"] for r in rows) for a in ["neutral", *arms]},
               "paired": {f"{a}_minus_{b}": paired(rows, a, b)
                          for j, a in enumerate(arms) for b in arms[j + 1:]}}
    save(args.output / "validation_summary.json", summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("seed", "pilot", "formal", "validate"), required=True)
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pool", type=Path, help="Starting pool for pilot and formal stages")
    parser.add_argument("--threshold", type=float, help="Similarity threshold for pilot and formal stages")
    parser.add_argument("--pool-arm", action="append", default=[], help="name=path frozen pool for validation")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.stage in ("pilot", "formal") and (args.pool is None or args.threshold is None):
        parser.error("pilot and formal require --pool and --threshold")
    if args.stage == "seed" and (args.pool or args.threshold is not None):
        parser.error("seed starts from an empty pool without a threshold")
    if args.stage == "validate" and not args.pool_arm:
        parser.error("validate requires at least one --pool-arm name=path")
    key = args.key_file.read_text(encoding="utf-8-sig").strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError("Key file must contain one API key")
    dataset = Dataset(version="v2", split_scheme="A_half_month")
    env = VoltageControlEnv(dataset)
    system = system_prompt(dataset.system)
    if args.stage == "validate":
        validate(args, key, dataset, env, system)
    else:
        build(args, key, dataset, env, system, day_groups(dataset))


if __name__ == "__main__":
    main()
