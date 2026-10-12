# Phase 5 P-7 — HITL + Cancellation 架构冻结与实现任务书

## 一、阶段目标

在已经完成：

```text
AgentRun
AgentStep
AgentToolResult
Tool Result Write Point
AgentCheckpoint
AgentDecision
Tool Idempotency
Tool Replay
Resume
Replay
Fail Closed
```

之后，本阶段实现 Phase 5 剩余两个核心运行时能力：

```text
HITL
Human-In-The-Loop Approval

Cancellation
Cooperative Cancellation
```

本阶段目标：

> 让 Agent 能够在 Tool 执行前进入 `WAITING_APPROVAL`，等待外部批准/拒绝；同时支持对正在运行的 Agent 发出取消请求，并按照“协作式取消”安全结束。

---

# 二、最终目标架构

最终形成：

```text
                    AgentRuntime
                         │
                         ▼
                    LLM Decision
                         │
                         ▼
                 Tool Authorization
                         │
              ┌──────────┴──────────┐
              │                     │
          APPROVAL NO            APPROVAL YES
              │                     │
              ▼                     ▼
        ToolExecution        WAITING_APPROVAL
                                    │
                                    ▼
                              Checkpoint
                                    │
                             Human Decision
                              │         │
                         APPROVED     REJECTED
                              │         │
                              ▼         ▼
                           Tool       Agent
                         Execution    Stop/Continue
```

Cancellation：

```text
Agent Running
      │
      │ cancel requested
      ▼
CANCEL_REQUESTED
      │
      ▼
当前 Tool 如果正在执行
      │
      │ 不强杀
      ▼
等待 Tool 返回
      │
      ▼
CANCELLED
```

核心原则：

```text
HITL = Tool 执行前暂停
Cancellation = 协作式停止
```

---

# 三、严格范围

本阶段只实现：

```text
1. Approval Request
2. WAITING_APPROVAL
3. Approval Decision
4. Approval Resume
5. Cooperative Cancellation
6. Cancellation Persistence
7. Tests
```

---

# 四、明确禁止

本阶段禁止：

```text
LangGraph
MCP
Multi-Agent
Workflow Engine
Background Queue
Celery
Redis
Kafka
Streaming
Agent UI
完整 Agent Read API
Timeline API
Notification Service
Email
WebSocket
Webhook
Timer Engine
Scheduled Approval
自动升级
自动重试
Tool Side-effect Classification
Tool Compensation
Saga
Distributed Transaction
```

禁止修改：

```text
TextToSQL
RAG
Router 核心策略
Tool Registry
ToolExecutionService contract
SQL Validator
SQL Executor
Conversation Idempotency
Evidence
LLM Provider
Prompt
```

除非发现明确的 P-7 必要 bug。

如果发现非本阶段问题：

**停止并报告，不要顺手修复。**

---

# 五、开始前必须阅读

## Agent

```text
backend/app/agent/runtime.py
backend/app/agent/models.py
backend/app/agent/decision.py
backend/app/agent/checkpoint.py
backend/app/agent/resume.py
backend/app/agent/recorder.py
backend/app/agent/errors.py
backend/app/agent/capability.py
backend/app/agent/llm.py
backend/app/agent/ids.py
```

## Database

```text
backend/app/db/models/agent_run.py
backend/app/db/models/agent_step.py
backend/app/db/models/agent_tool_result.py
backend/app/db/models/agent_checkpoint.py
backend/app/db/models/agent_decision.py

backend/app/db/agent_run_repository.py
backend/app/db/agent_checkpoint_repository.py
backend/app/db/agent_tool_result_repository.py
backend/app/db/agent_decision_repository.py
```

## Tool

```text
backend/app/services/tool_execution_service.py
backend/app/tools/
```

## Orchestrator

```text
backend/app/services/ai_orchestrator_service.py
backend/app/projects/project_orchestrator_factory.py
```

## Tests

