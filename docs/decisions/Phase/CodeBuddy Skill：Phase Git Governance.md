# CodeBuddy Skill：Phase Git Governance

请为当前项目：

```text
D:\coding\ai\wms-ai-assistant
```

创建一个**项目级 CodeBuddy Skill**，用于统一管理：

```text
Phase / Step 开发
Git Branch
Commit
Push
Pull Request
Regression
Matrix Gate
Merge
Main 同步
```

Skill 名称：

```text
phase-git-governance
```

创建：

```text
.codebuddy/skills/phase-git-governance/SKILL.md
```

CodeBuddy 项目级 Skill 使用：

```text
.codebuddy/skills/<skill-name>/SKILL.md
```

不要创建用户级 Skill。

---

# 一、Skill Frontmatter

`SKILL.md` 必须使用：

```yaml
---
name: phase-git-governance
description: >
  用于 wms-ai-assistant 项目的 Phase/Step 增量开发、
  Git 分支管理、测试验证、Commit、Push、Pull Request、
  Merge Readiness 和 main 同步。用户要求继续 Phase、
  实现 Step、检查提交、准备 PR 或完成合并时使用。
  严格执行小步开发、测试、边界控制和 STOP 规则。
---
```

本 Skill 默认允许 AI 自动根据任务上下文使用。

但是：

**涉及以下副作用操作时，不得自动执行，必须等待用户明确确认：**

```text
git push
创建 PR
merge PR
删除 branch
git reset --hard
git clean
force push
```

---

# 二、核心工作原则

本项目采用：

```text
Phase
  ↓
Step
  ↓
小范围实现
  ↓
测试
  ↓
Review
  ↓
Commit
  ↓
下一个 Step
  ↓
Phase 完成
  ↓
Full Regression
  ↓
Matrix Gate
  ↓
Push
  ↓
PR
  ↓
Required Checks
  ↓
Merge
  ↓
main 同步
```

必须遵守：

> **一次只完成一个 Step。**

禁止自动：

```text
跨 Step
跨 Phase
顺手重构
顺手优化架构
自动进入下一阶段
自动 Merge
自动删除分支
```

每个 Step 完成后：

```text
STOP
```

等待用户下一步指令。

---

# 三、开始任何 Phase 前

首先检查：

```powershell
git branch --show-current
git status --short
git log --oneline -5
```

如果当前不是 `main`：

根据用户当前任务判断是否应该切回 main。

开始新 Phase 时必须：

```powershell
git checkout main
git pull
git status --short
```

要求：

```text
main 与 origin/main 同步
working tree clean
```

---

# 四、创建 Phase 分支

新 Phase 不允许继续使用旧 Phase branch。

例如：

```powershell
git switch -c phase-4.1
```

或者：

```powershell
git switch -c phase-3.12-step-XXX
```

推荐：

```text
一个 Phase 一个长期开发分支。
```

例如：

```text
main
  ↓
phase-4.1
```

Step 在该 Phase branch 中产生多个 commit。

---

# 五、Step 开发规则

开始 Step 前：

必须先阅读真实代码。

禁止：

```text
假设接口
假设 DTO
假设测试
假设目录结构
```

必须：

```text
阅读
→ 定位
→ 最小设计
→ 实现
→ 测试
→ diff
→ STOP
```

---

# 六、修改范围

每个 Step 都必须有明确：

```text
Allowed files
Forbidden files
```

只修改允许范围。

如果发现范围外 Bug：

不要顺手修复。

报告：

```text
发现问题：
影响：
根因：
是否属于当前 Step：
建议后续处理：
```

等待用户决定。

---

# 七、禁止为了通过测试而降低测试强度

禁止：

```text
删除测试
pytest.skip
xfail
修改 baseline
降低断言
return True 绕过
try/except 吞异常
```

如果测试和当前架构冲突：

先判断：

```text
REAL_IMPLEMENTATION_PROBLEM
STALE_TEST
TEST_ASSUMPTION_MISMATCH
```

不要直接修改 production code。

---

# 八、Step 完成后的测试

根据 Step 指定测试运行。

至少检查：

```powershell
python -m pytest -q <target-tests>
```

必要时：

```powershell
python -m compileall -q backend tests scripts
```

如果 Step 要求 DB：

PowerShell 使用：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

不要使用 Unix：

```text
RUN_DB_TESTS=1 pytest
```

---

# 九、Step 完成后的 Git 检查

执行：

```powershell
git status --short
git diff --stat
git diff
```

确认：

```text
修改文件符合 Step 范围
没有 secrets
没有 DATABASE_URL
没有 API Key
没有无关文件
没有生产代码越界修改
```

