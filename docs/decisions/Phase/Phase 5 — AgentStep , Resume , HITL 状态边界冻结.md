你现在开始执行：

# Phase 5 — AgentStep / Resume / HITL 状态边界冻结

## 一、阶段性质

本阶段：

**只做架构审计、状态机设计、恢复语义设计和 Decision 文档。**

**禁止实现 AgentStep Persistence。**

**禁止实现 Resume。**

**禁止实现 HITL。**

**禁止修改生产代码。**

目标只有一个：

> 在进入 AgentStep Persistence 之前，把 AgentRun、AgentStep、Checkpoint、Resume、HITL、Cancellation 之间的责任边界一次性冻结。

当前已经完成：

```text
Phase 5 Agent Core Loop                 ✅
Phase 5 Provider-native Tool Calling   ✅
Phase 5 Agent Integration              ✅
Phase 5 AgentRun Persistence           ✅
```

当前明确未开始：

```text
AgentStep Persistence
Agent Resume
Checkpoint
HITL
Cancellation
```

不要因为本阶段发现潜在问题而直接实现上述能力。

---

# 二、必须先阅读真实代码

先阅读：

```text
backend/app/agent/models.py
backend/app/agent/runtime.py
backend/app/agent/decision.py
backend/app/agent/policy.py
backend/app/agent/llm.py
backend/app/agent/recorder.py
backend/app/agent/ids.py

backend/app/db/models/agent_run.py
backend/app/db/agent_run_repository.py

backend/app/services/ai_orchestrator_service.py
backend/app/services/project_orchestrator_factory.py

backend/app/services/tool_execution_service.py

backend/app/db/models/
backend/app/db/
backend/app/api/orchestrator_chat.py

backend/app/services/conversation*
backend/app/db/models/conversation*
backend/app/db/models/conversation_turn*

tests/test_agent_runtime.py
tests/test_agent_orchestrator_integration.py
tests/test_agent_run_persistence.py
tests/test_agent_run_persistence_db.py

docs/decisions/Phase/Phase 5 — Scope & Architecture Decision.md
docs/architecture.md
```

同时搜索：

```text
AgentRun
AgentStep
step_records
LIMIT_REACHED
WAITING_APPROVAL
CANCELLED
request_id
run_id
assistant_request_id
tool_call_id
Idempotency-Key
ConversationTurn
ConversationEvidence
LLMUsageRecord
ToolExecutionRecord
AssistantOutcomeRecord
```

必须以真实代码为准。

---

# 三、当前已冻结的事实

不要重新讨论这些：

## 1. AgentRun

已经存在：

```text
ai_ops.agent_run
```

它是：

> Agent Run-level persistence / terminal execution record。

它不是：

```text
checkpoint
resume state
step state
HITL state
```

当前字段保持：

```text
run_id
request_id
project_id
status
error_code
limit_reason
step_count
tool_call_count
llm_call_count
token_usage_unverifiable
started_at
finished_at
duration_ms
created_at
```

---

## 2. AgentRuntime

必须继续：

```text
DB-free
Conversation-free
HTTP-free
Provider-independent
```

不能让 Runtime 直接：

```text
Session
Repository
DB Model
Conversation
```

---

## 3. AgentStep

当前：

```text
AgentRuntimeResult.step_records
```

仍然是：

```text
in-memory
```

本阶段不要持久化。

---

# 四、核心问题

本阶段必须回答：

## Question 1

`AgentStep` 到底是什么？

必须在以下三个定义中明确选择：

```text
A. Observation
B. Runtime State
C. Runtime State + Audit Projection
```

不能模糊成：

```text
“AgentStep 就是 AgentRun 的明细”
```

---

## Question 2

如果未来支持 Resume：

Resume 到底恢复什么？

必须明确：

```text
run
step
loop iteration
LLM decision
tool execution
pending approval
```

哪些需要恢复。

哪些不恢复。

---

## Question 3

如果 Tool 已经执行成功：

```text
resume
```

是否允许再次执行这个 Tool？

必须明确：

```text
exactly-once
at-most-once
at-least-once
```

不要使用模糊描述。

