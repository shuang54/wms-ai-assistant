# Phase 4.1 Step 50：Git Closeout / Push / PR / Merge

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段目标

Phase 4.1 Step 49 已明确：

```text
Final Decision = RELEASE READY
```

本阶段唯一目标：

> 将已经完成并通过 Release Readiness Audit 的 Phase 4.1 变更安全地完成 Git Closeout、Push、PR、CI、Merge 和 Merge Verification。

完整流程：

```text
Step 49 RELEASE READY
        ↓
Git Working Tree Audit
        ↓
Diff Review
        ↓
Commit
        ↓
Push
        ↓
Pull Request
        ↓
CI
        ↓
PR Review / Merge
        ↓
Fetch
        ↓
Merge Verification
        ↓
STOP
```

---

# 二、绝对禁止

Step 50 不允许：

```text
修改生产逻辑
修改数据库结构
修改 Frozen Contract
修改 Lifecycle Contract
修改 Roadmap
修改 baseline
修改测试行为
删除测试
降低 assertion
新增 skip
新增 xfail
实现 Evidence Builder
实现 OD-12
实现 RBAC
实现 Multi-tenancy
实现 Agent
实现 MCP
实现 Memory
实现 Workflow
进入 Phase 4.2
```

Step 50 是：

```text
GIT CLOSEOUT ONLY
```

---

# 三、Git 操作授权范围

本阶段允许：

```text
git status
git diff
git diff --check
git log
git branch
git fetch
git add
git commit
git push
git show
```

允许创建：

```text
Pull Request
```

允许：

```text
PR Review
PR Merge
```

允许：

```text
git fetch
git log
git diff
```

进行 Merge Verification。

---

# 四、Step 50 开始前必须确认

读取：

```text
docs/evaluation/Phase 4.1 Step 49 — Release Readiness Audit.md
```

确认：

```text
Final Decision:
RELEASE READY
```

如果不是：

```text
RELEASE READY
```

立即停止。

---

# 五、Step 50 初始 Git 审计

执行：

```powershell
git status --short
```

执行：

```powershell
git branch --show-current
```

执行：

```powershell
git log --oneline --decorate -10
```

执行：

```powershell
git remote -v
```

执行：

```powershell
git diff --stat
```

执行：

```powershell
git diff --name-only
```

---

# 六、确认当前 Branch

当前历史报告：

```text
branch:
phase4.1-step36
```

HEAD：

```text
c079c1c
```

但是：

**不要假设当前状态仍然完全一致。**

必须以：

```powershell
git branch --show-current
git rev-parse HEAD
```

实际输出为准。

如果 branch / HEAD 与 Step 49 报告不同：

```text
STOP
```

先报告：

```text
Git state drift
```

不要继续。

---

# 七、工作区文件审计

根据 Step 49：

预期未跟踪：

```text
Step 49 task document
docs/evaluation/Phase 4.1 Step 49 — Release Readiness Audit.md
```

同时 Step 48 应包含：

```text
tests/test_phase_4_1_traceability.py
docs/evaluation/Phase 4.1 Step 48 — Regression Traceability Audit.md
```

Step 45 Guard Reconciliation 的修改：

```text
tests/test_conversation_architecture_contract.py
tests/test_conversation_model_contract.py
tests/test_conversation_persistence_contract.py
tests/test_evidence_persistence_boundary.py
```

Step 50 必须逐一确认。

---

# 八、Unexpected File 检查

执行：

```powershell
git status --short
```

建立：

```text
EXPECTED
```

与：

```text
UNEXPECTED
```

如果发现：

```text
.env
.env.*
credentials
secret files
database dumps
logs
temporary files
cache
venv
__pycache__
IDE files
unrelated source files
```

不得提交。

---

# 九、AI Coding Agent 特别审计

必须检查：

```text
AGENTS.md
CLAUDE.md
.cursorrules
.cursor/
.github/
.github/workflows/
```

