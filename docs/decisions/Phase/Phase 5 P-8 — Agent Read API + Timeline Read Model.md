# Phase 5 P-8 — Agent Read API + Timeline Read Model

## 一、阶段目标

在 Phase 5 P-7 HITL + Cooperative Cancellation 完成之后：

建立 Agent Runtime 的**只读观察面**。

本阶段只解决：

```text
已经存在的 Agent Runtime 状态
        ↓
安全读取
        ↓
统一 Agent Run View
        ↓
Timeline
        ↓
Read API
```

目标不是增加 Agent 能力。

目标是：

> **让外部应用可以安全地知道一个 Agent Run 当前发生了什么。**

当前已有：

```text
AgentRun
AgentStep
AgentDecision
AgentToolResult
AgentCheckpoint
AgentApproval
ToolExecutionRecord
LLMUsageRecord
```

P-8 不重新设计这些持久化结构。

---

# 二、P-8 完成后的架构

最终形成：

```text
                    Agent Runtime
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
    AgentRun        AgentStep       AgentCheckpoint
        │                │                │
        ▼                ▼                ▼
 AgentDecision     AgentToolResult   AgentApproval
        │                │                │
        └────────────────┼────────────────┘
                         ▼
                Agent Read Service
                         │
              ┌──────────┴──────────┐
              │                     │
              ▼                     ▼
        AgentRunSummary          Timeline
              │                     │
              └──────────┬──────────┘
                         ▼
                   Read API
```

**Read API 只读。**

不得通过 Read API 修改：

```text
AgentRun
AgentStep
AgentCheckpoint
AgentDecision
AgentToolResult
AgentApproval
Conversation
Evidence
```

---

# 三、开始前必须阅读

先阅读真实代码：

```text
backend/app/agent/
backend/app/db/models/agent_run.py
backend/app/db/models/agent_step.py
backend/app/db/models/agent_decision.py
backend/app/db/models/agent_tool_result.py
backend/app/db/models/agent_checkpoint.py
backend/app/db/models/agent_approval.py

backend/app/db/agent_run_repository.py
backend/app/db/agent_step_repository.py
backend/app/db/agent_decision_repository.py
backend/app/db/agent_tool_result_repository.py
backend/app/db/agent_checkpoint_repository.py
backend/app/db/agent_approval_repository.py

backend/app/services/ai_orchestrator_service.py

backend/app/api/
tests/
docs/architecture.md
```

同时搜索：

```text
AgentRun
AgentStep
AgentDecision
AgentToolResult
AgentCheckpoint
AgentApproval
request_id
run_id
```

确认当前真实 DTO、Repository、API 结构。

**不要根据任务书猜接口。**

---

# 四、严格范围

## 允许

允许新增：

```text
Agent Read Service
Agent Timeline DTO
Agent Run View DTO
Read-only Repository 查询方法
Read-only API
测试
架构文档
```

允许修改：

```text
backend/app/api/
backend/app/services/
backend/app/db/*repository.py
docs/architecture.md
tests/
```

如果项目当前 API 结构要求新增 router，可以新增最小 router。

---

# 五、严格禁止

本阶段禁止修改：

```text
AgentRuntime 核心 Loop
Agent Decision Contract
ToolExecutionService
ToolDefinition
Tool Registry
TextToSQL
RAG
Router 策略
Conversation Runtime
Conversation Turn
Idempotency
Evidence
LLM Provider
Prompt
AgentCheckpoint 状态机
AgentApproval 状态机
Cancellation 状态机
```

禁止新增：

```text
Streaming
WebSocket
SSE
Background Agent
Workflow Runtime
Multi-Agent
LangGraph
MCP
A2A
Memory
Event Bus
Kafka
Redis
```

禁止：

```text
Read API → 修改状态
Read API → Resume
Read API → Approve
Read API → Cancel
```

这些动作继续走现有内部 Application Service。

---

# 六、核心设计原则

P-8 必须坚持：

> **Command 与 Query 分离。**

```text
Command
  ├── execute
  ├── resume
  ├── approve
  └── cancel

Query
  ├── get run
  ├── get steps
  ├── get timeline
  └── get status
```

Read API：

```text
Query only
```

不得产生：

```text
LLM call
Tool call
DB write
Conversation mutation
Checkpoint mutation
Approval mutation
Cancellation mutation
```

---

