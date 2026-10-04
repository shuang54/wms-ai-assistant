---

name: phase-git-governance
description: >
wms-ai-assistant 项目的 Phase/Step 增量开发、Git 分支、
测试、Commit、Push、PR、Merge 和 main 同步治理。
用于用户要求开始 Phase、继续 Step、验收 Step、提交代码、
发布 Phase 或准备 PR 时。严格执行小步开发、范围控制、
测试优先和 STOP 规则。
allowed-tools: Read, Write, Edit, Grep, Bash(git:*), Bash(python:*), Bash(pytest:*), Bash(powershell:*)
-------------------------------------------------------------------------------------------------------

# Phase Git Governance

你正在维护：

```text
D:\coding\ai\wms-ai-assistant
```

这是一个长期演进的 AI/WMS 项目。

必须遵循：

```text
main
  ↓
Phase branch
  ↓
Step
  ↓
Implementation
  ↓
Targeted Tests
  ↓
Diff Review
  ↓
Commit
  ↓
Next Step
  ↓
Phase Regression
  ↓
Push
  ↓
PR
  ↓
Required Checks
  ↓
Manual Merge Confirmation
  ↓
main sync
  ↓
STOP
```

---

# 1. 核心原则

## 1.1 一次只做一个 Step

用户说：

```text
继续 Step N
```

只执行 Step N。

禁止：

```text
自动进入 Step N+1
自动进入下一 Phase
自动扩展需求
```

完成后：

```text
STOP
```

---

## 1.2 不擅自扩大范围

如果当前 Step 明确：

```text
允许修改 A/B/C
```

只允许修改：

```text
A/B/C
```

如果发现必须修改范围外文件：

```text
停止
说明原因
等待用户确认
```

---

# 2. Phase 开始

用户开始一个新的 Phase 时：

先执行：

```powershell
git checkout main
git pull --ff-only
git status --short
```

要求：

```text
main == origin/main
working tree clean
```

如果不是 clean：

```text
STOP
```

不要覆盖用户工作。

然后创建：

```powershell
git switch -c phase-X.Y
```

例如：

```text
phase-4.2
phase-3.12
```

如果分支已经存在：

```text
不要删除
不要强制覆盖
先检查分支状态
```

---

# 3. Step 开发

每个 Step 必须：

```text
阅读现有实现
→ 最小修改
→ targeted tests
→ diff
→ scope audit
```

测试优先使用项目已有命令。

不要为了通过测试修改生产逻辑。

---

# 4. Step 验收

如果用户说：

```text
Step N 完成
```

或者执行：

```text
/step-done
```

执行：

```powershell
git status --short
git diff --check
git diff --stat
git diff
```

然后执行当前 Step 指定的测试。

如果 Step 没有指定测试：

根据修改范围选择最小合理测试。

同时检查：

```text
secrets
范围外修改
DB migration
生产数据写入
真实 LLM
真实网络
```

---

# 5. 自动 Commit

Step 验收全部通过后：

可以自动创建 Commit。

但是必须：

```text
只 add 当前 Step 修改文件
```

禁止：

```powershell
git add .
```

除非明确确认工作区所有未提交修改都属于当前 Step。

推荐：

```powershell
git add <explicit-files>
```

Commit message：

```text
feat: Phase X.Y Step N：<简短描述>
```

测试/文档类可以使用：

```text
test: Phase X.Y Step N：<描述>
docs: Phase X.Y Step N：<描述>
chore: Phase X.Y Step N：<描述>
```

Commit 后：

```powershell
git status --short
git log -1 --oneline
```

必须确认工作区状态。

然后：

```text
STOP
```

---

# 6. 禁止自动 Push

即使 Commit 成功：

**不要自动 push。**

等待用户明确说：

```text
Push
```

或者：

```text
推送
```

才允许：

```powershell
git push -u origin <current-branch>
```

Push 前重新：

```powershell
git status --short
git log -1 --oneline
```

---

# 7. 禁止自动 Merge

禁止自动：

```text
merge
squash
rebase main
delete main
force push
```

必须等待用户明确确认。

---

# 8. Phase Release

当用户说：

```text
Phase 完成
```

或者：

```text
/phase-release
```

执行完整验收：

```text
Full pytest
compileall
项目既有 lint
Matrix Gate
DB regression（如果项目要求）
```

必须确认：

```text
0 failed
0 errors
0 unexpected diagnostics
DB residue = 0
```

并检查：

```powershell
git status --short
git diff --check
git diff main...HEAD --stat
git diff main...HEAD
```

同时确认：

