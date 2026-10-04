---

description: 一键完成当前 Phase 的测试、Commit、Push，并创建 GitHub PR；不自动 Merge
argument-hint: "[PR标题（可选）]"
allowed-tools: Read, Bash(git:*), Bash(python:*), Bash(pytest:*), Bash(powershell:*), Bash(gh:*)
disable-model-invocation: true
------------------------------

# /submit-pr

你现在执行：

> **当前 Phase → 一键提交 GitHub PR**

这是一个有副作用的操作，因此只有用户手动执行 `/submit-pr` 时才能运行。

---

# 一、核心目标

将当前已经完成的 Phase 自动完成：

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
STOP
```

**禁止自动 Merge。**

---

# 二、第一步：检查 Git 状态

执行：

```powershell
git branch --show-current
git status --short
git log --oneline -5
```

要求：

```text
当前 branch != main
```

如果当前是：

```text
main
```

立即：

```text
SUBMIT PR BLOCKED
原因：当前位于 main，禁止直接提交 PR。
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

如果存在明显异常：

```text
secrets
.env
API key
password
credentials
database URL
临时文件
```

立即：

```text
SUBMIT PR BLOCKED
STOP
```

---

# 四、判断当前修改范围

确定：

```text
BASE = main
HEAD = 当前 branch
```

检查：

```powershell
git diff main...HEAD --stat
git diff main...HEAD
```

不要修改：

```text
main
```

不要执行：

```text
git reset --hard
git clean -fd
git push --force
git rebase
```

---

# 五、自动测试

优先读取当前 Phase / Step 中已经定义的测试命令。

如果当前 Phase 明确指定测试：

严格使用当前 Phase 测试。

如果没有明确指定：

至少执行：

```powershell
python -m pytest -q
```

如果项目当前存在 DB-gated 测试：

不要自动打开：

```text
RUN_DB_TESTS=1
```

除非当前 Phase 明确要求 DB regression。

如果 Phase 明确要求：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

---

# 六、Compile

执行：

```powershell
python -m compileall -q backend tests scripts
```

如果不存在 `scripts/`：

使用：

```powershell
python -m compileall -q backend tests
```

Compile 失败：

```text
SUBMIT PR BLOCKED
STOP
```

---

# 七、Diff 安全检查

执行：

```powershell
git diff --check
git diff main...HEAD --stat
```

检查：

```text
无 secrets
无 .env
无 credentials
无无关临时文件
无数据库 migration
无生产数据
```

如果发现：

```text
范围外修改
```

不要自动删除。

输出：

```text
SUBMIT PR BLOCKED

发现范围外修改：
...

请人工确认。
STOP
```

---

# 八、自动 Commit

如果：

```text
working tree clean
```

说明所有修改已经 commit。

继续。

如果存在未提交修改：

先检查修改是否全部属于当前 Phase。

如果确认属于当前 Phase：

执行：

```powershell
git add <明确列出的当前 Phase 文件>
```

**禁止默认使用：**

```powershell
git add .
```

然后生成 Commit。

Commit message：

如果用户通过 `$ARGUMENTS` 提供 PR 标题：

可以根据标题生成 commit message。

否则使用：

```text
feat: Phase X.Y 完成
```

或者根据当前实际 Phase：

```text
feat: Phase 3.12 Step XX 完成
```

执行：

```powershell
git commit -m "<message>"
```

然后：

```powershell
git status --short
git log -1 --oneline
```

Commit 失败：

```text
SUBMIT PR BLOCKED
STOP
```

---

# 九、Push

确认：

```text
current branch != main
working tree clean
tests passed
compileall passed
```

执行：

```powershell
git push -u origin <current-branch>
```

如果 remote branch 已存在：

使用普通：

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
SUBMIT PR BLOCKED
原因：Push failed
STOP
```

---

# 十、创建 Pull Request

Push 成功后：

优先检查：

```powershell
gh --version
```

如果 GitHub CLI 可用：

执行：

```powershell
gh pr create
```

Base：

```text
main
```

Head：

```text
当前 branch
```

PR Title：

优先使用：

```text
$ARGUMENTS
```

如果没有：

根据当前 Phase 自动生成。

PR Body 必须包含：

```text
## Summary

本 Phase 完成的主要内容。

## Changes

- 修改内容
- 新增内容
- 测试内容

## Tests

- pytest
- compileall
- DB tests（如果执行）

## Safety

- DB writes
- Network
- LLM
- Secrets
- Migration

## Scope

本 PR 未进入下一 Phase。
```

不要：

```text
Merge
```

---

# 十一、如果 gh 不可用

不要安装 GitHub CLI。

不要修改系统环境。

不要失败。

输出：

```text
PUSH COMPLETE

Branch:
...

Remote:
...

GitHub CLI:
unavailable

请打开 GitHub：
从当前 branch 创建 Pull Request → main。

STOP
```

---

# 十二、PR 创建成功

输出：

```text
================================
SUBMIT PR COMPLETE
================================

Branch:
...

Commit:
...

Push:
PASS

PR:
<PR URL>

Tests:
PASS

Compile:
PASS

Secrets:
PASS

DB:
...

Network:
...

LLM:
...

Merge:
NOT PERFORMED

STOP
```

---

# 十三、禁止事项

本命令绝对禁止：

```text
git reset --hard
git clean -fd
git push --force
git push --force-with-lease
git rebase
git checkout -- .
git restore .
git branch -D
git merge
```

也禁止：

```text
自动修改测试
自动修改 baseline
自动修改生产逻辑
自动删除异常文件
自动解决冲突
自动 Merge PR
```

---

# 十四、PR Merge

本命令执行到：

```text
PR CREATED
```

立即停止。

即使 GitHub Required Checks 已经通过：

**也不要自动 Merge。**

Merge 必须由用户明确执行。

---

# 十五、最终原则

用户以后只需要：

```text
/submit-pr
```

完成：

```text
Test
 ↓
Compile
 ↓
Diff
 ↓
Commit
 ↓
Push
 ↓
PR
 ↓
STOP
```

**不要自动 Merge。**