尤其考虑：

```text
worker crash
DB commit success
response lost
resume
```

这种情况。

---

# 五、必须建立 Agent State Machine

不要实现代码。

只设计状态机。

建议至少分析：

```text
RUNNING
   │
   ├── FINAL
   │      ↓
   │   COMPLETED
   │
   ├── TOOL_CALL
   │      ↓
   │   TOOL_EXECUTION
   │      │
   │      ├── success → next LLM step
   │      └── failure → FAILED
   │
   ├── limit
   │      ↓
   │   LIMIT_REACHED
   │
   ├── cancellation
   │      ↓
   │   CANCELLED
   │
   └── approval required
          ↓
   WAITING_APPROVAL
```

但是：

**不要直接照抄。**

必须根据当前 Runtime 的真实语义重新画最终状态机。

---

# 六、区分 Terminal State / Paused State

必须明确：

### Terminal

```text
COMPLETED
FAILED
LIMIT_REACHED
CANCELLED
```

### Non-terminal / Paused

```text
WAITING_APPROVAL
```

如果认为 `WAITING_APPROVAL` 不应该属于 AgentRun.status：

必须明确提出替代设计。

不能让：

```text
WAITING_APPROVAL
```

被当成：

```text
FAILED
```

也不能让：

```text
WAITING_APPROVAL
```

被当成：

```text
COMPLETED
```

---

# 七、Resume Contract

定义：

```text
Resume(run_id)
```

未来必须满足什么条件。

至少分析：

### 1. Run identity

```text
run_id
```

是否保持不变？

要求明确。

---

### 2. Request identity

```text
request_id
```

Resume 时是否：

```text
保持原 request_id
```

还是：

```text
产生新的 request_id
```

必须明确原因。

---

### 3. Conversation identity

```text
conversation_id
turn_id
assistant_request_id
```

Resume 是否：

```text
创建新 turn
```

还是：

```text
继续原 turn
```

必须明确。

不要在没有结论的情况下实现。

---

### 4. Agent step identity

定义未来：

```text
step_index
```

是否：

```text
从 0 开始
```

是否：

```text
单调递增
```

是否：

```text
(run_id, step_index) UNIQUE
```

---

### 5. Tool call identity

必须分析：

```text
tool_call_id
```

与：

```text
step_index
```

的关系。

例如：

```text
AgentStep
  └── tool_call_id
```

还是：

```text
AgentStep
  └── tool_call
```

当前阶段只设计，不实现。

---

# 八、Checkpoint Contract

必须回答：

> 什么东西才算 checkpoint？

不能把：

```text
agent_run row
```

直接称为 checkpoint。

至少分析：

```text
AgentRun
+
current_step
+
execution status
+
next action
+
pending approval
+
required runtime state
```

是否足以构成 checkpoint。

必须明确：

```text
AgentRun persistence ≠ checkpoint persistence
```

---

# 九、最小恢复状态

设计：

```text
AgentCheckpointState
```

但：

**只作为架构 DTO / 文档概念，不要实现 Python class。**

分析它未来至少可能需要：

```text
run_id
step_index
state
next_action
pending_tool_call_id
pending_approval_id
```

但不要默认这些字段全部必须存在。

逐项判断：

```text
required
optional
derived
not persisted
```

---

# 十、Prompt / Context 是否进入 Checkpoint

必须明确：

### 禁止直接持久化：

```text
system prompt
user prompt
RAG chunks
full conversation context
tool definitions
SQL
tool result full body
API key
authorization
database URL
```

如果 Resume 需要其中某些信息：

必须设计：

```text
reference
ID
reconstructable state
```

而不是直接把大文本塞入 checkpoint。

---

# 十一、Tool Result 的恢复语义

这是本阶段最重要的问题之一。

假设：

```text
Step 1
LLM → get_inventory
Tool → SUCCESS
DB commit
Worker crash
```

Resume 时：

### Option A

重新执行 Tool。

### Option B

读取历史 Tool Result。

### Option C

要求 Tool execution record 成为 replay source。

必须比较：

```text
重复执行风险
幂等性
数据新鲜度
成本
安全
实现复杂度
```

