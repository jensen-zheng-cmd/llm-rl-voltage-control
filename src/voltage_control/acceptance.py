from __future__ import annotations

import argparse
import dataclasses
from datetime import datetime
from html import escape
import importlib.metadata
from pathlib import Path
import shutil
import subprocess
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pandapower as pp
import pandapower.networks as pn

from .common import PROJECT_ROOT, sha256, write_json
from .data import Dataset
from .environment import DayPlan, VoltageControlEnv, manual_fixture
from .figures import make_figures
from .network import Case33Network


def validate_snapshot(snapshot):
    if snapshot.max_nodal_mismatch_mva > 1e-7 or abs(snapshot.p_balance_error_mw) > 1e-7 or abs(snapshot.q_balance_error_mvar) > 1e-7:
        raise AssertionError("Independent nodal/global AC balance check failed")
    if not np.isfinite(snapshot.voltage_pu).all():
        raise AssertionError("Nonfinite voltage")
    if np.any(np.abs(snapshot.pv_q_mvar) > snapshot.pv_q_limit_mvar+1e-10):
        raise AssertionError("Inverter Q bound violated")


def physics_checks(dataset, day):
    network = Case33Network(dataset.capacities)
    base = network.static_base_case()
    reference = pn.case33bw()
    pp.runpp(reference, algorithm="nr", init="flat", numba=False, calculate_voltage_angles=True,
             voltage_depend_loads=False, lightsim2grid=False, tolerance_mva=1e-9, max_iteration=50)
    golden_error = float(np.max(np.abs(base.voltage_pu-reference.res_bus.vm_pu.to_numpy())))
    if golden_error > 1e-8:
        raise AssertionError("Frozen case conversion differs from packaged case33bw")
    records = []
    indices = {"night": 0, "max_pv": int(day["p_pv_mw"].sum(axis=1).argmax()),
               "max_load": int(day["p_load_mw"].sum(axis=1).argmax())}
    largest_residual = base.max_nodal_mismatch_mva
    for label, t in indices.items():
        inputs = [day[key][t] for key in ("p_load_mw", "q_load_mvar", "p_pv_mw")]
        original = network.solve(*inputs)
        alternative = network.solve(*inputs, algorithm="bfsw")
        validate_snapshot(original); validate_snapshot(alternative)
        agreement = float(np.max(np.abs(original.voltage_pu-alternative.voltage_pu)))
        if agreement > 5e-8:
            raise AssertionError("NR and backward/forward sweep disagree")
        records.append({"snapshot": label, "step": t, "kind": "solver_agreement", "label": "NR vs BFSW", "max_delta_v_pu": agreement, "pass": True})
        changes = [("tap", f"OLTC{tap:+d}", 0, tap, {"tap": tap}) for tap in (1, -1)]
        for i, bus in enumerate(network.sc_buses):
            status = np.zeros(3, dtype=int); status[i] = 1
            changes.append(("sc", f"SC@{bus+1}+", int(bus), 1, {"sc_on": status}))
        for i, bus in enumerate(network.pv_buses):
            for sign in (1, -1):
                fraction = np.zeros(6); fraction[i] = sign*0.5
                changes.append(("pv", f"PV@{bus+1}{'+' if sign>0 else '-'}", int(bus), sign, {"q_fraction": fraction}))
        for kind, name, target, sign, kwargs in changes:
            changed = network.solve(*inputs, **kwargs)
            validate_snapshot(changed)
            delta = float(changed.voltage_pu[target]-original.voltage_pu[target])
            if delta*sign <= 1e-8:
                raise AssertionError(f"Actuator direction failed: {label} {name}")
            if kind == "tap" and abs(delta-sign*0.006) > 1e-12:
                raise AssertionError("Root-voltage/tap equation failed")
            largest_residual = max(largest_residual, changed.max_nodal_mismatch_mva)
            records.append({"snapshot": label, "step": t, "kind": kind, "label": name,
                            "target_bus_one_based": target+1, "delta_v_pu": delta, "pass": True})
    summary = {"base_case_min_voltage_pu": float(base.voltage_pu.min()), "base_case_loss_mw": base.p_loss_mw,
               "packaged_case_max_voltage_error_pu": golden_error, "checks_passed": len(records),
               "largest_independent_nodal_residual_mva": largest_residual}
    return records, summary


