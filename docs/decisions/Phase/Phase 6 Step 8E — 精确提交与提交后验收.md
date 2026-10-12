# Phase 6 Step 8E — 精确提交与提交后验收

项目：`D:\coding\ai\wms-ai-assistant`

## 一、执行目标

1. 只提交 Step 8D 审查通过的 42 个文件。
2. 提交前核对暂存区，确保没有额外文件。
3. 创建一个 Phase 6 提交并记录 commit hash。
4. 提交后复验治理门、全量离线测试和全量 DB 测试。
5. 形成可复核的 Phase 6 验收报告。

本步骤已获用户继续授权，可以按下方精确清单执行提交。

## 二、硬性约束

* 仅提交当前分支 `phase5-1` 上已审查的变更。
* 不推送远程。
* 不修改业务代码或测试来绕过失败。
* 不执行 `git reset --hard`、`git clean` 或 `git stash`。
* 不删除、移动或归档任务书副本。
* 不修改数据库结构。
* 不得将 16 份任务书副本纳入提交。
* 测试失败时如实报告，不得自动修复后再次提交。
* 若基线与 Step 8D 报告不一致，立即停止，不提交。

## 三、提交前重新核对

执行：

```powershell
git branch --show-current
git rev-parse HEAD
git status --short
git diff --check
git diff --stat
```

预期基线：

```text
Branch = phase5-1
HEAD = 3747a665e7e6d86961814dcb091ddc00af1ceb64
Tracked modified = 18
```

新增文件必须与 Step 8D 审查清单一致。

如果发现其他人新增的修改、文件缺失或内容变化，立即停止并报告。

## 四、精确暂存 42 个文件

### 4.1 原有的 18 个 tracked 修改

```powershell
git add -- `
  ".env.example" `
  "README.md" `
  "backend/app/api/agent_read.py" `
  "backend/app/config.py" `
  "backend/app/db/init_db.py" `
  "backend/app/db/models/workflow_run.py" `
  "backend/app/db/workflow_run_repository.py" `
  "backend/app/main.py" `
  "docs/api.md" `
  "docs/architecture.md" `
  "docs/security.md" `
  "tests/conftest.py" `
  "tests/test_agent_read_api.py" `
  "tests/test_assistant_trace_timeline_regression.py" `
  "tests/test_project_capability_isolation.py" `
  "tests/test_project_datasource_isolation.py" `
  "tests/test_workflow_persistence.py" `
  "tests/test_workflow_persistence_db.py"
```

### 4.2 新增的 9 个 backend 文件

```powershell
git add -- `
  "backend/app/api/agent_read_auth.py" `
  "backend/app/api/workflow_read.py" `
  "backend/app/api/workflow_read_auth.py" `
  "backend/app/dto/workflow_read_api.py" `
  "backend/app/services/workflow_runtime/adapters.py" `
  "backend/app/services/workflow_runtime/factory.py" `
  "backend/app/services/workflow_runtime/read_factory.py" `
  "backend/app/services/workflow_runtime/read_service.py" `
  "backend/app/services/workflow_runtime/store_adapters.py"
```

### 4.3 新增的 12 个测试文件

```powershell
git add -- `
  "tests/observability_residue_contract.py" `
  "tests/test_agent_read_api_security.py" `
  "tests/test_observability_residue_lifecycle.py" `
  "tests/test_workflow_read_api.py" `
  "tests/test_workflow_read_api_db.py" `
  "tests/test_workflow_read_api_security_audit.py" `
  "tests/test_workflow_runtime_adapters.py" `
  "tests/test_workflow_runtime_factory.py" `
  "tests/test_workflow_runtime_factory_db.py" `
  "tests/test_workflow_runtime_read_service.py" `
  "tests/test_workflow_runtime_read_service_db.py" `
  "tests/test_workflow_runtime_store_adapters.py"
```

### 4.4 新增的 3 份正式审计文档

```powershell
git add -- `
  "docs/roadmap/Project-Recovery-Audit.md" `
  "docs/roadmap/Phase-6-Exit-Audit.md" `
  "docs/security/Phase-6-Step-7.1-Workflow-Read-API-Security-Audit.md"
```