```text
没有 secrets
没有无关文件
没有 Prompt 越权修改
没有 DB migration
没有生产数据写入
没有未授权依赖
```

输出：

```text
PHASE RELEASE READY
```

然后：

```text
STOP
```

---

# 9. Push

只有用户明确要求：

```text
Push
```

才执行。

Push 后：

```powershell
git status --short
git log -1 --oneline
```

输出：

```text
PUSH COMPLETE
```

不要自动创建 PR，除非用户要求。

---

# 10. PR

用户要求：

```text
创建 PR
```

时：

先确认：

```text
branch
base=main
working tree
commit history
tests
```

然后生成 PR：

```text
Title
Summary
Changes
Tests
Risk
Scope
```

PR 不应包含：

```text
secrets
API keys
credentials
temporary files
```

创建 PR 后：

```text
等待 Required Checks
```

可以检查 CI。

但：

**不要自动 Merge。**

---

# 11. Merge

只有用户明确说：

```text
合并
Merge
确认合并
```

才允许执行 Merge。

Merge 前：

```text
Required Checks = PASS
```

如果检查失败：

```text
STOP
```

不要绕过 CI。

---

# 12. Merge 后同步 main

Merge 完成后：

```powershell
git checkout main
git pull --ff-only
git status --short
git log --oneline -5
```

要求：

```text
HEAD == origin/main
working tree clean
```

不要自动进入下一 Phase。

---

# 13. Branch Cleanup

旧 Phase branch 删除属于破坏性操作。

默认：

```text
不自动删除
```

只有用户明确要求删除：

```text
delete branch
```

才执行。

禁止：

```powershell
git branch -D
```

除非用户明确确认强制删除。

优先：

```powershell
git branch -d <branch>
git push origin --delete <branch>
```

---

# 14. 禁止危险 Git 操作

没有用户明确确认，禁止：

```text
git reset --hard
git clean -fd
git push --force
git push --force-with-lease
git branch -D
git rebase
git checkout -- .
git restore .
```

如果发现需要这些操作：

```text
STOP
说明风险
等待用户确认
```

---

# 15. Git Diff 安全检查

每次 Commit / Release 前检查：

```powershell
git diff --check
```

同时检查：

```text
.env
*.key
*.pem
credentials
password
token
api_key
DATABASE_URL
```

如果发现疑似 secret：

```text
STOP
不要 commit
```

---

# 16. Matrix / Baseline

项目已有：

```text
Regression Matrix
Baseline
Contract tests
```

不得擅自修改 baseline 让测试通过。

如果出现：

```text
baseline drift
```

必须：

```text
报告
定位
STOP
```

除非当前 Phase 明确要求更新 baseline。

---

# 17. DB / Network / LLM

默认原则：

```text
单元测试
    ↓
Fake
```

不要因为方便而调用：

```text
DeepSeek
SiliconFlow
PostgreSQL
生产数据库
外部 HTTP
```

如果 Step 明确要求真实 DB：

按照项目现有：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest ...
```

执行。

如果 Step 明确要求真实 LLM：

必须明确标记为：

```text
real_llm
```

并且不要让普通：

```powershell
python -m pytest -q
```

调用真实 LLM。

---

# 18. STOP 规则

以下情况必须 STOP：

```text
Step 完成
Step 测试通过
Step Commit 完成

Phase 完成
Phase Release Ready

Push 完成

PR 创建完成

Merge 完成
main 同步完成
```

禁止自动连续执行整个生命周期。

---

# 19. 标准输出

Step：

```text
Phase X.Y Step N COMPLETE

Files:
...

Tests:
...

Diff:
...

Commit:
...

Production Code:
...

DB:
...

Network:
...

LLM:
...

Next:
WAITING FOR USER

STOP
```

Phase：

```text
Phase X.Y RELEASE READY

Tests:
...

Matrix:
...

DB:
...

Compile:
...

Git:
...

PR:
READY

STOP
```

Merge：

```text
MERGE COMPLETE

main:
...

origin/main:
...

working tree:
clean

STOP
```

---

# 20. 最重要的规则

永远遵循：

```text
小步
→ 测试
→ Diff
→ Commit
→ STOP

Phase 完成
→ Full Regression
→ Matrix
→ Release Ready
→ STOP

用户确认 Push
→ Push
→ STOP

用户确认 Merge
→ Merge
→ main pull
→ STOP
```

不要自动跨 Step。

不要自动跨 Phase。

不要自动 Merge。

不要隐藏失败。

不要为了测试通过修改 baseline。

不要覆盖用户未提交代码。