重点阅读：

```text
tests/test_agent_runtime.py
tests/test_agent_checkpoint.py
tests/test_agent_checkpoint_db.py
tests/test_agent_resume.py
tests/test_agent_resume_integration.py
tests/test_agent_decision_store.py
tests/test_agent_tool_result_integration.py
```

---

# 六、开始编码前必须确认 P-6 状态

必须确认：

```text
AgentRun                  READY
AgentStep                 READY
AgentToolResult           READY
Tool Result Write Point   READY
AgentCheckpoint           READY
AgentDecision             READY
Tool Idempotency          DECIDED
Tool Replay               READY
Resume                    READY
```

以及：

```text
HITL                     NOT STARTED
Cancellation             NOT STARTED
```

不要重新实现 P-6。

---

# 七、HITL 架构原则

HITL 必须发生在：

```text
LLM Decision
    ↓
Tool Authorization
    ↓
Approval Required?
    ↓ YES
WAITING_APPROVAL
    ↓
Human Decision
    ↓
APPROVED
    ↓
Tool Execution
```

绝对禁止：

```text
LLM
 ↓
Tool Execute
 ↓
Ask Human
```

审批必须发生在 Tool 执行之前。

这也是常见 Agent HITL 设计的核心边界：对需要审批的 Tool Call，在执行前暂停并保存待处理请求，收到响应后再继续。

---

# 八、Approval 不是 Tool Result

明确区分：

```text
AgentDecision
    ↓
Tool Call Proposal

ApprovalRequest
    ↓
Human 是否允许执行

AgentToolResult
    ↓
Tool 实际执行结果
```

不能把：

```text
Approval
```

写进：

```text
AgentToolResult
```

---

# 九、Approval Request 最小模型

建议增加：

```text
AgentApprovalRequest
```

至少：

```text
approval_id
run_id
step_index
tool_call_id
tool_name
status
created_at
resolved_at
decision
```

状态：

```text
PENDING
APPROVED
REJECTED
```

如果现有架构适合 DB persistence：

建议新增：

```text
ai_ops.agent_approval
```

---

# 十、Approval Persistence

如果新增数据库表：

```text
ai_ops.agent_approval
```

最小字段：

```text
id BIGINT PK

approval_id VARCHAR(128) UNIQUE NOT NULL

run_id VARCHAR(64) NOT NULL

step_index INTEGER NOT NULL

tool_call_id VARCHAR(128) NULL

tool_name VARCHAR(128) NOT NULL

status VARCHAR(32) NOT NULL

decision VARCHAR(32) NULL

created_at TIMESTAMPTZ NOT NULL DEFAULT now()

resolved_at TIMESTAMPTZ NULL
```

可以增加：

```text
reason_code
```

但不要保存自由文本审批意见。

---

# 十一、Approval 安全边界

Approval Record 禁止保存：

```text
prompt
messages
RAG chunks
tool definitions
SQL
API Key
password
Authorization
database URL
Tool Result
LLM raw response
exception body
stack trace
```

尤其：

```text
tool arguments
```

原则上不要直接持久化。

如果 UI/未来 Read API 需要展示参数：

本阶段只保留：

```text
arguments_hash
```

或者：

```text
arguments_ref
```

不要保存原始敏感参数。

---

# 十二、Approval Identity

明确：

```text
approval_id
```

是审批身份。

```text
run_id
```

是 Agent Run 身份。

```text
step_index
```

是 Agent Step 身份。

```text
tool_call_id
```

是 Provider protocol correlation。

不要混淆：

```text
approval_id != tool_call_id
approval_id != request_id
approval_id != run_id
```

---

# 十三、Approval Decision

外部审批只能接受：

```text
APPROVED
REJECTED
```

不要允许：

```text
retry
modify
edit_tool
change_arguments
change_tool
```

本阶段不允许人工修改 LLM Tool Call。

如果人工不同意：

```text
REJECTED
```