以上使用精确路径，而不是 `git add -A`，避免把任务书、日志和其他未审查文件一并提交。Git 官方文档说明，`git add` 会把指定路径的内容加入暂存区，后续应通过 `git status` 与暂存区 diff 核对提交内容。([Git 官方文档](https://git-scm.com/docs/git-add))

## 五、提交前暂存区审计

执行：

```powershell
git status --short
git diff --cached --check
git diff --cached --stat
git diff --cached --name-only
git diff --cached
```

必须确认：

* 暂存文件总数为 42。
* 18 个修改文件与审查清单完全一致。
* 新增文件为 21 个代码/测试文件和 3 个正式审计文档。
* 没有任务书副本进入暂存区。
* 没有凭证、无关变更、意外删除或未审查内容。
* 暂存区 diff 与工作区目标内容一致。

**如果任一条件不满足，停止，不要提交。** 不要通过自动取消暂存或删除文件来擅自调整范围；先报告差异。

## 六、创建提交

只有第五步所有检查通过后，才执行：

```powershell
git commit -m "feat: complete Phase 6 read APIs and residue guards"
```

不执行 `git push`。

提交完成后记录：

```powershell
git rev-parse HEAD
git show --stat --oneline HEAD
git status --short
```

核实：

* 新 commit 的父提交是原始 HEAD。
* commit 仅包含批准的 42 个文件。
* 提交前后的 diff 与预期一致。
* 工作区中剩余的任务书副本仍然保留。
* 没有将其他用户变更一并提交。

如果提交命令失败，不要自动 amend、reset 或改写历史；记录错误并停止。

## 七、提交后治理门验证

先运行原始治理门：

```powershell
python -m pytest -q tests/test_observability_http_allowlist_audit.py -k "test_11_backend_working_tree_is_unmodified"
```

然后定位并运行四个级联门对应的实际测试节点：

```text
TestOfflineRegressionExecutionSummary
TestMatrixExecutionBaseline::test_baseline_matches_step89_actual_execution
TestMatrixBaselineGate::test_gate_passes_when_current_matches_baseline
TestRegressionSuite::test_offline_gate_suite_is_green
```

必须先核实这些节点的真实文件路径和完整 pytest node ID，不能凭名称猜测命令。

如果治理门仍然失败：

* 保留原始 traceback。
* 不修改 baseline。
* 不跳过测试。
* 不更改治理门断言。
* 继续记录后续测试结果，但最终状态必须反映该失败。

## 八、提交后全量离线回归

执行：

```powershell
$env:RUN_DB_TESTS="0"
python -m pytest -q
```

记录完整退出码及：

```text
passed
failed
skipped
errors
```

不要将治理门失败直接视为功能缺陷，也不要在没有证据时把其他失败归类为级联。

## 九、提交后全量 DB 回归

先检查项目已有的 DB 分片脚本、分片算法和启动方式，优先复用原有机制。

然后按项目实际支持的方式运行全量 DB 测试。例如，项目若使用环境变量启用 DB 测试：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

如果项目要求按 10 个 shard 执行，则按既有分片脚本逐片运行，并记录每片的完整退出码、通过数、失败数、跳过数及错误。

不得把不同版本的分片结果拼成一个未经验证的总数。

同时核验 residue 相关测试：

* `test_db_residue_is_zero`
* `test_observability_residue_lifecycle.py`
* 两个项目隔离测试中的工具执行记录清理守卫

数据库计数只能通过只读查询核实，不得为了让计数归零而额外 DELETE。

如果 DB 测试失败，报告失败节点与 traceback，不修改代码，不自动追加第二个提交。

## 十、编译与差异检查

执行：

```powershell
python -m compileall backend tests
git diff --check
git status --short
```

记录所有命令的真实退出码。

不要把“命令已执行”当作“验证通过”。

## 十一、最终报告

```text
【Phase 6 Step 8E — Commit & Regression Report】

1. Commit
- Parent HEAD:
- New commit hash:
- Commit message:
- Push: NOT EXECUTED

2. Submitted files
- Tracked modified:
- New backend files:
- New test files:
- Audit documents:
- Total:
- Taskbook copies excluded:

3. Governance gates
- Original gate:
- Four dependent gates:
- Result:

4. Offline full suite
- Passed:
- Failed:
- Skipped:
- Errors:

5. DB full suite
- Shard results:
- Total passed:
- Total failed:
- Total skipped:
- Errors:

6. Residue
- Monitored table counts:
- Read-only verification:
- Residue failure:

7. Compile / diff
- compileall:
- diff --check:

8. Remaining work
- Open blockers:
- Security deployment boundary:
- Phase 6 Exit:

9. Final state
- Commit: DONE / FAILED
- Push: NOT EXECUTED
- Phase 7: NOT STARTED
```

## 十二、停止条件

完成提交与验证报告后立即停止。

不要推送远程，不要修复测试，不要创建额外提交，不要进入 Phase 7。若测试仍有失败，将其作为明确的验收阻塞项交给用户决策。
