你现在开始执行：

# Phase 5 开工前 Baseline Audit

项目：

```text
D:\coding\ai\wms-ai-assistant
```

## 一、目标

Phase 4.2 已完成并发布。

现在只做：

> **Phase 5 开工前的代码、Git、架构、测试基线确认。**

本阶段：

**只审计，不开发。**

完成后停止。

---

# 二、第一步：Git 状态

执行并记录：

```powershell
git branch --show-current
git status --short
git status -sb
git log --oneline -10
git remote -v
git fetch origin
```

确认：

```text
当前分支
HEAD
origin/main
origin/<current-branch>
工作区是否 clean
是否存在未提交修改
是否存在未 push commit
```

如果当前分支不是预期 Phase 5 工作分支：

不要自动创建/切换。

只报告。

---

# 三、Phase 4.2 发布状态

确认：

```text
Phase 4.2
```

是否已经：

```text
代码完成
测试完成
commit 完成
push 完成
PR 完成
main 同步
```

如果 PR #10 已经合并：

确认：

```text
origin/main
```

是否包含：

```text
8847cca
5525822
```

如果 PR #10 仍然 OPEN：

不要合并。

不要修改 PR。

不要绕过 required checks。

只报告当前状态。

---

# 四、Phase 4.2 代码基线

重点阅读实际代码，不要只看历史文档。

检查：

```text
backend/app/services/chat_application_service.py
backend/app/services/conversation_context_selection_service.py
backend/app/services/conversation_context_builder.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/ai_router_service.py
backend/app/services/rag_service.py
backend/app/services/text_to_sql_service.py

backend/app/db/models/conversation.py
backend/app/db/models/conversation_turn.py
backend/app/db/conversation_repository.py
backend/app/db/init_db.py

backend/app/api/conversations.py

backend/app/dto/conversation_api.py
```

确认当前真实实现：

```text
Conversation
    ↓
ConversationTurn
    ↓
Idempotency
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

确认没有出现：

```text
Agent
MCP
Workflow
Memory
Planning
Multi-Agent
```

等 Phase 5 之前不应该存在的能力。

---

# 五、Conversation Runtime 基线

确认：

## Idempotency

```text
Idempotency-Key
conversation scope
UNIQUE(conversation_id,idempotency_key)
replay
conflict
retry
```

行为没有变化。

## Context

确认：

```text
MAX_TURNS = 20
MAX_CONTEXT_CHARS = 12000
```

以及：

```text
current turn excluded
newest → oldest selection
oldest → newest restore
contiguous suffix
newest oversized preserved
older oversized stops
no truncation
no summary
no embedding
no reranker
```

## Context Consumption

确认 Conversation Context 目前只进入：

```text
Router LLM fallback
Final generation
```

确认没有偷偷扩展到：

```text
RAG retrieval
RelevantTableSelector
DatabaseContextComposer
ToolArgumentExtractor
Tool execution
SQL Validator
SQL Executor
```

---

# 六、安全基线

检查：

```text
Conversation Context
```

仍然属于：

```text
untrusted reference
```

优先级必须保持：

```text
System / Safety
    >
Capability / Business Constraints
    >
Conversation Context
    >
Current Question
```

特别验证：

```text
Conversation history
    ↓
DELETE SQL
```

不能绕过：

```text
SQL Validator
```

以及：

```text
Conversation history
    ↓
Tool permission
```

不能新增 Tool capability。

---

# 七、Evidence 基线

确认 Phase 4.1 Evidence contract 没有被 Phase 4.2 修改。

确认：

```text
Evidence
```

仍然是：

```text
Source / Provenance Unit
```

不是：

```text
AI Answer
Tool Result
RAG Result
SQL Result
Conversation Turn
```

确认：

```text
dataset_version
source_type
```

仍然是 Evidence provenance boundary。

不要修改 Evidence。

---

# 八、测试基线

先不要修测试。

运行：

```powershell
python -m pytest -q
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

再：

```powershell
python -m compileall backend tests scripts
```

记录：

```text
offline:
passed
failed
skipped

DB:
passed
failed
skipped

compileall:
```

如果仍然出现 Phase 4.2 已知的：

```text
TestMatrixExecutionBaseline::test_baseline_matches_step89_actual_execution
TestMatrixExecutionBaseline::test_db_residue_is_zero
TestMatrixBaselineGate::test_gate_passes_when_current_matches_baseline
```

必须单独标记：

```text
PRE-EXISTING / KNOWN
```

不要为了本次 Audit 修改它们。

---

# 九、检查 Phase 5 前置条件

搜索：

```text
docs/
```

尤其检查：

```text
docs/architecture.md
docs/requirements.md
docs/decisions/
docs/evaluation/
```

以及 Git 历史：

```powershell
git log --all --oneline --decorate --graph -30
```

找出：

```text
Phase 5
Agent
Agent Runtime
Tool Loop
Planning
Bounded Agent
```

相关已有设计。

不要重新设计。

如果已经存在 Phase 5 架构决策：

整理出来。

如果没有：

不要开始写实现。

---

# 十、Phase 5 风险扫描

只列事实。

检查当前系统是否已经具备：

```text
1. LLM Provider
2. LLM error boundary
3. Orchestrator
4. Router
5. Tool Registry
6. Tool execution
7. Conversation Runtime
8. Conversation Context
9. Idempotency
10. Evidence
11. Observability
12. Evaluation
13. Security validation
```

然后判断：

```text
Phase 5 Agent Runtime
```

缺少哪些**架构前置条件**。

不要提出大量新功能。

不要进入实现设计。

---

# 十一、严格禁止

本次 Audit 禁止：

```text
❌ 修改生产代码
❌ 修改 Prompt
❌ 修改 SQL Validator
❌ 修改 SQL Executor
❌ 修改 Conversation
❌ 修改 Idempotency
❌ 修改 Context
❌ 修改 Evidence
❌ 新增 Agent
❌ 新增 MCP
❌ 新增 Workflow
❌ 新增 Memory
❌ 新增 Planning
❌ 修改数据库结构
❌ 新增 migration
❌ 修改 baseline
❌ 修改测试让测试通过
❌ merge PR
❌ rebase
❌ force push
❌ reset
```

---

# 十二、最终报告

严格使用：

```text
【Phase 5 PRE-FLIGHT BASELINE AUDIT】

1. Git
- Branch:
- HEAD:
- origin/main:
- Working tree:
- Upstream:
- Unpushed commits:

2. Phase 4.2 Release
- PR:
- Merge status:
- Main status:
- Release commits:

3. Conversation Runtime
- Idempotency:
- Context Selection:
- Context Consumption:
- Security:

4. Evidence
- Contract:
- 是否变化:

5. Test Baseline
- Offline:
- DB:
- compileall:

6. Known Failures
- ...

7. Phase 5 Existing Architecture
- ...

8. Phase 5 Missing Preconditions
- ...

9. Scope Changes
- Production code:
- DB:
- API:
- Prompt:
- Evidence:

10. Conclusion
- Phase 5 是否具备开始条件:
- 阻塞项:
- 建议下一步:

```

## 最重要的规则

本次只：

```text
Audit
→ Report
→ STOP
```

**不要开始 Phase 5 实现。**

不要自行拆成：

```text
5A
5A-1
5A-1-1
5A-1-1-1
```

完成本次 Baseline Audit 后停止，等待下一条指令。
