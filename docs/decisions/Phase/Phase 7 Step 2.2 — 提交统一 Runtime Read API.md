# Phase 7 Step 2.2 — 提交统一 Runtime Read API

项目目录：`D:\coding\ai\wms-ai-assistant`

## 目标

将已完成的 Phase 7 Step 2 交付提交到当前 `phase7` 分支。

### 1. 提交前检查

```powershell
git branch --show-current
git status --short
git diff --check
```

确认当前分支为 `phase7`，HEAD 为 `1b23eba`，并核实以下 7 个交付文件与此前报告一致。

### 2. 只暂存以下文件

```powershell
git add backend/app/main.py
git add docs/api.md
git add backend/app/dto/runtime_read_api.py
git add backend/app/api/runtime_read_auth.py
git add backend/app/api/runtime_read.py
git add tests/test_runtime_read_api.py
git add tests/test_runtime_read_api_db.py
```

**不要使用 `git add -A` 或 `git add .`。**

排除 `docs/decisions/Phase/` 下的 4 份任务书副本及其他无关文件。Git 的路径级暂存可以限制本次提交范围。([Git 官方文档](https://git-scm.com/docs/git-add))

### 3. 核对暂存内容

```powershell
git diff --cached --check
git diff --cached --stat
git status --short
```

确认暂存区仅有上述 7 个文件。若出现其他文件，停止提交并报告，不要擅自扩大范围。

### 4. 本地提交

确认无误后执行：

```powershell
git commit -m "feat: Phase 7 unified runtime read API"
```

### 5. 提交后验证

```powershell
git log -1 --oneline
git status --short
git diff HEAD^ HEAD --check
```

确认新提交包含预期的 7 个文件，且没有意外提交任务书副本。

如提交后 `backend` 已无未提交修改，可单独运行：

```powershell
python -m pytest -q tests/test_11*
```

如果该路径不存在，使用仓库中实际的 `test_11` 测试路径。不要为此重新跑 40 分钟的全量测试。

## 报告

只返回：

- 新 commit hash 和提交信息
- 实际提交的文件数量
- 工作区状态
- `test_11` 是否恢复
- 是否有异常

## STOP

不推送、不创建 PR、不部署、不进入 Phase 7 Step 3。