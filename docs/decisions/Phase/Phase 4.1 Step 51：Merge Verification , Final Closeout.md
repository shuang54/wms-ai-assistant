你现在开始执行：

# Phase 4.1 Step 51：Merge Verification / Final Closeout

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段目标

Phase 4.1 Step 50 已完成：

```text
Commit
↓
Push
↓
Pull Request
↓
Merge
```

现在用户已经：

```text
PR = MERGED
并且已经切换到新的开发分支
```

Step 51 唯一目标：

> 验证 Phase 4.1 已经真实进入 main，并确认本地 Git、远程 Git、PR Merge、历史提交、测试基线和工作区全部一致。

本阶段：

```text
ONLY VERIFICATION
```

---

# 二、绝对禁止

本阶段禁止：

```text
修改生产代码
修改测试
修改 baseline
修改 Semantic
修改 Prompt
修改 Lifecycle Contract
修改 Roadmap
修改 Evidence
修改 Annotation
修改 ConversationEvidence
修改数据库结构
创建 migration
修复历史 baseline failure
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

如果发现问题：

```text
先报告
不要自动修复
```

---

# 三、第一步：确认当前 Git 状态

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
git rev-parse HEAD
```

执行：

```powershell
git log --oneline --decorate -10
```

执行：

```powershell
git remote -v
```

记录：

```text
CURRENT_BRANCH
CURRENT_HEAD
WORKTREE_STATUS
REMOTE
```

---

# 四、确认当前新分支

用户已经切换到新分支。

不要假设名称。

执行：

```powershell
git branch -vv
```

确认：

```text
当前 branch
upstream
ahead / behind
```

如果当前 branch 是新的 Phase 4.2 / feature branch：

这是正常的。

但是：

**不要开始 Phase 4.2。**

只把它作为当前工作分支记录。

---

# 五、Fetch 最新远程状态

执行：

```powershell
git fetch origin --prune
```

然后：

```powershell
git branch -r
```

然后：

```powershell
git log --oneline --decorate --graph --all -30
```

确认：

```text
origin/main
```

是最新状态。

---

# 六、确认 Phase 4.1 Commit 已进入 main

Step 50 原始 commit：

```text
0e92e53
```

执行：

```powershell
git merge-base --is-ancestor 0e92e53 origin/main
```

如果：

```text
exit code = 0
```

说明：

```text
Step 50 commit
    ↓
reachable from origin/main
```

但是注意：

如果 GitHub 使用的是：

```text
Squash and Merge
```

则 `0e92e53` 可能不会作为 `main` 上的独立 commit 出现。

这种情况下：

**不要判定失败。**

继续通过：

```text
git log
git diff
PR merge commit
```

确认。

---

# 七、确认 PR Merge Commit

如果可以从 GitHub PR 页面获得：

```text
PR number
merge commit SHA
squash commit SHA
```

记录：

```text
PR
MERGE_COMMIT
```

如果是 Merge Commit：

```powershell
git show --no-patch --oneline <merge_commit_sha>
```

如果是 Squash Commit：

```powershell
git show --no-patch --oneline <squash_commit_sha>
```

确认其祖先包含：

```text
Phase 4.1 Step 50
```

GitHub 支持 Merge Commit、Squash Merge、Rebase Merge 三种方式，所以不能假设一定存在传统 merge commit。

---

# 八、验证 main 上的 Phase 4.1 文件

执行：

```powershell
git ls-tree -r --name-only origin/main | Select-String "Phase 4.1"
```

同时检查：

```powershell
git ls-tree -r --name-only origin/main | Select-String "conversation_evidence"
```

确认以下关键文件存在：

```text
backend/app/db/models/conversation_evidence.py
backend/app/db/conversation_evidence_repository.py
tests/test_conversation_evidence_persistence_db.py
tests/test_conversation_evidence_e2e_db.py
tests/test_phase_4_1_security_boundary_db.py
tests/test_phase_4_1_traceability.py
```

以及：

```text
docs/decisions/Phase/Phase 4.1 Roadmap v1.1.md
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
```

如果历史项目结构中名称不同：

以实际 Git tree 为准。

---

# 九、验证 Phase 4.1 关键提交链

检查：

```powershell
git log origin/main --oneline --decorate --all --grep="Phase 4.1"
```

重点确认：

