# 单日 MISOCP 参照

[配置](config.json) · [数值摘要](summary.json) · [联合动作](joint_actions.json) · [固定机械计划动作](fixed_llm_actions.json) · [脚本](../../scripts/run_misocp_day.py)

2014-05-22 在 181 个验证日中最接近全网日均负荷与光伏出力的标准化中位特征。两种优化都读入当天未加噪的 96 步节点时序，使用带损耗的 DistFlow、旋转二阶锥松弛、0.95–1.05 p.u. 模型硬限值和电压平方绝对偏差目标。支路电流上界按下游节点电流和三角不等式计算，属于物理可行域的上界，不是线路热额定值。

| 方案 | AC 平均绝对偏差（p.u.） | AC 越限率 | AC 最低 / 最高电压（p.u.） |
|---|---:|---:|---:|
| LLM＋零无功 | 0.018065 | 0.726% | 0.95034 / 1.08708 |
| LLM＋预训练 PPO | 0.011057 | 0% | 0.96288 / 1.04972 |
| LLM＋微调 PPO | 0.011485 | 0.316% | 0.95979 / 1.05563 |
| 固定 LLM 计划＋SOCP 无功 | 0.008593 | 0% | 0.96547 / 1.04979 |
| 联合 MISOCP | 0.006003 | 0.063% | 0.97616 / 1.05735 |

联合模型用时 7.76 秒，MIP gap 0.4976%，固定计划用时 0.31 秒。两套 AC 回代各完成 96 步，最大节点功率残差低于 1e-8 MVA。求解器电压和 AC 回代最大差分别为 0.00869 和 0.00555 p.u.。联合优化的模型网损为 2.767 MWh，回代为 1.880 MWh，且回代有 2 个节点时段过压。锥松弛有可见误差，MIP gap 仅对应所建模型。联合结果用于理想信息下的比较，尚未得到满足 AC 电压硬限值的联合方案。

使用 Gurobi Python API 13.0.1 和本机已有的完整许可。Python 默认捆绑许可证因模型规模受限，改用本机 CLI 所用的许可文件后通过，仅在进程内设置 GRB_LICENSE_FILE。完整日志与逐步数据见 D:\Codex\outputs\llm-rl-voltage\runs\misocp_v1_1793717132569_licensed。

## 电压曲线与低控制基线

[29 号节点曲线](bus29_voltage.png) · [全网最高和最低电压](network_voltage_envelope.png) · [七组数值](voltage_comparison_metrics.json) · [绘图与回放脚本](../../scripts/plot_misocp_comparison.py)

新增 Original 同日对照（OLTC 零档、SC 全关、PV 无功零）及 No-LLM 诊断（机械设备同 Original，冻结预训练 PPO 调无功）。既有五组的回放平均偏差均与原记录一致。图 9 对应节点 29，原文画 Original、No-LLM、No-RL、Proposed、Pure-RL、No-PT、No-Reflexion 和 MISOCP。本图额外区分两版 PPO，并列出固定 LLM 计划的 SOCP。No-LLM 诊断使用已训练权重，后三个待补对照为 Pure-RL、No-PT、No-Reflexion。

[四日对照和图](multi/README.md)

## 三维电压预览

2014-05-22 的交互预览包含六种方案。可旋转、缩放、切换方案及查看节点数值。独立 HTML 保存在 D:\Codex\outputs\llm-rl-voltage\voltage-3d-2014-05-22.html。