# 七、AgentRunSummary

新增内部只读 DTO。

例如：

```text
AgentRunSummary
```

建议至少包含：

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
```

注意：

**只暴露现有 AgentRun 已经拥有的数据。**

不要为了 Read API 修改 AgentRun schema。

---

# 八、状态来源

状态必须来自：

```text
AgentRun
+
AgentCheckpoint
```

不要重新推导状态。

尤其：

```text
AgentRun.status
```

仍然是 Run 生命周期状态。

Checkpoint：

```text
where to resume
```

不要把：

```text
Checkpoint.next_action
```

当成：

```text
AgentRun.status
```

---

# 九、Checkpoint View

可以提供内部：

```text
AgentCheckpointView
```

建议：

```text
run_id
state_version

status
next_action

pending_step_index
pending_tool_call_id
pending_decision_ref
pending_tool_result_ref

cancel_requested
cancel_requested_at

updated_at
```

但：

**不要暴露 claim_token。**

绝对禁止：

```text
claim_token
```

进入 API Response。

---

# 十、Approval View

P-7 已经定义：

```text
AgentApprovalRequest
```

Read API 可以暴露一个安全投影：

```text
AgentApprovalView
```

允许：

```text
approval_id
run_id
step_index
tool_call_id
tool_name
status
decision
reason_code
created_at
resolved_at
```

禁止：

```text
arguments
prompt
messages
context
RAG chunks
SQL
credentials
Tool Result
raw LLM response
exception body
```

Approval 本身是控制状态，不是 Tool Result。

---

# 十一、Decision View

可以提供：

```text
AgentDecisionView
```

但必须严格控制。

建议只暴露：

```text
step_index
decision_type
tool_call_id
tool_name
finish_reason
created_at
decision_unavailable
```

禁止：

```text
arguments
content
```

默认尤其不要返回：

```text
LLM content
```

因为 Decision Store 的内容属于 Runtime Recovery 数据，而不是 UI Timeline 数据。

如果现有安全投影已经定义，则优先复用。

---

# 十二、Tool Result View

可以提供：

```text
AgentToolResultView
```

建议只暴露：

```text
step_index
tool_name
success
error_code
result_hash
result_size_bytes
result_truncated
result_unavailable
created_at
```

默认：

**不要直接返回 result_payload。**

原因：

Tool Result 是：

```text
UNTRUSTED DATA
```

而且可能：

```text
包含敏感信息
包含大量数据
包含 Prompt Injection
```

如果未来需要查看完整 Tool Result：

应该单独设计 Artifact / Result Detail 能力。

本阶段不做。

---

# 十三、Timeline

新增：

```text
AgentTimelineEvent
```

Timeline 是：

> **只读展示模型。**

不是新的持久化事件系统。

不要新增：

```text
agent_timeline_event
```

数据库表。

---

# 十四、Timeline Event 类型

至少支持：

```text
RUN_STARTED
STEP_STARTED
DECISION_RECORDED
APPROVAL_REQUIRED
APPROVAL_RESOLVED
TOOL_EXECUTED
STEP_COMPLETED
RUN_COMPLETED
RUN_FAILED
RUN_CANCELLED
RUN_LIMIT_REACHED
```

如果当前持久化数据不足以可靠生成某种事件：

**不要伪造。**

可以不返回该事件。

---

# 十五、Timeline 来源

Timeline 必须由现有数据读取组合而来：

```text
AgentRun
AgentStep
AgentDecision
AgentToolResult
AgentApproval
AgentCheckpoint
```

例如：

```text
AgentRun
    ↓
RUN_STARTED

AgentStep
    ↓
STEP_STARTED / STEP_COMPLETED

AgentDecision
    ↓
DECISION_RECORDED

AgentApproval
    ↓
APPROVAL_REQUIRED / APPROVAL_RESOLVED

AgentToolResult
    ↓
TOOL_EXECUTED

AgentRun terminal state
    ↓
