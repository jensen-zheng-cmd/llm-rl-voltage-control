"""Generate and score mechanical plans with the DeepSeek API."""
import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from voltage_control.data import Dataset
from voltage_control.environment import DayPlan, VoltageControlEnv

PROVIDER = os.environ.get("LLM_PROVIDER", "paratera")
ENDPOINT, MODEL = {
    "paratera": ("https://llmapi.paratera.com/v1/chat/completions", "DeepSeek-V4.1-Flash-1017"),
    "deepseek": ("https://api.deepseek.com/chat/completions", "deepseek-flash"),
}[PROVIDER]
FORECAST_SEED = 42
SYSTEM = """You schedule an OLTC and three switched capacitors in the balanced
Baran-Wu 33-bus radial feeder. Bus 1 is the source. Use only the provided
hourly forecasts and any simulation feedback. Aim to minimize summed absolute
node voltage deviations from 1.0 pu; flag severe under/overvoltage in your
reasoning. High net import tends to cause undervoltage, reverse flow can cause
overvoltage. Higher taps and energized capacitors usually raise voltage.
Each capacitor injects 0.15 MVAr. PV reactive power is zero.

Return only a JSON object with this exact schema, for example:
{"initial_tap":0,"tap_changes":[[10,-1],[17,1]],"sc_intervals":{"4":null,"9":[17,22],"26":null}}
Use integer tap positions -5 through 5 (each step is 0.006 pu), no more than
four changes at strictly increasing integer hours 1 through 23, and avoid
redundant changes. Capacitor intervals are [start, end) with integer hours
0 <= start < end <= 24; null means off all day. Use only buses 4, 9 and 26.
Each capacitor may have at most one interval. Output the full schedule.
Forecast arrays start at hour 0. All power arrays are MW or MVAr."""


def save(path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                    encoding="utf-8")


def parse_plan(spec):
    if not isinstance(spec, dict) or set(spec) != {"initial_tap", "tap_changes", "sc_intervals"}:
        raise ValueError("Expected initial_tap, tap_changes, sc_intervals")
    initial, changes, intervals = spec["initial_tap"], spec["tap_changes"], spec["sc_intervals"]
    if type(initial) is not int or not -5 <= initial <= 5:
        raise ValueError("Invalid initial tap")
    if not isinstance(changes, list) or len(changes) > 4:
        raise ValueError("Expected at most four tap changes")
    tap = np.full(24, initial)
    previous = 0
    for pair in changes:
        if not isinstance(pair, list) or len(pair) != 2 or any(type(x) is not int for x in pair):
            raise ValueError("Tap change must be [hour, position] integers")
        hour, position = pair
        if not previous < hour < 24 or not -5 <= position <= 5:
            raise ValueError("Invalid tap change hour or position")
        tap[hour:] = position
        previous = hour
    if not isinstance(intervals, dict) or set(intervals) != {"4", "9", "26"}:
        raise ValueError("Expected capacitor buses 4, 9, 26")
    sc = np.zeros((24, 3), dtype=int)
    for i, bus in enumerate(("4", "9", "26")):
        pair = intervals[bus]
        if pair is None:
            continue
        if (not isinstance(pair, list) or len(pair) != 2 or
                any(type(x) is not int for x in pair) or not 0 <= pair[0] < pair[1] <= 24):
            raise ValueError("Invalid capacitor interval")
        sc[pair[0]:pair[1], i] = 1
    return DayPlan(tap.repeat(4), sc.repeat(4, axis=0)).validate()


NEUTRAL = {"initial_tap": 0, "tap_changes": [], "sc_intervals": dict.fromkeys(("4", "9", "26"))}


