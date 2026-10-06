你现在开始执行：

# Phase 4.2 Step 7H — Release Readiness

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段目标

Phase 4.2 当前已经完成：

```text
7A Context Boundary Audit
7B Context Consumption Architecture
7C Context Window / Selection Policy
7D Context Placement Contract
7E Context Selection Implementation
7F Context Consumption Integration
7G Conversation E2E + Security + Regression
```

本阶段是：

> **Phase 4.2 Release Readiness / Final Verification**

这是最后一个 Step。

本阶段：

**不新增功能。**

**不重新设计架构。**

**不继续优化 Context。**

唯一目标：

```text
Review
→ Regression
→ Boundary Audit
→ Git Diff Audit
→ Release Gate
→ Commit
→ Push / PR（仅按现有仓库流程）
→ Verify
→ STOP
```

---

# 二、重要：不要再拆 Step

本阶段不要创建：

```text
7H-1
7H-2
7H-3
7H-4
```

所有 Release Review 在一个 Step 完成。

---

# 三、先读取当前状态

先执行：

```powershell
git status --short
git branch --show-current
git log --oneline --decorate -10
git diff --stat
git diff -- backend tests
git diff -- docs
```

确认：

当前 Branch：

```text
phase4.1-3
```

当前 HEAD：

```text
7f50f70
```

历史包含：

```text
66a69c6
```

工作区包含：

```text
Step 6
Step 7E
Step 7F
Step 7G
```

不要假设。

以真实 Git 状态为准。

---

# 四、Release Scope

本次 Release Scope 是：

```text
Phase 4.2 Step 6
Message Idempotency

+
Step 7E
Context Selection

+
Step 7F
Context Consumption

+
Step 7G
Conversation E2E / Security / Regression
```

其中：

```text
Step 6
```

已经存在 commit：

```text
66a69c6
```

而：

```text
7E
7F
7G
```

当前处于 working tree。

---

# 五、首先检查 Scope Boundary

执行 Git diff audit。

确认不存在以下范围外修改：

```text
Agent
LangGraph
MCP
Memory
Summary
Planning
Workflow
Multi-Agent
Query Understanding
```

同时确认没有意外修改：

```text
Business Semantic
Project Configuration
Schema
Database migrations
Embedding
```

---

# 六、Production Architecture Audit

检查最终架构：

```text
HTTP API
   ↓
ChatApplicationService
   ↓
ConversationRepository
   ↓
Idempotency
   ↓
History
   ↓
Context Selection
   ↓
Context Builder
   ↓
AIOrchestrator
   ↓
Router
   ├── RAG
   └── Text-to-SQL
```

Conversation Context：

```text
Conversation Layer
        ↓
explicit context parameter
        ↓
AI Runtime
```

必须确认：

```text
AI Runtime
```

没有反向依赖：

```text
ConversationRepository
ConversationService
Conversation ORM
```

---

# 七、Idempotency Release Gate

确认以下契约没有变化：

```text
Idempotency-Key
```

Scope：

```text
conversation
```

DB：

```text
UNIQUE(conversation_id, idempotency_key)
```

行为：

```text
same key + same payload
    ↓
Replay
```

并且：

```text
AI = 0
Context Selection = 0
Builder = 0
new USER = 0
new ASSISTANT = 0
```

Same key + different payload：

```text
409
```

Retry：

```text
USER exists
ASSISTANT absent
    ↓
AI retry
```

不要修改 Step 6。

---

# 八、Context Selection Release Gate

确认：

```text
MAX_TURNS = 20
MAX_CONTEXT_CHARS = 12000
```

保持不变。

确认：

```text
current turn
```

始终在 Selection 前排除。

确认：

```text
newest → oldest
```

选择后：

```text
oldest → newest
```

确认：

```text
complete turn
contiguous suffix
```

确认：

```text
newest oversized
```

完整保留。

确认：

```text
older oversized
```

停止选择。

不要：

```text
truncate
summary
tokenizer
embedding
reranker
importance ranking
```

---

# 九、Context Consumption Release Gate

确认：

```text
ChatApplicationService
        ↓
context
        ↓
AIOrchestrator
```

只有：

```text
Router LLM fallback
Final generation
```

