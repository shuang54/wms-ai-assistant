你现在开始执行：

# Phase 3.12 Step 120：创建 PR、验证 Required Check 并最终合并

## 一、目标

这是 Phase 3.12 GitHub / CI 治理的最后一步。

当前：

```text
branch:
ci/phase-3.12-steps-102-107

HEAD:
808a490ea5e4942a000689abc0f32714704b2082

origin/main:
83cecdb

Observability Matrix Gate:
SUCCESS

Required Check:
YES

Merge Queue:
DEFER
```

Step 119 已确认：

```text
PR Diff:

D docs/decisions/Phase/… Step 111 — Branch Synchronization Strategy Audit.md
A docs/evaluation/phase-3.12-step-115-merge-queue-readiness.md
M tests/test_github_actions_matrix_gate.py
```

其中 Step 111 是 `origin/main` 已存在的 0-byte 空文件。

**不要恢复 Step 111 文件。**

**不要再为了“2 文件 diff”修改历史。**

---

# 二、创建 PR

如果当前没有开放 PR：

创建：

```text
base:
main

head:
ci/phase-3.12-steps-102-107
```

PR Title：

```text
Phase 3.12 — CI Governance Contract Audit
```

PR Body：

```text
Phase 3.12 CI Governance / Required Check work.

Includes:
- Step 108 CI Governance Contract
- Step 109 Self-Consistency Audit
- Step 110 Commit Boundary Audit
- Step 111 Branch Synchronization Audit
- Step 112 Safe Merge Synchronization
- Step 113 Local Commit Boundary
- Step 115 Merge Queue Readiness Audit

Production backend unchanged.
GitHub Actions workflow unchanged.

Gate / Adapter / CLI / Baseline unchanged.

Required Check:
Observability Matrix Gate

Merge Queue:
Deferred by design.
```

如果创建 PR 需要 GitHub Web UI：

**可以提示用户手动创建。**

不要编造已经创建成功。

---

# 三、PR 创建后验证

确认：

```text
base = main
head = ci/phase-3.12-steps-102-107
```

确认 PR HEAD：

```text
808a490ea5e4942a000689abc0f32714704b2082
```

检查：

```text
Observability Matrix Gate / pull_request
```

必须：

```text
Required
SUCCESS
```

同时检查：

```text
Observability Matrix Gate / push
```

不要求作为 PR merge gate，但应该保持 SUCCESS。

---

# 四、如果 Required Check 失败

如果：

```text
Observability Matrix Gate / pull_request
```

失败：

**立即停止。**

不要：

```text
修改 workflow
修改 Gate
修改 Baseline
修改 Ruleset
修改 test
修改 backend
重新设计 CI
```

只报告：

```text
PR Check FAILED
Run:
Commit:
Failure:
```

等待下一步指令。

---

# 五、如果 Required Check 成功

确认 GitHub Merge box 显示：

```text
All required checks have passed
```

并确认：

```text
Observability Matrix Gate
Required
Passed
```

此时：

**合并 PR。**

优先使用 GitHub Web UI 正常 Merge。

不要：

```text
force push
rebase
reset
squash 本地历史
修改 Ruleset
开启 Merge Queue
```

如果 GitHub 当前提供：

```text
Merge
Squash and merge
Rebase and merge
```

使用正常的 GitHub 默认合并方式即可。

---

# 六、合并后验证

确认：

```text
PR = MERGED
```

确认：

```text
base main
```

确认：

```text
origin/main
```

已经包含：

```text
Step 115
Step 119 cleanup
```

确认最新 main 上：

```text
Observability Matrix Gate = SUCCESS
```

---

# 七、最终 Git 状态

本地不要自动清理用户未跟踪任务副本。

保持：

```text
untracked task copies
```

原样存在。

不要：

```text
git clean
```

不要删除这些任务文档。

---

# 八、最终安全检查

确认：

```text
Backend:
UNCHANGED

Workflow:
UNCHANGED

Gate:
UNCHANGED

Adapter:
UNCHANGED

CLI:
UNCHANGED

Baseline:
UNCHANGED

Ruleset:
UNCHANGED

Merge Queue:
DEFER

Required Check:
ENABLED

DB migration:
0

DB writes:
0

DeepSeek:
0

Network:
仅 GitHub PR / CI 正常操作
```

---

# 九、最终报告

只报告：

```text
Phase 3.12 FINAL

1. Branch
2. PR
3. PR HEAD
4. Required Check
5. GitHub Actions
6. Merge Result
7. origin/main
8. Production Code
9. Workflow
10. Gate / Adapter / CLI / Baseline
11. Merge Queue
12. DB
13. Git history
14. Untracked task copies
15. Final Status
```

最终：

```text
Phase 3.12 COMPLETE

CI Governance:
READY

Required Check:
ENABLED

Observability Matrix Gate:
PASS

Merge Queue:
DEFERRED

Production Runtime:
UNCHANGED

STOP
```

**完成后立即停止。**

不要进入新的 GitHub Step。

不要继续做 CI 治理。

不要开发 Merge Queue。

不要开发 Dashboard。

不要开发 OpenTelemetry。

不要开发 Conversation / Memory / Agent / MCP。