即可。

---

# 十四、WAITING_APPROVAL

当 Tool 需要审批：

```text
LLM Decision
 ↓
Tool Authorization
 ↓
Approval Required
 ↓
create Approval
 ↓
Checkpoint
```

Checkpoint：

```text
status = PAUSED

next_action = WAITING_APPROVAL

pending_step_index = current step

pending_tool_call_id = tool_call_id

pending_decision_ref = decision_ref

pending_tool_result_ref = NULL
```

---

# 十五、关键不变量

进入：

```text
WAITING_APPROVAL
```

之后：

```text
Tool Handler calls = 0
```

必须保证：

```text
ToolExecutionService.execute()
```

没有发生。

---

# 十六、WAITING_APPROVAL 不写 AgentRun Terminal

P-5 已经确定：

```text
AgentRun
```

是：

```text
terminal execution record
```

因此：

```text
WAITING_APPROVAL
```

不能写：

```text
AgentRun.status = WAITING_APPROVAL
```

也不能把：

```text
WAITING_APPROVAL
```

映射成：

```text
FAILED
```

正确状态：

```text
AgentRun = 尚未终态

AgentCheckpoint = PAUSED / WAITING_APPROVAL
```

---

# 十七、Approval Resolve

增加一个独立操作：

```python
resolve_agent_approval(
    approval_id,
    decision,
)
```

其中：

```text
decision = APPROVED | REJECTED
```

---

# 十八、Approval Resolve 幂等

第一次：

```text
PENDING → APPROVED
```

成功。

第二次：

```text
APPROVED → APPROVED
```

必须：

```text
idempotent
```

不能再次执行 Tool。

---

# 十九、Approval Conflict

如果：

```text
PENDING
```

同时被两个 Worker：

```text
A → APPROVED
B → REJECTED
```

只能一个成功。

另一个：

```text
APPROVAL_CONFLICT
```

必须：

```text
fail closed
```

不得：

```text
retry
overwrite
force
```

建议使用：

```text
state_version
```

或者数据库条件 UPDATE。

---

# 二十、APPROVED 后才允许 Tool

严格：

```text
APPROVED
 ↓
Resume
 ↓
Decision Replay
 ↓
Authorization 再检查
 ↓
Tool Execution
```

特别注意：

> Approval 不是 Authorization。

即使 Approval：

```text
APPROVED
```

也必须重新经过：

```text
Tool Authorization
```

因为：

```text
project
capability
policy
```

可能已经变化。

如果重新 Authorization 失败：

```text
AGENT_TOOL_AUTHORIZATION_FAILED
```

不得执行 Tool。

---

# 二十一、Approval REJECTED

如果：

```text
REJECTED
```

本阶段默认：

```text
Agent FAILED
```

稳定错误码：

```text
AGENT_APPROVAL_REJECTED
```

不要：

```text
重新调用 LLM
```

不要：

```text
重新提出相同 Tool
```

不要：

```text
自动换 Tool
```

---

# 二十二、Approval 不生成 Tool Result

如果：

```text
REJECTED
```

没有发生 Tool execution。

因此：

```text
AgentToolResult = 0
```

---

# 二十三、Approval 与 Resume

Approval Resolution 本身不直接执行复杂 Agent Loop。

建议：

```text
resolve approval
    ↓
APPROVED
    ↓
resume_agent_run(run_id)
```

或者使用内部统一 continuation。

核心要求：

```text
Approval
→ 只改变持久化控制状态

Resume
→ 执行 Agent
```

不要让 Approval Repository 直接调用：

```text
LLM
ToolExecutionService
```

---

# 二十四、Cancellation 架构

Cancellation 采用：

> **Cooperative Cancellation**

而不是：

> Force Kill

---

# 二十五、Cancellation 原则

取消请求：

```text
cancel(run_id)
```

只设置：

```text
cancel_requested = true
```

或者等价的持久化控制状态。