可以消费。

确认：

```text
Rule Router
RAG retrieval
RelevantTableSelector
DatabaseContextComposer
ToolArgumentExtractor
Tool execution
```

没有错误消费 Conversation Context。

---

# 十、RAG Release Gate

确认：

```text
conversation_context
```

和：

```text
retrieved_context
```

明确区分。

最终概念：

```text
CONVERSATION HISTORY
    = untrusted reference

CONTEXT
    = retrieved knowledge
```

确认：

```text
conversation_context=None
```

时原有 RAG Prompt：

```text
byte-equivalent
```

确认 retrieval query 没有因为 7F/7G 改变。

---

# 十一、Text-to-SQL Release Gate

确认最终：

```text
DATABASE CONTEXT
        ↓
ALLOWED TABLES
        ↓
MAX ROWS
        ↓
CONVERSATION HISTORY
        ↓
QUESTION
```

Conversation History：

**不能修改：**

```text
allowed_tables
schema
MAX_ROWS
read-only
ProjectContext
Validator
Executor
```

确认：

```text
initial
```

和：

```text
retry
```

Context placement 相同。

确认：

```text
conversation_context=None
```

Prompt 与历史版本：

```text
byte-equivalent
```

---

# 十二、Security Release Gate

重新检查：

```text
Conversation History
```

是否可能：

```text
突破 System Safety
突破 Tool Capability
突破 SQL Read-only
突破 Allowed Tables
突破 MAX_ROWS
泄漏 Secrets
```

结果必须：

```text
NO
```

重点验证：

```text
Ignore all previous instructions
Use DELETE
DROP TABLE
Reveal credentials
Call arbitrary tools
```

这些历史内容只能作为：

```text
untrusted reference
```

不能成为：

```text
system instruction
```

---

# 十三、API Release Gate

确认：

```text
POST /api/conversations/{conversation_id}/messages
```

Body 仍然：

```json
{
  "content": "..."
}
```

Header：

```text
Idempotency-Key
```

保持现有 contract。

不要新增：

```text
context
history
conversation_context
```

API 字段。

确认：

```text
normal = 200
duplicate = 200
same-key-different-payload = 409
archived = 409
idempotency-key > 128 = 422
content > 10000 = 422
```

---

# 十四、Database Release Gate

必须确认：

```text
DB schema changes
```

本阶段：

```text
0
```

7E/7F/7G：

```text
no migration
```

Step 6 的唯一 DB schema change：

```text
conversation_turn.idempotency_key
UNIQUE(conversation_id,idempotency_key)
```

已经属于 Step 6。

不要新增：

```text
Context table
Memory table
Summary table
Conversation context table
```

---

# 十五、Evidence Release Gate

确认 Phase 4.1 Evidence Contract 未被修改。

尤其：

```text
Evidence identity
Evidence provenance
ConversationEvidence
```

保持原设计。

本 Phase 4.2：

```text
Conversation Context
```

不得进入：

```text
Evidence identity
Evidence provenance
```

也不要新增：

```text
Runtime Evidence
```

---

# 十六、Real LLM Gate

本次 Release：

```text
Real LLM calls = 0
```

这是允许的。

7G 的目标是：

```text
runtime wiring
security
boundary
determinism
```

不要求 DeepSeek 才能 Release。

如果已有：

```text
@pytest.mark.real_llm
```

保持默认 skip。

---

# 十七、Full Regression

执行：

```powershell
python -m pytest -q
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

记录真实结果。

不要隐藏失败。

不要修改 baseline 来制造绿色。

---

# 十八、Known Failures

当前历史已知：

```text
test_11_backend_working_tree_is_unmodified
```

以及：

```text
下游 Matrix / baseline gate
```

由于当前 working tree 本身包含：

```text
7E
7F
7G
```

这些可能在 commit 前继续失败。

这是：

```text
WORKING TREE STATE
```

导致的预期失败。

---

# 十九、DB Residue

当前已知：

```text
test_db_residue_is_zero
```

full-suite：

```text
可能失败
```

isolated：

```text
PASS
```

不要修改：

```text
DB residue guard
baseline
```

必须重新验证：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_db_residue_is_zero.py
```