---

# 十、Step Commit

只有：

```text
代码完成
测试通过
diff 审核完成
```

才允许 commit。

推荐：

```powershell
git add <specific-files>
git commit -m "feat: Phase X.Y Step N：描述"
```

不要默认：

```text
git add .
```

优先明确指定文件。

Commit message：

```text
feat: Phase 4.1 Step 31：Real Annotation Execution Contract
```

测试：

```text
test: Phase 4.1 Step 31：...
```

文档：

```text
docs: Phase 4.1 Step 31：...
```

---

# 十一、每个 Step 一个 Commit

推荐：

```text
Phase 4.1
 ├── Step 31 commit
 ├── Step 32 commit
 ├── Step 33 commit
 └── Step 34 commit
```

不要把整个 Phase 压成一个无法追踪的巨大 commit。

也不要为了每个小文件创建独立 commit。

原则：

> 一个有明确边界的 Step 对应一个 commit。

---

# 十二、Phase 完成前

Phase 的所有 Step 完成后，不要立即 Push。

先执行：

```powershell
python -m pytest -q
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

如果项目存在 Matrix：

```powershell
python scripts/run_matrix_gate.py
```

必须确认：

```text
pytest failed = 0
compileall errors = 0
Matrix = PASS
DB residue = 0
```

如果有 DB regression：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

---

# 十三、Phase Merge Readiness

执行：

```powershell
git status --short
git diff main...HEAD --stat
git diff main...HEAD -- backend
git diff main...HEAD -- .github
git log --oneline main..HEAD
```

确认：

```text
backend 修改符合 Phase 范围
.github 修改符合 Phase 范围
baseline 没有被偷偷修改
没有 secrets
没有无关文件
```

---

# 十四、一个 Phase 一个 PR

Phase 完成后：

```text
phase-X.Y
    ↓
PR
    ↓
main
```

不要：

```text
Step 31 → PR
Step 32 → PR
Step 33 → PR
```

除非用户明确要求。

推荐：

```text
一个 Phase 一个开发分支
一个 Phase 一个 PR
多个 Step commits
```

---

# 十五、Push

Push 是副作用操作。

必须等待用户明确确认。

确认后：

```powershell
git push -u origin phase-X.Y
```

第一次 push 使用：

```text
-u origin <branch>
```

后续：

```powershell
git push
```

---

# 十六、Pull Request

PR 创建前必须确认：

```text
Full pytest = 0 failed
Matrix Gate = PASS
compileall = PASS
working tree = clean
```

PR 标题：

```text
Phase X.Y：主题
```

PR body 至少包含：

```text
## Scope

## Completed Steps

## Tests

## Matrix Gate

## Database

## Security

## Production Code Changes

## Limitations
```

不要在 PR 中输出：

```text
API Key
password
DATABASE_URL
credentials
```

---

# 十七、PR Required Checks

PR 创建后：

不要因为本地测试通过就立即 Merge。

等待 GitHub Required Checks。

必须：

```text
Required Checks = ALL GREEN
```

如果失败：

先分类：

```text
Code failure
Test failure
CI configuration failure
Environment failure
Stale test
```

不要直接修改 baseline。

---

# 十八、Merge 前最终 Gate

只有同时满足：

```text
Full pytest failed = 0
Full pytest errors = 0
Matrix Gate = PASS
compileall = OK
Required Checks = GREEN
backend diff = expected
.github diff = expected
DB migration = expected
secrets = 0
```

才可以报告：

```text
MERGE READY
```

---

# 十九、Merge

Merge 是副作用操作。

必须等待用户明确确认。

默认推荐：

```text
普通 Merge Pull Request
```

不要自动：

```text
Squash
Rebase
Amend
Force push
```

除非用户明确要求。

---

# 二十、Merge 后同步 main

Merge 完成后：

```powershell
git checkout main
git pull
git status --short
git log --oneline -5
```

确认：

```text
HEAD == origin/main
working tree clean
```

---

# 二十一、删除旧 Phase Branch

删除 branch 是副作用操作。

必须等待用户确认。

本地：

```powershell
git branch -d phase-X.Y
```

远程：

```powershell
git push origin --delete phase-X.Y
```

也可以直接在 GitHub PR 页面使用：

```text
Delete branch
```

---

# 二十二、不要复用已合并 Phase Branch

例如：

```text
phase-4.1
```

已经 Merge：

不要继续：

```text
git checkout phase-4.1
```

然后继续开发。

必须：

```text
main
 ↓
git pull
 ↓