Agent Runtime 在安全边界检查：

```text
LLM call 前
Tool call 前
Tool 返回后
下一轮开始前
```

---

# 二十六、正在运行的 Tool 不强杀

如果：

```text
Tool Handler
```

已经开始：

```text
cancel request
```

不要：

```text
thread.kill
task.cancel
process.kill
```

本阶段不支持强制中断 Tool。

必须：

```text
等待 Tool 返回
```

然后：

```text
CANCELLED
```

这属于 cooperative cancellation；长期运行 Agent 系统通常也将取消/关闭作为协作式信号，而不是强制终止当前 handler。

---

# 二十七、Cancellation 状态

建议：

```text
AgentCancellation
```

最小：

```text
run_id
cancel_requested
requested_at
cancelled_at
reason_code
```

如果当前 Checkpoint 已经足够表达：

可以不新增表。

优先考虑：

```text
AgentCheckpoint
```

增加：

```text
cancel_requested
```

但：

### 必须先检查现有 P-5/P-6 Schema。

如果修改 `agent_checkpoint`：

必须明确记录：

```text
为什么当前 12 列不足
```

不要随便加字段。

---

# 二十八、Cancellation 状态模型

目标：

```text
ACTIVE
   │
   │ cancel()
   ▼
CANCEL_REQUESTED
   │
   │ safe boundary
   ▼
CANCELLED
```

但：

### `CANCEL_REQUESTED` 是否持久化为独立状态，需要根据现有 Checkpoint Schema 决定。

不要为了状态完整而随意增加枚举。

---

# 二十九、Cancellation 与 LLM

如果 Agent 在：

```text
LLM call 前
```

发现：

```text
cancel_requested
```

则：

```text
不调用 LLM
```

直接：

```text
CANCELLED
```

---

# 三十、Cancellation 与 Tool

如果 Agent 在：

```text
Tool authorization 前
```

发现：

```text
cancel_requested
```

则：

```text
Tool = 0
```

直接：

```text
CANCELLED
```

---

# 三十一、Cancellation 与 Tool Execution

如果 Tool 已经执行：

```text
Tool running
    ↓
cancel requested
    ↓
Tool returns
    ↓
persist AgentToolResult
    ↓
AgentStep
    ↓
AgentRun = CANCELLED
```

不能丢失 Tool Result。

---

# 三十二、Cancellation 与 Approval

如果：

```text
WAITING_APPROVAL
```

期间收到：

```text
cancel
```

则：

```text
PENDING
   ↓
CANCELLED
```

Approval：

```text
Tool = 0
```

之后如果有人再：

```text
APPROVED
```

必须：

```text
APPROVAL_CONFLICT / CANCELLED
```

不能执行 Tool。

---

# 三十三、Cancellation 与 Resume

如果：

```text
AgentRun = CANCELLED
```

本阶段：

**禁止 Resume。**

Resume：

```text
AGENT_RUN_NOT_RESUMABLE
```

不要：

```text
CANCELLED → ACTIVE
```

除非未来单独定义 Restart。

---

# 三十四、Cancellation 与 AgentRun

最终：

```text
AgentRun.status = CANCELLED
```

这与：

```text
FAILED
LIMIT_REACHED
COMPLETED
```

明确区分。

不要把：

```text
CANCELLED
```

映射成：

```text
FAILED
```

---

# 三十五、Checkpoint Terminal

Cancellation 完成后：

```text
Checkpoint:
status = TERMINAL

next_action = CANCELLED
```

如果现有 `next_action` 没有 `CANCELLED`：

必须进行最小 domain extension。

不要使用：

```text
FAILED
```

冒充 Cancellation。

---

# 三十六、HITL + Cancellation 竞争

这是本阶段必须测试的核心场景。

情况：

```text
WAITING_APPROVAL
```

同时：

```text
cancel()
```

和：

```text
approve()
```

必须通过：

```text
state_version
claim_token
```

