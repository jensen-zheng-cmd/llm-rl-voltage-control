# LLM-RL 两阶段电压控制论文复现

本项目已选定：**Baran–Wu 33节点径向配电网 + MAPDN历史负荷/光伏时序 + 目标论文的OLTC/SC配置**。

先完成Proposed方法，再从同一实现派生其他对照；MISOCP单独补做优化验证。选型与实施顺序于2026-11-02由用户确认。

## 建议先看

1. [系统与数据说明](docs/系统与数据说明.md)：选用的系统、设备、数据含义、一天的采样点数，以及待补参数。
2. [实施顺序与进度](docs/实施顺序与进度.md)：后续按什么顺序搭建，各阶段如何验收。
3. [系统配置](configs/system_selection.json)：机器可读的网参、区域、设备、数据版本和未定项。
4. [文件树与用途](docs/文件树与用途.md)：逐项说明每个文件放什么；接手agent先看根目录的 [AGENTS.md](AGENTS.md)。

## 各部分内容

| 位置 | 保存内容 |
|---|---|
| `docs/` | 系统/数据说明和后续实施顺序 |
| `configs/` | 当前选定系统的配置草案；尚未确定的容量等保持空值 |
| `models/case33bw.m` | MATPOWER官方33节点网参快照，保留原始单位转换代码 |
| `data/raw/mapdn_case33/` | case33完整有功负荷、无功负荷、PV有功CSV及原配网络参考文件 |
| `data/samples/` | 三类CSV开头样本，方便快速查看列结构 |
| `evidence/` | 数据来源/版本、文件完整性、时间轴和网参静态核验记录 |
| `scripts/` | 只下载case33成员并校验的脚本；未执行原配网络pickle |
| `references/目标论文.pdf` | 用户原论文的副本 |
| `references/前期分析/` | 前面两轮的论文拆解、系统选型报告、补充PDF、渲染页和核验脚本原件 |

这里保存的是资料、数据和配置准备。潮流仿真环境、PPO训练、LLM调用、测试及MISOCP均尚未执行。

## 数据的时间尺度

- 下载源CSV每3分钟一个功率点：完整24小时有 **480个区间/点**。
- 目标论文每15分钟控制一次：一天 **96步**。
- 后续将对齐后每5个3分钟点聚合为一个15分钟点，保持负荷/PV同一时间轴。
- 文件时间从00:03开始，需先明确时间戳是区间起点还是终点；自然日期内的首尾记录数未必恰好480。完整核验见[数据下载与时间轴记录](evidence/case33_download_manifest.json)。

原始数据本轮按发布文件保存，尚未制作15分钟转换版。

已核对完整文件：每类CSV均为526,080行，时间为2012-01-01 00:03至2015-01-01 00:00，连续3分钟间隔；记录数对应2012—2014三年的1,096个480点日段。原文件按自然日期分组时，首日479条、末日1条，需在预处理时明确区间标签。

## 来源与使用边界

- [MATPOWER case33bw](https://github.com/MATPOWER/matpower/blob/master/data/case33bw.m)
- [MAPDN代码与说明](https://github.com/Future-Power-Networks/MAPDN)
- [MAPDN数据集](https://huggingface.co/datasets/hsvgbkhgbv/Multi-Agent-Power-Distribution-Networks)
- [目标论文补充材料](https://github.com/YangXuSteve/Knowledge-Data-Driven)

MAPDN的负荷/PV来自历史来源加工后形成仿真场景，不是该33节点馈线的现场联合量测。目前没有证据认定它就是目标论文的原始训练集。源文件及来源记录保留，后续处理另存派生版本。

工作根目录已改为本文件所在目录；前期分析副本中的旧路径仅作为历史记录，后续脚本优先使用项目内相对路径。