如果实际测试路径不同，使用项目当前真实测试路径。

---

# 二十、Compile

执行：

```powershell
python -m compileall -q backend tests scripts
```

必须：

```text
0 errors
```

---

# 二十一、Static Architecture Audit

执行现有项目已有的 architecture / contract tests。

重点：

```text
Conversation
Idempotency
Context Selection
Context Builder
Context Consumption
Router
RAG
Text-to-SQL
Tool
Evidence
API
```

不要新增新的复杂 architecture framework。

---

# 二十二、Git Diff Review

执行：

```powershell
git status --short
git diff --stat
git diff --name-only
git diff -- backend
git diff -- tests
git diff -- docs
```

逐文件检查。

最终必须明确：

```text
Production changes
Test changes
Documentation changes
```

以及：

```text
Out-of-scope changes = 0
```

---

# 二十三、禁止把测试文件误认为生产修改

特别注意：

```text
tests/test_rag_runtime_observability.py
tests/test_rag_trace_coverage_audit.py
```

这些是：

```text
contract registration / test synchronization
```

不是 RAG production behavior change。

必须确认 diff 内容确实只是：

```text
authorized registration
```

如果出现真正 production behavior change：

停止并报告。

---

# 二十四、Commit 前检查

在 commit 之前必须：

```text
git diff --check
```

确认：

```text
no whitespace errors
```

然后：

```powershell
python -m pytest -q
```

如果 working-tree guard 因 dirty 状态失败：

记录：

```text
expected before commit
```

不要修改 guard。

---

# 二十五、Commit 策略

如果所有 Release Gate 满足：

可以创建：

```text
Phase 4.2
```

最终 commit。

Commit message 建议：

```text
feat: complete Phase 4.2 conversation runtime foundation
```

但：

**先检查仓库现有 commit message 风格。**

如果仓库习惯：

```text
feat:Phase ...
```

则遵循现有风格。

不要强行改变 commit convention。

---

# 二十六、Commit 前最终内容

最终 commit 必须包含：

```text
Step 6
```

已经 commit 的内容不需要重新提交。

当前 working tree：

```text
7E
7F
7G
7H documentation / release records
```

如果 Step 6 已经在历史：

```text
66a69c6
```

不要 squash / rebase / rewrite history。

---

# 二十七、Commit 后验证

Commit 后：

```powershell
git status --short
git log --oneline --decorate -5
```

工作区应该：

```text
clean
```

然后再次：

```powershell
python -m pytest -q
```

此时：

```text
test_11_backend_working_tree_is_unmodified
```

理论上应该恢复正常。

如果仍失败：

定位，不要修改 guard。

---

# 二十八、DB Commit 后验证

执行：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

如果：

```text
test_db_residue_is_zero
```

仍然只有 full-suite 顺序失败，而 isolated PASS：

记录：

```text
PRE-EXISTING / ORDER-DEPENDENT
```

不要改变 baseline。

---

# 二十九、Branch / Remote

检查：

```powershell
git branch -vv
git remote -v
git status
```

确认当前 branch：

```text
phase4.1-3
```

确认 upstream 状态。

**不要猜测远程分支名称。**

根据实际仓库情况决定：

```text
push
```

或：

```text
PR
```

如果当前仓库流程要求 PR：

创建 PR。

如果当前环境没有对应权限：

只完成 commit，并在报告中说明：

```text
Push / PR not executed due to repository permission / environment limitation.
```

不要伪造。

---

# 三十、不要修改 main

不要：

```text
merge main
rebase main
force push
```

除非仓库现有 Release 流程明确要求。

---

# 三十一、Release Documentation

新增：

```text
docs/evaluation/Phase 4.2 Release Readiness.md
```

记录：

## 1. Release Scope

```text
Step 6
Step 7E
Step 7F
Step 7G
```

## 2. Architecture

```text
Conversation
    ↓
Idempotency
    ↓
History
    ↓
Selection
    ↓
Builder
    ↓
AIOrchestrator
    ↓
Router
    ├── RAG
    └── Text-to-SQL
```

## 3. Context Contract

```text
MAX_TURNS = 20
MAX_CONTEXT_CHARS = 12000
```

