from __future__ import annotations

import argparse
import platform
from pathlib import Path

import numpy as np
import pandas as pd

from .common import PROJECT_ROOT, load_configs, processed_directory, read_json, sha256, write_json
from .mapping import verify_mapping


def aggregate_five(values):
    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 2 or len(values) % 5 or not np.isfinite(values).all():
        raise ValueError("Expected finite 2-D data with complete groups of five")
    return values.reshape(-1, 5, values.shape[1]).mean(axis=1)


def prepare_data():
    runtime, system = load_configs()
    raw = PROJECT_ROOT / system["preferred_timeseries"]["raw_data_path"]
    manifest = read_json(PROJECT_ROOT / system["preferred_timeseries"]["download_manifest"])
    mapping = verify_mapping(raw, system)
    output = processed_directory(runtime)
    output.mkdir(parents=True, exist_ok=True)
    arrays, timestamps, source_records, energy_checks, statistics = {}, None, [], {}, {}
    training_peak = None
    train_start, train_end = map(np.datetime64, runtime["splits"]["train"])
    specs = [("load_active.csv", "p_load_mw", 32, runtime["load_scale"]),
             ("load_reactive.csv", "q_load_mvar", 32, runtime["load_scale"]),
             ("pv_active.csv", "p_pv_mw", 6, runtime["pv_scale"])]
    for name, key, channels, scale in specs:
        print(f"Reading and verifying {name}", flush=True)
        path = raw / name
        source_hash = sha256(path)
        expected = next(item for item in manifest["files"] if Path(item["path"]).name == name)
        if source_hash != expected["sha256"]:
            raise ValueError(f"Raw source hash differs: {name}")
        frame = pd.read_csv(path)
        if list(frame.columns) != ["DateTime", *map(str, range(channels))]:
            raise ValueError(f"Unexpected columns in {name}")
        current_times = pd.to_datetime(frame.pop("DateTime"), errors="raise").to_numpy(dtype="datetime64[ns]")
        if timestamps is None:
            timestamps = current_times
            if not np.all(np.diff(timestamps) == np.timedelta64(3, "m")):
                raise ValueError("Raw time axis must be complete, strictly increasing 3-minute intervals")
        elif not np.array_equal(timestamps, current_times):
            raise ValueError("Power files do not share the same timestamps")
        values = frame.to_numpy(dtype=np.float64) * scale
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError(f"Nonfinite or negative source powers in {name}; inspect before choosing a correction")
        aggregated = aggregate_five(values)
        source_energy = values.sum(axis=0) * 3 / 60
        aggregate_energy = aggregated.sum(axis=0) * 15 / 60
        relative_error = np.max(np.abs(source_energy-aggregate_energy) / np.maximum(1, np.abs(source_energy)))
        if relative_error > 1e-11:
            raise AssertionError("Aggregation failed to conserve interval energy")
        arrays[key] = aggregated
        energy_checks[key] = {"maximum_relative_error": float(relative_error), "pass": True}
        statistics[key] = {"raw_min": values.min(axis=0).tolist(), "raw_max": values.max(axis=0).tolist(),
                           "aggregate_min": aggregated.min(axis=0).tolist(), "aggregate_max": aggregated.max(axis=0).tolist()}
        source_records.append({"file": str(path.relative_to(PROJECT_ROOT)), "sha256": source_hash, "rows": len(values)})
        if key == "p_pv_mw":
            raw_starts = timestamps - np.timedelta64(3, "m")
            train_mask = (raw_starts >= train_start) & (raw_starts < train_end)
            training_peak = values[train_mask].max(axis=0)
        del frame, values
    step_start = timestamps[::5] - np.timedelta64(3, "m")
    step_end = timestamps[4::5]
    if not np.all(step_end-step_start == np.timedelta64(15, "m")):
        raise ValueError("Right-endpoint interpretation did not yield complete 15-minute windows")
    days, counts = np.unique(step_start.astype("datetime64[D]"), return_counts=True)
    if not np.all(counts == 96):
        raise ValueError("Every episode day must contain exactly 96 windows")
    masks = {}
    for name, (start, end) in runtime["splits"].items():
        masks[name] = (step_start >= np.datetime64(start)) & (step_start < np.datetime64(end))
    if not np.all(sum(mask.astype(int) for mask in masks.values()) == 1):
        raise ValueError("Calendar split masks must form a disjoint partition of the data")
    capacities = runtime["capacity_factor"] * training_peak
    over_capacity = (arrays["p_pv_mw"] > capacities[None, :] + 1e-10).sum(axis=0)
    np.savez_compressed(output / "timeseries.npz", step_start=step_start, step_end=step_end, **arrays)
    dataset_info = {
        "dataset_revision": system["preferred_timeseries"]["revision"], "source_files": source_records,
        "source_timestamp_assumption": runtime["source_timestamp_assumption"],
        "state_time_semantics": runtime["state_time_semantics"], "aggregation": runtime["aggregation"],
        "step_count": len(step_start), "days": len(days), "steps_per_day": 96,
        "first_step_start": str(step_start[0]), "last_step_end": str(step_end[-1]),
        "mapping": mapping, "splits": {name: {"dates": runtime["splits"][name], "steps": int(mask.sum()), "days": int(mask.sum() // 96)} for name, mask in masks.items()},
        "training_raw_pv_peaks_mw": training_peak.tolist(), "pv_rated_apparent_capacity_mva": capacities.tolist(),
        "capacity_rule": runtime["capacity_rule"], "all_split_15min_points_exceeding_capacity": over_capacity.tolist(),
        "capacity_uses_heldout_values": False, "load_scale": runtime["load_scale"], "pv_scale": runtime["pv_scale"],
        "energy_conservation": energy_checks, "power_statistics": statistics,
        "runtime": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__},
    }
    write_json(output / "manifest.json", dataset_info)
    write_json(PROJECT_ROOT / "support/evidence/mapping_verification_v1.json", mapping)
    write_json(PROJECT_ROOT / "configs/derived_capacities_v1.json", {
        "source": runtime["capacity_rule"], "training_dates": runtime["splits"]["train"],
        "raw_training_peak_mw": training_peak.tolist(), "pv_buses_one_based": mapping["pv_buses_one_based"],
        "s_mva": capacities.tolist(), "factor": runtime["capacity_factor"], "assumption_not_paper_reported_rating": True,
    })
    print(f"Prepared {len(days)} days / {len(step_start)} steps at {output}", flush=True)
    return output


class Dataset:
    def __init__(self, directory=None):
        self.runtime, self.system = load_configs()
        self.directory = Path(directory) if directory else processed_directory(self.runtime)
        self.info = read_json(self.directory / "manifest.json")
        with np.load(self.directory / "timeseries.npz", allow_pickle=False) as stored:
            self.arrays = {key: stored[key] for key in stored.files}
        self.capacities = np.asarray(self.info["pv_rated_apparent_capacity_mva"])

    def day(self, date):
        date = np.datetime64(date, "D")
        mask = self.arrays["step_start"].astype("datetime64[D]") == date
        if mask.sum() != 96:
            raise ValueError(f"Missing or incomplete episode day: {date}")
        return {name: values[mask].copy() for name, values in self.arrays.items()}

    def representative_training_day(self):
        start, end = map(np.datetime64, self.runtime["splits"]["train"])
        times = self.arrays["step_start"]
        mask = (times >= start) & (times < end)
        p_load = self.arrays["p_load_mw"][mask].sum(axis=1).reshape(-1, 96).mean(axis=1)
        p_pv = self.arrays["p_pv_mw"][mask].sum(axis=1).reshape(-1, 96).mean(axis=1)
        features = np.column_stack((p_load, p_pv))
        score = np.linalg.norm((features-np.median(features, axis=0))/features.std(axis=0), axis=1)
        dates = times[mask][::96].astype("datetime64[D]")
        return str(dates[np.argmin(score)])