确认没有因为 CodeBuddy / AI Agent 工作而发生未预期修改。

如果这些文件没有变化：

记录：

```text
AI steering files = unchanged
```

如果发生变化：

**STOP。**

不要自动提交。

AI coding agent 的规则文件属于持久化 steering configuration，应像 CI/CD 配置一样进行额外审查。

---

# 十、CI / Build 文件审计

确认：

```text
.github/workflows/
Dockerfile
docker-compose.yml
pyproject.toml
package.json
Makefile
```

没有 Step 50 之外的修改。

如果存在未预期变化：

```text
STOP
```

不要提交。

CI/CD 配置本身属于高敏感的供应链边界，提交前应进行显式 review。

---

# 十一、Diff Review

执行：

```powershell
git diff --check
```

要求：

```text
0 whitespace errors
```

然后：

```powershell
git diff --stat
```

然后：

```powershell
git diff --name-status
```

然后：

```powershell
git diff
```

必须逐文件检查。

---

# 十二、Production Code Boundary

确认：

```text
backend production changes
```

与 Step 49 报告一致。

Step 48/49 报告：

```text
Step 48:
production code = 0

Step 49:
production code = 0
```

但是 Step 37～47 存在正式 production changes。

因此必须确认：

> 这些 production changes 已经被历史提交 / 当前 working tree 正确纳入 Phase 4.1 Closeout。

不得出现：

```text
production change unexpectedly missing
```

或者：

```text
production change unexpectedly added
```

---

# 十三、Database Migration Boundary

确认本次提交没有：

```text
Alembic migration
SQL migration
CREATE TABLE script
ALTER TABLE script
DROP TABLE script
```

Phase 4.1 已经使用：

```text
Base.metadata.create_all()
```

并且 Step 49 已确认：

```text
DB Schema = Contract compliant
```

不要在 Step 50 新增 migration。

---

# 十四、Test Integrity Review

重点检查：

```text
tests/
```

不能出现：

```text
deleted tests
weakened assertions
new skip
new xfail
mocking real dependency unexpectedly
```

尤其检查 Step 45～49 新增测试。

确认：

```text
Step 45 Guard
Step 46 E2E
Step 47 Security
Step 48 Traceability
```

测试仍然保持真实断言。

AI-generated test changes 必须特别检查是否存在删除测试、降低断言或用 mock 替代真实边界等情况。

---

# 十五、3 个历史 DB Baseline Failure

本阶段：

**绝对不要修改。**

保持：

```text
test_db_residue_is_zero
test_baseline_matches_step89_actual_execution
test_gate_passes_when_current_matches_baseline
```

当前分类：

```text
PRE_EXISTING NON-BLOCKER
```

不要：

```text
skip
xfail
delete
rewrite
baseline update
```

PR 描述中必须明确：

```text
Known pre-existing DB baseline failures:
3
```

并说明：

```text
isolated run = 3 passed
core Phase 4.1 DB collection = PASS
not introduced by this release
```

---

# 十六、不要修改 Baseline

确认：

```text
tests/fixtures/
tests/baselines/
```

没有因为 Step 50 被修改。

如果 baseline 被修改：

```text
STOP
```

除非用户明确要求进行 baseline 更新。

当前任务明确：

```text
NO BASELINE UPDATE
```

---

# 十七、Commit Scope

只提交：

```text
Phase 4.1
```

相关文件。

不要提交：

```text
unrelated files
IDE settings
credentials
temporary logs
local DB dumps
task scratch files
```

Step 49 的：

```text
task document
```

是否提交：

根据项目现有任务文档惯例判断。

如果历史 Phase task docs 都进入 repository：

可以提交。

如果历史 task docs 不进入 repository：

不要强行提交。

必须检查 Git 历史后决定。

---

# 十八、Commit 前最终测试

在 `git add` 前运行：