最后给出明确选择。

不能写：

```text
以后再决定。
```

如果当前阶段不适合做最终选择：

必须明确：

```text
Decision Deferred
```

并说明：

```text
什么前置条件满足后才能决定
```

---

# 十二、LLM Decision 的恢复语义

同样分析：

```text
LLM call
→ TOOL_CALL
→ Tool execution
→ crash
```

Resume 是否重新请求 LLM？

必须明确：

```text
是否保存 decision
是否 replay decision
是否重新生成 decision
```

尤其注意：

LLM 是非确定性的。

如果 Resume 重新调用 LLM：

```text
same state
→ potentially different decision
```

必须记录这个事实。

---

# 十三、HITL Contract

本阶段只定义语义。

未来：

```text
WAITING_APPROVAL
```

至少需要：

```text
approval_id
run_id
step_index
requested_action
status
created_at
resolved_at
decision
```

但：

**不要实现数据库表。**

---

# 十四、HITL 状态

必须设计：

```text
PENDING
APPROVED
REJECTED
EXPIRED
CANCELLED
```

并明确：

```text
APPROVED
    ↓
resume

REJECTED
    ↓
FAILED / CANCELLED / business decision
```

不能在本阶段实现 UI/API。

---

# 十五、Approval 与 Tool Execution 的边界

非常重要。

未来：

```text
LLM proposes tool
        ↓
Authorization
        ↓
Approval required?
        ↓
YES
        ↓
WAITING_APPROVAL
        ↓
Human decision
        ↓
APPROVED
        ↓
Tool Execution
```

不要变成：

```text
Tool already executed
↓
ask human
```

如果当前系统存在潜在冲突，记录为：

```text
Architecture issue
```

不要修改生产逻辑。

---

# 十六、Cancellation

只定义语义。

未来：

```text
RUNNING
    ↓
CANCEL REQUEST
    ↓
CANCELLED
```

必须分析：

### 如果当前 Tool 正在执行：

```text
cancel
```

是否：

```text
interrupt tool
```

还是：

```text
等待 tool 返回后不再继续
```

必须明确当前阶段选择。

---

# 十七、Concurrency

必须分析：

```text
Resume(run_id)
```

同时被两个请求调用怎么办？

例如：

```text
worker A → resume(run-1)
worker B → resume(run-1)
```

必须明确未来需要：

```text
lock
optimistic concurrency
claim token
state version
```

中的哪一种。

不要实现。

但必须指出：

> Resume 没有并发控制就不能安全上线。

---

# 十八、Idempotency

区分：

```text
Conversation Idempotency-Key
```

和：

```text
Resume Idempotency
```

它们不是同一个东西。

必须定义未来：

```text
same approval response twice
same resume request twice
```

应该发生什么。

---

# 十九、AgentRun 与 AgentStep 关系

最终必须明确：

```text
AgentRun
   │
   └── AgentStep*
```

还是：

```text
AgentRun
   │
   ├── AgentStep observation
   └── separate checkpoint
```

或者：

```text
AgentRun
   │
   ├── terminal record
   └── AgentExecutionState
```

必须选择最终架构。

---

# 二十、与现有 Observation 的关系

必须保持：

```text
LLMUsageRecord
    = LLM-level observation

ToolExecutionRecord
    = Tool-level observation

AssistantOutcomeRecord
    = Assistant-level outcome

AgentRun
    = Agent-run-level record
```

未来：

```text
AgentStep
```

不能简单复制：

```text
LLMUsageRecord
ToolExecutionRecord
```

它应该表达：

> Agent runtime execution state / transition。

---

# 二十一、与 Evidence 的关系

继续保持：

```text
AgentRun ≠ Evidence
AgentStep ≠ Evidence
ToolResult ≠ Evidence
```

不要因为未来需要恢复上下文，就把 Tool Result 自动变成 Evidence。

---

# 二十二、与 Conversation 的关系

继续：

```text
Conversation
    owns:
        Turn
        Idempotency
        Context
```

Agent 不拥有：

```text
ConversationTurn
Idempotency-Key
ConversationContext
```