def run_day(dataset, date, plan, fractions, directory, name):
    environment = VoltageControlEnv(dataset)
    initial_obs, _ = environment.reset(date, plan)
    dimensions = {key: len(value) for key, value in initial_obs.items()}
    snapshots, info_rows, terminated_count = [], [], 0
    for t in range(96):
        observation, reward, terminated, truncated, info = environment.step(fractions[t])
        if truncated or terminated != (t == 95):
            raise AssertionError("Incorrect episode boundary")
        terminated_count += int(terminated)
        snapshot = info["snapshot"]
        validate_snapshot(snapshot)
        if abs(reward-snapshot.reward) > 1e-12:
            raise AssertionError("Reward inconsistent with controlled voltage")
        snapshots.append(snapshot)
        info_rows.append({"step": t, "step_start": info["step_start"], "tap": info["tap"],
                          "reward": reward, "v_min_pu": snapshot.voltage_pu.min(), "v_max_pu": snapshot.voltage_pu.max(),
                          "violation_pct": snapshot.violation_pct, "loss_mw": snapshot.p_loss_mw,
                          "nodal_residual_mva": snapshot.max_nodal_mismatch_mva})
    if terminated_count != 1:
        raise AssertionError("Episode must terminate exactly once")
    arrays = {"voltage_pu": np.stack([s.voltage_pu for s in snapshots]),
              "angle_degree": np.stack([s.angle_degree for s in snapshots]),
              "pv_q_mvar": np.stack([s.pv_q_mvar for s in snapshots]),
              "q_limit_mvar": np.stack([s.pv_q_limit_mvar for s in snapshots]),
              "tap": plan.tap, "sc_on": plan.sc_on, "q_fraction": fractions}
    np.savez_compressed(directory / f"{name}.npz", **arrays)
    pd.DataFrame(info_rows).to_csv(directory / f"{name}_steps.csv", index=False)
    summary = {"steps": len(info_rows), "mean_absolute_voltage_deviation_pu": float(np.mean(np.abs(arrays["voltage_pu"]-1))),
               "violation_pct": float(np.mean((arrays["voltage_pu"]<0.95)|(arrays["voltage_pu"]>1.05))*100),
               "minimum_voltage_pu": float(arrays["voltage_pu"].min()), "maximum_voltage_pu": float(arrays["voltage_pu"].max()),
               "total_undiscounted_reward": float(sum(row["reward"] for row in info_rows)),
               "maximum_nodal_residual_mva": float(max(s.max_nodal_mismatch_mva for s in snapshots)),
               "observation_dimensions": dimensions, "oltc_changes_after_initial": int(np.count_nonzero(np.diff(plan.tap))),
               "sc_commitment_intervals": [int(np.count_nonzero(np.diff(np.r_[0, plan.sc_on[:, i], 0])==1)) for i in range(3)]}
    if not np.isclose(summary["total_undiscounted_reward"], -96*summary["mean_absolute_voltage_deviation_pu"]):
        raise AssertionError("Daily reward/MAD relation failed")
    return arrays, summary


