"""Checks for the v2 C2 LLM pool-building and validation entry point."""
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_llm_planning
from run_llm_planning import Dataset, SYSTEM
from run_llm_planning_v2 import build_payload, day_groups, generate_plan, system_prompt


def test_day_groups_are_disjoint_training_days_with_fixed_order():
    dataset = Dataset(version="v2", split_scheme="A_half_month")
    train = set(dataset.split_dates("train").astype(str))
    held_out = set(dataset.split_dates("validation").astype(str)) | set(dataset.split_dates("test").astype(str))
    groups = day_groups(dataset)
    sizes = {name: len(days) for name, days in groups.items()}
    assert sizes == {"seed": 6, "formal": 200, "pilot": 3, "unused": 12}
    every = [day for days in groups.values() for day in days]
    assert len(every) == len(set(every)) == 221 and set(every) == train
    assert not set(every) & held_out
    assert day_groups(dataset) == groups


def test_prompt_lists_regions_and_devices():
    dataset = Dataset(version="v2", split_scheme="A_half_month")
    prompt = system_prompt(dataset.system)
    assert prompt != SYSTEM
    for name in dataset.system["regions_from_paper_topology"]:
        assert name in prompt
    assert "capacitor at bus [4]" in prompt and "PV at buses [13, 18]" in prompt


def test_payload_sends_only_paper_feedback():
    regions = {"r1": [1, 2], "r2": list(range(3, 34))}
    hourly = np.full((24, 33), 1.0).tolist()
    entry = {"forecast": {"date": "2013-01-02"}, "plan": {"initial_tap": 1},
             "metrics": {"reward": -1.0, "violation_pct": 3.0, "violations": [{"bus": 18}],
                         "mad_pu": .01, "hourly_bus_voltage_pu": hourly}}
    payload = build_payload({"date": "2013-01-03"}, regions, entry, [entry])
    text = json.dumps(payload)
    assert "violation" not in text and "hourly_bus_voltage" not in text and "mad_pu" not in text
    assert set(payload["similar_day"]) == {"forecast", "plan", "reward", "hourly_region_voltage_pu",
                                           "hourly_total_voltage_pu"}
    assert "previous_attempts" in payload and "task" in payload
    assert set(build_payload({"date": "x"}, regions)) == {"forecast"}


def test_generate_plan_repairs_format_then_succeeds(tmp_path, monkeypatch):
    calls = []

    def fake(key, payload, directory, system):
        calls.append(("format_correction" in payload, directory.name))
        if len(calls) == 1:
            (directory / "response.json").write_text(json.dumps({"content": "bad"}), encoding="utf-8")
            raise ValueError("bad plan")
        return {"ok": True}

    monkeypatch.setattr("run_llm_planning_v2.generate", fake)
    plan, attempt = generate_plan("k", {"forecast": {}}, tmp_path / "round_0", "sys")
    assert plan == {"ok": True} and attempt.name == "round_0_repair_1"
    assert calls == [(False, "round_0"), (True, "round_0_repair_1")]


def test_v1_generate_default_prompt_unchanged():
    import inspect
    assert inspect.signature(run_llm_planning.generate).parameters["system"].default is SYSTEM
