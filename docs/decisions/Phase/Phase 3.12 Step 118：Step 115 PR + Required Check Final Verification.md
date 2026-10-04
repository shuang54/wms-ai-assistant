你现在开始执行：

# Phase 3.12 Step 118：Step 115 PR + Required Check Final Verification

## 一、目标

Step 117 已完成：

```text
Step 115 commit:
6cee7cb704db517dd64d075b95676a5eb57fcfac

remote feature branch:
ci/phase-3.12-steps-102-107

Observability Matrix Gate:
SUCCESS

Merge Queue:
DEFER
```

本阶段只完成：

```text
创建 PR
    ↓
等待 pull_request Check
    ↓
验证 Required Check
    ↓
验证 Gate SUCCESS
    ↓
STOP
```

---

# 二、PR

Base：

```text
main
```

Head：

```text
ci/phase-3.12-steps-102-107
```

建议标题：

```text
Phase 3.12 — Merge Queue Readiness Audit
```

建议 Body：

```text
Phase 3.12 Step 115 Merge Queue Readiness Audit.

Current governance:
- Required Check: Observability Matrix Gate
- Merge Queue: DEFER
- merge_group trigger: intentionally absent

Step 115 adds only governance audit tests and evaluation documentation.

Production backend unchanged.
GitHub Actions workflow unchanged.
Gate / Adapter / CLI / Baseline unchanged.
No database schema changes.
```

如果已有对应 PR：

```text
复用已有 PR
```

不要创建重复 PR。

---

# 三、严格禁止

本阶段禁止：

```text
Merge PR
Close PR
Enable Merge Queue
Modify Ruleset
Modify Required Check
Modify Workflow
Add merge_group
Modify Gate
Modify Adapter
Modify CLI
Modify Baseline
Modify backend
```

禁止：

```text
git merge
git rebase
git reset
git clean
git stash
git force-push
```

本阶段：

```text
PR creation
+
READ ONLY verification
```

---

# 四、PR 创建前检查

执行：

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git rev-parse origin/ci/phase-3.12-steps-102-107
git rev-parse origin/main
```

要求：

```text
HEAD == origin/ci/phase-3.12-steps-102-107
```

必须仍然：

```text
6cee7cb
```

如果 SHA 已变化：

**STOP。**

---

# 五、PR 内容检查

确认 PR diff 只包含 Step 115：

```text
tests/test_github_actions_matrix_gate.py

docs/evaluation/phase-3.12-step-115-merge-queue-readiness.md
```

任务副本不得进入 PR：

```text
docs/decisions/Phase/Phase 3.12 Step 115：Merge Queue Readiness Audit.md
docs/decisions/Phase/Phase 3.12 Step 116：Step 115 Commit Boundary + Branch Sync Audit.md
docs/decisions/Phase/Phase 3.12 Step 117：Step 115 Safe Sync + Commit + Push.md
```

---

# 六、创建 PR

如果没有 PR：

使用 GitHub Web UI 创建：

```text
base:
main

compare:
ci/phase-3.12-steps-102-107
```

如果当前环境有可用 GitHub PR 创建能力，也可以使用。

不要修改其他 GitHub 设置。

---

# 七、等待 Required Check

PR 创建后检查：

```text
Observability Matrix Gate / pull_request
```

确认：

```text
Status = completed
Conclusion = success
```

并确认：

```text
Required = YES
```

不要使用：

```text
push
```

Check 替代：

```text
pull_request
```

必须验证 PR 对应的 `pull_request` Check。

---

# 八、检查最新 SHA

PR HEAD 必须：

```text
6cee7cb704db517dd64d075b95676a5eb57fcfac
```

确认：

```text
Observability Matrix Gate
head_sha == 6cee7cb...
```

不能使用旧 SHA 的成功结果。

---

# 九、验证 Merge 状态

只读检查 PR：

```text
mergeable
mergeable_state
```

如果：

```text
Required Check = SUCCESS
```

但：

```text
mergeable_state = BLOCKED
```

不要猜原因。

记录：

```text
Merge State = BLOCKED
Reason = unknown
```

然后 STOP。

如果：

```text
mergeable_state = CLEAN
```

只记录：

```text
PR is merge-ready
```

但：

**不要 Merge。**

---

# 十、Required Check Contract

确认：

```text
Required Check:
Observability Matrix Gate

Ruleset:
Protect Main

Merge Queue:
OFF

merge_group:
ABSENT
```

不得因为创建 PR 而改变这些设置。

---

# 十一、Step 115 测试

本阶段不需要重新运行完整测试。

如果需要本地 sanity check：

```powershell
python -m pytest tests/test_github_actions_matrix_gate.py -q
```

要求：

```text
66 passed
0 failed
```

compile：

```powershell
python -m compileall -q backend tests scripts
```

要求：

```text
0 errors
```

---

# 十二、Git 安全

确认：

```text
backend = unchanged
.github/workflows = unchanged
Gate = unchanged
Adapter = unchanged
CLI = unchanged
Baseline = unchanged
```

---

# 十三、最终报告

严格输出：

```text
Phase 3.12 Step 118 COMPLETE

1. Branch
2. HEAD
3. PR Number
4. PR Base
5. PR Head
6. PR HEAD SHA
7. pull_request Check
8. Required Status
9. Observability Matrix Gate
10. Ruleset
11. Mergeable State
12. Merge Queue
13. merge_group
14. Tests
15. compileall
16. Production Code
17. Workflow
18. GitHub Settings
19. Git Operations
20. Final Decision
```

最终：

```text
PR = OPEN
Required Check = SUCCESS
Observability Matrix Gate = SUCCESS
Merge Queue = DEFER
Merge = NOT EXECUTED

Phase 3.12 Step 118 STOP
```

如果：

```text
Required Check FAILED
Gate FAILED
PR creation FAILED
SHA mismatch
```

立即停止。

不要自行修复。

不要 Merge。

不要进入 Step 119。