RUN_COMPLETED / FAILED / CANCELLED / LIMIT_REACHED
```

---

# 十六、Timeline 不允许制造不存在的事实

例如：

数据库只有：

```text
AgentStep(step_index=1)
```

不能自动推导：

```text
LLM_REQUEST_STARTED
LLM_REQUEST_FINISHED
```

因为当前数据库没有这些事实。

Timeline 只表达：

> 当前持久化数据能够证明的事实。

---

# 十七、Timeline 排序

统一使用：

```text
occurred_at
```

如果不同实体没有完全一致的时间精度：

使用：

```text
timestamp
+
stable tie-breaker
```

保证：

```text
同一 Run
→
Timeline 顺序 deterministic
```

不得依赖 Python：

```text
dict
set
DB natural order
```

---

# 十八、Timeline 不包含敏感内容

禁止 Timeline 返回：

```text
API Key
Authorization
Password
Database URL
SQL
Prompt
System Prompt
RAG Chunk
完整 Tool Result
LLM Raw Response
Stack Trace
Headers
```

即使数据库存在这些信息：

**也不能进入 Read API DTO。**

---

# 十九、API

如果当前项目已有：

```text
/api/
```

增加最小 Read API。

建议：

```http
GET /api/agent/runs/{run_id}
```

返回：

```text
AgentRunSummary
```

以及：

```http
GET /api/agent/runs/{run_id}/timeline
```

返回：

```text
AgentTimelineEvent[]
```

如果项目已有 API versioning：

遵循当前项目：

```text
/api/v1/...
```

不要自行创建第二套版本体系。

---

# 二十、Pagination

Timeline 必须考虑分页。

第一版可以：

```text
limit
offset
```

或者如果项目已有 cursor pagination：

直接复用。

建议：

```text
limit <= 100
```

必须：

```text
server-side cap
```

禁止：

```text
limit=1000000
```

拖垮数据库。

如果不需要分页也不要返回无限数据。

---

# 二十一、Project Isolation

Read API 必须验证：

```text
run_id
project_id
```

不能允许：

```text
project A
```

读取：

```text
project B AgentRun
```

至少服务层必须提供：

```text
get_run(run_id, project_id)
```

而不是：

```text
get_run(run_id)
```

然后在 API 层再猜 project。

---

# 二十二、权限边界

P-8 不重新实现完整 RBAC。

但必须留下：

```text
project-scoped query boundary
```

即：

```text
current project
    ↓
Agent Read Service
    ↓
run.project_id == current project
```

如果当前 API 尚无认证上下文：

使用现有 project_id 注入方式。

不要为了 P-8 新建：

```text
User
Role
Permission
JWT
OAuth
RBAC
```

Enterprise Governance 留到 Phase 7。

---

# 二十三、404 / 403

至少区分：

### Run 不存在

```text
404
```

### Run 存在但不属于当前 project

不要泄露其存在性。

可以统一：

```text
404
```

避免跨 Project enumeration。

### 参数非法

```text
400
```

### limit 超过最大值

```text
400
```

具体 HTTP error contract 遵循现有 API 风格。

---

# 二十四、Read Service

建议新增：

```text
backend/app/services/agent_read_service.py
```

负责：

```text
get_run()
get_checkpoint()
get_approval()
get_timeline()
```

但：

**Service 不负责 HTTP。**

API：

```text
API
 ↓
AgentReadService
 ↓