```text
Step 37
Step 38
Step 39
Step 40
Step 41
Step 42
Step 43
Step 44
Step 45
Step 46
Step 47
Step 48
Step 49
Step 50
```

不要求 commit message 必须逐字一致。

目标是：

> Phase 4.1 已完成的实际变更全部存在于 `origin/main`。

---

# 十、确认 Step 49 Release Readiness 已进入 main

检查：

```powershell
git log origin/main --oneline -- "docs/evaluation/Phase 4.1 Step 49 — Release Readiness Audit.md"
```

以及：

```powershell
git log origin/main --oneline -- "docs/decisions/Phase/Phase 4.1 Roadmap v1.1.md"
```

确认 Release Readiness / Roadmap 等正式文档存在。

---

# 十一、确认 Step 50 Git Closeout 已进入 main

检查：

```powershell
git log origin/main --oneline -- "docs/decisions/Phase/Phase 4.1 Step 50：Git Closeout , Push , PR , Merge.md"
```

应该能够看到：

```text
0e92e53
```

或者对应的 squash/rebase 后 commit。

如果没有：

```text
STOP
```

不要修改任何内容。

---

# 十二、检查 main 与当前新分支关系

执行：

```powershell
git merge-base origin/main HEAD
```

然后：

```powershell
git rev-list --left-right --count origin/main...HEAD
```

记录：

```text
main...current branch
```

不要要求：

```text
ahead = 0
```

因为用户已经切换到新开发分支。

当前新分支可能自然地：

```text
HEAD > origin/main
```

或者：

```text
HEAD = origin/main
```

都不自动视为错误。

关键是：

```text
Phase 4.1
        ↓
origin/main
```

已经完成。

---

# 十三、确认当前新分支没有偷偷包含 Phase 4.2 工作

查看：

```powershell
git log --oneline origin/main..HEAD
```

如果存在 commit：

```text
Phase 4.2
```

或者明显属于下一阶段：

不要修改。

只记录：

```text
Current development branch contains post-Phase-4.1 work.
```

不要把它合并回 main。

---

# 十四、确认 main 工作区状态

不要切换到 main，除非安全。

如果当前 working tree clean：

可以执行：

```powershell
git status --short
```

确认：

```text
empty
```

如果当前工作区有用户新的未提交开发：

```text
DO NOT TOUCH
```

不要：

```text
reset
clean
stash
checkout
restore
```

---

# 十五、Main 最终代码验证

如果当前工作区 clean，可以：

```powershell
git switch main
git pull --ff-only origin main
```

如果当前工作区不是 clean：

**不要切换。**

直接使用：

```text
origin/main
```

进行验证。

---

# 十六、Main 编译验证

如果已经安全切到 main：

执行：

```powershell
python -m compileall -q backend tests scripts
```

要求：

```text
PASS
```

如果没有切到 main：

可以不重新执行。

Step 50 已经 compileall PASS。

---

# 十七、Main 非 DB Regression

如果当前 main 工作区 clean：

执行：

```powershell
python -m pytest -q
```

要求：

```text
0 failed
```

如果出现失败：

不要修改。

先判断是否与 Step 49 的状态一致。

---

# 十八、历史 DB Baseline Failure

再次强调：

以下 3 个测试：

```text
test_db_residue_is_zero
test_baseline_matches_step89_actual_execution
test_gate_passes_when_current_matches_baseline
```

仍然是：

```text
PRE_EXISTING NON-BLOCKER
```

禁止：

```text
skip
xfail
delete
baseline update
assertion weakening
```

如果 main 上仍然出现：

```text
3 failed
```

并且与 Step 42–50 完全一致：

记录：

```text
Historical DB baseline issue remains unchanged.
```

不要处理。

---

# 十九、确认 Production DB Writes

Step 51：

```text
DB writes = 0
```

不要运行任何需要修改生产数据库的命令。

如果执行 DB regression：

只能使用项目现有测试数据库。

禁止：

```text
TRUNCATE
DELETE production
UPDATE production
DROP production table
```

---

# 二十、PR 最终状态

如果可以通过 GitHub Web 查看：

确认：

```text
PR = MERGED
```

确认：

```text
base = main
head = phase4.1-step36
```

或者实际 Step 50 branch。

记录：

```text
PR number
PR URL
merge method
merge commit / squash commit
merged at
```