```powershell
python -m pytest -q
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

然后：

```powershell
python scripts/run_matrix_gate.py
```

如果这些结果与 Step 49 不一致：

```text
STOP
```

不得 commit。

---

# 十九、Commit 前状态记录

记录：

```text
Branch
HEAD before commit
Working tree
Full regression
DB regression
Matrix
Compile
```

例如：

```text
Branch:
phase4.1-step36

HEAD before:
c079c1c

Full:
0 failed

DB:
3 historical pre-existing failures

Matrix:
PASS

Compile:
PASS
```

实际值必须以命令输出为准。

---

# 二十、Git Add

只有上述所有检查通过后：

执行：

```powershell
git add <明确的Phase 4.1文件>
```

不要：

```powershell
git add .
```

除非你已经确认：

```text
working tree only contains intended Phase 4.1 changes
```

更推荐：

```text
explicit file list
```

---

# 二十一、Staged Diff Review

执行：

```powershell
git status --short
```

然后：

```powershell
git diff --cached --stat
```

然后：

```powershell
git diff --cached --name-status
```

然后：

```powershell
git diff --cached
```

**必须再次逐文件审查。**

如果 staged diff 与预期不同：

```text
git restore --staged <unexpected>
```

然后重新检查。

---

# 二十二、Staged Secret Audit

检查 staged 内容：

```powershell
git diff --cached
```

重点搜索：

```text
sk-
Bearer
api_key
apikey
password
secret
token
authorization
database_url
connection_string
postgresql://
```

如果出现真实 credential：

```text
STOP
```

不得 commit。

---

# 二十三、Commit Message

建议：

```text
feat: Phase 4.1 complete conversation evidence foundation
```

或者：

```text
feat: complete Phase 4.1 conversation evidence foundation
```

必须：

```text
imperative / concise
```

不要：

```text
phase4.1 everything final final
```

不要在 commit message 中声称：

```text
all tests pass
```

因为存在已知 3 个历史 DB baseline failures。

---

# 二十四、Commit

执行：

```powershell
git commit -m "feat: complete Phase 4.1 conversation evidence foundation"
```

Commit 后：

```powershell
git status --short
```

要求：

```text
working tree clean
```

然后：

```powershell
git log -1 --oneline
```

记录：

```text
NEW_COMMIT
```

---

# 二十五、Commit 内容验证

执行：

```powershell
git show --stat --oneline HEAD
```

然后：

```powershell
git show --name-status --oneline HEAD
```

确认：

```text
commit
files
scope
```

正确。

---

# 二十六、不要直接 Push main

确认当前 branch：

```text
phase4.1-step36
```

如果仍是该 branch：

不要直接 push 到：

```text
main
```

先确认：

```text
remote branch
```

---

# 二十七、Remote 检查

执行：

```powershell
git fetch origin
```

然后：

```powershell
git branch -vv
```

然后：

```powershell
git log --oneline --decorate --graph --all -20
```

确认：

```text
origin/main
```

当前 HEAD 与 main 的关系。

如果远程发生新的 Phase 4.1 相关提交：

```text
STOP
```

不要直接 rebase。

先报告 divergence。

---

# 二十八、Push

如果 branch 当前没有 upstream：

执行：

```powershell
git push -u origin <current-branch>
```

如果已有 upstream：

```powershell
git push
```

不要 force push。

禁止：

```text
git push --force
git push --force-with-lease
```

除非用户明确授权。

---

# 二十九、Push 后验证

执行：

```powershell
git status -sb
```

然后：

```powershell
git fetch origin
```

然后：

```powershell
git log --oneline --decorate -5
```

确认：

```text
local HEAD
remote HEAD
```

一致。

---

# 三十、Pull Request

创建 PR。

如果项目使用 GitHub CLI 且可用：

```powershell
gh pr create
```

如果 `gh` 不可用：

使用 GitHub Web UI。

不要因为 CLI 不存在而修改 Git history。

---

# 三十一、PR Title

建议：

```text
Phase 4.1: Complete Conversation Evidence Foundation
```

---

# 三十二、PR Body

必须包含：

```text
## Summary

