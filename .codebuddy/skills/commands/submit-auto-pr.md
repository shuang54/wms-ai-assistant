---

description: 自动完成当前 Phase 的测试、Commit、Push、创建 PR、等待 CI、自动 Merge、同步 main 并清理已合并分支
argument-hint: "[PR标题（可选）]"
allowed-tools: Read, Bash(git:*), Bash(python:*), Bash(pytest:*), Bash(powershell:*), Bash(gh:*)
disable-model-invocation: true
------------------------------

# /submit-auto-pr

你现在执行：

> **当前 Phase → 自动完成 GitHub PR 全生命周期**

完整流程：

```text
检查
 ↓
测试
 ↓
Compile
 ↓
Diff / Security
 ↓
Commit
 ↓
Push
 ↓
创建 PR
 ↓
等待 Required Checks
 ↓
确认 PR 可 Merge
 ↓
自动 Merge
 ↓
同步 main
 ↓
清理已合并 branch
 ↓
STOP
```

---

# 一、绝对安全边界

允许：

```text
git status
git diff
git diff --check
git log
git add
git commit
git push
gh pr create
gh pr view
gh pr checks
gh pr merge
git checkout
git switch
git pull --ff-only
git branch -d
```

禁止：

```text
git push --force
git push --force-with-lease
git reset --hard
git clean -fd
git rebase
git checkout -- .
git restore .
git branch -D
```

禁止修改：

```text
production logic
baseline
database schema
prompt
secrets
```

---

# 二、检查当前 Branch

执行：

```powershell
git branch --show-current
git status --short
git log --oneline -5
```

要求：

```text
current branch != main
```

如果当前是：

```text
main
```

立即：

```text
SUBMIT AUTO PR BLOCKED

原因：
当前位于 main，不允许直接自动提交/合并。

STOP
```

---

# 三、检查工作区

执行：

```powershell
git status --short
git diff --check
git diff --stat
```

检查：

```text
.env
*.pem
*.key
credentials
password
api_key
token
DATABASE_URL
```

如果发现疑似 secret：

```text
SUBMIT AUTO PR BLOCKED

发现疑似 secret。

STOP
```

禁止自动删除文件。

---

# 四、检查当前 Phase

读取当前 Phase 文档和当前 Step 状态。

确认：

```text
当前 Phase 已经完成
```

如果当前 Phase 尚未完成：

```text
SUBMIT AUTO PR BLOCKED

原因：
当前 Phase 尚未达到 Release Ready。

STOP
```

---

# 五、自动测试

执行当前 Phase 明确要求的完整测试。

优先使用 Phase 文档中的命令。

如果没有明确命令：

```powershell
python -m pytest -q
```

如果 Phase 明确要求 DB regression：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

如果 DB regression 没有明确要求：

不要主动打开：

```text
RUN_DB_TESTS=1
```

测试失败：

```text
SUBMIT AUTO PR BLOCKED

Tests FAILED.

STOP
```

---

# 六、Compile

执行：

```powershell
python -m compileall -q backend tests scripts
```

如果 scripts 不存在：

```powershell
python -m compileall -q backend tests
```

失败：

```text
SUBMIT AUTO PR BLOCKED

Compile FAILED.

STOP
```

---

# 七、Matrix Gate

如果项目存在 Regression Matrix：

执行当前 Phase 要求的 Matrix Gate。

必须确认：

```text
0 failed
0 unexpected errors
baseline 未被擅自修改
```

如果 Matrix Gate 失败：

```text
SUBMIT AUTO PR BLOCKED

Matrix Gate FAILED.

STOP
```

---

# 八、检查 Diff

执行：

```powershell
git diff main...HEAD --stat
git diff main...HEAD
git diff --check
```

确认：

```text
没有：
- secrets
- 临时文件
- 无关修改
- DB migration
- 生产数据
- 未授权依赖
- baseline 修改
```

如果发现范围外修改：

```text
SUBMIT AUTO PR BLOCKED

发现范围外修改：

...

STOP
```

不要自动删除或恢复。

---

# 九、自动 Commit

如果：

```text
working tree clean
```

说明当前所有修改已经 Commit。

继续。

如果存在未提交修改：

只添加属于当前 Phase 的文件。

禁止无条件：

```powershell
git add .
```

执行：

```powershell
git add <explicit-files>
```

生成 Commit。

如果用户提供：

```text
$ARGUMENTS
```

使用其作为 PR 标题参考。

否则：

```text
feat: Phase X.Y 完成
```

Commit：

```powershell
git commit -m "<commit message>"
```

然后：

```powershell
git status --short
git log -1 --oneline
```

Commit 失败：

```text
SUBMIT AUTO PR BLOCKED

Commit FAILED.

STOP
```

---

# 十、Push

确认：

```text
current branch != main
working tree clean
tests passed
compile passed
matrix passed
```

执行：

```powershell
git push -u origin <current-branch>
```

如果 remote branch 已存在：

```powershell
git push
```

禁止：

```text
--force
--force-with-lease
```

Push 失败：

```text
SUBMIT AUTO PR BLOCKED

Push FAILED.

STOP
```

---

# 十一、检查 GitHub CLI

执行：

```powershell
gh --version
```

