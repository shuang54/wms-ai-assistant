【Phase 6 ADR-01：Workflow Runtime Scope & Architecture Decision】

项目：
D:\coding\ai\wms-ai-assistant

当前状态：

Phase 5 Agent Runtime 已经 FROZEN。

当前 Branch：
phase5-1

当前 HEAD：
a50869c
chore: Phase 5 agent runtime exit audit

Phase 5 已完成：
- Agent Loop
- Agent Decision
- Tool Authorization
- Tool Execution
- Tool Result Persistence
- AgentRun
- AgentStep
- AgentDecision
- AgentCheckpoint
- AgentApproval
- Replay
- Resume
- HITL
- Cooperative Cancellation
- Agent Read API
- Agent Timeline

全部 READY。

Phase 5 明确冻结：

1. Agent Loop
2. Decision Contract
3. Tool Authorization
4. Tool Execution
5. 六张 Agent persistence tables
6. Replay / Resume
7. HITL
8. Cancellation
9. Agent Read API
10. Agent Timeline
11. 错误码
12. Source of Truth 关系

Phase 5 明确禁止：

- Workflow retry 下沉到 Agent Runtime
- Workflow parallelism 下沉到 Agent Runtime
- Workflow branching 下沉到 Agent Runtime
- Workflow compensation 下沉到 Agent Runtime
- 修改 Agent Loop
- 修改 Agent Decision Contract
- 修改六张 Agent 表 schema
- 修改 Agent Read API
- 修改 Agent Timeline 语义
- Workflow 直接写 Agent 六张表

Phase 6 的目标：

设计一个独立的：

Workflow Runtime

它负责：

- developer-defined deterministic control flow
- sequential execution
- condition / branch
- approval gate
- parallel execution
- retry policy
- scheduled execution
- long-running execution
- workflow-level persistence
- workflow-level recovery
- workflow-level observability

Agent Runtime 继续负责：

- model-driven control flow
- LLM decision
- Tool decision
- Agent HITL
- Agent replay
- Agent resume
- Agent cancellation

两者必须严格分层。

---

# 一、这次任务性质

本次任务：

READ ONLY ARCHITECTURE DECISION PREPARATION

禁止：

- 修改生产代码
- 修改测试
- 修改 schema
- 创建 ORM
- 创建 migration
- 创建 Workflow Runtime
- 创建 Workflow API
- 创建 scheduler
- 创建 worker
- 创建 queue
- 创建 Redis
- 创建 Kafka
- 创建 LangGraph
- 创建 MCP
- 创建 Multi-Agent
- 修改 Agent 六表
- 修改 ToolDefinition
- 打开生产 agent_enabled
- commit
- reset
- checkout

本轮只允许：

1. 阅读真实代码
2. 阅读 Phase 5 Frozen 文档
3. 阅读现有 Architecture / ADR
4. 分析架构候选
5. 输出 Phase 6 ADR

---

# 二、必须首先阅读的真实资料

必须先阅读：

backend/app/agent/
backend/app/services/ai_orchestrator_service.py
backend/app/services/ai_router_service.py
backend/app/services/tool_execution_service.py
backend/app/projects/
backend/app/tools/
backend/app/api/
backend/app/db/models/
backend/app/db/
tests/
docs/architecture.md
docs/decisions/Phase/
Phase 5 Frozen 文档
Phase 5 Exit Audit
Phase 6 PRE-ARCHITECTURE AUDIT

特别关注：

1. AgentRuntime 的真实 Service API
2. ToolExecutionService
3. ProjectContext
4. ProjectCapabilities
5. RuntimePolicy
6. Agent 六表
7. Agent Read API
8. Agent Timeline
9. Conversation
10. Evidence
11. ToolDefinition
12. 当前数据库初始化方式
13. 当前测试数据库模式
14. 当前 HTTP 组装方式

不要根据文件名猜架构。

必须以真实代码为准。

---

# 三、当前已经确认的 Phase 5 Boundary

Workflow 如果需要调用 Agent，只能使用：

AIOrchestratorService.execute()
AIOrchestratorService.resume_agent_run()
AIOrchestratorService.resolve_agent_approval()
AIOrchestratorService.cancel_agent_run()

禁止：

Workflow → AgentRuntime._run_scoped()

禁止：

Workflow → AgentRepository

禁止：

Workflow → agent_run
Workflow → agent_step
Workflow → agent_decision
Workflow → agent_tool_result
Workflow → agent_checkpoint
Workflow → agent_approval

Workflow 不允许绕过 Agent Service API。

---

# 四、必须解决的核心问题

本 ADR 必须一次性回答以下问题。

## P1 Workflow Control Model

比较至少：

A. DAG

B. State Machine

C. Sequential Node Pipeline

D. DAG + State Machine hybrid

分析：

