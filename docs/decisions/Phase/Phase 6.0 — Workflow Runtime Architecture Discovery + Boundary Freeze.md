# Phase 6.0 — Workflow Runtime Architecture Discovery + Boundary Freeze

你现在开始执行：

```text
Phase 6.0 — Workflow Runtime Architecture Discovery + Boundary Freeze
```

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段目标

本阶段：

> **只完成 Workflow Runtime 的架构勘察、边界定义、Source of Truth 设计和 Architecture Decision。**

**不要实现 Workflow Runtime。**

尤其禁止现在创建：

```text
WorkflowEngine
WorkflowRuntime
WorkflowExecutor
WorkflowScheduler
WorkflowWorker
WorkflowDefinition ORM
WorkflowRun ORM
WorkflowStep ORM
WorkflowCheckpoint ORM
```

本阶段不是 Coding Phase。

---

# 二、Phase 6 的核心定位

Phase 5 已经冻结：

```text
Agent Runtime
```

Phase 6 不得重新定义 Agent。

必须建立：

```text
Workflow Runtime
        │
        ├── deterministic control flow
        ├── branching
        ├── parallelism
        ├── retry policy
        ├── approval gate
        ├── long-running execution
        │
        └── Agent Step
                ↓
          Existing Agent Service API
```

核心区别：

```text
Agent Runtime

LLM decides:
    ↓
Tool
    ↓
LLM decides:
    ↓
Tool
    ↓
FINAL
```

而：

```text
Workflow Runtime

Developer-defined:
    ↓
Step A
    ↓
Condition
    ↓
Step B / Step C
    ↓
Parallel
    ↓
Approval Gate
    ↓
Agent Step
    ↓
Step D
```

也就是说：

> **Phase 5 是 model-driven execution。**

> **Phase 6 是 developer-defined execution。**

不要让 Workflow Runtime 变成：

```text
Agent Loop v2
```

---

# 三、外部架构参考

本阶段可以参考成熟 Workflow / Durable Execution 架构，但：

**不能直接复制第三方框架。**

参考原则：

确定性 Workflow 通常由代码定义顺序、分支、并行和错误处理；Agent Loop 则由模型决定下一步工具和结束条件。两者可以组合，但控制流责任必须清晰。

Workflow 中的 HITL 通常表现为：

```text
Workflow
    ↓
Approval Gate
    ↓
WAITING
    ↓
External Decision
    ↓
Resume
```

而不是让 Workflow 自己模拟一个 Agent Loop。

并行 Workflow 还需要明确：

```text
fan-out
    ↓
multiple branches
    ↓
join / fan-in
```

同步边界和 checkpoint 是 Workflow Runtime 的责任，而不是 Agent Runtime 的责任。

---

# 四、开始前必须完整阅读

不要假设当前项目结构。

首先阅读：

## Phase 5 冻结文档

搜索：

```text
docs/decisions/
```

重点找到：

```text
Phase 5 Agent Runtime
Phase 5 Architecture Frozen
Agent Runtime Exit Audit
HITL
Cancellation
Resume
Replay
Agent Read API
Agent Timeline
```

---

## AI Runtime

必须阅读：

```text
backend/app/services/ai_orchestrator_service.py
backend/app/services/ai_router_service.py

backend/app/agent/
```

重点理解：

```text
AgentRuntime
AgentLLM
AgentDecision
AgentRun
AgentStep
AgentCheckpoint
AgentApproval
AgentToolResult
Resume
Replay
Cancellation
```

---

## Tool Runtime

阅读：

```text
backend/app/services/tool_execution_service.py
backend/app/services/tool_registry_service.py
backend/app/tools/
```

确认：

```text
ToolExecutionService
ToolAuthorization
ToolExecutionContext
ToolExecutionObserver
ToolExecutionRecord
```

---

## Conversation Runtime

阅读：

```text
backend/app/services/conversation_runtime_service.py
backend/app/services/conversation_context_service.py
backend/app/db/models/conversation.py
backend/app/db/models/conversation_turn.py
```

确认：

```text
Conversation
ConversationTurn
Idempotency
Context
```

的真实 ownership。

---

## Persistence

阅读：

```text
backend/app/db/models/
backend/app/db/*repository*
```

特别搜索：

```text
AgentRun
AgentStep
AgentDecision
AgentCheckpoint
AgentApproval
AgentToolResult
```

