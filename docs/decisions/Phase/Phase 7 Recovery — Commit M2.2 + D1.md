# Phase 7 Recovery — Commit M2.2 + D1

## 一、执行授权

用户已明确授权：

- `git add`
- `git commit`

提交目标：**Phase 7 Recovery — M2.2 Conversation Authentication & Ownership + D1 Project Scope Hardening**

项目路径：

`D:\coding\ai\wms-ai-assistant`

当前预期分支：`phase7`

当前预期 HEAD：

`f919e7209045be3c9d12efe49c83086000c0641b`

必须以实际 Git 输出为准。

本次允许正常提交 M2.2 + D1 及其配套测试、文档和阶段任务书。

**不授权以下操作：**

- `git push`
- 创建 PR
- 部署
- 修改历史提交
- `git reset` / `git clean` / `git checkout` / `git restore` / `git stash`
- 修改冻结测试或历史 baseline
- 修复 Step 8 历史遗留失败
- 开始 M2.3

## 二、提交前最后核验

先执行：

```powershell
git status --short --branch
git log -5 --oneline
git diff --check
git diff --stat
git diff --name-status
git diff
git diff --cached --stat
git ls-files --others --exclude-standard
```

确认：

1. 当前仍在 `phase7`。
2. HEAD 与预期一致；如果不一致，停止并报告。
3. 暂存区没有意外内容。
4. 所有变更都属于已审查的 M2.2 + D1。
5. 没有 `.env`、真实凭据、数据库连接串或无关文件。
6. 没有修改冻结模型、migration、Prompt、Validator、Executor、Router 策略或历史 baseline。
7. `.env.example`、`docs/api.md`、`docs/security.md` 的变更属于本次已审查的文档补齐。
8. 四份阶段任务书和两个新增测试文件确实属于本次提交范围。

**如果出现未审查的新文件、HEAD 变化、额外暂存内容或范围不明的改动，先停止，不要直接提交。**

## 三、精确暂存

只暂存以下已审查的文件。

### 生产代码

```text
backend/app/api/conversations.py
backend/app/db/conversation_repository.py
backend/app/services/conversation_service.py
```

### 修改过的测试

```text
tests/test_conversation_api.py
tests/test_conversation_api_db_e2e.py
tests/test_conversation_multiturn_db_e2e.py
tests/test_conversation_context_e2e_db.py
tests/test_conversation_message_api_contract.py
tests/test_conversation_message_idempotency.py
tests/test_conversation_message_api_architecture_audit.py
```

### 新增测试

```text
tests/conversation_auth_support.py
tests/test_conversation_ownership.py
```

### 文档

```text
.env.example
docs/api.md
docs/security.md
```

另外，将 Git 状态中实际存在且已确认属于本阶段的四份任务书逐个加入暂存区：

- M2.1 Audit 任务书
- M2.2 实现任务书
- D1 Project Scope Hardening 任务书
- M2.2 Pre-Commit Final Audit 任务书

必须使用 `git ls-files --others --exclude-standard` 的实际文件名确认路径，不要猜文件名，也不要把整个 `docs/decisions/Phase/` 目录批量加入。

不要使用：

```powershell
git add .
git add -A
```

例如，可以按确认后的路径执行多个明确的 `git add <path>` 命令。

## 四、核验暂存区

暂存完成后，执行：

```powershell
git status --short
git diff --cached --check
git diff --cached --stat
git diff --cached --name-status
git diff --cached
```

逐项确认：

- 暂存区只包含 M2.2 + D1。
- 所有新文件都在预期范围内。
- 文档和代码一致。
- 不存在 secrets。
- 不存在意外删除。
- 未暂存的剩余改动如果存在，必须查明原因；不得直接忽略。

只有暂存范围完全符合预期才继续。

## 五、创建提交

提交信息：

```text
Phase 7 Recovery — M2.2 Conversation Auth & D1 Project Scope
```

执行：

```powershell
git commit -m "Phase 7 Recovery — M2.2 Conversation Auth & D1 Project Scope"
```

如果 Git hooks 或提交检查失败，不要绕过 hook，也不要使用 `--no-verify`。记录错误并停止。

## 六、提交后验证

提交成功后执行：

```powershell
git status --short --branch
git log -1 --format="%H%n%s"
git show --stat --oneline HEAD
```

确认提交内容和工作区状态。

然后重新验证原先因未提交改动而失败的治理检查：

```powershell
$env:PYTHONUTF8="1"
python -m pytest -q tests/test_observability_http_allowlist_audit.py
python scripts/run_matrix_gate.py
```

如果项目测试入口或 Matrix Gate 有既定的参数要求，按仓库现有说明执行。

另外执行：

```powershell
python -m compileall backend tests scripts
```

### 失败归因

必须区分：

1. `test_11_backend_working_tree_is_unmodified` 是否在正常提交后通过。
2. Matrix Gate 是否恢复 PASS。
3. 既有 Step 8 的 3 个失败是否仍存在。
4. 是否出现任何新的 M2.2 / D1 失败。
5. 工作区是否干净。

不要为了通过 Matrix Gate 修改基线、冻结测试或历史快照。

如果 Matrix Gate 仍然失败，记录真实结果，不要把它报告成 PASS。

## 七、Git 操作边界

即使提交成功，也只执行本地验证。

```text
Git add: AUTHORIZED
Git commit: AUTHORIZED
Git push: NOT AUTHORIZED
PR: NOT AUTHORIZED
Deployment: NOT AUTHORIZED
M2.3: NOT STARTED
```

## 八、最终报告

```text
【Phase 7 Recovery — M2.2 + D1 COMMIT REPORT】

1. Branch / HEAD before commit
2. Commit hash
3. Commit message
4. 实际提交文件数量
5. 生产代码 / 测试 / 文档变更摘要
6. Git status after commit
7. test_observability_http_allowlist_audit.py
8. Matrix Gate
9. compileall
10. Step 8 历史遗留失败
11. 新增失败
12. DB residue（如果本次验证有查询）
13. 是否 push / PR / deploy
14. M2.2 + D1 最终状态

Git add: EXECUTED / NOT EXECUTED
Git commit: EXECUTED / NOT EXECUTED
Git push: NOT EXECUTED
Deployment: NOT EXECUTED
M2.3: NOT STARTED
STOP
```

**提交并完成上述验证后立即停止，等待下一步指令。**