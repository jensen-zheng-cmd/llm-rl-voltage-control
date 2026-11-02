from __future__ import annotations

from dataclasses import dataclass
import re

import numpy as np
import pandapower as pp

from .common import PROJECT_ROOT, load_configs, sha256


def case_matrix(text, name):
    match = re.search(r"mpc\." + name + r"\s*=\s*\[(.*?)\];", text, re.S)
    if match is None:
        raise ValueError(f"Missing frozen case33 matrix: {name}")
    rows = []
    for line in match.group(1).splitlines():
        line = line.split("%", 1)[0].strip().rstrip(";")
        if line:
            rows.append([float(value) for value in line.split()])
    return np.asarray(rows)


@dataclass
class Snapshot:
    voltage_pu: np.ndarray
    angle_degree: np.ndarray
    pv_q_mvar: np.ndarray
    pv_q_limit_mvar: np.ndarray
    root_voltage_pu: float
    p_loss_mw: float
    q_loss_mvar: float
    grid_p_mw: float
    grid_q_mvar: float
    max_nodal_mismatch_mva: float
    p_balance_error_mw: float
    q_balance_error_mvar: float

    @property
    def reward(self):
        return -float(np.mean(np.abs(self.voltage_pu - 1.0)))

    @property
    def violation_pct(self):
        return float(np.mean((self.voltage_pu < 0.95) | (self.voltage_pu > 1.05)) * 100)


