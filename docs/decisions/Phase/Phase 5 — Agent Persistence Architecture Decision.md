你现在开始执行：

# Phase 5 — Agent Persistence Architecture Decision

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、目标

当前 Phase 5 Agent Runtime 已经完成：

```text
Agent Core Loop
AgentLLM Adapter
DeepSeek Tool Calling
Agent Authorization
Agent → Router
Agent → Orchestrator
Conversation Context
```

当前 Agent 状态仍然：

```text
IN-MEMORY ONLY
```

本阶段只解决一个架构问题：

> **AgentRun / AgentStep 是否需要持久化，以及应该如何进入现有 ai_ops 数据模型。**

本阶段：

```text
只做 Architecture Decision
不写生产代码
不建数据库表
不修改 API
不修改 Agent Runtime
不修改 Conversation
不修改 Evidence
不修改 Observability
```

---

# 二、先阅读真实代码

必须先阅读：

```text
backend/app/agent/
backend/app/llm/
backend/app/services/ai_orchestrator_service.py
backend/app/services/chat_application_service.py
backend/app/services/tool_execution_service.py
backend/app/db/
backend/app/db/models/
```

重点检查：

```text
AgentRuntimeResult
AgentRun
AgentStep
AgentRuntimePolicy
AgentDecision
AgentCapability
```

以及：

```text
ai_ops
conversation
conversation_turn
assistant_outcome_record
llm_usage_record
tool_execution_record
rag_execution_record
```

同时阅读：

```text
docs/decisions/Phase/Phase 5 — Scope & Architecture Decision.md
docs/architecture.md
```

---

# 三、必须回答的问题

最终必须明确：

```text
1. AgentRun 是否持久化？
2. AgentStep 是否持久化？
3. 如果持久化，谁拥有这些记录？
4. Conversation 与 AgentRun 是什么关系？
5. request_id / run_id / turn_id 如何关联？
6. ToolExecutionRecord 是否继续作为 Tool 级记录？
7. LLMUsageRecord 是否继续作为 LLM 级记录？
8. AgentRun 是否成为新的 execution aggregate？
9. AgentStep 是否应该成为新的 domain entity？
10. Agent 状态恢复是否属于本阶段？
```

---

# 四、首先确认一个关键原则

不要把：

```text
AgentRun
```

设计成：

```text
ConversationTurn
```

两者必须继续保持不同职责。

建议分析：

```text
ConversationTurn
    = 用户可见消息

AgentRun
    = 一次 Agent 执行生命周期

AgentStep
    = AgentRun 内的一次逻辑执行步骤
```

例如：

```text
ConversationTurn
    ↓
AIOrchestrator
    ↓
AgentRun
    ├── Step 1 LLM Decision
    ├── Step 2 Tool Call
    ├── Step 3 LLM Final
    └── Completed
```

---

# 五、Conversation Ownership

必须保持：

```text
ConversationRuntime
    owns:
        Conversation
        ConversationTurn
        Idempotency
        Conversation Context
```

Agent：

```text
AgentRuntime
    owns:
        AgentRun
        AgentStep
        Agent Policy
        Agent Loop
```

不要让：

```text
AgentRuntime
```

创建：

```text
ConversationTurn
```

也不要让：

```text
AgentRuntime
```

处理：

```text
Idempotency-Key
```

---

# 六、AgentRun 与 Conversation 的关系

分析以下三种方案：

## Option A

```text
AgentRun
    conversation_id FK
    turn_id FK
```

## Option B

```text
AgentRun
    request_id
    conversation_id
```

不直接 FK Turn。

## Option C

```text
AgentRun
    request_id
```

完全独立于 Conversation。

比较：

```text
ownership
correlation
lifecycle
idempotency
replay
retention
deletion
```

最终选择一个方案。

特别注意：

已有：

```text
assistant_request_id
request_id
```

不能因为 Agent Persistence 就改变之前已经冻结的：

```text
ConversationTurn
assistant_request_id
```

语义。

---

# 七、request_id / run_id / turn_id

必须画清楚：

```text
conversation_id
        │
        ▼
conversation_turn.turn_id
        │
        │
        └────── request_id
                    │
                    ▼
                 AgentRun
                    │
                    ▼
                 run_id
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
      AgentStep           AgentStep
```

