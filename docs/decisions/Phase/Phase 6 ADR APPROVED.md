【Phase 6 ADR APPROVED】

确认批准：

docs/decisions/Phase/Phase 6 — Workflow Runtime Architecture Decision.md

状态从：

PROPOSED

变更为：

ACCEPTED

正式进入：

Phase 6 Implementation

执行规则：

1. 严格按照 ADR §18 A4 顺序实施：

Domain
→ Persistence
→ Runtime
→ Read Service
→ API

2. Phase 5 Agent Runtime 保持 FROZEN。

禁止修改：

* Agent Loop
* Agent Decision Contract
* Agent Authorization
* Agent Execution
* Agent 六表
* Agent Replay / Resume
* Agent HITL
* Agent Cancellation
* Agent Read API
* Agent Timeline
* Agent Error Codes

3. Workflow 必须保持独立 namespace：

WorkflowRun ≠ AgentRun
WorkflowNodeRun ≠ AgentStep

禁止复用 agent_run / agent_step 作为 Workflow 状态。

4. Workflow 通过既有 AIOrchestratorService Service API 调用 Agent：

execute()
resume_agent_run()
resolve_agent_approval()
cancel_agent_run()

禁止 Workflow 直接写 Agent 六表。

5. Workflow Runtime 负责：

* deterministic control flow
* node lifecycle
* node-level retry
* workflow persistence
* workflow HITL
* workflow cancellation
* project isolation
* durable resume

Agent Runtime 仍然只负责：

* model-driven decision
* AgentStep
* tool authorization
* tool execution coordination
* Agent replay / resume

6. Phase 6 第一阶段不实现：

* Parallel
* Scheduler
* Worker
* Queue
* Celery
* Redis
* Kafka
* Backoff
* Write Tool
* Compensation
* Multi-Agent
* MCP
* LangGraph

7. OD-1 采用 Option 1：

AI Node 默认通过：

AIOrchestratorService.execute()

进入现有 Router。

暂不新增 execute_agent()。

只有出现真实业务需求时，才触发 Phase 5 Change Procedure。

8. Workflow Definition 采用：

Python Code + Version Metadata

必须保证 WorkflowRun 能稳定记录至少：

workflow_name
workflow_version
project_id

Resume 时不得因为代码演进而失去 Workflow Definition 的版本身份。

9. Tool Node：

继续使用唯一的：

ToolExecutionService

Phase 6 当前只允许安全只读 Tool。

Write Tool BLOCK。

不得创建第二套 Tool Executor。

10. Retry：

Owner = Workflow Node

采用当前 ADR 定义的：

fail-before-retry

不得把 Retry 下沉到 Agent Runtime。

11. Parallel：

继续 Deferred。

不要为了实现 Workflow 基础能力提前引入并行执行。

12. Persistence：

严格按照 ADR 的最小模型实现。

优先：

workflow_run
workflow_node_run
workflow_approval

不要提前增加 checkpoint / event store / queue 等架构。

13. Project Isolation：

Workflow 全部持久化实体必须携带 project_id。

project_id 必须来自服务器端可信 ProjectContext / ProjectRegistry。

禁止依赖客户端提交的 project_id 作为最终授权依据。

14. 测试：

优先复用现有测试基础设施。

每完成一个小步骤立即执行真实测试。

不得为了通过测试修改冻结边界。

15. 开始前：

先重新读取：

docs/decisions/Phase/Phase 6 — Workflow Runtime Architecture Decision.md

以及当前 Phase 5 Frozen 文档。

然后只输出：

【Phase 6 Step 1 Implementation Plan】

内容包括：

* Domain Model
* Entity Responsibilities
* State Machine
* Persistence Mapping
* Service Boundaries
* Test Strategy
* Files To Add
* Files To Modify
* Files Forbidden To Modify

暂时不要写代码。

等我确认 Step 1 Implementation Plan 后再开始编码。

STOP。