def snapshot_sources(directory):
    files = [PROJECT_ROOT/"pyproject.toml", *sorted((PROJECT_ROOT/"src").rglob("*.py")),
             *sorted((PROJECT_ROOT/"configs").glob("*.json")), *sorted((PROJECT_ROOT/"scripts").glob("*.py")),
             *sorted((PROJECT_ROOT/"tests").glob("*.py"))]
    lock = PROJECT_ROOT/"requirements-lock.txt"
    if lock.exists(): files.append(lock)
    records = []
    for source in files:
        relative = source.relative_to(PROJECT_ROOT)
        destination = directory/"source_snapshot"/relative
        destination.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, destination)
        records.append({"path": relative.as_posix(), "sha256": sha256(source)})
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Training date; default is selected by median training load/PV features")
    args = parser.parse_args()
    dataset = Dataset()
    date = args.date or dataset.representative_training_day()
    start, end = dataset.runtime["splits"]["train"]
    if not np.datetime64(start) <= np.datetime64(date) < np.datetime64(end):
        raise ValueError("Environment acceptance day must be selected from training period")
    day = dataset.day(date)
    run_id = datetime.now(ZoneInfo("Asia/Singapore")).strftime("%Y-%m-%d_%H%M%S") + "_environment_v1"
    directory = Path(dataset.runtime["output_root"])/"runs"/run_id
    directory.mkdir(parents=True, exist_ok=False)
    print(f"Acceptance run: {directory}; training day: {date}", flush=True)
    checks, physics = physics_checks(dataset, day)
    write_json(directory/"physics_checks.json", {"summary": physics, "checks": checks})
    print(f"Physical checks passed: {physics['checks_passed']}", flush=True)
    manual_plan, fractions = manual_fixture(dataset.runtime)
    zero, zero_summary = run_day(dataset, date, DayPlan.zero(), np.zeros((96, 6)), directory, "zero_action_reference")
    manual, manual_summary = run_day(dataset, date, manual_plan, fractions, directory, "manual_schedule")
    np.savez_compressed(directory/"day_inputs.npz", **day)
    make_figures(directory, date, day, zero, manual, checks, dataset.system)
    versions = {name: importlib.metadata.version(name) for name in ("pandapower", "numpy", "pandas", "matplotlib", "scipy")}
    code_records = snapshot_sources(directory)
    git_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=PROJECT_ROOT, text=True).strip())
    result = {"status": "pass", "run_id": run_id, "training_date": date,
              "day_selection": "closest to training median daily P-load and P-PV features in standardized feature space" if args.date is None else "explicit training date",
              "physics": physics, "zero_action_reference": zero_summary, "manual_schedule": manual_summary,
              "capacities_mva": dataset.capacities.tolist(), "pv_buses_one_based": dataset.system["paper_devices"]["pv_buses"],
              "assumptions": dataset.runtime, "dataset_manifest_path": str(dataset.directory/"manifest.json"),
              "processed_npz_sha256": sha256(dataset.directory/"timeseries.npz"),
              "runtime_versions": versions, "git_head_before_acceptance": git_head, "worktree_uncommitted": dirty,
              "source_snapshot": code_records,
              "claim_boundary": "Environment checks and manual fixtures only; no PPO/LLM training, no optimized-performance claim, no field deployment, no thermal-rating validation"}
    write_json(directory/"summary.json", result)
    report = f"""# 33节点一天96步环境验收\n\n训练日：{date}。状态：通过。\n\n这是固定人工计划的环境检查，不是Proposed训练结果，也不要求人工计划优于零动作参考。\n\n| 项目 | 零动作参考 | 人工计划 |\n|---|---:|---:|\n| 平均绝对电压偏差 / p.u. | {zero_summary['mean_absolute_voltage_deviation_pu']:.6f} | {manual_summary['mean_absolute_voltage_deviation_pu']:.6f} |\n| 节点时段越限率 / % | {zero_summary['violation_pct']:.4f} | {manual_summary['violation_pct']:.4f} |\n| 最低电压 / p.u. | {zero_summary['minimum_voltage_pu']:.6f} | {manual_summary['minimum_voltage_pu']:.6f} |\n| 最高电压 / p.u. | {zero_summary['maximum_voltage_pu']:.6f} | {manual_summary['maximum_voltage_pu']:.6f} |\n\n物理检查共{physics['checks_passed']}项；NR与前推回代交叉核对、独立节点导纳矩阵功率平衡检查通过。两个全天场景均完成96步。电压越限是场景结果，不等于计算失败。\n\n1. [输入曲线](01_inputs.png)\n2. [全天电压](02_voltage.png)\n3. [设备动作](03_actions.png)\n4. [单设备响应](04_device_responses.png)\n\n完整数值与假设在[summary.json](summary.json)，逐步结果在manual_schedule_steps.csv和zero_action_reference_steps.csv，原始数组在NPZ文件。source_snapshot保存本次实际代码和配置，Git基线为{git_head}，运行时存在未提交实现：{dirty}。\n\n边界：平衡单相等效AC；SC固定无功模型；日内为15分钟等效平均输入，不能据此证明所有3分钟时刻都合规。原始时间标签按右端点解释，容量仅由训练期3分钟峰值估计；热限额、PPO/LLM与MISOCP尚未验证。\n"""
    (directory/"report.md").write_text(report, encoding="utf-8")
    cards = "".join(f'<figure><img src="{name}.png" alt="{title}"><figcaption>{title}</figcaption></figure>' for name, title in
                    [("01_inputs", "历史输入"), ("02_voltage", "全天节点电压"), ("03_actions", "人工设备计划"), ("04_device_responses", "固定输入下的单设备响应")])
    html = f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>33节点环境验收</title><style>body{{font-family:"Microsoft YaHei",sans-serif;max-width:1100px;margin:32px auto;background:#f5f7fa;color:#172436;padding:0 18px}}figure{{margin:24px 0;background:white;padding:18px;border-radius:12px}}img{{width:100%;height:auto}}figcaption{{margin-top:8px;color:#48566b}}p{{line-height:1.8}}</style><h1>33节点 · 一天96步环境验收</h1><p>训练日：{escape(date)}；{physics["checks_passed"]}项物理检查通过。人工计划仅用于环境验收，尚未训练AI。</p><p>电压偏差：零动作 {zero_summary["mean_absolute_voltage_deviation_pu"]:.5f} / 人工计划 {manual_summary["mean_absolute_voltage_deviation_pu"]:.5f} p.u.；越限率：{zero_summary["violation_pct"]:.2f}% / {manual_summary["violation_pct"]:.2f}%。</p>{cards}<p><a href="summary.json">完整数值与配置</a> · <a href="report.md">文字报告</a> · <a href="physics_checks.json">物理检查记录</a></p></html>'
    (directory/"index.html").write_text(html, encoding="utf-8")
    write_json(PROJECT_ROOT/"support/evidence/latest_environment_run.json", {
        "status": "pass", "run_directory": str(directory), "report": str(directory/"index.html"),
        "summary": str(directory/"summary.json"), "training_date": date,
        "physics_checks_passed": physics["checks_passed"], "manual_schedule": manual_summary,
        "zero_action_reference": zero_summary, "runtime_versions": versions,
    })
    print(f"ENVIRONMENT_ACCEPTANCE_PASS: {directory}", flush=True)
    print({"manual": manual_summary, "zero_reference": zero_summary}, flush=True)


if __name__ == "__main__":
    main()