未来 Resume 只通过明确的：

```text
run_id
```

恢复 Agent Execution。

不要让 Agent Runtime 自己查询 Conversation。

---

# 二十三、需要输出的架构产物

本阶段必须产生一个新的 Decision 文档：

```text
docs/decisions/Phase/Phase 5 — Agent State & Resume Boundary Decision.md
```

采用：

```text
append-only
```

不要修改已经冻结的：

```text
Phase 5 — Scope & Architecture Decision.md
```

---

# 二十四、Decision 文档必须包含

至少：

```text
1. Context
2. Current State
3. AgentRun Definition
4. AgentStep Definition
5. Terminal States
6. Paused States
7. Resume Contract
8. Checkpoint Definition
9. Minimum Recoverable State
10. Tool Replay Semantics
11. LLM Decision Replay Semantics
12. HITL Contract
13. Approval State Machine
14. Cancellation Semantics
15. Concurrency Model
16. Resume Idempotency
17. Conversation Relationship
18. Observation Relationship
19. Evidence Relationship
20. Security Boundary
21. Persistence Boundary
22. Deferred Decisions
23. Migration Path
24. Final Decision
```

---

# 二十五、必须画两张图

## 图 1：Agent State Machine

```text
RUNNING
   │
   ├── COMPLETED
   ├── FAILED
   ├── LIMIT_REACHED
   ├── CANCELLED
   └── WAITING_APPROVAL
```

根据最终设计修改。

---

## 图 2：Persistence Boundary

必须清晰表达：

```text
Conversation
    │
    ├── Turn
    ├── Idempotency
    └── Context

Agent Runtime
    │
    ├── in-memory execution state
    ├── AgentRun
    └── future AgentStep / Checkpoint

LLM Observation
    └── LLMUsageRecord

Tool Observation
    └── ToolExecutionRecord

Assistant Outcome
    └── AssistantOutcomeRecord

Evidence
    └── ConversationEvidence
```

---

# 二十六、禁止修改

本阶段禁止修改：

```text
AgentRuntime
AgentRun
AgentRunRepository
AgentRunRecorder
AIOrchestrator
Conversation
ConversationTurn
Idempotency
Evidence
LLMUsageRecord
ToolExecutionRecord
AssistantOutcomeRecord
Tool Registry
Text-to-SQL
RAG
Prompt
Provider
API
Database Schema
```

只允许：

```text
新增 Decision 文档
```

如果发现代码与架构 Decision 不一致：

**只报告，不修改。**

---

# 二十七、验证

只执行静态检查，不执行 DB 写入：

```powershell
git status --short
git diff --check
python -m compileall -q backend tests scripts
```

如果发现当前代码与 Decision 存在冲突：

记录：

```text
Architecture Drift
```

不要修。

---

# 二十八、最终报告

完成后严格输出：

```text
【Phase 5 AGENT STATE / RESUME BOUNDARY DECISION COMPLETE】

1. 新增文件
2. AgentRun Definition
3. AgentStep Definition
4. Terminal States
5. Paused States
6. Resume Contract
7. Checkpoint Definition
8. Tool Replay Semantics
9. LLM Replay Semantics
10. HITL Contract
11. Cancellation
12. Concurrency
13. Resume Idempotency
14. Conversation Relationship
15. Observation Relationship
16. Evidence Relationship
17. Persistence Boundary
18. Architecture Drift
19. git diff --check
20. compileall

最终状态：

AgentRun Persistence = READY
AgentStep Persistence = [NOT STARTED]
Resume = [NOT STARTED]
Checkpoint = [NOT STARTED]
HITL = [NOT STARTED]
Cancellation = [NOT STARTED]

STOP
```

---

# 二十九、强制 STOP

完成后：

**不要创建 AgentStep 表。**

不要：

```text
AgentStep ORM
AgentStep Repository
Checkpoint Repository
Resume API
HITL API
Approval API
Cancel API
Agent Timeline API
```

不要进入：

```text
Phase 5 AgentStep Persistence
```

除非下一步明确授权。

**本阶段只冻结架构，不写生产代码。**