- branch
- condition
- parallel
- join
- retry
- approval
- compensation
- long-running
- persistence
- resume
- observability
- developer ergonomics

不要只给概念解释。

必须结合本项目实际需求判断。

最后给出：

RECOMMENDED MODEL

但必须提供理由。

---

# 五、Workflow Definition 如何存在

比较：

A. Python Code Definition

例如：

WorkflowDefinition(
    nodes=[
        ...
    ]
)

B. YAML / JSON Definition

C. Database Definition

D. Code + persisted version metadata

必须分析：

- 类型安全
- IDE 支持
- 可测试性
- 动态修改能力
- versioning
- auditability
- migration
- security
- enterprise maintainability

当前项目是 Python backend。

不要因为“Workflow”这个词就直接引入低代码 JSON Workflow。

必须根据当前项目实际情况判断。

---

# 六、Workflow Persistence

必须设计概念层，不创建代码。

至少分析：

WorkflowDefinition
WorkflowVersion
WorkflowRun
WorkflowNodeRun
WorkflowCheckpoint

是否全部需要？

是否可以减少？

哪些是 SoT？

哪些是 projection？

哪些是 execution state？

必须明确：

WorkflowRun ≠ AgentRun

WorkflowNodeRun ≠ AgentStep

不能复用：

run_id

不能复用：

step_index

建议明确独立 namespace。

例如概念上：

workflow_run_id
workflow_node_run_id

但不要提前锁死具体字段，除非 ADR 有充分理由。

---

# 七、Workflow 与 Agent 的关系

必须画清楚：

Workflow
    ↓
Workflow Node
    ↓
Agent Invocation
    ↓
AIOrchestratorService
    ↓
AgentRuntime
    ↓
Tool

必须回答：

一个 Workflow Node 是否可以：

1. 调 Agent
2. 调 Tool
3. 调 RAG
4. 调 Text-to-SQL
5. 调普通 Service

这些能力分别通过什么边界调用。

特别分析：

Workflow Node → ToolExecutionService

是否允许？

如果允许：

必须说明：

ToolExecutionService 仍然是唯一 Tool execution boundary。

禁止创建第二套 Tool executor。

---

# 八、Agent Node 的语义

必须定义：

Workflow Node：

    "调用一个 Agent"

和：

Agent Step：

    "Agent 内部一次 LLM decision"

不是同一层。

例如：

Workflow：

Node A
  ↓
Agent A
  ├── AgentStep 0
  ├── AgentStep 1
  ├── AgentStep 2
  └── AgentStep 3
  ↓
Node B

必须明确：

Workflow Node 是父层执行单元。

AgentStep 是 Agent 内部执行单元。

两者：

不能混表。

不能共享状态机。

不能共享生命周期。

不能让 Workflow 修改 AgentStep。

---

# 九、Retry Architecture

这是本 ADR 最重要的问题之一。

当前系统：

LLM retry = NONE
Tool retry = NONE
Agent retry = NONE
DB retry = NONE
Workflow retry = NONE

Phase 6 必须决定：

Retry Owner = Workflow Runtime

还是：

Retry Owner = Node Executor

或者：

其他方案。

重点分析：

如果一个 Node 调用了 Agent：

Workflow retry Agent Node

是否意味着：

重新调用 Agent？

如果 Agent 已经产生部分 Tool side effect：

怎么办？

必须回答：

- retry boundary
- idempotency
- side effect
- duplicate execution
- timeout
- failure classification
- retryable / non-retryable
- max attempts
- backoff
- terminal failure

不能简单写：

"失败自动重试 3 次"

必须证明为什么安全。

---

# 十、Tool Side Effect Classification

Phase 5 OQ-P5-18：

ToolDefinition 当前没有：

read_only
write
external_side_effect
idempotent
retryable

本 ADR 必须决定：

Phase 6 是否需要扩展 ToolDefinition。

比较：

方案 A：

Phase 6 暂时只允许 READ_ONLY Tool

方案 B：

Phase 6 增加 Tool Capability Classification

方案 C：

Workflow 自己维护 Tool Policy

必须分析：

如果 Workflow 支持：

retry
parallel
compensation

那么系统如何知道：

Tool 是否可以安全 retry？

Tool 是否可以 parallel？

Tool 是否产生 external side effect？

最终必须给出：

Phase 6 Tool Safety Boundary。

如果无法安全证明：

宁可 BLOCK write Tool。

不要为了功能完整而放开。

---

# 十一、Parallel Execution

必须分析：

Sequential：

A → B → C

Branch：

A → B
  → C

Parallel：

A
├── B
├── C
└── D
      ↓
     Join
      ↓
      E

必须回答：

