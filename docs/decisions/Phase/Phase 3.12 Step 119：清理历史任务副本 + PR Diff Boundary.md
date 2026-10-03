你现在开始执行：

# Phase 3.12 Step 119：清理历史任务副本 + PR Diff Boundary

## 一、阶段目标

Step 118 发现一个真实边界问题：

当前 PR 如果直接创建，会包含历史 commit `cdb8961` 中的 3 个任务副本：

```text
docs/decisions/Phase/Phase 3.12 Step 111 — Branch Synchronization Strategy Audit.md
docs/decisions/Phase/Phase 3.12 Step 113 提交边界审计与本地Commit.md
docs/decisions/Phase/Phase 3.12 Step 114：Push + PR + Required Check 验证.md
```

这些不是 Step 115 正式交付物。

本阶段采用：

> **新增 cleanup commit 删除任务副本，不重写历史。**

最终 PR diff 应只包含：

```text
tests/test_github_actions_matrix_gate.py

docs/evaluation/phase-3.12-step-115-merge-queue-readiness.md
```

---

# 二、严格禁止

禁止：

```text
git rebase
git reset
git filter-branch
git filter-repo
git commit --amend
git squash
git force-push
```

禁止修改：

```text
backend/
.github/workflows/
Gate
Adapter
CLI
Baseline
Ruleset
Required Check
```

禁止：

```text
删除 Step 115 正式测试
删除 Step 115 evaluation 文档
```

禁止创建 PR。

禁止 Merge PR。

---

# 三、Step 1：删除 3 个历史任务副本

只删除：

```text
docs/decisions/Phase/Phase 3.12 Step 111 — Branch Synchronization Strategy Audit.md

docs/decisions/Phase/Phase 3.12 Step 113 提交边界审计与本地Commit.md

docs/decisions/Phase/Phase 3.12 Step 114：Push + PR + Required Check 验证.md
```

不要删除：

```text
tests/test_github_actions_matrix_gate.py

docs/evaluation/phase-3.12-step-115-merge-queue-readiness.md
```

使用 Git 删除：

```powershell
git rm "docs/decisions/Phase/Phase 3.12 Step 111 — Branch Synchronization Strategy Audit.md"
git rm "docs/decisions/Phase/Phase 3.12 Step 113 提交边界审计与本地Commit.md"
git rm "docs/decisions/Phase/Phase 3.12 Step 114：Push + PR + Required Check 验证.md"
```

---

# 四、Step 2：任务副本处理

如果以下文件存在：

```text
docs/decisions/Phase/Phase 3.12 Step 115：Merge Queue Readiness Audit.md
docs/decisions/Phase/Phase 3.12 Step 116：Step 115 Commit Boundary + Branch Sync Audit.md
docs/decisions/Phase/Phase 3.12 Step 117：Step 115 Safe Sync + Commit + Push.md
docs/decisions/Phase/Phase 3.12 Step 118：Step 115 PR + Required Check Final Verification.md
```

它们属于当前工作区任务副本。

原则：

```text
不加入 PR
不删除
不提交
```

保持 untracked。

---

# 五、Step 3：检查 PR Diff

执行：

```powershell
git diff origin/main...HEAD --name-status
```

此时预期仍然可能包含：

```text
Step 111
Step 113
Step 114
Step 115
```

因为 cleanup commit 尚未创建。

然后检查：

```powershell
git diff -- backend
git diff -- .github/workflows
```

必须为空。

---

# 六、Step 4：验证删除范围

执行：

```powershell
git diff --cached --name-status
```

必须恰好是：

```text
D docs/decisions/Phase/Phase 3.12 Step 111 — Branch Synchronization Strategy Audit.md
D docs/decisions/Phase/Phase 3.12 Step 113 提交边界审计与本地Commit.md
D docs/decisions/Phase/Phase 3.12 Step 114：Push + PR + Required Check 验证.md
```

如果出现其他文件：

**STOP。**

---

# 七、Step 5：Commit

Commit message：

```text
chore: remove phase task copies from PR diff
```

执行：

```powershell
git commit -m "chore: remove phase task copies from PR diff"
```

不要 amend。

不要 squash。

---

# 八、Step 6：Commit 后验证

执行：

```powershell
git status --short
git log --oneline --decorate -6
```

确认：

```text
Step 115 commit
+
cleanup commit
```

历史仍然保留。

没有 rebase。

没有 reset。

---

# 九、Step 7：验证最终 PR Diff

这是本阶段最重要的检查。

执行：

```powershell
git diff origin/main...HEAD --name-status
```

最终必须**恰好只有**：

```text
M tests/test_github_actions_matrix_gate.py
A docs/evaluation/phase-3.12-step-115-merge-queue-readiness.md
```

不能出现：

```text
Step 111 task copy
Step 113 task copy
Step 114 task copy
Step 115 task copy
Step 116 task copy
Step 117 task copy
Step 118 task copy
```

---

# 十、Step 10：确认生产边界

执行：

```powershell
git diff origin/main...HEAD -- backend
git diff origin/main...HEAD -- .github/workflows
```

必须：

```text
backend = EMPTY
workflow = EMPTY
```

同时确认：

```text
Gate = unchanged
Adapter = unchanged
CLI = unchanged
Baseline = unchanged
```

---

# 十一、Step 11：测试

执行：

```powershell
python -m pytest tests/test_github_actions_matrix_gate.py -q
```

要求：

```text
66 passed
0 failed
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

要求：

```text
0 errors
```

如果失败：

**STOP。**

---

# 十二、Step 12：Push

确认：

```powershell
git branch --show-current
git rev-parse HEAD
git rev-parse origin/ci/phase-3.12-steps-102-107
```

此时本地 HEAD 会比远端 feature branch 多一个 cleanup commit。

然后：

```powershell
git push origin ci/phase-3.12-steps-102-107
```

禁止：

```text
--force
--force-with-lease
```

---

# 十三、Step 13：GitHub Actions

Push 后只检查：

```text
Observability Matrix Gate
```

确认最新 commit：

```text
head_sha == local HEAD
```

并且：

```text
conclusion = success
```

如果失败：

**STOP。**

不要自动修改。

---

# 十四、Step 14：不要创建 PR

本阶段只完成：

```text
cleanup
commit
push
CI
```

不要创建 PR。

Step 120 再专门处理：

```text
PR creation
Required Check
mergeable state
```

---

# 十五、最终报告

严格：

```text
Phase 3.12 Step 119 COMPLETE

1. Deleted Task Copies
2. Cleanup Commit
3. Final PR Diff
4. Production Diff
5. Workflow Diff
6. Tests
7. compileall
8. Push
9. GitHub Actions
10. Untracked Task Copies
11. Git History
12. Git Operations

Deleted:
- Step 111 task copy
- Step 113 task copy
- Step 114 task copy

Preserved:
- Step 115 tests
- Step 115 evaluation document

PR Diff:
- tests/test_github_actions_matrix_gate.py
- docs/evaluation/phase-3.12-step-115-merge-queue-readiness.md

Backend:
UNCHANGED

Workflow:
UNCHANGED

Required Check:
UNCHANGED

Merge Queue:
DEFER

Phase 3.12 Step 119 STOP
```

**完成后立即停止。**

不要创建 PR。
不要 Merge。
不要开启 Merge Queue。
不要 rebase。
不要 force-push。
