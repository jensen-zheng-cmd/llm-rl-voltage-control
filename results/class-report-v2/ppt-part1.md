# 正式 PPT：当前结构与第一部分

产物：项目根目录 `LLM-RL.pptx`，当前共 16 页。第一部分按用户确认的两张生图预览重建；第二部分正文一页，后续四部分各放章节封页与空白内容模板。

| 页码 | 内容 |
|---|---|
| 1 | 原封面 |
| 2 | 六部分目录 |
| 3 | 原第一部分分隔页 |
| 4 | 产业背景、波动挑战与可控资源、数据驱动应用、RL 信息利用矛盾的逻辑图 |
| 5 | 原文四项挑战：信息不完整且异构、语义运行约束、长尾需求指令、策略改进方向 |
| 6 | Part-02 Literature Review 封页 |
| 7 | 文献综述：电力系统调度与 LLM 辅助 RL |
| 8 | Part-03 Model Formulation 封页 |
| 9 | Model Formulation 空白内容模板 |
| 10 | Part-04 Case Study 封页 |
| 11 | Case Study 空白内容模板 |
| 12 | Part-05 Teamwork 封页 |
| 13 | Teamwork 空白内容模板 |
| 14 | Part-06 Conclusion and Future 封页 |
| 15 | Conclusion and Future 空白内容模板 |
| 16 | 原结束页 |

来源为用户的 `D:\obsidian_save\清深课程\PPT 文字稿.md`、`D:\obsidian_save\论文库\Two-Stage Active Distribution Network Vo\zh-Two-Stage Active Distribution Network Vo.md` 的引言和问题建模部分，以及当前仓库。详细锚点保存在各内容页备注中。

沿用原第 4 页的紫色顶栏、标志、字体和页码。文字、框、连线、电压与 P-Q 示意图均为原生可编辑对象，不采用整页图片。光伏与逆变器配图取自用户确认的生图预览，作为两张独立图片对象裁切显示。电压与 P-Q 图为定性示意，来源及说明保存在备注中。

当前脚本：`scripts/build_ppt_background_editable.ps1`。默认图片来源为 `assets/approved-background-preview.png`。脚本从本批修改前备份重建草稿，不作为后续新增章节后的通用重绘入口。

第一部分备份、逐页预览及 `verification.json` 位于 `D:\Codex\work\llm-rl-ppt-part1-editable-20261112`。当批第 1、2、3、6 页渲染保持一致，第 4、5 页顶部模板逐像素一致，新增文字无边界溢出。第 4 页有 55 个原生形状及 2 张独立配图，第 5 页有 21 个原生形状，无图片。7 段原文节选逐段核对，均已逐页目视检查。

此前 T27 的四页正文草稿及脚本 `scripts/build_ppt_part1.ps1` 为历史版本；备份保留在 `D:\Codex\work\llm-rl-ppt-part1-20261112`，不再作为当前版入口。

## 第二部分与章节模板

文献综述依据目标论文引言及 [19]–[24] 的作者/出版社摘要，按“电力系统调度”和“LLM 辅助 RL”两类陈述六篇工作。完整书目、链接及本文位置说明写入第 7 页备注，不在正文增加资料核验说明。未将 PowerGraph-LLM 概括为仅做语言接口。

新增封页直接复制第一部分封页并更新标号和标题；第 9、11、13、15 页只保留章节顶栏及原有模板页码，正文区域为空白。脚本为 `scripts/build_ppt_literature_and_sections.ps1`；修改前备份、全部逐页预览及 `verification.json` 位于 `D:\Codex\work\llm-rl-ppt-part2-20261112`。第 1–5 页渲染保持一致，新增文本无溢出，四张模板正文区域逐像素检查为空白。