1. parallel branch 如何持久化？
2. branch failure 怎么处理？
3. join 条件是什么？
4. all / any / quorum 是否需要？
5. cancellation 如何传播？
6. branch retry 如何处理？
7. 一个 branch 完成、另一个 branch 失败时状态是什么？
8. Workflow Run 如何保持一致状态？

当前项目没有：

asyncio.gather
TaskGroup
queue
worker

不要假设可以直接 parallel。

必须做架构决策：

Phase 6 第一阶段是否支持 parallel？

如果支持：

执行模型必须同时成立。

---

# 十二、Execution Driver

这是 F-06。

必须重点比较：

方案 A：

Request-scoped synchronous execution

方案 B：

External API driven

方案 C：

In-process background executor

方案 D：

Worker + Queue

方案 E：

Scheduler + Worker

必须分析：

- crash recovery
- long-running
- HITL
- scheduled
- retry
- concurrency
- deployment
- horizontal scaling
- operational complexity
- dependency增加
- Windows local development
- 当前 PostgreSQL 架构

特别注意：

不能为了"未来可能需要"直接引入：

Redis
Celery
Kafka
RabbitMQ
Temporal
Dramatiq
APScheduler

除非 ADR 能证明当前阶段确实需要。

---

# 十三、Long-running Workflow

必须定义：

Workflow 可以运行：

秒
分钟
小时
天

那么：

请求结束之后：

谁继续执行？

HITL 等待：

谁负责恢复？

进程重启：

如何恢复？

数据库：

保存什么？

必须区分：

短流程

与：

durable workflow

不要把：

asyncio task

当成 durable execution。

---

# 十四、Scheduled Workflow

Phase 6 是否真的需要：

cron
schedule
timer
delayed execution

必须分析：

如果支持：

谁负责触发？

Scheduler 是：

Workflow Runtime 的一部分？

还是：

External Scheduler？

当前企业环境已经可能存在：

XXL-JOB

但：

XXL-JOB 属于业务系统调度能力。

不要直接把它当作 AI Workflow Runtime。

必须明确：

AI Workflow Scheduler 是否属于本项目。

---

# 十五、HITL

Phase 5 已经有 Agent HITL。

Phase 6 不能直接把 Agent Approval 当成 Workflow Approval。

必须分析：

Workflow Approval：

Workflow
  ↓
Approval Node
  ↓
WAITING
  ↓
Human
  ↓
APPROVED / REJECTED
  ↓
Next Node

Agent Approval：

Agent
  ↓
Tool Decision
  ↓
Approval
  ↓
Tool Execution

两者不是同一个状态机。

必须明确：

Workflow Approval 是否拥有自己的 persistence。

不要修改 AgentApproval。

---

# 十六、Cancellation

Phase 5：

AgentCancellationCoordinator

只负责 Agent。

Phase 6：

Workflow cancellation

必须定义：

Cancel Workflow

是否：

1. 只停止调度新 Node
2. 同时 cancel running Agent
3. 同时 cancel Tool
4. 等待 running Node 自然结束
5. force kill

必须明确：

Workflow Cancellation

与：

Agent Cancellation

的上下级关系。

推荐重点分析 cooperative cancellation。

不要设计 force kill，除非有真实需求。

---

# 十七、Project Isolation

必须解决：

WorkflowRun.project_id

来源必须是什么？

禁止：

从 Runtime memory 猜。

禁止：

从 AgentRun fallback。

禁止：

从用户输入直接信任。

必须优先分析：

ProjectRegistry

服务器端解析。

然后：

Workflow
  ↓
ProjectContext
  ↓
Agent
  ↓
Tool

必须保证整个链路 project 一致。

---

# 十八、Production Agent Enable

当前：

agent_enabled=True

backend/app 中生产调用点：

0

必须决定：

Phase 6 是否：

A. 保持 Agent disabled
B. Workflow 内部显式启用 Agent
C. 全局打开 Agent
D. Project-level capability

禁止直接修改代码打开。

ADR 只做决策。

---

# 十九、Workflow API

必须决定：

是否需要：

POST /api/workflows
POST /api/workflows/{id}/runs
GET /api/workflow/runs/{id}
POST /api/workflow/runs/{id}/cancel
POST /api/workflow/runs/{id}/resume

但：

API 不是第一优先级。

必须先确定：

Domain Model
Runtime
Persistence
Ownership

再决定 HTTP surface。

---

# 二十、Workflow Read Model

Agent 已经有：

AgentReadService
Agent Timeline

Workflow 不得扩展 Agent Read API。

必须决定是否建立：

WorkflowReadService

例如：

WorkflowRun
WorkflowNodeRun
Workflow Timeline

Timeline 是否：

DB Event Store

还是：

Projection

必须分析。

优先保持当前项目：

"Source of Truth + Derived Read Model"

的架构风格。

---

# 二十一、Workflow Event / Timeline

必须明确：

是否需要：

workflow_event

event store