def forecast(dataset, day):
    rng = np.random.default_rng(np.random.SeedSequence([FORECAST_SEED, date.fromisoformat(day).toordinal()]))
    data = dataset.day(day)
    regions = dataset.system["regions_from_paper_topology"]
    pv_buses = dataset.system["paper_devices"]["pv_buses"]
    values = {}
    for name in ("p_load_mw", "q_load_mvar", "p_pv_mw"):
        hourly = data[name].reshape(24, 4, -1).mean(axis=1)
        values[name] = np.maximum(0, hourly * (1 + rng.normal(0, .05, hourly.shape)))
    profiles = {}
    for name, buses in regions.items():
        load = np.array(buses) - 2
        pv = [i for i, bus in enumerate(pv_buses) if bus in buses]
        profiles[name] = {"buses": buses, "pv_buses": [pv_buses[i] for i in pv],
                          "sc_buses": [b for b in (4, 9, 26) if b in buses],
                          "p_load_mw": values["p_load_mw"][:, load].sum(axis=1).round(5).tolist(),
                          "q_load_mvar": values["q_load_mvar"][:, load].sum(axis=1).round(5).tolist(),
                          "p_pv_mw": values["p_pv_mw"][:, pv].sum(axis=1).round(5).tolist()}
    net = values["p_load_mw"].sum(axis=1) - values["p_pv_mw"].sum(axis=1)
    return {"date": day, "regions": profiles, "net_load_mw": net.round(5).tolist()}


def forecast_v2(dataset, day):
    """Paper-style v2 input: noisy regional hourly net demand, plus its consistent total.

    Noise is multiplicative Gaussian with relative standard deviation 0.05 and is
    applied after regional aggregation. Reverse net demand stays signed. The paper
    does not specify the noise distribution or operation; these are explicit v2
    implementation choices. Existing v1 forecasts above are unchanged.
    """
    if getattr(dataset, "version", "v1") != "v2":
        raise ValueError("forecast_v2 requires the v2 dataset configuration")
    rng = np.random.default_rng(np.random.SeedSequence([FORECAST_SEED, date.fromisoformat(day).toordinal()]))
    data = dataset.day(day)
    regions = dataset.system["regions_from_paper_topology"]
    pv_buses = dataset.system["paper_devices"]["pv_buses"]
    load_hourly = data["p_load_mw"].reshape(24, 4, -1).mean(axis=1)
    pv_hourly = data["p_pv_mw"].reshape(24, 4, -1).mean(axis=1)
    profiles, total = {}, np.zeros(24)
    for name, buses in regions.items():
        load = np.asarray(buses) - 2
        pv = [i for i, bus in enumerate(pv_buses) if bus in buses]
        net = load_hourly[:, load].sum(axis=1) - pv_hourly[:, pv].sum(axis=1)
        noisy = net * (1 + rng.normal(0, .05, 24))
        profiles[name] = {"net_load_mw": noisy.round(5).tolist()}
        total += noisy
    return {"date": day, "regions": profiles, "net_load_mw": total.round(5).tolist()}


def score(env, day, spec):
    env.reset(day, parse_plan(spec))
    snapshots = []
    for _ in range(96):
        _, _, done, _, info = env.step(np.zeros(6))
        snapshots.append(info["snapshot"].voltage_pu)
    if not done:
        raise RuntimeError("Expected end of day at step 96")
    v = np.stack(snapshots)
    deviation = np.abs(v - 1)
    worst = []
    for hour, window in enumerate(v.reshape(24, 4, 33)):
        for kind, index in (("under", np.argmin(window)), ("over", np.argmax(window))):
            step, bus = np.unravel_index(index, window.shape)
            value = float(window[step, bus])
            if (kind == "under" and value < .95) or (kind == "over" and value > 1.05):
                worst.append({"hour": hour, "quarter": int(step), "bus": int(bus + 1),
                              "kind": kind, "voltage_pu": round(value, 5)})
    return {"reward": -float(deviation.mean(axis=1).sum()), "mad_pu": float(deviation.mean()),
            "violation_pct": float(100 * ((v < .95) | (v > 1.05)).mean()),
            "min_pu": float(v.min()), "max_pu": float(v.max()), "violations": worst,
            "hourly_bus_voltage_pu": v.reshape(24, 4, 33).mean(axis=1).round(6).tolist()}


