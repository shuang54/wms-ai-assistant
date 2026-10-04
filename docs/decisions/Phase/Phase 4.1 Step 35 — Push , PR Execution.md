继续 Phase 4.1。

当前状态：

```text
Branch:
phase4

HEAD:
b6ba250 feat:Phase 4.1 Step 33：Real Annotation Execution Release Readiness Audit

Step 32:
1cf524d feat:Phase 4.1 Step 32：Real Annotation Execution Contract

Working tree:
clean
```

现在执行：

# Phase 4.1 Step 35 — Push / PR Execution

## 一、目标

将当前：

```text
phase4
```

分支推送到远程，并创建/确认对应 PR。

本步骤：

```text
commit = 已完成
push   = 执行
PR     = 执行
merge  = 不执行
```

---

# 二、开始前检查

执行：

```powershell
git status --short
git branch --show-current
git log --oneline -5
git diff --stat
```

必须确认：

```text
branch = phase4
working tree = clean
```

如果工作区出现任何未提交修改：

**立即 STOP。**

不要自动 commit。

---

# 三、检查远程

执行：

```powershell
git remote -v
git fetch origin
```

确认：

```text
origin
```

存在。

然后：

```powershell
git rev-parse HEAD
git rev-parse origin/main
```

确认当前分支基于最新 main。

如果无法确认：

**STOP 并报告。**

不要 rebase。

---

# 四、Push

确认无问题后执行：

```powershell
git push -u origin phase4
```

禁止：

```text
git push --force
git push --force-with-lease
```

Push 成功后：

```powershell
git status --short
git branch -vv
```

确认：

```text
phase4 → origin/phase4
```

---

# 五、PR

优先使用 GitHub CLI：

```powershell
gh auth status
```

如果 GitHub CLI 未登录：

```text
STOP
```

不要要求修改 token。

---

# 六、检查已有 PR

执行：

```powershell
gh pr list --head phase4 --state open
```

如果已有 PR：

**不要重复创建。**

记录：

```text
PR number
PR title
PR URL
```

如果没有：

创建：

```powershell
gh pr create `
  --base main `
  --head phase4 `
  --title "feat: Phase 4.1 Real Annotation Contract" `
  --body "Phase 4.1 Step 31/32 Real Evidence Import and Real Annotation Execution Contract.`n`nStep 32: 39 passed; full regression 5896 passed / 633 skipped / 0 failed.`n`nProduction Code = 0.`nDB / Network / LLM = 0.`nG3 = BLOCKED.`nG4 = BLOCKED."
```

如果仓库已有 PR 模板：

**遵循 PR 模板。**

不要覆盖模板要求。

---

# 七、PR 内容检查

创建后执行：

```powershell
gh pr view <PR_NUMBER>
```

以及：

```powershell
gh pr diff <PR_NUMBER> --stat
```

确认 PR 只包含：

```text
Step 31 / Step 32 相关测试、fixture、evaluation、decision artifacts
```

禁止出现：

```text
backend production changes
DB migration
Prompt changes
LLM changes
RAG changes
Tool changes
Router changes
```

---

# 八、Required Checks

检查：

```powershell
gh pr checks <PR_NUMBER>
```

如果 checks 尚未完成：

**等待，不要 merge。**

可以轮询：

```text
最多 30 次
每次间隔 10 秒
```

最终要求：

```text
all required checks = PASS
```

如果出现：

```text
FAIL
```

不要修改测试来“修 CI”。

停止并报告失败 check。

---

# 九、Merge 禁止

本步骤明确：

```text
PR = OPEN
Merge = NOT EXECUTED
```

禁止：

```powershell
gh pr merge
```

禁止：

```text
squash
rebase
merge commit
delete branch
```

等待人工确认。

---

# 十、不要修改 Phase 4.1 Contract

Push / PR 阶段不得修改：

```text
Step 28
Step 29
Step 30
Step 31
Step 32
G1
G2
G3
G4
```

特别：

```text
G3 = BLOCKED
G4 = BLOCKED
```

保持不变。

---

# 十一、最终报告

严格输出：

```text
Phase 4.1 Step 35 Push / PR 完成报告

1. Branch
2. HEAD
3. Remote Branch
4. Push
5. PR Number
6. PR URL
7. PR Base
8. PR Head
9. PR Diff Scope
10. Required Checks
11. Check Result
12. Merge
    NOT EXECUTED
13. Production Code
14. DB
15. Network
16. LLM
17. G3
18. G4
19. Current Limitations

Push:
SUCCESS / FAILED

PR:
CREATED / EXISTING / FAILED

Merge:
NOT EXECUTED

Phase 4.1 Step 35 STOP
```

完成后立即停止。

**不要 merge。**
**不要删除 `phase4` 分支。**
**不要进入 Step 36。**