或者 approval conditional update：

只允许一个状态转换获胜。

可能结果：

```text
CANCELLED
```

或者：

```text
APPROVED → Tool
```

但：

**绝不能出现：**

```text
CANCELLED
+
Tool executed
```

或者：

```text
APPROVED
+
cancelled
+
Tool twice
```

---

# 三十七、Cancellation 幂等

第一次：

```text
cancel(run_id)
```

成功。

第二次：

```text
cancel(run_id)
```

返回当前状态：

```text
CANCELLED
```

不得再次：

```text
LLM
Tool
```

---

# 三十八、Terminal Cancellation

已经：

```text
COMPLETED
```

再 Cancel：

```text
no-op
```

已经：

```text
FAILED
```

再 Cancel：

```text
no-op
```

已经：

```text
LIMIT_REACHED
```

再 Cancel：

```text
no-op
```

---

# 三十九、HITL / Cancellation 错误码

至少：

```text
AGENT_APPROVAL_REQUIRED
AGENT_APPROVAL_NOT_FOUND
AGENT_APPROVAL_CONFLICT
AGENT_APPROVAL_REJECTED
AGENT_APPROVAL_EXPIRED
AGENT_TOOL_AUTHORIZATION_FAILED
AGENT_CANCEL_REQUESTED
AGENT_CANCELLED
AGENT_RUN_NOT_CANCELLABLE
AGENT_RUN_NOT_RESUMABLE
AGENT_CANCELLATION_CONFLICT
```

如果现有体系已经存在等价稳定码：

优先复用。

不要重复创建。

注意：

`AGENT_APPROVAL_EXPIRED` 如果本阶段没有 TTL：

**不要实现。**

如果不实现：

不要注册。

---

# 四十、HITL Tool Policy

本阶段不要引入：

```text
ToolDefinition.requires_approval
```

如果当前项目没有这个字段：

不要直接修改 ToolDefinition contract。

可以先由：

```text
AgentCapability / RuntimePolicy
```

提供：

```text
approval_required_tools: frozenset[str]
```

例如：

```text
approval_required_tools = {
    "some_future_tool"
}
```

当前已有只读 Tool：

```text
get_inventory
get_work_order
```

原则上：

```text
默认无需 HITL
```

因为本阶段不应该强行给现有只读 Tool 增加审批。

测试可以使用：

```text
FakeApprovalTool
```

但必须：

```text
read-only
```

---

# 四十一、Authorization 与 Approval 顺序

严格：

```text
LLM Decision
    ↓
Tool Authorization
    ↓
Approval Required?
    ↓
WAITING_APPROVAL
    ↓
Human Approval
    ↓
Tool Authorization AGAIN
    ↓
Tool Execution
```

禁止：

```text
Approval
    ↓
Authorization
```

因为 Approval 不是安全授权。

---

# 四十二、Approval Request 内容

Approval Request 对外只需要：

```text
approval_id
run_id
step_index
tool_name
tool_call_id
status
created_at
```

不要直接返回：

```text
arguments
prompt
context
RAG chunks
SQL
```

---

# 四十三、Approval API

本阶段：

**不建立公开 HTTP API。**

不要新增：

```text
POST /agent/approvals
POST /agent/cancel
```

只实现内部 Application/Service API：

```python
request_approval(...)
resolve_approval(...)
cancel_agent_run(...)
```

未来 Agent Read API 阶段再暴露 HTTP。

---

# 四十四、AgentRuntime Boundary

AgentRuntime 仍然：

```text
DB dependency = 0
```

只能依赖：

```text
ApprovalPort
CheckpointSink
DecisionStorePort
ToolResultSink
```

以及：

```text
CancellationStatePort
```

如果需要。

不得：

```text
SQLAlchemy
Session
Engine
Repository
```

---

# 四十五、Orchestrator Boundary

Orchestrator 可以：

```text
create request_id
resolve Agent capability
call run/resume
resolve approval
request cancellation
```