def generate(key, payload, directory, system=SYSTEM):
    params = {"model": MODEL, "messages": [{"role": "system", "content": system},
              {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
              "response_format": {"type": "json_object"}, "thinking": {"type": "disabled"},
              "temperature": .7, "top_p": .8, "max_tokens": 1000}
    save(directory / "request.json", params)
    request = Request(ENDPOINT,
                      data=json.dumps(params).encode(),
                      headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    started = perf_counter()
    try:
        with urlopen(request, timeout=90) as response:
            result = json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"DeepSeek API HTTP {error.code}") from None
    except (URLError, TimeoutError):
        raise RuntimeError("DeepSeek API connection failed or timed out") from None
    elapsed = perf_counter() - started
    choice = result["choices"][0]
    content = choice["message"].get("content")
    save(directory / "response.json", {"provider": PROVIDER, "requested_model": MODEL, "returned_model": result.get("model"),
         "id": result.get("id"), "usage": result.get("usage"), "finish_reason": choice["finish_reason"],
         "received_at_utc": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": round(elapsed, 3),
         "content": content})
    if choice["finish_reason"] != "stop" or not content or not content.strip():
        raise ValueError("DeepSeek returned an empty or incomplete plan")
    spec = json.loads(content)
    parse_plan(spec)
    return spec


def similarity(a, b):
    a, b = np.array(a["net_load_mw"]), np.array(b["net_load_mw"])
    norms = np.linalg.norm(a) * np.linalg.norm(b)
    masses = sorted([abs(a.sum()), abs(b.sum())])
    if not norms:
        return float(not np.any(a) and not np.any(b))
    if not masses[1]:
        return 0.0
    return float((a @ b) / norms * masses[0] / masses[1])


def closest(pool, prediction):
    if not pool:
        return None, 0.0
    index = max(range(len(pool)), key=lambda i: similarity(pool[i]["forecast"], prediction))
    return index, similarity(pool[index]["forecast"], prediction)


def feedback(entry):
    metrics = entry["metrics"]
    return {"plan": entry["plan"], "reward": metrics["reward"],
            "violation_pct": metrics["violation_pct"], "violations": metrics["violations"]}


def feedback_v2(entry, regions_from_paper_topology):
    """Restrict LLM feedback to the paper's total reward and hourly region/total voltages."""
    metrics = entry["metrics"]
    hourly = np.asarray(metrics["hourly_bus_voltage_pu"], dtype=float)
    if hourly.shape != (24, 33):
        raise ValueError("Expected hourly voltage profiles for all 33 buses")
    regions = {
        name: hourly[:, np.asarray(buses) - 1].mean(axis=1).round(6).tolist()
        for name, buses in regions_from_paper_topology.items()
    }
    return {"plan": entry["plan"], "reward": metrics["reward"],
            "hourly_region_voltage_pu": regions,
            "hourly_total_voltage_pu": hourly.mean(axis=1).round(6).tolist()}


def representative_days(dataset):
    times = dataset.arrays["step_start"]
    start, end = map(np.datetime64, dataset.runtime["splits"]["train"])
    mask = (times >= start) & (times < end)
    dates = times[mask][::96].astype("datetime64[D]").astype(str)
    load = dataset.arrays["p_load_mw"][mask].sum(axis=1).reshape(-1, 96)
    pv = dataset.arrays["p_pv_mw"][mask].sum(axis=1).reshape(-1, 96)
    features = np.column_stack([load.mean(axis=1), pv.mean(axis=1)])
    ordinary = np.linalg.norm((features - np.median(features, axis=0)) / features.std(axis=0), axis=1)
    picks = []
    for order in (np.argsort(ordinary), np.argsort(-load.max(axis=1)), np.argsort(-pv.max(axis=1))):
        picks.extend([dates[i] for i in order if dates[i] not in picks][:2])
    return picks, dates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--stage", choices=("smoke", "seed", "pilot", "formal"), required=True)
    parser.add_argument("--pool", type=Path)
    args = parser.parse_args()
    if (args.stage in ("pilot", "formal")) != (args.pool is not None):
        parser.error("pilot and formal require --pool, smoke and seed start with an empty pool")
    key = args.key_file.read_text(encoding="utf-8-sig").strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError("Key file must contain one API key")
    args.output.mkdir(parents=True)
    dataset = Dataset()
    env = VoltageControlEnv(dataset)
    seeds, train = representative_days(dataset)
    pool = json.loads(args.pool.read_text(encoding="utf-8")) if args.pool else []
    if any(entry["forecast"]["date"] not in train for entry in pool):
        raise ValueError("Experience pool must contain training dates only")
    if args.stage == "smoke":
        days = seeds[:1]
    elif args.stage == "seed":
        days = seeds
    else:
        remaining = sorted(set(train) - set(seeds))
        order = np.random.default_rng(2026).permutation(remaining)
        days = order[:3] if args.stage == "pilot" else order[3:203]
    save(args.output / "config.json", {"stage": args.stage, "dates": list(days),
         "forecast_seed": FORECAST_SEED, "relative_noise_sd": .05, "provider": PROVIDER, "model": MODEL,
         "initial_pool": str(args.pool) if args.pool else None, "pv_q_fraction": 0,
         "similarity_threshold": .7})
    summaries = []
    for day in days:
        directory = args.output / day
        directory.mkdir()
        prediction = forecast(dataset, day)
        save(directory / "forecast.json", prediction)
        baseline = score(env, day, NEUTRAL)
        index, sim = closest(pool, prediction) if args.pool else (None, 0.0)
        candidates = []
        for round_id in range(4 if args.pool else 1):
            attempt = directory / f"round_{round_id}"
            attempt.mkdir()
            payload = {"forecast": prediction}
            if index is not None:
                payload["similar_day"] = {"forecast": pool[index]["forecast"], **feedback(pool[index])}
            if candidates:
                payload["previous_attempts"] = [feedback(entry) for entry in candidates]
                payload["task"] = "Revise the schedule using the same-day simulated voltage feedback."
            for retry in range(3):
                try:
                    plan = generate(key, payload, attempt)
                    break
                except ValueError as error:
                    if retry == 2:
                        raise
                    payload["format_correction"] = {
                        "error": str(error),
                        "previous_output": json.loads(
                            (attempt / "response.json").read_text(encoding="utf-8"))["content"],
                        "instruction": "Correct the invalid schedule and return the full JSON plan.",
                    }
                    attempt = directory / f"round_{round_id}_repair_{retry + 1}"
                    attempt.mkdir()
            entry = {"forecast": prediction, "plan": plan, "metrics": score(env, day, plan)}
            save(attempt / "scored.json", entry)
            candidates.append(entry)
        best = max(candidates, key=lambda e: e["metrics"]["reward"])
        if args.stage == "seed" and baseline["reward"] > best["metrics"]["reward"]:
            best = {"forecast": prediction, "plan": NEUTRAL, "metrics": baseline}
        if index is None or sim < .7:
            pool.append(best)
            update = "added"
        elif best["metrics"]["reward"] > pool[index]["metrics"]["reward"]:
            pool[index] = best
            update = "replaced"
        else:
            update = "retained"
        summary = {"date": day, "neutral": feedback({"plan": NEUTRAL, "metrics": baseline}),
                   "rounds": [feedback(entry) for entry in candidates], "selected": feedback(best),
                   "similarity": sim, "pool_update": update}
        summaries.append(summary)
        save(directory / "summary.json", summary)
        save(args.output / "pool.json", pool)
        save(args.output / "summary.json", summaries)
        print(day, "neutral", baseline["reward"], "rounds",
              [entry["metrics"]["reward"] for entry in candidates], update, flush=True)


if __name__ == "__main__":
    main()