但：

**不要直接接受上面的图。**

根据实际代码判断。

必须明确：

### request_id

用途：

```text
一次 AI Runtime 请求的 correlation
```

### run_id

用途：

```text
一次 Agent Runtime 执行的 identity
```

### turn_id

用途：

```text
Conversation message identity
```

三者不得混成一个 ID。

---

# 八、是否需要 AgentRun Table

分析：

```text
ai_ops.agent_run
```

是否必要。

至少比较：

### Option A

```text
ai_ops.agent_run
ai_ops.agent_step
```

### Option B

只：

```text
ai_ops.agent_run
```

Step 使用：

```text
ai_ops.observation
```

### Option C

完全不持久化 Agent Step，只持久化已有：

```text
tool_execution_record
llm_usage_record
assistant_outcome_record
```

分析：

```text
recovery
audit
debugging
HITL
future cancellation
future resume
```

对三种方案进行架构比较。

---

# 九、不要重复已有 Observability

特别检查现有：

```text
llm_usage_record
tool_execution_record
```

如果已经可以表达：

```text
一次 LLM 调用
一次 Tool 调用
```

那么：

**不要在 AgentStep 里复制完整的 LLM / Tool payload。**

AgentStep 应该表达：

```text
Agent logical step
```

而不是：

```text
another LLMUsageRecord
another ToolExecutionRecord
```

建议考虑：

```text
AgentStep
    ↓
step_type
step_index
status
decision_source
llm_usage_record_id?
tool_execution_record_id?
```

但：

**不要直接采用。**

先根据实际现有模型判断。

---

# 十、AgentStep 是否必须持久化

分析：

```text
AgentStep
```

是否属于：

```text
domain state
```

还是：

```text
observability event
```

必须特别讨论：

```text
WAITING_APPROVAL
CANCELLED
LIMIT_REACHED
FAILED
```

因为未来：

```text
HITL
resume
cancel
```

都可能需要 Step State。

如果最终结论：

```text
AgentStep 必须持久化
```

说明原因。

如果结论：

```text
MVP 暂不持久化
```

也必须说明未来迁移路径。

---

# 十一、Agent State 与 Observation 的边界

必须明确：

```text
Agent State
≠
Observability
```

Agent State 可能包括：

```text
run status
current step
tool call pending
approval pending
policy limits
```

Observation 包括：

```text
LLM call
Tool execution
latency
usage
```

不能因为已有 Observation 表，就直接认为：

```text
Agent State 已经解决
```

---

# 十二、是否支持 Resume

本阶段必须明确：

```text
Agent Persistence
    ≠
Agent Resume
```

即使最终决定持久化：

也不代表本阶段实现：

```text
resume
retry from step
checkpoint
```

分析未来：

```text
AgentRun
    ↓
WAITING_APPROVAL
    ↓
persist
    ↓
future resume
```

但：

**本阶段不要实现 resume。**

---

# 十三、失败与 LIMIT_REACHED

必须保持已有 Phase 5 语义：

```text
FAILED
LIMIT_REACHED
CANCELLED
WAITING_APPROVAL
```

尤其：

```text
LIMIT_REACHED != FAILED
```

不要因为 persistence 设计改变这个语义。

如果当前 Orchestrator 映射：

```text
LIMIT_REACHED
→ outcome=FAILED
```

记录为当前既有行为。

本阶段不要修改。

---

# 十四、Idempotency

必须明确：

```text
Idempotency-Key
```

仍由：

```text
ConversationRuntime
```

负责。

AgentRun 不负责：

```text
Idempotency-Key
```

但 AgentRun 必须能够通过：

```text
request_id
```

关联到当前 AI Runtime request。

分析：

```text
duplicate request
    ↓
ConversationRuntime replay
    ↓
Agent 不再次执行
```

这个原则必须保持。

---

# 十五、Evidence Boundary

必须保持：

```text
AgentRun
    ≠
Evidence
```

以及：

```text
Tool Result
    ≠
Evidence
```

本阶段不要把：

```text
AgentRun
AgentStep
ToolResult
```

直接写入 Evidence。