但：

不能：

```text
实现 Agent Loop
```

不能：

```text
解析 Tool Call
```

不能：

```text
直接执行 Tool
```

---

# 四十六、Conversation Boundary

本阶段：

```text
Conversation
```

仍然只负责：

```text
Conversation
Turn
Idempotency
Context
```

不能：

```text
存 Approval
```

不能：

```text
存 Cancellation
```

不能：

```text
执行 Agent Resume
```

---

# 四十七、Evidence Boundary

继续保持：

```text
Approval != Evidence
Cancellation != Evidence
Checkpoint != Evidence
AgentRun != Evidence
AgentStep != Evidence
AgentToolResult != Evidence
```

本阶段不新增 Evidence。

---

# 四十八、Observability Boundary

保持：

```text
LLMUsageRecord
ToolExecutionRecord
AgentRun
AgentStep
AgentToolResult
AgentCheckpoint
AgentDecision
```

职责不变。

如果 HITL/Cancellation 产生审计需求：

优先扩展：

```text
AgentStep
AgentRun
```

不要复制：

```text
ToolExecutionRecord
```

---

# 四十九、AgentStep

HITL 如果产生：

```text
WAITING_APPROVAL
```

不要伪造一个：

```text
ToolExecution
```

因为 Tool 尚未执行。

如果需要记录：

```text
approval requested
```

可以扩展：

```text
AgentStep.step_type
```

但必须先检查现有 schema。

不要无理由增加新的 AgentStep 表。

---

# 五十、AgentStep State

如果当前：

```text
step_type = FINAL | TOOL_CALL
status = OK | FAILED
```

本阶段需要支持：

```text
WAITING_APPROVAL
CANCELLED
```

则进行最小 domain extension。

但是：

**必须保持：**

```text
step_index
```

不变。

---

# 五十一、Approval State Machine

严格：

```text
PENDING
  │
  ├── APPROVED
  │
  └── REJECTED
```

不得：

```text
APPROVED → PENDING
REJECTED → PENDING
APPROVED → REJECTED
REJECTED → APPROVED
```

---

# 五十二、Cancellation State Machine

严格：

```text
ACTIVE
  │
  ▼
CANCEL_REQUESTED
  │
  ▼
CANCELLED
```

如果当前实现不需要持久化：

```text
CANCEL_REQUESTED
```

可以作为：

```text
runtime signal
```

但最终必须持久化：

```text
AgentRun = CANCELLED
Checkpoint = TERMINAL/CANCELLED
```

---

# 五十三、Cancellation Request Identity

至少：

```text
run_id
```

不需要新增：

```text
cancel_id
```

除非现有并发模型证明确实需要。

本阶段优先保持简单。

---

# 五十四、Cancellation 原子性

如果：

```text
cancel(run_id)
```

与：

```text
resume(run_id)
```

同时发生：

必须使用：

```text
state_version
claim_token
```

保证只有一个状态转换成功。

不能：

```text
resume成功
+
cancel成功
+
两个 LLM
```

---

# 五十五、HITL 与 Resume

Approval：

```text
APPROVED
```

之后必须：

```text
resume(run_id)
```

Resume：

```text
读取 Decision
↓
重新 Authorization
↓
Tool
```

不能：

```text
Approval callback
↓
直接 Tool
```

这样可以保证所有 Agent continuation 都通过统一 Runtime。

---

# 五十六、P-6 Replay 不得回归

必须保持：

```text
历史 LLM Decision
    → replay

历史 Tool Result
    → replay

历史 Tool
    → 0 handler
```

P-7 不得修改：

```text
Decision Store
Tool Result Store
Resume
```

除非发现明确 bug。

---

# 五十七、测试设计

新增：

```text
tests/test_agent_hitl.py
tests/test_agent_cancellation.py
tests/test_agent_hitl_cancellation_race.py
```