不要猜测。

---

# 二十一、CI 最终状态

确认 Step 50 PR 的：

```text
observability-matrix-gate.yml
```

状态。

GitHub Status Checks 是 PR 合并前的重要验证边界；如果它被配置成 required check，则必须通过才能合并。

记录：

```text
CI = PASS / FAIL
```

如果：

```text
PASS
```

继续。

如果：

```text
FAIL
```

且属于历史 baseline：

记录。

如果是新失败：

```text
STOP
```

---

# 二十二、确认 Branch Cleanup

用户已经切换新分支。

不要强制删除：

```text
phase4.1-step36
```

先查看：

```powershell
git branch -a
```

如果 GitHub 已自动删除远程旧 branch：

记录：

```text
Phase 4.1 branch cleanup = completed
```

如果仍存在：

记录：

```text
Phase 4.1 branch retained
```

不要自行删除。

GitHub 的常规 GitHub Flow 在合并后通常可以删除已经完成工作的 topic branch，但这不是 Step 51 必须动作。

---

# 二十三、Final Git Integrity

执行：

```powershell
git fetch origin --prune
```

然后：

```powershell
git status --short
```

然后：

```powershell
git branch -vv
```

然后：

```powershell
git log --oneline --decorate -10
```

要求：

```text
No unexpected working-tree changes
No Phase 4.1 divergence
origin/main contains Phase 4.1
```

---

# 二十四、Phase 4.1 最终 Contract Verification

确认：

### Persistence

```text
Evidence persistence       PASS
Annotation persistence     PASS
ConversationEvidence       PASS
```

### Provenance

```text
dataset_version + source_type
PASS
```

### Lifecycle

```text
Evidence:
IMPORTED
→ PERSISTED
→ ANNOTATED
→ REVIEWED
→ FINALIZED

PASS
```

### Annotation

```text
DRAFT
→ REVIEWED

PASS
```

### Conversation

```text
Conversation
↔
ConversationEvidence
↔
Evidence

PASS
```

### Security

```text
Identity Boundary       PASS
Provenance Boundary     PASS
Repository Boundary     PASS
Read Model Boundary     PASS
Cross-Conversation      PASS
Cross-Evidence          PASS
Sensitive Data          PASS
```

### Traceability

```text
Contract Drift          0
Orphan Implementation   0
Orphan Test             0
Traceability Gap        0
```

---

# 二十五、Step 51 不得改变以下 Deferred

保持：

```text
OD-12 turn-level provenance
Production Evidence Builder
```

仍然：

```text
DEFERRED
```

不得在 Step 51 实现。

---

# 二十六、Step 51 不得改变以下 Out-of-Scope

保持：

```text
RBAC
Multi-tenancy
ACL
Object-level Authorization
Agent
MCP
Memory
Workflow
Chat UI
Streaming
```

全部：

```text
OUT OF SCOPE
```

---

# 二十七、最终报告格式

完成后严格输出：

```text
【Phase 4.1 Step 51 COMPLETE】

1. Current Branch
2. Current HEAD
3. origin/main
4. PR
5. PR URL
6. Merge Method
7. Merge Commit / Squash Commit
8. Phase 4.1 Commit Reachability
9. Phase 4.1 Files on main
10. CI
11. Review
12. Non-DB Regression
13. DB Regression
14. Compile
15. DB residue
16. Production DB writes
17. Working Tree
18. Branch Cleanup
19. Deferred
20. Out-of-Scope
21. Contract Verification
22. Final Decision
```

---

# 二十八、最终 Decision

如果：

```text
PR merged
AND
origin/main contains Phase 4.1
AND
CI PASS
AND
no new regression
AND
working tree safe
AND
DB writes = 0
```

则：

```text
Phase 4.1 = COMPLETE
```

最终写：

```text
Phase 4.1 Final Status:

RELEASED / MERGED / VERIFIED
```

---

# 二十九、最终 STOP

这是 Phase 4.1 的最后一步。

完成后：

```text
STOP
```

不要：

```text
进入 Phase 4.2
修改代码
设计 Chat API
开发 Agent
开发 MCP
开发 Memory
开发 Workflow
实现 Evidence Builder
解决 OD-12
扩展 RBAC
```

只有用户明确下达下一阶段指令后，才能继续。

# END — Phase 4.1
