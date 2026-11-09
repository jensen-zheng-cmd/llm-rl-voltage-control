"""Checks for paper-style v2 day-ahead information restrictions."""
import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_llm_planning import Dataset, date, forecast, forecast_v2, feedback_v2

REGIONS = {"region1": [2, 3, 4, 5, 19, 20, 21, 22, 23, 24, 25],
           "region2": list(range(8, 19)), "region3": [6, 7, *range(26, 34)]}
PV_BUSES = [13, 18, 22, 25, 29, 33]


class StubDataset:
    version = "v2"
    system = {"regions_from_paper_topology": REGIONS,
              "paper_devices": {"pv_buses": PV_BUSES}}

    def day(self, day):
        rng = np.random.default_rng(123)
        load = rng.uniform(.01, .10, (96, 32))
        pv = rng.uniform(0, .03, (96, 6))
        pv[40:60] *= 100  # Ensure signed regional reverse flow is represented.
        return {"p_load_mw": load, "p_pv_mw": pv}


def test_v2_forecast_is_regional_noisy_net_and_reproducible():
    dataset = StubDataset()
    first, second = forecast_v2(dataset, "2013-01-01"), forecast_v2(dataset, "2013-01-01")
    assert first == second
    assert set(first) == {"date", "regions", "net_load_mw"}
    assert set(first["regions"]) == set(REGIONS)
    assert all(set(profile) == {"net_load_mw"} for profile in first["regions"].values())
    assert len(first["net_load_mw"]) == 24
    for profile in first["regions"].values():
        assert len(profile["net_load_mw"]) == 24
        assert np.min(profile["net_load_mw"]) < 0
    summed = sum(np.asarray(profile["net_load_mw"]) for profile in first["regions"].values())
    np.testing.assert_allclose(first["net_load_mw"], summed, atol=1e-5)

    # Reproduce the specified noise sequence independently: aggregate first, then
    # apply one relative Gaussian noise sample per region and hour.
    data = dataset.day("2013-01-01")
    load = data["p_load_mw"].reshape(24, 4, 32).mean(axis=1)
    pv = data["p_pv_mw"].reshape(24, 4, 6).mean(axis=1)
    ordinal = date.fromisoformat("2013-01-01").toordinal()
    rng = np.random.default_rng(np.random.SeedSequence([42, ordinal]))
    for name, buses in REGIONS.items():
        ids = [i for i, bus in enumerate(PV_BUSES) if bus in buses]
        clean = load[:, np.asarray(buses) - 2].sum(axis=1) - pv[:, ids].sum(axis=1)
        expected = clean * (1 + rng.normal(0, .05, 24))
        np.testing.assert_allclose(first["regions"][name]["net_load_mw"], expected, atol=1e-5)


def test_v2_forecast_cannot_silently_use_v1():
    with pytest.raises(ValueError, match="requires the v2"):
        forecast_v2(type("Old", (), {"version": "v1"})(), "2014-01-01")


def test_v2_feedback_has_no_node_level_or_violation_details():
    hourly = np.linspace(.90, 1.10, 24 * 33).reshape(24, 33)
    entry = {"plan": {"initial_tap": 0},
             "metrics": {"reward": -1.25, "violation_pct": 99,
                         "violations": [{"bus": 33}], "hourly_bus_voltage_pu": hourly.tolist()}}
    result = feedback_v2(entry, REGIONS)
    assert set(result) == {"plan", "reward", "hourly_region_voltage_pu", "hourly_total_voltage_pu"}
    assert result["reward"] == -1.25
    np.testing.assert_allclose(result["hourly_total_voltage_pu"], hourly.mean(axis=1), atol=1e-6)
    for name, buses in REGIONS.items():
        np.testing.assert_allclose(result["hourly_region_voltage_pu"][name],
                                   hourly[:, np.asarray(buses) - 1].mean(axis=1), atol=1e-6)
        assert len(result["hourly_region_voltage_pu"][name]) == 24


def test_v1_forecast_still_matches_frozen_historical_record():
    # This also guards the default construction of Dataset against accidental v2 drift.
    dataset = Dataset()
    frozen = json.loads((ROOT / "results/llm-v1/frozen_pool.json").read_text(encoding="utf-8"))
    historical = frozen[0]["forecast"]
    assert forecast(dataset, historical["date"]) == historical
