# LLM-RL 两阶段电压控制论文复现

已选定：**Baran–Wu 33节点径向配电网 + MAPDN历史负荷/光伏时序 + 目标论文的OLTC/SC配置**。

公开仓库：[llm-rl-voltage-control](https://github.com/jensen-zheng-cmd/llm-rl-voltage-control)。协作指令：“接受”只做本地提交，明确要求“push”才推送。

先完成Proposed，之后派生对照，MISOCP独立补做验证。平时从本页和 [TASKS.md](TASKS.md) 看起。

## 阅读入口

| 文件 | 内容 |
|---|---|
| [TASKS.md](TASKS.md) | 当前进展、未定问题、做完后的待验收记录 |
| [AGENTS.md](AGENTS.md) | 执行与记录规矩、技术和文件约定 |
| [GitHub工作流](docs/GitHub工作流.md) | 本地提交、公开仓库创建、push及结果核对方法 |
| [系统与数据说明](docs/系统与数据说明.md) | 系统设备、历史数据、采样与处理边界 |
| [环境验收说明](docs/环境验收说明.md) | 一天96步的结果、4张图、工程约定与重跑命令 |
| [实施路线](docs/实施路线.md) | 已确认的六步路线与每阶段验收要求 |
| [文件树与用途](docs/文件树与用途.md) | 当前目录的通俗说明 |
| [系统配置](configs/system_selection.json) | 当前有效的机器可读配置，未定项保留空值 |
| [目标论文](references/目标论文.pdf)、[补充材料](references/补充材料.pdf) | 常用阅读原文 |
| [已验收记录](archive/TASKS-DONE.md) | 按实际完成顺序保存的工作记录 |

## 文件位置

```text
docs/          常读说明与稳定实施路线
configs/       当前系统配置
models/        原始电网参数
data/          完整原始时序与小样本
scripts/       当前使用的工具脚本
src/           数据处理、网络、日计划和潮流环境实现
tests/         数据与物理环境的自动验证
references/    目标论文和补充材料
support/       低频核验记录、前期分析、重复资料
archive/       已验收工作记录
```

源数据每3分钟一个点，完整24小时为480点；目标控制每15分钟一次，一天96步。三类CSV各526,080行，时间从2012-01-01 00:03至2015-01-01 00:00。当前已有15分钟转换与环境验收实现，进度和待验收记录统一见TASKS。

MAPDN数据由历史来源加工形成仿真场景，不是本测试馈线的现场联合量测，也未证实为目标论文原始训练集。数据来源与完整性见[下载记录](support/evidence/case33_download_manifest.json)，更多说明在docs。

原始数据、PDF与页图片保存在本地并被Git忽略；来源版本、校验记录、脚本和小文件进入Git。前期快照中的旧路径保留历史含义，后续以当前配置和本页入口为准。

阶段成果与本地模型入口见 [阶段结果](results/README.md)。