如果现有结构适合合并，可以调整。

---

# 五十八、HITL Unit Tests

至少：

```text
approval required
approval not required
request approval
duplicate request
approve
reject
approve twice
reject twice
approve after reject
reject after approve
approval conflict
approval missing
```

---

# 五十九、HITL Security Tests

验证：

```text
WAITING_APPROVAL
→ Tool handler = 0
→ ToolExecutionRecord = 0
→ AgentToolResult = 0
```

批准后：

```text
Authorization = 1
Tool = 1
```

如果 Authorization 失败：

```text
Tool = 0
```

---

# 六十、Cancellation Unit Tests

至少：

```text
cancel active run
cancel completed run
cancel failed run
cancel limit reached run
cancel twice
cancel before LLM
cancel before Tool
cancel after Tool starts
```

---

# 六十一、Cooperative Cancellation Test

重点：

```text
Tool starts
    ↓
cancel requested
    ↓
Tool completes
    ↓
Tool Result persisted
    ↓
AgentRun CANCELLED
```

验证：

```text
Tool handler = 1
Tool Result = 1
LLM after cancellation = 0
final status = CANCELLED
```

---

# 六十二、Cancellation During LLM

如果 LLM call 已经开始：

本阶段不强制中断网络请求。

采用：

```text
LLM returns
    ↓
check cancellation
    ↓
CANCELLED
```

不要：

```text
HTTP force abort
```

除非当前 client 已经具备安全 cancellation contract。

---

# 六十三、Cancellation During Tool

必须：

```text
do not kill
wait
persist result
cancel
```

---

# 六十四、HITL + Cancellation Race

至少：

### Case A

```text
WAITING_APPROVAL
cancel
approve
```

结果只能一个：

```text
CANCELLED
```

或者：

```text
APPROVED → Tool
```

不能：

```text
Tool twice
```

---

### Case B

```text
WAITING_APPROVAL
approve
cancel
```

同样。

---

# 六十五、Resume After Cancellation

测试：

```text
CANCELLED
 ↓
resume()
```

必须：

```text
AGENT_RUN_NOT_RESUMABLE
```

验证：

```text
LLM = 0
Tool = 0
```

---

# 六十六、DB Integration

新增：

```text
tests/test_agent_hitl_db.py
tests/test_agent_cancellation_db.py
```

如果测试结构允许，也可以合并。

使用：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest ...
```

必须验证真实 PostgreSQL：

```text
agent_run
agent_step
agent_tool_result
agent_checkpoint
agent_decision
```

以及新增 Approval persistence（如果采用新表）。

---

# 六十七、DB Residue

完成 DB tests 后：

```text
agent_run = 0
agent_step = 0
agent_tool_result = 0
agent_checkpoint = 0
agent_decision = 0
agent_approval = 0
```

如果没有：

```text
agent_approval
```

则明确：

```text
agent_approval = N/A
```

最终：

```text
DB residue = 0
```

---

# 六十八、Full Regression

执行：

```powershell
python -m pytest -q
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

如果仍然存在历史 order-dependent failures：

必须记录：

```text
pre-existing
```

不得修改 baseline。

---

# 六十九、Compile / Lint

执行：

```powershell
python -m compileall -q backend tests scripts
```

如果项目已有 lint：

继续执行。

要求：

```text
compileall = PASS
lint = PASS
```

---

# 七十、Architecture Audit

必须确认：

```text
AgentRuntime DB dependency = 0
Tool Framework Agent dependency = 0
Conversation Agent persistence dependency = 0
Evidence dependency = 0
```

---

# 七十一、Git Diff

执行：

```powershell
git status --short
git diff --stat
git diff
```

检查：

```text
没有 Prompt 修改
没有 TextToSQL 修改
没有 RAG 修改
没有 Router 策略修改
没有 Tool Framework 修改
没有 SQL Validator 修改
没有 SQL Executor 修改
没有 Conversation Idempotency 修改
没有 Evidence 修改
没有无关依赖
没有 secrets
没有生产数据
```

