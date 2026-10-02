你现在开始执行：

# Phase 3.12 Step 115：Merge Queue Readiness Audit

## 一、目标

在 Step 114 GitHub Required Check 验证完成后，只做一次：

> **Merge Queue Readiness Audit**

本阶段只审计当前状态。

**禁止开启 Merge Queue。**

**禁止修改 GitHub Ruleset。**

**禁止修改 GitHub Actions Workflow。**

**禁止修改生产代码。**

---

# 二、当前已知状态

当前：

```text
Repository:
shuang54/wms-ai-assistant

Protected branch:
main

Ruleset:
Protect Main

Required Check:
Observability Matrix Gate

Required Check:
ENABLED

Merge Queue:
当前未启用

Workflow:
.github/workflows/observability-matrix-gate.yml
```

当前 workflow 已有：

```yaml
on:
  push:
  pull_request:
```

当前没有：

```yaml
merge_group:
```

---

# 三、本阶段严格禁止

禁止：

```text
修改 Ruleset
开启 Merge Queue
修改 Required Check
修改 workflow
增加 merge_group
修改 Gate
修改 Adapter
修改 CLI
修改 Baseline
修改 backend
修改数据库
```

禁止：

```text
git push
git merge
git rebase
git reset
git clean
git stash
```

除非用户明确要求。

本阶段只做：

```text
READ
AUDIT
TEST
DOCUMENT
STOP
```

---

# 四、Step 1：审计 GitHub Ruleset

只读检查：

```text
Protect Main
```

确认：

```text
target = main
required status check = Observability Matrix Gate
required check = active
```

确认：

```text
merge_queue rule = absent
```

如果 API 无权限：

不要猜测。

明确：

```text
Merge Queue status = NOT AUDITED
```

---

# 五、Step 2：审计 Workflow

阅读：

```text
.github/workflows/observability-matrix-gate.yml
```

确认：

```text
push = present
pull_request = present
merge_group = absent
```

确认：

```text
job name = observability-matrix-gate
workflow name = Observability Matrix Gate
```

确认 Required Check 名称：

```text
Observability Matrix Gate
```

与 workflow job 产生的 check identity 一致。

---

# 六、Step 3：确认 Merge Queue 影响

不要开启 Merge Queue。

只验证设计事实：

当前：

```text
pull_request
    ↓
Observability Matrix Gate
```

如果未来开启：

```text
Merge Queue
    ↓
merge_group
    ↓
Observability Matrix Gate
```

当前缺少：

```yaml
merge_group:
```

因此：

```text
Merge Queue Readiness = NOT READY
```

原因：

```text
workflow 当前没有 merge_group trigger
```

GitHub 官方要求：如果 Merge Queue 中需要 GitHub Actions 的 required check，workflow 必须响应 `merge_group` 事件，否则 required check 不会在 merge queue 中被报告。

---

# 七、Step 4：确认当前 Required Check 不受影响

确认当前状态仍然：

```text
Required Check:
Observability Matrix Gate
```

并且：

```text
Merge Queue:
OFF
```

不要修改。

---

# 八、Step 5：Future Minimum Change

只记录设计，不实现。

未来如果明确决定开启 Merge Queue：

最小 workflow change：

```yaml
on:
  push:
  pull_request:
  merge_group:
    types:
      - checks_requested
```

具体 branch filter 是否需要增加：

```text
以未来真实 Ruleset 为准。
```

不要现在添加。

---

# 九、Step 6：测试

新增或复用现有测试文件：

```text
tests/test_github_actions_matrix_gate.py
```

不要新建第二个 governance framework。

增加最小测试：

### 1

确认当前 contract：

```text
merge_queue_enabled == False
```

### 2

确认：

```text
merge_group_trigger == False
```

### 3

确认：

```text
decision == DEFER
```

### 4

确认未来要求：

```text
merge_queue_enabled == True
→ merge_group trigger 必须存在
```

### 5

确认当前 Required Check identity 不变：

```text
Observability Matrix Gate
```

---

# 十、Documentation

新增：

```text
docs/evaluation/phase-3.12-step-115-merge-queue-readiness.md
```

记录：

```text
Current Merge Queue:
OFF

Current merge_group trigger:
ABSENT

Required Check:
Observability Matrix Gate

Current Decision:
DEFER

Merge Queue Ready:
NO
```

未来最小变化：

```text
Add merge_group trigger
```

但是：

```text
DO NOT IMPLEMENT NOW
```

---

# 十一、验证

执行：

```powershell
python -m pytest tests/test_github_actions_matrix_gate.py -q
```

然后：

```powershell
python -m compileall backend tests scripts
```

不要执行：

```text
全量 pytest
DB Matrix
DeepSeek
真实网络测试
```

本阶段要求：

```text
Production code = 0
DB writes = 0
DeepSeek = 0
Network = 0
Workflow modification = 0
Ruleset modification = 0
```

---

# 十二、Git Diff

检查：

```powershell
git status --short
git diff --stat
git diff
```

确认：

```text
backend = unchanged
.github/workflows = unchanged
Gate = unchanged
Adapter = unchanged
CLI = unchanged
Baseline = unchanged
Ruleset = unchanged
```

允许：

```text
tests/test_github_actions_matrix_gate.py
docs/evaluation/phase-3.12-step-115-merge-queue-readiness.md
```

---

# 十三、完成后报告

严格输出：

```text
Phase 3.12 Step 115 COMPLETE

1. Ruleset
2. Required Check
3. Merge Queue
4. merge_group Trigger
5. Current Readiness
6. Future Minimum Change
7. Tests
8. compileall
9. Production Code
10. Workflow
11. GitHub Settings
12. Git Diff

Decision:

Merge Queue = DEFER
Required Check = ENABLED
Observability Matrix Gate = ACTIVE

Phase 3.12 Step 115 STOP
```

完成后立即停止。

不要开启 Merge Queue。

不要增加 merge_group。

不要进入 Step 116。