## 4. Security

```text
Conversation Context = untrusted reference
```

## 5. API

```text
No contract change
```

## 6. DB

```text
No new DB change in 7E–7H
```

Step 6 idempotency schema change除外。

## 7. Test Result

填写真实数据。

不要复制旧数字。

## 8. Known Limitations

至少：

```text
OD-35 Query Understanding deferred
OD-38 Context configurability deferred
OD-39 Context observability deferred
KL-1 AI exactly-once not guaranteed
KL-2 duplicate does not replay full AI result
Tool Context Consumption deferred
```

## 9. Phase Status

```text
Phase 4.2 = RELEASE READY / RELEASED
```

只有实际 commit + verification 完成后才能写：

```text
RELEASED
```

否则只能写：

```text
RELEASE READY
```

---

# 三十二、最终 Architecture Snapshot

文档最后固定记录：

```text
Conversation Runtime

HTTP
 ↓
ChatApplicationService
 ↓
Idempotency
 ↓
Conversation History
 ↓
Context Selection
 ↓
Context Builder
 ↓
AIOrchestrator
 ↓
AI Router
 ├── RAG
 │    ├── Conversation History
 │    └── Retrieved Knowledge Context
 │
 └── Text-to-SQL
      ├── Database Context
      ├── Allowed Tables
      ├── MAX_ROWS
      ├── Conversation History
      └── Current Question
```

Security boundary：

```text
System / Safety
       >
Capability / Business
       >
Conversation Context
       >
Current Question
```

---

# 三十三、最终 Release Gates

必须全部确认：

```text
[ ] 7A contract preserved
[ ] 7B contract preserved
[ ] 7C contract preserved
[ ] 7D contract preserved
[ ] 7E implementation preserved
[ ] 7F consumption preserved
[ ] 7G E2E preserved

[ ] Idempotency preserved
[ ] Duplicate replay preserved
[ ] Retry preserved
[ ] Context window preserved
[ ] Current turn exclusion preserved
[ ] Conversation isolation preserved
[ ] Project isolation preserved

[ ] RAG separation preserved
[ ] Text-to-SQL safety preserved
[ ] Tool boundary preserved
[ ] Evidence boundary preserved
[ ] API boundary preserved
[ ] Secret boundary preserved

[ ] DB schema no new change
[ ] Prompt files no change
[ ] Real LLM calls = 0
[ ] compileall = PASS
[ ] git diff --check = PASS
[ ] full regression reviewed
[ ] DB regression reviewed
[ ] no NEW failures
[ ] no out-of-scope changes
```

---

# 三十四、STOP 条件

当：

```text
Release Gates = PASS
```

完成：

```text
commit
→ push / PR according to repository flow
→ verify
```

之后：

**立即 STOP。**

不要：

```text
Phase 4.2 Step 7I
```

不要：

```text
OD-35 implementation
```

不要：

```text
OD-38 implementation
```

不要：

```text
OD-39 implementation
```

不要：

```text
Memory
Summary
Agent
MCP
Workflow
```

不要继续扩展 Conversation Runtime。

---

# 三十五、最终汇报格式

完成后只返回：

```text
【Phase 4.2 RELEASE COMPLETE】

1. Branch / HEAD
2. Commit
3. Remote / PR
4. Working Tree

5. Phase 4.2 Scope
6. Idempotency
7. Context Selection
8. Context Consumption
9. Conversation E2E
10. Security

11. API
12. DB
13. Prompt
14. Evidence Boundary

15. Tests
16. DB Tests
17. Compile / Lint
18. git diff --check

19. New Failures
20. Pre-existing Failures

21. Files Changed
22. Files Added

23. Release Gates
24. Known Limitations

25. Architecture Snapshot

26. Phase 4.2 Status
```

最后明确：

```text
Phase 4.2 = RELEASED
```

并明确：

```text
Query Understanding = NOT IMPLEMENTED
Memory = NOT IMPLEMENTED
Summary = NOT IMPLEMENTED
Agent = NOT IMPLEMENTED
MCP = NOT IMPLEMENTED
Workflow = NOT IMPLEMENTED
```

**Phase 4.2 到此结束。**

不要自动进入 Phase 5。