---

## API

阅读：

```text
backend/app/api/
```

特别确认：

```text
Agent Read API
Conversation API
AI API
```

当前入口。

---

## Scheduler / Background

搜索整个项目：

```text
scheduler
schedule
background
worker
job
queue
task
cron
celery
apscheduler
```

不要假设项目已经存在 Scheduler。

---

## Tests

重点搜索：

```text
agent
resume
replay
approval
cancel
checkpoint
timeline
workflow
background
scheduler
```

---

# 五、Phase 6.0 严格禁止

本阶段禁止修改：

```text
Agent Runtime
Agent Decision Contract
Agent Tool Authorization
Agent Tool Execution
Agent Replay
Agent Resume
Agent HITL
Agent Cancellation
Agent Read API
Agent Timeline
Conversation Runtime
TextToSQL
RAG
Tool Framework
Router
Prompt
LLM Provider
```

禁止：

```text
LangGraph
MCP
Multi-Agent
Agent Loop 改造
Streaming
SSE
WebSocket
UI
Scheduler 实现
Queue 实现
Worker 实现
```

禁止：

```text
DB migration
新增 ORM Model
新增数据库表
新增 API endpoint
```

禁止为了验证架构而偷偷实现：

```text
WorkflowEngine
```

---

# 六、必须回答的 14 个架构问题

本阶段最终必须明确回答下面 14 个问题。

不要回避。

如果当前代码不足以决定：

```text
UNKNOWN
```

并给出需要后续决策的原因。

---

## Q1：Workflow Definition 是什么？

明确：

```text
Workflow Definition
```

究竟包含：

```text
steps
edges
conditions
parallel groups
retry policy
approval gates
timeouts
version
```

中的哪些。

必须区分：

```text
Definition
```

和：

```text
Execution Instance
```

---

# Q2：Workflow Instance 是什么？

定义：

```text
Workflow Definition
        ↓
Workflow Instance
        ↓
Execution
```

明确：

```text
workflow_id
workflow_version
workflow_run_id
request_id
project_id
```

各自含义。

---

# Q3：Workflow 是否需要自己的持久化模型？

必须认真比较：

### Option A

新增：

```text
WorkflowRun
WorkflowStep
WorkflowCheckpoint
```

### Option B

复用：

```text
AgentRun
AgentStep
AgentCheckpoint
```

### Option C

Workflow 只做控制流，另建通用：

```text
ExecutionRun
ExecutionStep
ExecutionCheckpoint
```

### Option D

其他。

不要因为 Phase 5 已经存在表就默认复用。

必须从：

```text
ownership
lifecycle
retry
parallelism
HITL
resume
read model
```

角度分析。

最终给出：

```text
Decision
Rationale
Trade-offs
```

---

# Q4：Workflow Step 是否和 AgentStep 相同？

必须回答：

```text
WorkflowStep
```

是否包含：

```text
AGENT
TOOL
FUNCTION
CONDITION
PARALLEL
APPROVAL
WAIT
```

以及：

```text
AgentStep
```

是否只是：

```text
WorkflowStep.type = AGENT
```

的一种内部执行记录。

重点：

> 不要让 Phase 5 AgentStep 被错误升级成通用 WorkflowStep，除非有明确架构依据。

---

# Q5：Agent 如何成为 Workflow Step？

目标概念：

```text
Workflow Runtime
      ↓
Agent Step
      ↓
Existing Agent Service API
      ↓
Agent Runtime
```

Workflow 不允许：

```text
Workflow
   ↓
AgentRuntime internal method
```

直接操作：

```text
AgentDecision
AgentCheckpoint
AgentToolResult
```

Workflow 必须通过 Phase 5 已冻结的 Application Service API。

至少考虑：

```text
execute
resume_agent_run
resolve_agent_approval
cancel_agent_run
```

---

# Q6：Tool Step 如何执行？

比较：

```text
Workflow
   ↓
ToolExecutionService
```

和：

```text
Workflow
   ↓
AIOrchestrator
   ↓
Tool
```

必须确定：

> Workflow 的 Tool Step 不应该绕过 ToolExecutionService。

保持：

```text
Authorization
→ Execution
→ Observation
```

现有边界。

---

# Q7：HITL Gate 谁拥有？

必须明确：

```text
Workflow Approval Gate
```

与：

```text
Agent Tool Approval
```

