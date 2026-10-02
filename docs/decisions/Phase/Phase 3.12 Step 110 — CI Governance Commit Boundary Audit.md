# Phase 3.12 Step 110 — CI Governance Commit Boundary Audit

## 一、阶段目标

对当前工作区进行一次：

> **Commit Boundary Audit**

目标不是提交代码。

目标是明确：

```text
Step 108
Step 109
```

产生的修改哪些属于 CI Governance，哪些是历史未推送提交，哪些是任务说明副本。

本 Step：

```text
只审计
不 commit
不 push
```

---

# 二、当前背景

当前已知：

```text
Step 108:
tests/test_github_actions_matrix_gate.py
docs/evaluation/phase-3.12-ci-governance-contract.md

Step 109:
tests/test_github_actions_matrix_gate.py
docs/evaluation/phase-3.12-ci-governance-self-consistency.md
```

同时当前本地 `main` 曾存在：

```text
Step 102
Step 103
Step 104
```

尚未 push 到：

```text
origin/main
```

因此本 Step 必须严格区分：

```text
历史提交
VS
Step 108/109 工作区修改
VS
任务说明副本
```

---

# 三、严格禁止

本阶段禁止：

```text
git commit
git push
git reset
git rebase
git merge
git cherry-pick
git stash
git clean
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
```

禁止：

```text
full pytest
RUN_DB_TESTS
DeepSeek
GitHub API
```

---

# 四、Step 1：读取 Git 状态

执行：

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
```

记录：

```text
current branch
local HEAD
origin/main
ahead / behind
```

不要修改任何内容。

---

# 五、Step 2：统计未提交修改

执行：

```powershell
git diff --stat
git diff -- tests/test_github_actions_matrix_gate.py
```

确认 Step 108/109 的修改主要集中在：

```text
tests/test_github_actions_matrix_gate.py
```

---

# 六、Step 3：检查新增 Evaluation 文档

确认：

```text
docs/evaluation/phase-3.12-ci-governance-contract.md
docs/evaluation/phase-3.12-ci-governance-self-consistency.md
```

分别存在。

只读取，不修改。

---

# 七、Step 4：识别任务说明副本

当前可能存在：

```text
docs/decisions/Phase/
```

中的：

```text
Phase 3.12 Step 108 — ...
Phase 3.12 Step 109 — ...
```

这些属于：

```text
task instruction copies
```

不要自动删除。

报告：

```text
tracked / untracked
```

即可。

---

# 八、Step 5：确认生产代码零修改

执行：

```powershell
git diff -- backend
git diff -- .github/workflows
```

预期：

```text
empty
```

同时检查：

```powershell
git status --short
```

不得出现：

```text
backend/*
.github/workflows/*
```

---

# 九、Step 6：Commit Boundary Classification

建立一个纯测试函数，例如：

```python
classify_ci_governance_change(path) -> str
```

只允许：

```text
STEP_108_109
HISTORICAL
TASK_COPY
OTHER
```

但：

**不要根据 git commit message 猜历史归属。**

优先依据：

```text
current working tree
known files
known paths
```

---

# 十、Step 7：明确本阶段允许提交的文件

Step 108/109 当前预期：

```text
tests/test_github_actions_matrix_gate.py

docs/evaluation/phase-3.12-ci-governance-contract.md

docs/evaluation/phase-3.12-ci-governance-self-consistency.md
```

注意：

这里只是：

```text
candidate commit set
```

**不执行 commit。**

---

# 十一、Step 8：禁止混入历史提交

确认：

```text
Step 102
Step 103
Step 104
```

已经存在于：

```text
HEAD ancestry
```

但不要重新操作它们。

不要：

```text
reset
rebase
squash
cherry-pick
```

---

# 十二、Step 9：禁止混入任务说明

如果：

```text
docs/decisions/Phase/
```

存在任务说明副本：

不要自动加入 candidate commit set。

分类：

```text
TASK_COPY
```

除非已经是项目既有正式文档体系的一部分。

不要猜。

---

# 十三、Step 10：检查是否存在意外修改

执行：

```powershell
git diff --name-only
git ls-files --others --exclude-standard
```

逐项分类：

```text
STEP_108_109
HISTORICAL
TASK_COPY
OTHER
```

如果出现：

```text
OTHER
```

必须：

```text
COMMIT BLOCKED
```

只报告，不处理。

---

# 十四、Step 11：Tests

只运行：

```powershell
python -m pytest -q tests/test_github_actions_matrix_gate.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

不要运行：

```text
full pytest
RUN_DB_TESTS
DeepSeek
GitHub Actions
```

---

# 十五、Step 12：Diff Safety Audit

确认：

```text
backend = 0
workflow = 0
Gate = 0
Adapter = 0
CLI = 0
Baseline = 0
```

允许：

```text
test file
evaluation docs
```

---

# 十六、最终报告

严格：

```text
【Phase 3.12 Step 110 COMPLETE】

1. Branch
- current:

2. Git State
- HEAD:
- origin/main:
- ahead:
- behind:

3. Working Tree

STEP_108_109:
- ...

HISTORICAL:
- ...

TASK_COPY:
- ...

OTHER:
- ...

4. Candidate Commit Set
- ...

5. Production Code
- backend:
- workflow:
- gate:
- adapter:
- cli:
- baseline:

6. Tests
- github_actions_matrix_gate:
- compileall:

7. Network / DB / LLM
- network:
- DB:
- DeepSeek:

8. Commit
- executed: NO

9. Push
- executed: NO

10. Conclusion
- COMMIT BOUNDARY = CLEAN / BLOCKED
```

---

# 十七、硬停止

完成后：

**立即 STOP。**

不要：

```text
commit
push
PR
Merge Queue
merge_group
Step 111
```

等待下一步指令。