class Case33Network:
    """Balanced AC, constant PQ loads; positive sgen Q is reactive injection.

    OLTC implements the paper's root-voltage equation directly. SCs implement
    constant Q injection, not the voltage-squared characteristic of a shunt.
    No branch thermal ratings are supplied by the case; no thermal guarantee is claimed.
    """

    def __init__(self, capacities_mva):
        self.runtime, self.system = load_configs()
        source = PROJECT_ROOT / self.system["network"]["snapshot"]
        if sha256(source) != self.system["network"]["snapshot_sha256"]:
            raise ValueError("Frozen network source changed")
        text = source.read_text(encoding="utf-8-sig")
        self.bus_data = case_matrix(text, "bus")
        self.branches = case_matrix(text, "branch")
        self.branches = self.branches[self.branches[:, 10] == 1]
        self.capacities = np.asarray(capacities_mva, dtype=float)
        if self.capacities.shape != (6,) or not np.isfinite(self.capacities).all() or (self.capacities <= 0).any():
            raise ValueError("Six finite positive inverter apparent capacities are required")
        self.pv_buses = np.asarray(self.system["paper_devices"]["pv_buses"]) - 1
        self.sc_buses = np.asarray(self.system["paper_devices"]["sc_buses"]) - 1
        self.base_mva = self.system["network"]["base_mva"]
        self.net = pp.create_empty_network(sn_mva=self.base_mva, f_hz=50)
        for row in self.bus_data:
            pp.create_bus(self.net, vn_kv=row[9], index=int(row[0])-1, name=f"bus_{int(row[0])}")
        pp.create_ext_grid(self.net, bus=0, vm_pu=1.0, va_degree=0.0)
        self.load_ids = []
        for row in self.bus_data[1:]:
            self.load_ids.append(pp.create_load(self.net, bus=int(row[0])-1, p_mw=row[2]/1000,
                                                q_mvar=row[3]/1000, name=f"load_bus_{int(row[0])}"))
        for row in self.branches:
            if row[4] != 0 or row[8] != 0 or row[9] != 0:
                raise ValueError("Frozen loader expects the case33 zero-charging, no-transformer lines")
            pp.create_line_from_parameters(self.net, from_bus=int(row[0])-1, to_bus=int(row[1])-1,
                length_km=1.0, r_ohm_per_km=row[2], x_ohm_per_km=row[3], c_nf_per_km=0,
                max_i_ka=np.nan, name=f"line_{int(row[0])}_{int(row[1])}")
        self.pv_ids = [pp.create_sgen(self.net, int(bus), p_mw=0, q_mvar=0, sn_mva=float(cap), name=f"pv_bus_{bus+1}")
                       for bus, cap in zip(self.pv_buses, self.capacities)]
        self.sc_ids = [pp.create_sgen(self.net, int(bus), p_mw=0, q_mvar=0, name=f"constant_q_sc_bus_{bus+1}") for bus in self.sc_buses]
        self.y_bus_pu = np.zeros((33, 33), dtype=complex)
        z_base_ohm = self.system["network"]["base_kv"] ** 2 / self.base_mva
        for row in self.branches:
            a, b = int(row[0])-1, int(row[1])-1
            admittance = z_base_ohm / complex(row[2], row[3])
            self.y_bus_pu[a, a] += admittance; self.y_bus_pu[b, b] += admittance
            self.y_bus_pu[a, b] -= admittance; self.y_bus_pu[b, a] -= admittance

    def q_limits(self, p_pv):
        p_pv = np.asarray(p_pv, dtype=float)
        if p_pv.shape != (6,) or not np.isfinite(p_pv).all() or (p_pv < 0).any():
            raise ValueError("Expected six nonnegative finite PV active powers")
        if np.any(p_pv > self.capacities + 1e-10):
            raise ValueError("PV exceeds frozen inverter capacity; no silent resizing or curtailment")
        return self.system["paper_devices"]["pv_reactive_capacity_factor"] * np.sqrt(np.maximum(0, self.capacities**2-p_pv**2))

    def solve(self, p_load, q_load, p_pv, tap=0, sc_on=(0, 0, 0), q_fraction=None, q_mvar=None, algorithm=None):
        p_load, q_load, p_pv = (np.asarray(values, dtype=float) for values in (p_load, q_load, p_pv))
        if any(values.shape != (32,) or not np.isfinite(values).all() or (values < 0).any() for values in (p_load, q_load)):
            raise ValueError("Expected 32 nonnegative finite P/Q loads")
        if not np.isfinite(tap) or int(tap) != tap or not -5 <= tap <= 5:
            raise ValueError("OLTC tap must be an integer in [-5,5]")
        sc_on = np.asarray(sc_on)
        if sc_on.shape != (3,) or not np.isin(sc_on, [0, 1]).all():
            raise ValueError("Expected three binary SC statuses")
        limit = self.q_limits(p_pv)
        if q_fraction is not None and q_mvar is not None:
            raise ValueError("Provide fractions or physical Q, not both")
        if q_mvar is not None:
            reactive = np.asarray(q_mvar, dtype=float)
        else:
            fraction = np.zeros(6) if q_fraction is None else np.asarray(q_fraction, dtype=float)
            if fraction.shape != (6,) or not np.isfinite(fraction).all() or np.any(np.abs(fraction) > 1):
                raise ValueError("Six finite Q fractions in [-1,1] are required")
            reactive = fraction * limit
        if reactive.shape != (6,) or not np.isfinite(reactive).all() or np.any(np.abs(reactive) > limit+1e-10):
            raise ValueError("Reactive action exceeds current capacity")
        self.net.load.loc[self.load_ids, ["p_mw", "q_mvar"]] = np.column_stack((p_load, q_load))
        self.net.sgen.loc[self.pv_ids, ["p_mw", "q_mvar"]] = np.column_stack((p_pv, reactive))
        sc_q = self.system["paper_devices"]["sc_capacity_each_mvar"] * sc_on
        self.net.sgen.loc[self.sc_ids, "q_mvar"] = sc_q
        self.net.ext_grid.loc[:, "vm_pu"] = 1 + int(tap)*self.system["paper_devices"]["oltc_voltage_step_pu"]
        options = dict(self.runtime["solver"])
        if algorithm:
            options["algorithm"] = algorithm
        pp.runpp(self.net, init="flat", calculate_voltage_angles=True, voltage_depend_loads=False,
                 run_control=False, lightsim2grid=False, **options)
        if not self.net.converged:
            raise RuntimeError("AC power flow did not converge")
        voltage = self.net.res_bus.vm_pu.to_numpy(copy=True)
        angle = self.net.res_bus.va_degree.to_numpy(copy=True)
        complex_v = voltage * np.exp(1j*np.deg2rad(angle))
        specified = np.zeros(33, dtype=complex)
        specified[1:] = -p_load - 1j*q_load
        specified[self.pv_buses] += p_pv + 1j*reactive
        specified[self.sc_buses] += 1j*sc_q
        grid_p = float(self.net.res_ext_grid.p_mw.sum()); grid_q = float(self.net.res_ext_grid.q_mvar.sum())
        specified[0] = complex(grid_p, grid_q)
        calculated = complex_v * np.conj(self.y_bus_pu @ complex_v) * self.base_mva
        p_loss = float(self.net.res_line.pl_mw.sum()); q_loss = float(self.net.res_line.ql_mvar.sum())
        return Snapshot(voltage, angle, reactive.copy(), limit, float(voltage[0]), p_loss, q_loss, grid_p, grid_q,
            float(np.max(np.abs(calculated-specified))),
            float(grid_p+p_pv.sum()-p_load.sum()-p_loss),
            float(grid_q+reactive.sum()+sc_q.sum()-q_load.sum()-q_loss))

    def static_base_case(self, algorithm=None):
        return self.solve(self.bus_data[1:, 2]/1000, self.bus_data[1:, 3]/1000, np.zeros(6), algorithm=algorithm)