不是同一概念。

例如：

```text
Workflow:
    Step A
      ↓
    Approval Gate
      ↓
    Step B
```

这是：

```text
Workflow-level HITL
```

而：

```text
Agent:
    LLM
      ↓
    Tool Authorization
      ↓
    AgentApproval
      ↓
    Tool
```

这是：

```text
Agent-level Tool Approval
```

两者可以复用基础设施，但 ownership 必须明确。

---

# Q8：Retry 谁负责？

这是 Phase 6 最重要的问题之一。

必须建立：

```text
Workflow Retry
```

与：

```text
Agent Retry
```

的边界。

例如：

```text
Workflow Step
    ↓
Agent
    ↓
Agent internal semantic retry
```

如果 Agent 失败：

```text
Workflow Retry Policy
```

是否可以再次执行整个 Agent Step？

必须定义：

```text
attempt
idempotency
side effect
replay
failure classification
```

原则：

> Workflow Retry 不能偷偷进入 Agent Runtime 内部改变 Agent Loop。

---

# Q9：Parallelism 谁负责？

Phase 5：

```text
one tool per round
```

保持不变。

Phase 6 可以：

```text
Step A ─┐
        ├── Join
Step B ─┘
```

必须明确：

```text
parallelism = Workflow Runtime
```

不是：

```text
Agent Runtime
```

至少定义：

```text
Fan-out
Fan-in
Join
Failure policy
Cancellation policy
```

---

# Q10：Workflow Timeline 怎么处理？

Phase 5 已经有：

```text
Agent Timeline
```

Phase 6 必须决定：

### Option A

Workflow Timeline 单独存在。

### Option B

Workflow Timeline 包含：

```text
Workflow events
+
Agent Timeline projection
```

### Option C

统一 Execution Timeline。

不要直接修改 Phase 5 Agent Timeline。

Phase 5 Timeline 已经 FROZEN。

---

# Q11：Conversation 与 Workflow 的关系

必须明确：

```text
Conversation
    ↓
Workflow Run
```

是否允许：

```text
Conversation
   ├── Turn
   ├── Agent Run
   └── Workflow Run
```

以及：

```text
Workflow
```

是否可以脱离 Conversation：

```text
scheduled workflow
event-driven workflow
background workflow
```

答案不能默认是“必须有 Conversation”。

---

# Q12：Evidence 与 Workflow 的关系

必须保持：

```text
Evidence = source/provenance
```

不能变成：

```text
Workflow execution log
```

明确：

```text
Workflow Step Result
≠
Evidence
```

如果某个 Workflow Step 产生 Evidence：

应该是：

```text
Step
   ↓
Business Result
   ↓
Evidence
```

而不是：

```text
Workflow
   ↓
Evidence table
```

---

# Q13：workflow_run_id / request_id 怎么定义？

必须明确：

```text
workflow_run_id
```

和：

```text
request_id
```

分别代表什么。

建议分析：

```text
request_id
=
一次外部调用 / correlation identity

workflow_run_id
=
一个 Workflow Instance 的 execution identity
```

同时考虑：

```text
AgentRun
```

如何关联。

例如：

```text
workflow_run_id
      │
      ├── workflow step
      │
      ├── agent_run_id
      │
      └── tool execution
```

不能直接把：

```text
request_id
```

当 Workflow identity。

---

# Q14：Scheduler 放在哪里？

必须搜索当前项目是否已有：

```text
scheduler
worker
queue
background task
```

然后决定：

```text
Workflow Runtime
      ↑
Scheduler
```

还是：

```text
Scheduler
      ↓
Workflow Application Service
      ↓
Workflow Runtime
```

原则上：

> Scheduler 负责“什么时候启动”，Workflow Runtime 负责“启动之后怎么执行”。

不要让 Scheduler 进入 Workflow Step 控制逻辑。

---

# 七、必须建立四张矩阵

## Matrix 1：Workflow Capability Matrix

至少：

| Capability    | Phase 6 Ownership | Persistence | Command/Query |
| ------------- | ----------------- | ----------- | ------------- |
| Definition    | ?                 | ?           | Query         |
| Instance      | ?                 | ?           | Command       |
| Step          | ?                 | ?           | Command       |
| Condition     | ?                 | ?           | Command       |
| Parallel      | ?                 | ?           | Command       |
| Retry         | ?                 | ?           | Command       |
| Approval Gate | ?                 | ?           | Command       |
| Resume        | ?                 | ?           | Command       |
| Cancellation  | ?                 | ?           | Command       |
| Timeline      | ?                 | ?           | Query         |
| Agent Step    | ?                 | ?           | Command       |

