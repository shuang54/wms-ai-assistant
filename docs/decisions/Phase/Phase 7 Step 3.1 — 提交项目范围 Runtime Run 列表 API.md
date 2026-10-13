# Phase 7 Step 3.1 — 提交项目范围 Runtime Run 列表 API

项目：`D:\coding\ai\wms-ai-assistant`

## 1. 提交前检查

```powershell
git branch --show-current
git log -1 --oneline
git status --short
git diff --check
```

确认当前分支为 `phase7`，HEAD 与 Step 2 提交 `b486d7c` 一致，且工作区中的 Step 3 变更与完成报告一致。

## 2. 只暂存以下 8 个交付文件

```powershell
git add backend/app/services/runtime_run_list_service.py
git add backend/app/db/agent_run_repository.py
git add backend/app/db/workflow_run_repository.py
git add backend/app/dto/runtime_read_api.py
git add backend/app/api/runtime_read.py
git add docs/api.md
git add tests/test_runtime_read_api.py
git add tests/test_runtime_read_api_db.py
```

不要使用 `git add -A` 或 `git add .`。Git 只会把明确指定的文件加入暂存区。([Git 官方文档](https://git-scm.com/docs/git-add))

## 3. 核对暂存内容

```powershell
git diff --cached --check
git diff --cached --stat
git status --short
```

确认暂存区只包含上述 8 个文件。若集合不一致，停止并报告，不要扩大提交范围。

## 4. 本地提交

确认无误后执行：

```powershell
git commit -m "feat: Phase 7 runtime run list API"
```

## 5. 提交后验证

```powershell
git log -1 --oneline
git status --short
git diff HEAD^ HEAD --check
git diff --name-only -- backend
```

随后只运行治理门相关测试：

- `test_11_backend_working_tree_is_unmodified`
- `test_offline_gate_suite_is_green`
- `test_real_offline_suite_execution_summary`
- `test_baseline_matches_step89_actual_execution`
- `test_gate_passes_when_current_matches_baseline`

按仓库实际测试路径运行，不要重跑全量测试。

## 6. 报告并停止

返回：

1. commit hash 与提交信息
2. 实际提交文件数量
3. 工作区状态
4. 5 个治理门结果
5. 异常（如有）

不推送、不创建 PR、不部署、不进入 Phase 7 Step 4。