---

# 七十二、Commit

全部通过后：

```powershell
git add backend tests docs
git commit -m "feat: Phase 5 HITL and cooperative cancellation"
```

不要：

```text
git push
git merge
git rebase
git reset
```

---

# 七十三、最终报告格式

完成后严格报告：

```text
【Phase 5 P-7 COMPLETE】

1. HITL Architecture
- Approval model:
- Approval identity:
- WAITING_APPROVAL:
- Tool execution before approval:
- Tool execution after approval:

2. Approval Persistence
- table:
- repository:
- idempotency:
- concurrency:

3. Cancellation Architecture
- cancellation model:
- cooperative boundary:
- Tool running时:
- LLM running时:

4. State Machine
- Approval:
- Cancellation:

5. Resume Integration
- APPROVED:
- REJECTED:
- CANCELLED:

6. Security
- approval:
- cancellation:
- authorization re-check:

7. Tests
- offline:
- DB:
- full regression:

8. DB Residue
- agent_run:
- agent_step:
- agent_tool_result:
- agent_checkpoint:
- agent_decision:
- agent_approval:

9. compile / lint

10. Architecture Audit
- AgentRuntime DB dependency:
- Tool Framework Agent dependency:
- Conversation dependency:
- Evidence dependency:

11. Git Diff

12. Commit
- hash:
- message:

13. Known Limitations

14. Architecture State
- AgentRun: READY
- AgentStep: READY
- AgentToolResult: READY
- Tool Result Write Point: READY
- AgentCheckpoint: READY
- AgentDecision: READY
- Tool Idempotency: DECIDED
- Tool Replay: READY
- Resume: READY
- HITL: READY
- Cancellation: READY
- Agent Read API: NOT STARTED

Phase 5 P-7 STOP
```

---

# 七十四、强制 STOP

完成 P-7 后：

**立即停止。**

不要继续：

```text
Agent Read API
Agent Timeline API
Agent UI
Streaming
Background Agent
Workflow Runtime
Multi-Agent
LangGraph
MCP
```

下一阶段必须由用户明确下达。

---

# 七十五、最终架构

P-7 完成后，Phase 5 Agent Runtime 应达到：

```text
                         AgentRuntime
                              │
                              ▼
                         LLM Decision
                              │
                              ▼
                       Authorization
                              │
                    ┌─────────┴─────────┐
                    │                   │
              approval=no         approval=yes
                    │                   │
                    │                   ▼
                    │            WAITING_APPROVAL
                    │                   │
                    │             Human Decision
                    │              │         │
                    │          APPROVED    REJECTED
                    │              │         │
                    └──────────────┤         ▼
                                   │       FAILED
                                   ▼
                              Tool Execute
                                   │
                                   ▼
                              Tool Result
                                   │
                                   ▼
                              Checkpoint
                                   │
                                   ▼
                               Next LLM
```

Cancellation：

```text
Agent
 │
 ├── before LLM
 │      └── CANCELLED
 │
 ├── before Tool
 │      └── CANCELLED
 │
 ├── Tool running
 │      └── wait
 │           ↓
 │       persist result
 │           ↓
 │        CANCELLED
 │
 └── WAITING_APPROVAL
        │
        └── cancel
             ↓
          CANCELLED
```

最终核心不变量：

```text
HITL:
    Approval before Tool
    Tool before approval = 0

Cancellation:
    cooperative
    no force kill
    running Tool finishes
    Tool Result is preserved

Resume:
    APPROVED → Resume
    REJECTED → no Tool
    CANCELLED → no Resume

Concurrency:
    state_version + claim_token

AgentRuntime:
    DB dependency = 0

Conversation:
    does not own Agent persistence

Evidence:
    independent

Historical Tool:
    never blindly rerun
```

**Phase 5 P-7 完成后立即 STOP。**