---

# 八、Phase 5 vs Phase 6 Boundary Matrix

必须形成：

| 能力                 | Phase 5 Agent        | Phase 6 Workflow           |
| ------------------ | -------------------- | -------------------------- |
| 下一步由谁决定            | LLM                  | Workflow Definition        |
| Tool Loop          | Agent                | Workflow 不拥有               |
| Tool Authorization | Agent                | Workflow Tool Step 复用基础能力  |
| Tool Replay        | Agent                | Workflow 自己定义 Step Replay  |
| Retry              | Agent semantic       | Workflow policy            |
| Parallel           | ❌                    | Workflow                   |
| Condition          | LLM decision         | Workflow                   |
| HITL Tool Approval | Agent                | Workflow Approval Gate     |
| Long-running       | Agent Resume         | Workflow durable execution |
| Timeline           | Agent Timeline       | Workflow Timeline          |
| Conversation       | Conversation Runtime | Optional                   |
| Scheduler          | ❌                    | 外部/Workflow trigger        |
| Multi-Agent        | ❌                    | Future                     |

---

# 九、Source of Truth Matrix

必须明确：

```text
Workflow Definition
Workflow Run
Workflow Step
Workflow Checkpoint
Agent Run
Agent Step
Agent Decision
Agent Tool Result
Agent Checkpoint
Agent Approval
Conversation
Evidence
```

每一个必须只有一个明确 SoT。

特别检查：

```text
WorkflowStep
≠
AgentStep
```

```text
WorkflowCheckpoint
≠
AgentCheckpoint
```

```text
WorkflowTimeline
≠
AgentTimeline
```

如果最终认为某两个概念可以共用：

必须写出为什么。

---

# 十、Command / Query Boundary

必须建立：

```text
Workflow Command
```

例如：

```text
start_workflow
resume_workflow
cancel_workflow
approve_workflow
```

与：

```text
Workflow Query
```

例如：

```text
get_workflow
get_workflow_timeline
get_workflow_status
```

明确：

```text
Query MUST NOT mutate.
```

延续 Phase 5 Read API 的冻结原则。

---

# 十一、Workflow 最小概念模型

本阶段只定义概念，不写代码。

目标模型：

```text
Workflow Definition
        ↓
Workflow Instance
        ↓
Workflow Runtime
        │
        ├── Deterministic Step
        │
        ├── Agent Step
        │       ↓
        │   Agent Service API
        │
        ├── Tool Step
        │       ↓
        │   ToolExecutionService
        │
        ├── Condition
        │
        ├── Approval Gate
        │
        ├── Parallel
        │
        └── Retry Policy
```

---

# 十二、必须定义 Workflow Step 类型

至少分析：

```text
FUNCTION
AGENT
TOOL
CONDITION
APPROVAL
PARALLEL
WAIT
```

不一定全部进入 MVP。

必须给出：

```text
Candidate
MVP
Deferred
```

三类。

不要为了“完整”全部实现。

---

# 十三、必须定义 Workflow 生命周期

至少分析：

```text
CREATED
RUNNING
PAUSED
WAITING_APPROVAL
WAITING
COMPLETED
FAILED
CANCELLED
```

是否需要：

```text
RETRYING
LIMIT_REACHED
```

必须说明。

注意：

不要直接复制 AgentRun status。

Workflow 生命周期和 Agent 生命周期不是同一个状态机。

---

# 十四、必须定义 Workflow Checkpoint

只做架构设计。

至少回答：

```text
checkpoint 保存什么？
```

例如：

```text
workflow_run_id
workflow_version
current_step
next_action
state_version
claim_token
pending_approval
pending_parallel
retry_attempt
```

以及：

```text
checkpoint 是否 UPDATE
```

还是：

```text
append-only event
```

必须做选择。

---

# 十五、Workflow State

必须区分：

```text
Definition
```

和：

```text
Runtime State
```

Runtime State 不允许存：

```text
Prompt
API Key
Authorization Header
完整 RAG chunks
完整 SQL
完整 Tool Result
```

如果业务数据需要保存：

必须设计：