Repository
```

---

# 二十五、Repository

优先复用现有 Repository。

如果缺查询能力：

只增加：

```text
read-only query method
```

例如：

```text
list_steps(run_id)
list_decisions(run_id)
list_tool_results(run_id)
list_approvals(run_id)
```

禁止：

```text
update
delete
create
```

出现在 Read API 调用链中。

---

# 二十六、性能

Timeline 查询至少避免：

```text
N+1 query
```

例如：

不要：

```text
100 steps
→ 100 次 DB query
```

可以：

```text
1 × AgentRun
1 × AgentStep
1 × AgentDecision
1 × AgentToolResult
1 × AgentApproval
1 × AgentCheckpoint
```

然后内存 merge。

P-8 不做复杂 SQL 优化。

---

# 二十七、Read API 不触发 Runtime

非常重要：

```text
GET /agent/runs/{id}
```

不能：

```text
resume
execute
LLM
Tool
checkpoint mutation
approval
cancel
```

它只是：

```text
DB read
→ DTO
→ response
```

---

# 二十八、Tests

新增：

```text
tests/test_agent_read_service.py
tests/test_agent_read_api.py
tests/test_agent_timeline.py
```

至少覆盖：

## Run

```text
existing run
missing run
cross-project run
```

## Checkpoint

```text
ACTIVE
PAUSED
TERMINAL
CANCELLED
```

## Approval

```text
PENDING
APPROVED
REJECTED
```

## Timeline

```text
RUN_STARTED
STEP
DECISION
APPROVAL
TOOL
RUN_TERMINAL
```

## Security

确认：

```text
prompt absent
SQL absent
credentials absent
raw response absent
claim_token absent
tool result body absent
```

---

# 二十九、Pagination Tests

测试：

```text
limit=1
limit=10
limit=100
limit=101 → reject
limit=0 → reject
negative → reject
```

如果使用 cursor：

按项目现有 cursor 规范测试。

---

# 三十、Project Isolation Tests

至少：

```text
project-A run
project-B run
```

验证：

```text
A can read A
A cannot read B
B can read B
B cannot read A
```

必须在：

```text
Service
API
```

至少一层真正验证。

---

# 三十一、Read API 不修改数据库

使用 DB integration：

请求：

```text
GET run
GET timeline
```

前后检查：

```text
AgentRun
AgentStep
AgentDecision
AgentToolResult
AgentCheckpoint
AgentApproval
```

行数：

```text
before == after
```

---

# 三十二、Regression

必须保证：

```text
P-6 Resume
P-7 HITL
P-7 Cancellation
Tool Replay
Decision Replay
```

全部不回归。

特别：

```text
PENDING Approval
→ Read API
```

不能改变状态。

```text
CANCELLED Run
→ Read API
```

不能变成 ACTIVE。

---

# 三十三、禁止新增 Timeline DB

明确：

```text
NO agent_timeline table
NO event_store
NO event sourcing
```

Timeline 是：

```text
Read Model
```

不是新的 Source of Truth。

---

# 三十四、禁止 UI

本阶段：

```text
NO Vue page
NO Agent Console
NO Timeline UI
NO WebSocket
NO SSE
```

API 完成即可。

---

# 三十五、禁止 Streaming

不要因为 Timeline 存在就做：

```text
stream timeline
```

Streaming 属于后续阶段。

---

# 三十六、测试命令

非 DB：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

Compile：

```powershell
python -m compileall backend tests scripts
```

Lint：

```text
继续使用当前项目已有 lint
```

---

# 三十七、Git Audit

完成后：

```powershell
git status --short
git diff --stat
git diff --check
git diff
```

重点确认：

```text
AgentRuntime loop = unchanged
Tool Framework = unchanged
Conversation = unchanged
Evidence = unchanged
LLM Provider = unchanged
Prompt = unchanged
Validator = unchanged
Executor = unchanged
AgentCheckpoint lifecycle = unchanged
AgentApproval lifecycle = unchanged
Cancellation semantics = unchanged
```

---

# 三十八、DB Residue

定向 DB 测试后检查：

```text
agent_run
agent_step
agent_decision
agent_tool_result
agent_checkpoint
agent_approval
tool_execution_record
llm_usage_record
assistant_outcome_record
rag_execution_record
```

要求：

```text
test residue = 0
```

---

# 三十九、提交

如果全部通过：

```powershell
git add backend/app/agent backend/app/db backend/app/services backend/app/api tests docs/architecture.md
git commit -m "feat: Phase 5 agent read API and timeline"
```

不要 push。

不要 PR。

不要 merge。

不要 rebase。

---

# 四十、最终报告

严格按照：

```text
【Phase 5 P-8 COMPLETE】

1. 新增文件
2. 修改文件
3. Agent Read Service
4. AgentRunSummary
5. Checkpoint View
6. Approval View
7. Timeline Read Model
8. API
9. Project Isolation
10. Security Boundary
11. Pagination
12. Tests
13. DB Tests
14. DB Residue
15. compile / lint
16. Regression
17. Git Diff
18. Commit
19. 当前限制
20. Architecture State
```

Architecture State：

```text
AgentRun                 READY
AgentStep                READY
AgentToolResult          READY
AgentDecision            READY
AgentCheckpoint          READY
AgentApproval            READY
Resume                   READY
HITL                     READY
Cancellation             READY
Agent Read API           READY
Agent Timeline           READY
```

---

# 四十一、P-8 完成后禁止自动进入

```text
Agent UI
Streaming
WebSocket
SSE
Background Agent
Workflow Runtime
Multi-Agent
LangGraph
MCP
A2A
Memory
```

P-8 完成后：

**立即 STOP。**

等待下一步架构决策。
