# PPO 微调 v1

150 个训练日的 LLM 计划全部生成并固定，完成 14,400 个交互步、75 个采样批次。PPO 训练及保存重载检查用时约 5 分 29 秒。181 日统一验证留作下一任务。

[配置与训练日](config.json) · [冻结日计划](plans.json) · [训练日奖励](episode_rewards.csv) · [运行摘要](summary.json)

最终模型和原观测归一化参数位于项目本地 results/models/ppo-finetune-v1/。完整运行记录位于 D:\Codex\outputs\llm-rl-voltage\runs\ppo_finetune_1793710124195。