```text
Reference
Hash
Bounded payload
```

不要直接复制 Phase 5 所有存储策略。

---

# 十六、Retry Policy

必须定义概念：

```text
max_attempts
backoff
retryable_errors
timeout
on_failure
```

但是：

**本阶段不要实现 retry。**

只冻结 ownership。

推荐重点分析：

```text
Workflow Retry
    ↓
重新执行 Workflow Step
```

而：

```text
Agent semantic retry
    ↓
仍然由 Agent Runtime 自己负责
```

---

# 十七、Parallel Policy

必须定义：

```text
fan_out
fan_in
join
failure policy
cancel policy
partial result policy
```

例如：

```text
parallel:
    A
    B
    C
      ↓
    JOIN
```

必须明确：

```text
A success
B failed
C success
```

Workflow 是：

```text
FAIL_FAST
```

还是：

```text
WAIT_ALL
```

还是：

```text
PARTIAL_SUCCESS
```

本阶段只设计，不实现。

---

# 十八、Workflow 与 Agent 的禁止关系

必须明确：

禁止：

```text
Workflow
 ↓
AgentRuntime internal loop
```

也禁止：

```text
Agent
 ↓
WorkflowRuntime
```

形成：

```text
Agent ↔ Workflow
```

无限递归。

推荐：

```text
Workflow
    ↓
Agent Application Service
    ↓
Agent Runtime
```

Agent Runtime：

```text
不能启动 Workflow
```

除非未来单独 ADR 批准。

---

# 十九、Workflow 与 Orchestrator

必须明确：

```text
AIOrchestrator
```

负责：

```text
用户请求
→ route
```

如果：

```text
route = WORKFLOW
```

才：

```text
Workflow Application Service
```

Workflow Runtime 不应该：

```text
调用 AI Router
```

否则会形成：

```text
Workflow
 ↓
Router
 ↓
Workflow
```

递归。

---

# 二十、Workflow 是否进入 AI Router？

必须做明确决策。

候选：

### Option A

```text
Router
 ├── RAG
 ├── Tool
 ├── Text-to-SQL
 ├── Agent
 └── Workflow
```

### Option B

```text
Router
 ├── AI capability
 └── Workflow Application Service
```

### Option C

Workflow 是外部 trigger：

```text
API / Scheduler / Event
       ↓
Workflow Service
```

最终选择必须基于：

```text
用户交互
后台任务
计划任务
业务事件
```

三个场景分别分析。

---

# 二十一、至少设计三个真实 WMS Workflow

只做架构示例，不实现。

---

## Workflow A：采购入库

例如：

```text
PO
 ↓
Receipt Notice
 ↓
Approval
 ↓
Warehouse Receive
 ↓
Inventory Update
 ↓
Evidence
```

明确：

哪些是：

```text
FUNCTION
TOOL
AGENT
APPROVAL
```

---

## Workflow B：生产日报

例如：

```text
读取工单
 ↓
读取生产进度
 ↓
计算完成率
 ↓
异常判断
 ↓
Agent Summary
 ↓
日报输出
```

这里特别分析：

```text
哪些步骤必须 deterministic
哪些步骤可以 Agent
```

---

## Workflow C：异常库存分析

例如：

```text
Inventory Query
 ↓
Threshold Check
 ↓
Parallel:
    Stock Analysis
    Work Order Analysis
    Purchase Analysis
 ↓
Join
 ↓
Agent Summary
 ↓
Human Approval
 ↓
Report
```

重点用于分析：

```text
Parallel
Agent
HITL
```

---

# 二十二、Persistence 方案必须做最终选择

最终报告必须明确：

```text
Workflow persistence:
A / B / C / D
```

并回答：

### 为什么不能直接复用 AgentRun？

### 为什么不能直接复用 AgentCheckpoint？

### Workflow 是否需要独立：

```text
workflow_run
workflow_step
workflow_checkpoint
```

如果决定复用：

必须明确：

```text
哪些字段语义完全一致
哪些字段不一致
为什么可以安全复用
```

如果决定新增：

必须明确：

```text
为什么独立 ownership 更清晰
```

---

# 二十三、Durability 方案

必须分析三种方向：

### Option 1

纯 PostgreSQL 自研 Durable Workflow Runtime

### Option 2

引入第三方 Durable Workflow Runtime

例如：

```text
Temporal
Durable Task
其他
```

### Option 3