Complete Phase 4.1 Steps 37–50 closeout for the
Conversation + Evidence foundation.

## Scope

- Evidence persistence
- Provenance persistence
- Annotation association
- Idempotency / transaction
- Annotation review
- Finalization contract
- Lifecycle matrix
- Conversation contract
- ConversationEvidence persistence
- Real Conversation Evidence E2E
- Security boundary audit
- Regression / traceability
- Release readiness

## Validation

- Full non-DB regression: 0 failed
- Core Phase 4.1 DB regression: PASS
- Matrix: PASS
- Compileall: PASS
- Production DB writes: 0

## Known Pre-existing Issue

The full DB suite still reports the same three historical baseline
guard failures:

- test_db_residue_is_zero
- test_baseline_matches_step89_actual_execution
- test_gate_passes_when_current_matches_baseline

These were present in Steps 42–49, pass when isolated, were not modified,
and were not introduced by this release.

## Deferred

- OD-12 turn-level provenance
- Production Evidence Builder / AI Result → Evidence orchestration

## Out of Scope

- RBAC
- Multi-tenancy
- ACL
- Object-level Authorization
- Agent
- MCP
- Memory
- Workflow
- Chat UI
- Streaming
```

不要写：

```text
ALL TESTS PASS
```

因为事实并非如此。

---

# 三十三、PR Diff Review

打开 PR 后：

**不要直接 Merge。**

逐文件检查：

```text
Files changed
```

重点：

```text
backend/app/db/
backend/app/db/models/
tests/
docs/
.github/
AGENTS.md
```

确认：

```text
no unexpected file
no deleted security test
no weakened assertion
no credential
no CI modification
no rules-file modification
```

AI-generated PR 不应只根据 PR summary 审批，而应检查完整 diff。

---

# 三十四、CI

等待 CI 完成。

记录：

```text
CI workflow
status
```

要求：

```text
PASS
```

如果 CI FAIL：

先判断：

```text
code regression
environment failure
historical baseline
CI infrastructure
```

不要自动修复。

---

# 三十五、CI Failure Policy

如果 CI 出现：

```text
new test failure
security failure
compile failure
matrix failure
```

则：

```text
MERGE BLOCKED
```

如果只是：

```text
historical known baseline behavior
```

必须确认 repository 当前 CI 已经允许该已知状态。

如果 CI policy 不允许：

```text
STOP
```

不要通过修改 CI 配置绕过。

---

# 三十六、Review

如果仓库需要 reviewer：

等待真实 review。

不要自己制造：

```text
approved
```

不要绕过 branch protection。

OWASP CI/CD guidance 也建议保护分支要求 PR review，并避免绕过 review gate。

---

# 三十七、Merge

只有：

```text
PR open
CI PASS
required review PASS
no unresolved conversation
no unexpected diff
```

才允许 Merge。

优先使用项目默认 Merge 方法：

```text
Merge commit
Squash
Rebase
```

不要自行改变 repository policy。

---

# 三十八、Merge 后立即停止开发

Merge 成功后：

```text
STOP FEATURE DEVELOPMENT
```

不要：

```text
继续 Phase 4.2
修改代码
创建 Agent
创建 MCP
实现 Memory
实现 Workflow
```

先做 Merge Verification。

---

# 三十九、Merge Verification

执行：

```powershell
git fetch origin
```

然后：

```powershell
git log --oneline --decorate --graph -20
```

确认：

```text
origin/main
```

已经包含 Phase 4.1 merge commit / squash commit。

---

# 四十、验证远程分支

执行：

```powershell
git branch -r
```

确认：

```text
origin/main
```

状态。

如果 feature branch 已自动删除：

记录：

```text
remote feature branch deleted
```

如果没有删除：

记录：

```text
remote feature branch retained
```

不要自行删除。

---

# 四十一、Merge Commit Verification

执行：

```powershell
git merge-base --is-ancestor <phase4.1-commit> origin/main
```

要求：

```text
exit code 0
```

如果采用 squash merge：

使用 PR merge commit / squash commit 对应的 SHA 做验证。

---

# 四十二、最终 main 验证

如果本地允许切换 branch：

```powershell
git fetch origin
```

然后根据项目当前 Git workflow：

```text
origin/main
```

确认包含 Phase 4.1。

**不要执行 destructive checkout/reset。**

如果当前 working tree clean，可以安全地：

```powershell
git switch main
git pull --ff-only
```

但如果 working tree 非 clean：

```text
STOP
```

不要强制切换。

---

# 四十三、最终回归

Merge 后不需要重新跑完整 DB suite 来“制造一个新状态”。

最少验证：

```powershell
python -m pytest -q
```

以及：

```powershell
python -m compileall -q backend tests scripts
```

如果项目 CI 已经在 Merge 前完整执行，则本地 Merge 后重点确认：

```text
HEAD
main
commit
CI
```

保持一致。

---

# 四十四、最终 Git 状态

执行：

```powershell
git status --short
```

要求：

```text
clean
```

然后：

```powershell
git branch -vv
```

记录：

```text
main
upstream
HEAD
```

---

# 四十五、最终 Phase 4.1 验证

最终确认：

```text
Step 37  PASS
Step 38  PASS
Step 39  PASS
Step 40  PASS
Step 41  PASS
Step 42  PASS
Step 43  PASS
Step 44  PASS
Step 45  PASS
Step 46  PASS
Step 47  PASS
Step 48  PASS
Step 49  RELEASE READY
Step 50  MERGED
```

---

# 四十六、历史 DB Baseline 最终记录

最终报告必须保留：

```text
Known historical DB baseline issue:

3 tests fail in full RUN_DB_TESTS=1 suite:

- test_db_residue_is_zero
- test_baseline_matches_step89_actual_execution
- test_gate_passes_when_current_matches_baseline

Classification:
PRE_EXISTING NON-BLOCKER

Reason:
- unchanged since Step 42
- isolated execution passes
- not introduced by Phase 4.1
- no Frozen Contract impact
- no Security impact
- core Phase 4.1 DB tests pass
```

不要在 Merge 后突然修改它们。

---

# 四十七、最终报告格式

完成整个 Step 50 后严格报告：

```text
【Phase 4.1 Step 50 COMPLETE】

1. Branch
2. HEAD before commit
3. Commit
4. Commit files
5. Working tree
6. Push
7. Remote branch
8. PR
9. PR URL
10. CI
11. Review
12. Merge
13. Merge commit / squash commit
14. origin/main verification
15. Final regression
16. Compile
17. DB residue
18. Production DB writes
19. Known historical DB baseline failures
20. Deferred
21. Out-of-scope
22. Final Git status
23. Phase 4.1 final status
```

---

# 四十八、最终结论

如果 Merge Verification 全部通过：

```text
Phase 4.1 FINAL STATUS

Persistence             PASS
Provenance              PASS
Annotation              PASS
Idempotency             PASS
Lifecycle               PASS
Conversation            PASS
ConversationEvidence    PASS
Real E2E                PASS
Security                PASS
Traceability            PASS
Release Readiness       PASS
Git Closeout            PASS
PR                      PASS
CI                      PASS
Merge                   PASS
Merge Verification     PASS
```

然后：

```text
Phase 4.1 = COMPLETE
```

---

# 四十九、最终 STOP 条件

完成：

```text
commit
→ push
→ PR
→ CI
→ review
→ merge
→ origin/main verification
```

之后：

**立即停止。**

不要：

```text
Phase 4.2
Agent
MCP
Memory
Workflow
RBAC
Chat UI
Streaming
Evidence Builder
OD-12
```

不要继续扩展 Phase 4.1。

Phase 4.1 到此正式结束。
