# GitHub与本地提交工作流

公开仓库：[jensen-zheng-cmd/llm-rl-voltage-control](https://github.com/jensen-zheng-cmd/llm-rl-voltage-control)。本地主分支main，远端名origin，HTTPS地址为 `https://github.com/jensen-zheng-cmd/llm-rl-voltage-control.git`。

## 用户指令的含义

| 用户指令 | 执行动作 |
|---|---|
| 接受 / 接受Txx | 将完成记录归档，并做相关本地commit；不push |
| 提交 / 做完直接提交 | 按已明确范围做本地commit；不push |
| push / 明确要求推送 | 推送已有本地提交，并核对远端结果；不自动提交待验收修改 |
| 创建公开仓库并推上去 | 明确授权创建公开远端及本次初始push；不授权以后自动push |

每次工作先讨论，执行结束后记录；已提交的本地工作可以积累，直到用户明确要求push才上传。push不包含Release、部署、改变其他仓库可见性等动作。

## 接受后的本地提交

1. 查看工作区，确认待验收内容与用户接受的范围一致。
2. 将对应完成记录移入 `archive/TASKS-DONE.md`，更新TASKS指针；只暂存该工作相关文件。
3. 检查暂存内容和忽略规则，提交后确认工作区状态。

```powershell
git status --short
git diff --stat
git add <本次相关文件或目录>
git diff --cached --stat
git -c core.whitespace=cr-at-eol diff --cached --check
git commit -m '接受Txx：简短说明'
git status --short
```

原始数据、PDF、页图片和大型运行产物沿用.gitignore，不因为要公开就强制加入。当前仓库上传代码、配置、文档、小型来源记录及已有Git历史；原始数据来源与获取脚本保留在仓库中。

## 已有仓库的push

确认用户本次明确要求push后执行：

```powershell
git remote -v
git status --short
git log -3 --oneline
git push origin main
git rev-parse HEAD
git ls-remote --heads origin main
git status --short
```

最后两条提交摘要应相同。首次建立上游跟踪时用 `git push -u origin main`。遇到远端新增提交、认证失败或推送拒绝，报告实际原因并处理，不使用强推覆盖。

## 从现有本地仓库新建GitHub仓库的方法

优先发现GitHub插件能力。本次插件没有创建仓库/本地Git推送工具，因此使用已安装并认证的GitHub CLI；本机实际命令帮助已核对。

```powershell
# 先核对登录账号；不要输出token
gh api user --jq .login

# 仅在该名称未被占用、且用户已授权创建公开仓库时执行
gh repo create jensen-zheng-cmd/llm-rl-voltage-control --public --source . --remote origin

# 只有明确授权push时才执行
git push -u origin main

# 核对公开状态、默认分支和提交
gh repo view jensen-zheng-cmd/llm-rl-voltage-control --json nameWithOwner,url,visibility,defaultBranchRef
git rev-parse HEAD
git ls-remote --heads origin main
```

本项目的仓库已经创建，以后不重复运行建仓命令。若现有origin与目标不一致，先核实情况，不直接覆盖。

## 本次建立记录

2026-11-02，用户明确授权“接受；建个GitHub公开仓库，原样推上去”，并规定以后“接受只提交、push才推送”。账号经插件和CLI交叉核对均为 `jensen-zheng-cmd`。

T02验收提交为 `f891736`。新建空的PUBLIC仓库后，首次将本地main及完整历史推送；远端main与 `f891736a550383bae892779a0e67c293103837bc` 一致。此次工作流文档同属本轮明确授权，完成本地提交后继续同步；以后推送仍需用户当次明确指令。
