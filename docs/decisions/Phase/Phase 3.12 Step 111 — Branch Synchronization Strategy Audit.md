# Phase 3.12 Step 111 — Branch Synchronization Strategy Audit

## 一、阶段目标

当前状态：

```text
origin/main
    ↓
07e9286  ← PR #2 已合入

local branch
    ↓
fdefcea  ← Step 108
```

当前：

```text
ahead  = 1
behind = 1
```

同时工作区存在：

```text
Step 109
Step 110
```

尚未提交。

本 Step 只回答：

> **在提交 Step 109/110 之前，如何安全地把当前分支同步到最新 origin/main？**

---

# 二、严格禁止

本阶段：

```text
NO COMMIT
NO PUSH
NO MERGE
NO REBASE
NO RESET
NO STASH
NO CLEAN
NO PR
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

不要改变工作区内容。

---

# 三、Step 1：确认当前图谱

执行：

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
git merge-base HEAD origin/main
```

然后：

```powershell
git log --oneline --graph --decorate -12
```

确认：

```text
local HEAD = fdefcea
origin/main = 07e9286
```

并明确：

```text
local branch has Step 108
origin/main has PR #2 merge commit
```

---

# 四、Step 2：确认共同祖先

执行：

```powershell
git merge-base --is-ancestor origin/main HEAD
echo $LASTEXITCODE
```

以及：

```powershell
git merge-base --is-ancestor HEAD origin/main
echo $LASTEXITCODE
```

预期：

```text
origin/main → HEAD = FALSE
HEAD → origin/main = FALSE
```

即：

```text
branches diverged
```

不要修改。

---

# 五、Step 3：确认双方独有提交

执行：

```powershell
git log --oneline origin/main..HEAD
```

预期只看到：

```text
feat:Phase 3.12 Step 108 — CI Governance Contract Freeze
```

再执行：

```powershell
git log --oneline HEAD..origin/main
```

确认只有：

```text
PR #2 merge commit
```

及其实际 base 差异。

不要根据 commit message 猜测，记录真实结果。

---

# 六、Step 4：确认工作区修改

执行：

```powershell
git diff --name-only
git ls-files --others --exclude-standard
```

确认：

```text
Step 109/110 test modification
Step 109 evaluation document
```

仍然存在。

特别注意：

**不要因为分支同步而丢失这些未提交修改。**

---

# 七、Step 5：计算 Merge / Rebase 风险

只做：

```powershell
git diff origin/main...HEAD --stat
```

以及：

```powershell
git diff origin/main...HEAD -- tests/test_github_actions_matrix_gate.py
```

判断 Step 108 与 PR #2 的实际差异。

同时检查：

```powershell
git diff origin/main..HEAD --stat
```

不要修改任何文件。

---

# 八、Step 6：输出两种方案

只记录，不执行。

### 方案 A：Merge

```text
origin/main
      ↓
merge into current branch
      ↓
Step 108 + PR #2 merge history
```

优点：

```text
不重写 Step 108 commit
```

缺点：

```text
产生额外 merge commit
```

---

### 方案 B：Rebase

```text
origin/main
      ↓
Step 108
```

优点：

```text
线性历史
```

缺点：

```text
Step 108 commit SHA 会改变
```

GitHub 官方说明 rebase 会改写提交历史，因此已经共享/推送过的提交需要谨慎处理。([docs.github.com](https://docs.github.com/en/get-started/using-git/about-git-rebase) 

---

# 九、Step 7：本阶段不要替用户选择

不要输出：

```text
最佳方案
推荐方案
一定应该 merge
一定应该 rebase
```

只报告：

```text
Merge:
风险 / 影响

Rebase:
风险 / 影响
```

由下一步决定。

---

# 十、Step 8：Tests

由于本阶段不修改代码：

只执行：

```powershell
python -m pytest -q tests/test_github_actions_matrix_gate.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

不要：

```text
full pytest
RUN_DB_TESTS
DeepSeek
GitHub Actions
```

---

# 十一、Step 9：Git Diff Safety

确认：

```text
backend = unchanged
workflow = unchanged
Gate = unchanged
Adapter = unchanged
CLI = unchanged
Baseline = unchanged
```

工作区中的 Step 109/110 修改必须保持。

---

# 十二、最终报告

严格：

```text
【Phase 3.12 Step 111 COMPLETE】

1. Branch
- current:
- HEAD:
- origin/main:
- ahead:
- behind:

2. Common Ancestor
- merge-base:

3. Local-only commits
- ...

4. Remote-only commits
- ...

5. Uncommitted Changes
- ...

6. Merge Strategy
- effect:
- risk:

7. Rebase Strategy
- effect:
- risk:

8. Recommended Next Action
- NOT DECIDED
```

最后：

```text
SYNC STRATEGY AUDIT = COMPLETE
```

---

# 十三、硬停止

完成后：

**立即 STOP。**

不要：

```text
commit
push
merge
rebase
reset
stash
clean
PR
Step 112
```

等待下一步指令。
