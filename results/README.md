# 阶段结果

## 环境 v1

[验收摘要](environment-v1/summary.json) · [代表日全天电压图](environment-v1/02_voltage.png) · [环境验收说明](../docs/环境验收说明.md)

2012-03-20 的 96 步环境验收。图中比较人工计划与零动作参考。完整报告保存在 D:\Codex\outputs\llm-rl-voltage\runs\2026-11-02_233807_environment_v1。

## PPO 预训练 v1

[结果摘要](ppo-v1/summary.json) · [181 天配对结果](ppo-v1/validation.csv) · [训练入口](../scripts/train_ppo.py) · [依赖](../requirements-ppo.txt)

种子 42，训练 1,500 个日回合（144,000 步）。日期划分为 2012–2013 年训练 731 天、2014 年上半年验证 181 天、下半年最终测试 184 天。验证时每个日期使用相同机械计划，比较无功置零和 PPO 调无功。

| 指标 | 零无功 | PPO |
|---|---:|---:|
| 平均绝对电压偏差（p.u.） | 0.02366 | 0.01370 |
| 节点时段越限率 | 9.12% | 1.82% |
| 最低电压（p.u.） | 0.9005 | 0.9183 |
| 最高电压（p.u.） | 1.1520 | 1.1318 |

181 天中 180 天平均偏差下降，逐日越限率均持平或下降。越限按 0.95–1.05 p.u. 计算，仍有时段越限。本轮使用单个训练种子，最终测试集留给完整方法。

本地最终模型与对应的观测归一化参数成对放在 `results/models/ppo-v1/`，分别为 `ppo.zip` 和 `normalization.npz`。该目录由 Git 忽略。

完整配置、阶段权重、日志及训练场景清单位于 D:\Codex\outputs\llm-rl-voltage\runs\ppo_v1_1793672833807_formal。训练脚本使用项目 Python，接受 `--episodes 20` 或 `--episodes 1500`，并通过 `--output` 指定新的运行目录。

## LLM 日前计划 v1

[接口、六日经验库与三日 Reflexion 结果](llm-v1/README.md)

[PPO 微调 v1](ppo-finetune-v1/README.md)