新 Phase branch
```

例如：

```text
phase-4.2
```

---

# 二十三、工作区隔离

如果发现当前 working tree 存在：

```text
Step N+1 未提交修改
```

不要：

```text
git checkout main
```

强行切换。

先报告：

```text
Working tree contains uncommitted changes.
```

并列出：

```text
git status --short
```

等待用户决定。

禁止：

```text
git reset --hard
git clean -fd
```

除非用户明确要求。

---

# 二十四、保护 Matrix Baseline

如果项目存在：

```text
MATRIX_EXECUTION_BASELINE
```

或者类似冻结 baseline：

禁止为了通过 Gate 修改：

```text
expected values
baseline
snapshot
```

如果 current != baseline：

先报告 Drift。

不要自动更新 baseline。

---

# 二十五、保护 CI

禁止为了通过 PR：

```text
删除 CI job
修改 required check
降低 test scope
修改 matrix baseline
skip tests
```

CI failure 必须先诊断。

---

# 二十六、数据库安全

默认：

```text
DB writes = 0
```

除非当前 Step 明确要求 migration / fixture。

任何 DB fixture 必须：

```text
可回滚
可清理
```

最终：

```text
DB residue = 0
```

---

# 二十七、LLM / Network 安全

默认测试：

```text
LLM = 0
Network = 0
```

除非当前 Step 明确要求真实 LLM smoke。

真实 LLM：

```text
必须显式标记
必须单独运行
不得进入默认 pytest
```

不要把 API Key 写入：

```text
代码
测试
文档
commit
PR
```

---

# 二十八、STOP 规则

这是本 Skill 最重要的规则。

### Step 完成：

```text
STOP
```

### Phase 完成：

```text
STOP
```

### Full regression 完成：

```text
STOP
```

### PR 创建完成：

```text
STOP
```

### Merge 完成：

```text
STOP
```

不得自动进入下一 Phase。

不得自动创建下一 branch。

不得自动创建下一 PR。

不得自动 Merge。

---

# 二十九、Step 完成报告

每个 Step 完成后使用：

```text
Phase X.Y Step N COMPLETE

1. 修改文件
2. 新增文件
3. Tests
4. Compile
5. DB / Network / LLM
6. Git Diff
7. 当前限制

Phase X.Y Step N READY
Phase X.Y Step N STOP
```

---

# 三十、Phase 完成报告

使用：

```text
Phase X.Y COMPLETE

1. Steps
2. Production Changes
3. Tests
4. Full Regression
5. Matrix Gate
6. DB
7. Network / LLM
8. Git Diff
9. Commits
10. PR Readiness
11. Limitations

Phase X.Y READY FOR PR
Phase X.Y STOP
```

---

# 三十一、PR Merge 后报告

使用：

```text
Phase X.Y MERGED

1. PR
2. Merge Commit
3. main HEAD
4. origin/main
5. Working Tree
6. Tests
7. Matrix
8. DB Residue
9. Old Branch

Phase X.Y CLOSED
STOP
```

---

# 三十二、当前项目特殊规则

本项目已经建立：

```text
AI Core
RAG
Tool
Text-to-SQL
LLM Observability
LLM Usage
Assistant Outcome
Conversation
Context Builder
Evidence / Annotation Contracts
Observability Matrix
CI Governance
```

因此：

**不要重复创建同类基础设施。**

开始任何新 Step 前必须搜索：

```text
已有 Service
已有 DTO
已有 Contract
已有 Test
已有 Fixture
已有 Baseline
已有 Architecture Decision
```

优先复用。

---

# 三十三、遇到不确定情况

如果不确定：

```text
接口
架构边界
测试范围
baseline
Git 状态
```

不要猜。

先：

```text
Read
Search
Inspect
```

仍然不确定：

```text
STOP
Ask user
```

---

# 三十四、最终原则

整个项目遵循：

```text
小步
明确边界
先测试
再提交
Phase 一个分支
Phase 一个 PR
Required Checks 全绿
再 Merge
Merge 后回 main
```

最终工作流：

```text
main
  ↓
Phase Branch
  ↓
Step
  ↓
Test
  ↓
Commit
  ↓
Step
  ↓
Test
  ↓
Commit
  ↓
Phase Complete
  ↓
Full Regression
  ↓
Matrix Gate
  ↓
Push
  ↓
PR
  ↓
Required Checks
  ↓
MERGE READY
  ↓
User Confirmation
  ↓
Merge
  ↓
main
  ↓
Pull
  ↓
Clean
  ↓
Delete old branch
  ↓
STOP
```

**不要自动越过任何一个人工确认点。**
