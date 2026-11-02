from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from .network import Case33Network


@dataclass
class DayPlan:
    tap: np.ndarray
    sc_on: np.ndarray

    def validate(self):
        tap, sc = np.asarray(self.tap), np.asarray(self.sc_on)
        if tap.shape != (96,) or not np.isfinite(tap).all() or not np.equal(tap, np.round(tap)).all() or np.any(np.abs(tap) > 5):
            raise ValueError("Day tap plan must contain 96 integer positions in [-5,5]")
        if sc.shape != (96, 3) or not np.isin(sc, [0, 1]).all():
            raise ValueError("SC plan must contain 96 x 3 binary states")
        if not np.all(tap.reshape(24, 4) == tap[::4, None]) or not np.all(sc.reshape(24, 4, 3) == sc[::4, None, :]):
            raise ValueError("Mechanical plan changes only at integer hours")
        if np.count_nonzero(np.diff(tap)) > 4:
            raise ValueError("OLTC exceeds four changes after initial setting")
        for i in range(3):
            if np.count_nonzero(np.diff(np.r_[0, sc[:, i], 0]) == 1) > 1:
                raise ValueError("Each SC has at most one contiguous commitment interval")
        return self

    @classmethod
    def zero(cls):
        return cls(np.zeros(96, dtype=int), np.zeros((96, 3), dtype=int))


def manual_fixture(runtime):
    spec = runtime["manual_schedule"]
    tap, sc, fractions = np.zeros(96, dtype=int), np.zeros((96, 3), dtype=int), np.zeros((96, 6))
    for start, position in zip(spec["oltc_times_hour"], spec["oltc_positions"]):
        tap[int(start)*4:] = position
    for i, (start, end) in enumerate(spec["sc_intervals_hour"]):
        sc[int(start)*4:int(end)*4, i] = 1
    for start, fraction in zip(spec["pv_q_fraction_times_hour"], spec["pv_q_fractions"]):
        fractions[int(start)*4:] = fraction
    return DayPlan(tap, sc).validate(), fractions


class VoltageControlEnv:
    """Gym-style reset/step interface; uses measured pre-action voltage, never post-action voltage as input.

    Exogenous inputs are equivalent 15-minute average values. A future field
    implementation must define causal measurement availability separately.
    The final observation is the last controlled state, not a fictitious next day.
    """

    def __init__(self, dataset):
        self.dataset = dataset
        self.network = Case33Network(dataset.capacities)
        self.t = 96

    def _state(self, snapshot):
        return {"p_load_mw": self.day_data["p_load_mw"][self.t].copy(),
                "q_load_mvar": self.day_data["q_load_mvar"][self.t].copy(),
                "voltage_pu": snapshot.voltage_pu.copy(),
                "p_pv_mw": self.day_data["p_pv_mw"][self.t].copy(),
                "q_pv_mvar": snapshot.pv_q_mvar.copy(),
                "tap_one_hot": np.eye(11)[int(self.plan.tap[self.t])+5],
                "sc_one_hot": np.eye(2)[self.plan.sc_on[self.t].astype(int)].ravel()}

    def _observe(self):
        limits = self.network.q_limits(self.day_data["p_pv_mw"][self.t])
        held_q = np.clip(self.previous_q, -limits, limits)
        snapshot = self.network.solve(self.day_data["p_load_mw"][self.t], self.day_data["q_load_mvar"][self.t],
            self.day_data["p_pv_mw"][self.t], int(self.plan.tap[self.t]), self.plan.sc_on[self.t], q_mvar=held_q)
        return self._state(snapshot)

    def reset(self, date, plan=None):
        self.day_data = self.dataset.day(date)
        source_plan = (plan or DayPlan.zero()).validate()
        self.plan = DayPlan(np.asarray(source_plan.tap, dtype=int).copy(), np.asarray(source_plan.sc_on, dtype=int).copy())
        self.previous_q = np.zeros(6)
        self.t = 0
        return self._observe(), {"date": date, "daily_reset": True}

    def step(self, q_fraction):
        if self.t >= 96:
            raise RuntimeError("Episode ended; reset is required")
        index = self.t
        snapshot = self.network.solve(self.day_data["p_load_mw"][index], self.day_data["q_load_mvar"][index],
            self.day_data["p_pv_mw"][index], int(self.plan.tap[index]), self.plan.sc_on[index], q_fraction=q_fraction)
        self.previous_q = snapshot.pv_q_mvar.copy()
        terminal = index == 95
        if terminal:
            observation = self._state(snapshot)
            self.t = 96
        else:
            self.t += 1
            observation = self._observe()
        info = {"step": index, "step_start": str(self.day_data["step_start"][index]),
                "step_end": str(self.day_data["step_end"][index]), "tap": int(self.plan.tap[index]),
                "sc_on": self.plan.sc_on[index].copy(), "snapshot": snapshot}
        return observation, snapshot.reward, terminal, False, info
