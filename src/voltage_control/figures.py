from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "D:/Codex/cache/matplotlib")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams.update({"font.family": ["Microsoft YaHei", "DejaVu Sans"], "axes.unicode_minus": False,
                     "font.size": 10, "axes.spines.top": False, "axes.spines.right": False})


def save(fig, directory: Path, name):
    fig.savefig(directory / f"{name}.png", dpi=180, bbox_inches="tight", facecolor="white")
    fig.savefig(directory / f"{name}.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def make_figures(directory, date, day, reference, manual, checks, system):
    directory.mkdir(exist_ok=True)
    hours = np.arange(96)/4
    p_load, p_pv, q_load = day["p_load_mw"].sum(axis=1), day["p_pv_mw"].sum(axis=1), day["q_load_mvar"].sum(axis=1)
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, constrained_layout=True)
    axes[0].plot(hours, p_load, label="负荷有功", color="#3366CC", lw=2)
    axes[0].plot(hours, p_pv, label="光伏有功", color="#E69F00", lw=2)
    axes[0].plot(hours, p_load-p_pv, label="净负荷", color="#009E73", lw=1.5)
    axes[0].axhline(0, color="0.6", lw=0.7); axes[0].set_ylabel("功率 / MW"); axes[0].legend(ncol=3)
    axes[1].plot(hours, q_load, color="#8054A1", lw=2, label="负荷无功")
    axes[1].set_ylabel("无功 / MVAr"); axes[1].legend(); axes[1].set_xlabel("时段起点 / 小时")
    for ax in axes: ax.grid(alpha=0.2); ax.set_xlim(0, 24); ax.set_xticks(np.arange(0, 25, 3))
    fig.suptitle(f"{date} · 历史输入（15分钟等效平均功率）")
    save(fig, directory, "01_inputs")

    fig, axes = plt.subplots(2, 1, figsize=(12, 9), sharex=True, constrained_layout=True, height_ratios=[1, 1.2])
    v0, v1 = reference["voltage_pu"], manual["voltage_pu"]
    axes[0].fill_between(hours, v1.min(axis=1), v1.max(axis=1), color="#3366CC", alpha=0.18, label="人工计划：全节点范围")
    axes[0].plot(hours, v1.mean(axis=1), color="#3366CC", lw=2, label="人工计划：节点均值")
    axes[0].plot(hours, v0.min(axis=1), "--", color="#E69F00", label="零动作参考：最低电压")
    axes[0].plot(hours, v0.max(axis=1), ":", color="#E69F00", label="零动作参考：最高电压")
    for bound in (0.95, 1.05): axes[0].axhline(bound, color="#C44040", ls="--", lw=1)
    axes[0].set_ylabel("电压 / p.u."); axes[0].legend(ncol=2, fontsize=9); axes[0].grid(alpha=0.2)
    image = axes[1].imshow(v1.T, aspect="auto", origin="lower", extent=[0, 24, 0.5, 33.5], cmap="coolwarm",
                           vmin=min(0.95, v1.min()), vmax=max(1.05, v1.max()), interpolation="nearest")
    axes[1].set_ylabel("节点编号"); axes[1].set_xlabel("时段起点 / 小时"); axes[1].set_yticks([1, 5, 10, 15, 20, 25, 30, 33])
    axes[1].set_xticks(np.arange(0, 25, 3)); fig.colorbar(image, ax=axes[1], label="电压 / p.u.", fraction=0.035)
    fig.suptitle(f"{date} · 一天96步电压检查（人工计划，无AI训练）")
    save(fig, directory, "02_voltage")

    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True, constrained_layout=True, height_ratios=[1, 0.8, 1.3])
    axes[0].step(np.r_[hours, 24], np.r_[manual["tap"], manual["tap"][-1]], where="post", color="#3366CC", lw=2)
    axes[0].set_ylabel("OLTC档位"); axes[0].set_ylim(-2, 2); axes[0].set_yticks([-1, 0, 1]); axes[0].grid(alpha=0.2)
    axes[1].imshow(manual["sc_on"].T, origin="lower", aspect="auto", extent=[0, 24, -0.5, 2.5],
                   cmap="Blues", vmin=0, vmax=1, interpolation="nearest")
    axes[1].set_yticks([0, 1, 2], labels=["SC@4", "SC@9", "SC@26"])
    axes[1].set_title("电容器：浅色关闭、深色投入", fontsize=10, loc="left")
    for i, bus in enumerate(system["paper_devices"]["pv_buses"]):
        axes[2].plot(hours, manual["pv_q_mvar"][:, i], label=f"PV@{bus}")
    axes[2].axhline(0, color="0.6", lw=0.7); axes[2].set_ylabel("PV无功 / MVAr"); axes[2].set_xlabel("时段起点 / 小时")
    axes[2].legend(ncol=3, fontsize=9); axes[2].grid(alpha=0.2)
    axes[2].set_xlim(0, 24); axes[2].set_xticks(np.arange(0, 25, 3))
    fig.suptitle(f"{date} · 预先给定的合法设备计划（仅用于环境验收）")
    save(fig, directory, "03_actions")

    noon_checks = [row for row in checks if row["snapshot"] == "max_pv" and row["kind"] != "solver_agreement"]
    fig, ax = plt.subplots(figsize=(13, 5), constrained_layout=True)
    labels = [row["label"] for row in noon_checks]
    changes = np.array([row["delta_v_pu"] for row in noon_checks])
    ax.bar(np.arange(len(changes)), changes, color=np.where(changes >= 0, "#3366CC", "#E69F00"))
    ax.axhline(0, color="0.3", lw=0.8); ax.set_xticks(np.arange(len(labels)), labels=labels, rotation=45, ha="right")
    ax.set_ylabel("目标节点电压变化 / p.u."); ax.grid(axis="y", alpha=0.2)
    ax.set_title("最大光伏时刻：固定相同输入，每次仅改变一个设备")
    save(fig, directory, "04_device_responses")