还是：

从 WorkflowRun + WorkflowNodeRun 派生 Timeline。

不要因为 Agent Timeline 是 projection，就机械复制。

分析：

- audit
- ordering
- replay
- debugging
- storage cost
- event sourcing complexity

---

# 二十二、Conversation Boundary

必须保持：

Conversation ≠ Workflow

Conversation ≠ Agent

Workflow 可以：

被 Conversation 触发。

但是：

Conversation 不成为 Workflow persistence。

Agent 不直接拥有 Conversation。

保持 Phase 4 / Phase 5 的隔离原则。

---

# 二十三、Evidence Boundary

保持：

Workflow ≠ Evidence

Agent ≠ Evidence

Evidence 是：

Source / Provenance

Workflow 如果未来需要证据：

通过业务结果 / evidence service 产生关联。

不要让 Workflow 直接修改 Evidence persistence。

---

# 二十四、必须给出 Architecture Layer

最终必须形成类似：

HTTP/API
    ↓
Application Layer
    ↓
Workflow Runtime
    ↓
┌───────────────┬────────────────┐
│ Workflow Node │ Agent Service  │
└───────────────┴────────────────┘
                    ↓
              Agent Runtime
                    ↓
              Tool Execution
                    ↓
             Enterprise Data

并明确：

Workflow Runtime
和
Agent Runtime

是：

Sibling Runtime Layers

不是：

Workflow Runtime 包含 Agent Runtime 内部代码。

---

# 二十五、必须输出 ADR

最终只创建：

docs/decisions/Phase/Phase 6 — Workflow Runtime Architecture Decision.md

但本轮仍然：

READ ONLY

如果无法真正写文件：

直接在输出中完整给出 ADR 内容。

ADR 至少包含：

1. Context
2. Problem
3. Goals
4. Non-Goals
5. Architecture Principles
6. Control Flow Decision
7. Workflow Definition Decision
8. Persistence Decision
9. Execution Driver Decision
10. Agent Integration Decision
11. Tool Integration Decision
12. Retry Decision
13. Parallelism Decision
14. HITL Decision
15. Cancellation Decision
16. Project Isolation Decision
17. Scheduling Decision
18. Read API Decision
19. Timeline Decision
20. Security Boundary
21. Testing Strategy
22. Migration / Compatibility
23. Rejected Alternatives
24. Phase 6 Scope
25. Phase 6 Deferred
26. Phase 6 Exit Criteria
27. Architecture Diagram
28. Decision Summary

---

# 二十六、特别重要：不要假装已经完成决策

如果某个问题：

现有证据不足

必须标：

OPEN

如果有两个可行方案：

必须：

比较方案
→ 给出推荐
→ 说明代价
→ 说明为什么不是另一个

不能：

"默认采用"

不能：

"后续再决定"

除非明确记录为：

OPEN DECISION

---

# 二十七、Architecture Quality Gate

ADR 完成后必须进行一次自审：

检查：

1. 是否修改 Agent Frozen Contract？
2. 是否复用了 agent_run？
3. 是否复用了 agent_step？
4. 是否让 Workflow 直接写 Agent 表？
5. 是否把 retry 下沉 Agent？
6. 是否把 parallel 下沉 Agent？
7. 是否把 compensation 下沉 Agent？
8. 是否新增第二套 Tool Executor？
9. 是否把 Conversation 当 Workflow State？
10. 是否把 Evidence 当 Workflow State？
11. 是否引入 Redis/Celery/Kafka 等未决基础设施？
12. 是否打开 production agent_enabled？
13. 是否产生 Orchestrator / Step 新语义冲突？
14. 是否存在 project isolation bypass？
15. 是否存在 Workflow retry 导致 Tool duplicate execution？
16. 是否存在 long-running 无恢复机制？
17. 是否存在 HITL 无持久化状态？
18. 是否存在 parallel 无 join semantics？
19. 是否存在 cancellation race？
20. 是否存在 API 先于 domain model 的设计？

全部检查完成后再给出最终判断：

PHASE 6 ADR STATUS

READY
或
READY WITH OPEN DECISIONS
或
BLOCKED

---

# 二十八、最终输出格式

严格输出：

【Phase 6 ADR PREPARATION COMPLETE】

1. Existing Architecture Summary
2. Phase 5 Frozen Boundary
3. Workflow Architectural Gap
4. Candidate Architectures
5. Recommended Architecture
6. Open Decisions
7. Rejected Alternatives
8. Phase 6 Scope
9. Phase 6 Deferred
10. ADR Content
11. Architecture Quality Gate
12. Final Status

最后必须明确：

是否可以进入：

Phase 6 Implementation

如果不能：

明确指出：

需要先解决哪些 ADR Decision。

不要开始写代码。

不要进入 Phase 6 Step 1。

STOP。