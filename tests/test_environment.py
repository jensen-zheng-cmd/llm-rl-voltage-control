import numpy as np
import pandapower as pp
import pandapower.networks as pn
import pytest

from voltage_control.data import Dataset, aggregate_five
from voltage_control.environment import DayPlan, VoltageControlEnv, manual_fixture
from voltage_control.network import Case33Network


@pytest.fixture(scope="module")
def data():
    return Dataset()


def test_interval_energy_and_partial_group():
    source = np.array([[1, 4], [2, 3], [3, 2], [2, 1], [2, 5]], dtype=float)
    target = aggregate_five(source)
    np.testing.assert_allclose(target, [[2, 3]])
    np.testing.assert_allclose(source.sum(axis=0)*0.05, target.sum(axis=0)*0.25)
    with pytest.raises(ValueError):
        aggregate_five(source[:4])


def test_full_days_calendar_and_training_capacity(data):
    info = data.info
    assert info["days"] == 1096
    assert [info["splits"][key]["days"] for key in ("train", "validation", "test")] == [731, 181, 184]
    np.testing.assert_allclose(data.capacities, np.array(info["training_raw_pv_peaks_mw"])*1.2)
    assert info["capacity_uses_heldout_values"] is False
    assert data.day("2012-01-01")["step_start"].shape == (96,)
    assert str(data.day("2014-12-31")["step_end"][-1])[:10] == "2015-01-01"


def test_case_conversion_independent_construction(data):
    ours = Case33Network(data.capacities).static_base_case()
    packaged = pn.case33bw()
    pp.runpp(packaged, numba=False, init="flat", tolerance_mva=1e-9, voltage_depend_loads=False, lightsim2grid=False)
    np.testing.assert_allclose(ours.voltage_pu, packaged.res_bus.vm_pu.to_numpy(), atol=1e-9, rtol=0)
    assert ours.voltage_pu.min() == pytest.approx(0.913090479, abs=1e-8)
    assert ours.p_loss_mw == pytest.approx(0.202677126, abs=1e-8)
    assert ours.max_nodal_mismatch_mva < 1e-8


def test_invalid_schedule_rejected():
    plan = DayPlan.zero()
    plan.tap[1] = 1
    with pytest.raises(ValueError, match="integer hours"):
        plan.validate()
    plan = DayPlan.zero()
    plan.sc_on[4:8, 0] = 1; plan.sc_on[12:16, 0] = 1
    with pytest.raises(ValueError, match="contiguous"):
        plan.validate()
    plan = DayPlan.zero()
    for i in range(1, 7): plan.tap[i*4:] = i % 2
    with pytest.raises(ValueError, match="four changes"):
        plan.validate()


def test_capacity_and_action_domain(data):
    network = Case33Network(data.capacities)
    with pytest.raises(ValueError, match="exceeds frozen"):
        network.q_limits(data.capacities*1.01)
    day = data.day(data.representative_training_day())
    args = [day[key][0] for key in ("p_load_mw", "q_load_mvar", "p_pv_mw")]
    with pytest.raises(ValueError, match="fractions"):
        network.solve(*args, q_fraction=np.ones(6)*1.01)
    with pytest.raises(ValueError, match="integer"):
        network.solve(*args, tap=0.5)


def test_observation_is_pre_action_and_reset_is_repeatable(data):
    date = data.representative_training_day()
    env = VoltageControlEnv(data)
    observation, _ = env.reset(date)
    np.testing.assert_array_equal(observation["q_pv_mvar"], np.zeros(6))
    original_voltage = observation["voltage_pu"].copy()
    _, reward, terminated, truncated, info = env.step(np.ones(6)*0.5)
    assert not terminated and not truncated
    np.testing.assert_allclose(reward, -np.abs(info["snapshot"].voltage_pu-1).mean())
    np.testing.assert_array_equal(observation["voltage_pu"], original_voltage)
    repeated, _ = env.reset(date)
    np.testing.assert_array_equal(repeated["voltage_pu"], original_voltage)
    assert repeated["tap_one_hot"].shape == (11,) and repeated["sc_one_hot"].shape == (6,)


def test_manual_plan_and_terminal_boundary(data):
    plan, actions = manual_fixture(data.runtime)
    assert np.count_nonzero(np.diff(plan.tap)) == 4
    env = VoltageControlEnv(data)
    env.reset(data.representative_training_day(), plan)
    for t in range(96):
        observation, _, done, truncated, info = env.step(actions[t])
        assert done == (t == 95) and not truncated
        assert info["snapshot"].max_nodal_mismatch_mva < 1e-7
        assert np.all(np.abs(info["snapshot"].pv_q_mvar) <= info["snapshot"].pv_q_limit_mvar+1e-10)
    with pytest.raises(RuntimeError, match="reset"):
        env.step(actions[-1])