如果未来 Agent 产生：

```text
Evidence Candidate
```

保留为未来扩展点。

---

# 十六、Database Schema

如果最终决定增加：

```text
ai_ops.agent_run
ai_ops.agent_step
```

必须只做：

```text
Architecture Decision
```

暂时不要：

```text
CREATE TABLE
ALTER TABLE
migration
ORM
repository
```

只设计：

```text
字段
PK
FK
UNIQUE
INDEX
status
timestamps
```

并说明：

```text
为什么需要
谁写
谁读
生命周期
删除策略
```

---

# 十七、Retention / Deletion

必须至少讨论：

```text
Conversation deleted
AgentRun 是否删除？
AgentStep 是否删除？
LLMUsageRecord 是否删除？
ToolExecutionRecord 是否删除？
```

注意：

Phase 4.1 已经明确：

```text
Evidence
```

与：

```text
Conversation
```

不是 ownership。

因此：

**不要自动套用 CASCADE。**

必须分别说明：

```text
ownership
reference
retention
```

---

# 十八、安全边界

AgentRun / AgentStep 禁止持久化：

```text
API Key
Authorization
Password
Database URL
System Prompt
完整 User Prompt
完整 Conversation Context
完整 Tool Result
SQL
HTTP Headers
```

除非未来有单独的安全设计。

本阶段只保留：

```text
metadata
state
references
```

---

# 十九、成本 / Token

现有：

```text
LLMUsageRecord
```

已经负责：

```text
prompt_tokens
completion_tokens
total_tokens
```

不要在 AgentRun 中复制 token 计费字段。

AgentRun 可以记录：

```text
total_llm_calls
total_tool_calls
```

但是否需要必须分析。

---

# 二十、Open Questions

如果发现新的问题：

只新增：

```text
OQ-P5-xx
```

不要直接编码解决。

重点关注：

```text
AgentRun persistence
AgentStep persistence
run summary
state recovery
cancellation
HITL
retention
```

---

# 二十一、必须产出

只允许：

```text
docs/decisions/Phase/Phase 5 — Scope & Architecture Decision.md
```

追加：

```text
§30 Agent Persistence Architecture Decision
```

必须包含：

```text
Problem
Current State
Options
Decision
Ownership
Correlation
State Model
AgentRun
AgentStep
Observability Boundary
Conversation Boundary
Evidence Boundary
Idempotency Boundary
Retention
Security
Future Resume
Open Questions
Migration Path
```

---

# 二十二、禁止修改

本阶段禁止修改：

```text
backend/
tests/
database
API
Prompt
Tool
Router
Orchestrator
Agent Runtime
AgentLLM
Provider
Evidence
Conversation
Observation
Baseline
Dataset
```

唯一允许修改：

```text
docs/decisions/Phase/Phase 5 — Scope & Architecture Decision.md
```

---

# 二十三、验证

因为只改文档：

检查：

```text
git diff --check
```

以及：

```text
git status --short
```

确认：

```text
production code = 0
DB schema = 0
API = 0
Prompt = 0
tests = 0
```

不要为了验证重新跑完整 pytest。

---

# 二十四、Commit

如果文档 Decision 完成：

```text
docs: close Phase 5 agent persistence architecture decision
```

不要：

```text
push
PR
merge
```

---

# 二十五、最终报告

严格：

```text
【Phase 5 AGENT PERSISTENCE ARCHITECTURE DECISION COMPLETE】

1. Current State
2. Decision
3. AgentRun Ownership
4. AgentStep Ownership
5. Conversation Relationship
6. request_id / run_id / turn_id
7. Observability Relationship
8. Evidence Relationship
9. Idempotency
10. State Model
11. Retention
12. Security
13. Resume Boundary
14. Database Schema Decision
15. Open Questions
16. Migration Path
17. Modified Files
18. Production Changes
19. git diff --check

Phase 5 Agent Persistence Decision = COMPLETE

STOP
```

---

# 二十六、强制 STOP

完成后立即停止。

不要进入：

```text
Agent Persistence Implementation
Agent Observability
HITL
Streaming
Memory
Planning
Multi-Agent
LangGraph
MCP
```

下一阶段必须等待新的明确指令。