短期轻量 Runtime + PostgreSQL，未来迁移

必须比较：

```text
开发复杂度
部署复杂度
状态持久化
Retry
Timer
HITL
Parallel
Recovery
Observability
Python compatibility
Windows development
Docker compatibility
```

**本阶段不安装第三方 Workflow Engine。**

---

# 二十四、不要提前引入 Temporal / LangGraph

即使发现第三方框架很适合：

本阶段也只能：

```text
Architecture Recommendation
```

不能：

```text
pip install
docker compose
runtime integration
```

---

# 二十五、最终必须输出 Architecture Decision

新增：

```text
docs/decisions/Phase 6 — Workflow Runtime Architecture Discovery.md
```

如果架构经过检查已经可以冻结：

可以在同一文件最终写：

```text
Architecture Status: FROZEN
```

否则：

```text
Architecture Status: PROPOSED
```

不要为了结束阶段强行 FROZEN。

---

# 二十六、Decision 文档必须包含

```text
1. Context

2. Phase 5 Frozen Boundary

3. Phase 6 Problem

4. Workflow vs Agent

5. Workflow Definition

6. Workflow Instance

7. Workflow Step

8. Workflow State

9. Workflow Checkpoint

10. Workflow Persistence

11. Agent Integration

12. Tool Integration

13. HITL Integration

14. Retry Ownership

15. Parallel Ownership

16. Conversation Relationship

17. Evidence Relationship

18. Scheduler Relationship

19. Router Relationship

20. Source of Truth Matrix

21. Command / Query Matrix

22. Real WMS Workflow Examples

23. Persistence Decision

24. Durability Decision

25. Security Boundary

26. Deferred Decisions

27. Phase 6 Implementation Boundary
```

---

# 二十七、Phase 6 Implementation Boundary

Architecture 完成后，必须明确下一阶段：

```text
Phase 6.1
```

允许做什么。

例如：

```text
WorkflowDefinition
WorkflowRun
WorkflowStep
WorkflowCheckpoint
WorkflowRuntime
```

但：

**本阶段不要实现。**

Phase 6.1 必须重新开始 Coding 前的 review。

---

# 二十八、Architecture Audit

完成后执行：

```powershell
git status --short
git diff --check
git diff --stat
git diff
```

确认：

```text
Production Code = 0
ORM = 0
Migration = 0
API = 0
Workflow Runtime Code = 0
Agent Runtime Change = 0
Prompt Change = 0
Schema Change = 0
```

如果只新增 Decision 文档：

这是正常结果。

---

# 二十九、Git

如果只有架构文档：

commit：

```text
docs: Phase 6 workflow runtime architecture discovery
```

不要：

```text
push
PR
merge
rebase
```

---

# 三十、最终报告

严格按照：

```text
【Phase 6.0 ARCHITECTURE DISCOVERY COMPLETE】

1. Architecture Status
2. Phase 5 Boundary
3. Workflow vs Agent
4. Workflow Definition
5. Workflow Instance
6. Workflow Step
7. Workflow State
8. Workflow Checkpoint
9. Persistence Decision
10. Durability Decision
11. Agent Integration
12. Tool Integration
13. HITL
14. Retry Ownership
15. Parallel Ownership
16. Conversation Relationship
17. Evidence Relationship
18. Scheduler Relationship
19. Router Relationship
20. Source of Truth
21. Command / Query
22. WMS Workflow Examples
23. Deferred Decisions
24. Phase 6.1 Boundary
25. Git Diff
26. Tests / Validation
27. Commit
```

最后输出：

```text
Phase 5 Agent Runtime = FROZEN

Phase 6 Workflow Runtime
        ↓
Architecture = [FROZEN / PROPOSED]
        ↓
Phase 6.1 = [READY / NOT READY]
```

---

# 三十一、强制 STOP

完成 Phase 6.0 后：

**立即停止。**

不要：

```text
❌ 实现 WorkflowEngine
❌ 创建 Workflow ORM
❌ 创建 migration
❌ 创建 Workflow API
❌ 创建 Scheduler
❌ 创建 Worker
❌ 安装 Temporal
❌ 安装 LangGraph
❌ 接 MCP
❌ 修改 Agent Runtime
❌ 修改 Agent Schema
❌ 修改 Phase 5 Timeline
```

等待下一步明确指令。

**Phase 6.0 到此结束。**