如果 `gh` 不存在：

```text
SUBMIT AUTO PR BLOCKED

GitHub CLI 不可用。

已经完成：
- Tests
- Compile
- Commit
- Push

但无法自动创建 PR。

STOP
```

不要安装 gh。

---

# 十二、检查是否已经存在 PR

执行：

```powershell
gh pr list --head <current-branch> --base main --state open
```

如果已经存在 PR：

直接记录：

```text
PR_NUMBER
PR_URL
```

不要创建重复 PR。

---

# 十三、创建 PR

如果不存在 PR：

执行：

```powershell
gh pr create --base main --head <current-branch> --title "<PR title>" --body "<PR body>"
```

PR Body：

```text
## Summary

完成当前 Phase 的实现。

## Changes

根据当前 Phase 实际修改自动生成。

## Tests

- pytest: PASS
- compileall: PASS
- Matrix: PASS

## Safety

- Secrets: PASS
- DB migration: 0
- Production DB writes: 0
- Unexpected network/LLM calls: 0

## Scope

本 PR 只包含当前 Phase。
```

创建失败：

```text
SUBMIT AUTO PR BLOCKED

PR creation FAILED.

STOP
```

---

# 十四、等待 Required Checks

获取：

```powershell
gh pr checks <PR_NUMBER>
```

如果存在 pending：

等待后再次检查。

可以进行有限次数轮询。

建议：

```text
最多 30 次
每次间隔 10 秒
```

如果超过限制仍然 pending：

```text
SUBMIT AUTO PR BLOCKED

Required Checks 仍然 pending。

PR:
<URL>

STOP
```

---

# 十五、Required Checks

只有以下全部满足才允许 Merge：

```text
所有 Required Checks = PASS
```

如果任意：

```text
FAIL
ERROR
CANCELLED
```

立即：

```text
SUBMIT AUTO PR BLOCKED

Required Checks FAILED.

PR:
<URL>

STOP
```

禁止：

```text
merge anyway
```

---

# 十六、检查 PR Mergeability

执行：

```powershell
gh pr view <PR_NUMBER> --json state,mergeable,mergeStateStatus,url
```

要求：

```text
state = OPEN
mergeable = MERGEABLE
```

如果：

```text
CONFLICTING
UNKNOWN
```

立即：

```text
SUBMIT AUTO PR BLOCKED

PR is not safely mergeable.

STOP
```

---

# 十七、自动 Merge

只有满足：

```text
Tests PASS
Compile PASS
Matrix PASS
Required Checks PASS
PR OPEN
PR MERGEABLE
```

才执行：

```powershell
gh pr merge <PR_NUMBER> --squash --delete-branch
```

使用：

```text
squash merge
```

保持 main 历史整洁。

禁止：

```text
--admin
```

禁止绕过 Required Checks。

禁止 force merge。

---

# 十八、确认 Merge

执行：

```powershell
gh pr view <PR_NUMBER> --json state,mergedAt,url
```

必须：

```text
state = MERGED
mergedAt != null
```

否则：

```text
SUBMIT AUTO PR BLOCKED

Merge status could not be confirmed.

STOP
```

---

# 十九、同步 main

Merge 成功后：

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

如果：

```text
git pull --ff-only
```

失败：

```text
MERGE COMPLETE

但是 main 同步失败。

STOP
```

不要 reset。

不要 force。

---

# 二十、清理本地 Branch

如果远程 branch 已经因为：

```text
--delete-branch
```

删除：

尝试：

```powershell
git branch -d <merged-branch>
```

如果已经不存在：

忽略。

禁止：

```powershell
git branch -D
```

---

# 二十一、最终报告

成功后严格输出：

```text
========================================
SUBMIT AUTO PR COMPLETE
========================================

Phase:
...

Branch:
...

Commit:
...

Tests:
PASS

Compile:
PASS

Matrix:
PASS

Push:
PASS

PR:
#<number>

PR URL:
...

Required Checks:
PASS

Merge:
PASS

Merge Method:
SQUASH

main:
SYNCED

working tree:
CLEAN

Remote branch:
DELETED

DB:
0 writes

Network:
0 unexpected calls

LLM:
0 unexpected calls

Secrets:
PASS

========================================
STOP
========================================
```

---

# 二十二、失败处理原则

任何步骤失败：

```text
停止
```

不要：

```text
自动修代码
自动修改测试
自动修改 baseline
自动 reset
自动 clean
自动 force push
自动重试危险 Git 操作
自动 Merge
```

允许重复检查：

```text
git status
git diff
gh pr checks
gh pr view
```

但不允许绕过失败。

---

# 二十三、核心原则

本命令代表：

```text
/submit-auto-pr
```

一次完成：

```text
Phase
 ↓
Test
 ↓
Compile
 ↓
Matrix
 ↓
Commit
 ↓
Push
 ↓
PR
 ↓
Required Checks
 ↓
Merge
 ↓
main sync
 ↓
branch cleanup
 ↓
STOP
```

**这是唯一允许自动 Merge 的命令。**

普通：

```text
/submit-pr
```

仍然：

```text
Commit
 ↓
Push
 ↓
PR
 ↓
STOP
```

不会自动 Merge。
