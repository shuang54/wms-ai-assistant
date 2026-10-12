# Phase 5 P-6 — Agent Resume / Replay 实现任务书

## 一、阶段目标

在已经完成：

```text
AgentRun
AgentStep
Agent Tool Observation
AgentToolResult
AgentToolResult Write Point
AgentCheckpoint
Tool Idempotency Decision
Tool Replay Decision
```

的基础上，实现：

> **Bounded Agent 的持久化 Resume / Replay。**

本阶段解决：

```text
Agent 执行中断
    ↓
已有 Checkpoint
    ↓
读取 Agent Decision
    ↓
读取 Tool Result
    ↓
恢复 Agent Runtime
    ↓
继续执行
```

核心原则：

```text
已经完成的 LLM 调用
    → 不重新调用 LLM

已经成功执行并持久化的 Tool
    → 不重新执行 Tool
    → Replay 历史 Tool Result

状态不确定
    → Fail Closed
    → 不盲目重跑
```

这与 Durable Agent 的基本恢复原则一致：持久化执行状态后，从最近完成状态继续，而不是重复已经完成的 LLM/Tool 工作。

---

# 二、严格范围

本阶段只实现：

```text
LLM Decision Store
        ↓
Checkpoint Decision Reference
        ↓
Resume API / Runtime Entry
        ↓
Decision Replay
        ↓
Tool Result Replay
        ↓
Continue Agent Runtime
```

---

# 三、明确禁止

本阶段禁止实现：

```text
HITL
WAITING_APPROVAL
Cancellation
Agent Read API
Agent Timeline API
Agent UI
Streaming
Background Job
Queue
Redis
Kafka
Celery
Durable Task
LangGraph
Multi-Agent
Workflow Engine
Model Fallback
自动 Retry
Tool Side-effect Classification
Tool 自动重试
```

也禁止：

```text
修改 Prompt
修改 Router 核心策略
修改 TextToSQL
修改 RAG
修改 Tool Registry
修改 SQL Validator
修改 SQL Executor
修改 Conversation Idempotency
```

不要因为 Resume 需求重新设计 Agent Runtime。

---

# 四、开始前必须阅读

先阅读真实代码。

## Agent

```text
backend/app/agent/runtime.py
backend/app/agent/models.py
backend/app/agent/decision.py
backend/app/agent/checkpoint.py
backend/app/agent/recorder.py
backend/app/agent/ids.py
backend/app/agent/capability.py
backend/app/agent/llm.py
```

## Database

```text
backend/app/db/models/agent_run.py
backend/app/db/models/agent_step.py
backend/app/db/models/agent_tool_result.py
backend/app/db/models/agent_checkpoint.py

backend/app/db/agent_run_repository.py
backend/app/db/agent_tool_result_repository.py
backend/app/db/agent_checkpoint_repository.py
```

## Orchestrator

```text
backend/app/services/ai_orchestrator_service.py
backend/app/projects/project_orchestrator_factory.py
backend/app/api/orchestrator_chat.py
```

## Tool

```text
backend/app/services/tool_execution_service.py
backend/app/tools/
```

## LLM

```text
backend/app/llm/provider.py
backend/app/llm/response.py
backend/app/llm/
```

## Tests

重点阅读：

```text
tests/test_agent_runtime.py
tests/test_agent_checkpoint.py
tests/test_agent_checkpoint_db.py
tests/test_agent_checkpoint_integration.py
tests/test_agent_tool_result_integration.py
tests/test_agent_run_persistence.py
```

以及当前所有 Agent Integration 测试。

---

# 五、先确认当前 P-5 实际状态

开始编码前必须确认：

```text
AgentRun READY
AgentStep READY
AgentToolResult READY
ToolResult Write Point READY
AgentCheckpoint READY
```

同时确认：

```text
pending_decision_ref = NULL
```

这是本阶段需要解决的核心缺口。

---

# 六、P-6 总体架构

目标：

```text
                 AgentRuntime
                      │
                      │ LLM Decision
                      ▼
              ┌──────────────────┐
              │ AgentDecisionStore│
              └────────┬─────────┘
                       │
                       │ decision_ref
                       ▼
                AgentCheckpoint
                       │
                       │
              ┌────────┴─────────┐
              │                  │
          TOOL_CALL             FINAL
              │
              ▼
       ToolExecutionService
              │
              ▼
       AgentToolResult
              │
              ▼
        AgentCheckpoint
```

Resume：

```text
resume(run_id)
      │
      ▼
AgentCheckpoint
      │
      ├── terminal
      │
      └── active
             │
             ▼
      pending_decision_ref
             │
             ▼
      AgentDecisionStore
             │
             ▼
       saved Decision
             │
       ┌─────┴─────┐
       │           │
   FINAL       TOOL_CALL
                   │
                   ▼
           AgentToolResult
                   │
              replay result
                   │
                   ▼
              next LLM call
```

---

# 七、Decision Store 是 P-6 第一优先级

当前：

```text
AgentCheckpoint.pending_decision_ref
```

没有实际内容。

因此首先建立：

```text
Agent Decision Store
```

---

# 八、Agent Decision Store 设计

新增：

```text
ai_ops.agent_decision
```

不要把 Decision 塞进：

```text
agent_checkpoint
```

不要把完整 messages 塞进数据库。

---

# 九、Decision Store 最小 Schema

建议：

```text
id BIGINT PK

run_id VARCHAR(64) NOT NULL

step_index INTEGER NOT NULL

decision_ref VARCHAR(128) NOT NULL

decision_type VARCHAR(32) NOT NULL

tool_call_id VARCHAR(128) NULL

tool_name VARCHAR(128) NULL

arguments JSONB NULL

content TEXT NULL

finish_reason VARCHAR(64) NULL

created_at TIMESTAMPTZ NOT NULL DEFAULT now()
```

必须：

```text
UNIQUE(run_id, step_index)
UNIQUE(decision_ref)
```

如果当前代码已有更合适的命名，可以遵循现有命名，但不要扩大字段。

---

# 十、Decision Store 安全边界

**绝对禁止保存：**

```text
system prompt
user prompt
完整 messages
conversation context
RAG chunks
tool definitions
API Key
Authorization
Cookie
password
database URL
SQL
Tool Result
LLM raw response
headers
stack trace
exception message
```

Decision Store 只保存：

```text
恢复 Agent 控制流真正需要的 Decision
```

例如：

```text
FINAL
TOOL_CALL
tool_name
tool_call_id
arguments
content
finish_reason
```

---

# 十一、Arguments 安全要求

`arguments` 来自：

```text
Provider ToolCall.arguments
```

虽然已经经过 Provider JSON parsing，但仍然属于：

```text
untrusted LLM output
```

因此：

1. 必须 JSON-safe
2. 禁止 ORM
3. 禁止 bytes
4. 禁止 Session
5. 禁止 Connection
6. 禁止 Python object
7. 必须限制 payload size

建议：

```text
MAX_DECISION_ARGUMENTS_SIZE = 32 KiB
```

超过限制：

```text
decision_unavailable
```

不要截断。

不要部分保存。

---

# 十二、Decision Identity

定义：

```text
run_id
```

Agent 执行身份。

```text
step_index
```

Agent Step 坐标。

```text
decision_ref
```

Decision Store 的稳定引用。

```text
tool_call_id
```

Provider Protocol correlation。

不要混淆：

```text
decision_ref != tool_call_id
```

---

# 十三、Decision Write Point

Decision 必须在：

```text
LLM Decision
        ↓
Decision validation
        ↓
Decision accepted
        ↓
Decision Store
        ↓
Tool execution / FINAL
```

之后才继续。

尤其：

```text
TOOL_CALL
```

必须：

```text
Decision persisted
    ↓
Tool execute
```

不能：

```text
Tool execute
    ↓
Decision persist
```

否则 Tool 已执行但 Decision 丢失，Resume 无法安全判断。

---

# 十四、Decision Store 写入失败

本阶段采用：

> **Fail Closed。**

如果：

```text
LLM returned valid decision
↓
Decision Store failed
```

则：

```text
不得执行 Tool
```

也不得：

```text
继续第二次 LLM
```

Agent 应：

```text
FAILED
```

稳定错误码建议：

```text
AGENT_DECISION_PERSISTENCE_FAILURE
```

---

# 十五、Checkpoint pending_decision_ref

P-6 后：

```text
AgentCheckpoint.pending_decision_ref
```

必须真正使用。

例如：

```text
run_id = abc
step_index = 2
decision_ref = abc:2
```

Checkpoint：

```text
pending_decision_ref = abc:2
```

Resume：

```text
checkpoint
 ↓
decision_ref
 ↓
decision store
 ↓
Decision
```

---

# 十六、Decision Replay 原则

Resume 时：

```text
pending_decision_ref
```

存在：

直接读取 Decision。

**不得重新调用 LLM。**

这是本阶段最重要的安全要求。

---

# 十七、Decision 缺失

如果：

```text
pending_decision_ref != NULL
```

但：

```text
AgentDecisionStore.get() == None
```

必须：

```text
FAIL CLOSED
```

稳定错误码：

```text
AGENT_DECISION_NOT_FOUND
```

禁止：

```text
重新问 LLM
重新生成 Decision
猜测 Tool
继续执行
```

---

# 十八、Decision 与 Checkpoint 一致性

读取：

```text
checkpoint
```

以后必须验证：

```text
decision.run_id == checkpoint.run_id
```

以及：

```text
decision.step_index == checkpoint.pending_step_index
```

如果不一致：

```text
AGENT_CHECKPOINT_DECISION_MISMATCH
```

立即停止。

---

# 十九、FINAL Decision Resume

如果保存：

```text
decision_type = FINAL
```

Resume：

```text
checkpoint
 ↓
Decision
 ↓
FINAL
 ↓
返回 final content
```

不得再次调用：

```text
LLM
Tool
Validator
```

如果已有 AgentRun terminal 状态：

直接返回 terminal result。

---

# 二十、TOOL_CALL Decision Resume

如果：

```text
decision_type = TOOL_CALL
```

必须：

```text
Decision
 ↓
Tool identity
 ↓
AgentToolResult
```

---

# 二十一、Tool Replay

根据 P-5 Decision：

### 已成功

```text
AgentToolResult.success = true
```

Resume：

```text
读取 result
↓
Replay
↓
禁止执行 handler
```

然后：

```text
LLM
```

继续下一步。

---

# 二十二、Tool Failure Replay

如果：

```text
AgentToolResult.success = false
```

Resume：

```text
Replay failure
```

本阶段：

**不要自动重试 Tool。**

按照当前 Agent 失败语义终止：

```text
FAILED
```

除非现有 Runtime 已定义明确的失败继续语义。

不要自行增加 retry policy。

---

# 二十三、Tool Result Missing

如果：

```text
Decision = TOOL_CALL
```

但是：

```text
AgentToolResult != found
```

必须：

```text
FAIL CLOSED
```

错误：

```text
AGENT_TOOL_RESULT_NOT_FOUND
```

禁止：

```text
重新执行 Tool
```

尤其不能因为 Tool 是：

```text
READ_ONLY
```

就自动重跑。

P-5 已明确：

> Read-only ≠ safe blind rerun。

---

# 二十四、Tool Result Identity

Resume 查找：

```text
run_id
step_index
```

而不是：

```text
tool_call_id
```

即：

```text
AgentToolResult(run_id, step_index)
```

是唯一 replay identity。

`tool_call_id` 只用于协议关联。

---

# 二十五、Resume 后新的 LLM Call

如果：

```text
Decision replay
↓
Tool Result replay
↓
继续 Agent
```

那么下一次 LLM 调用是：

> 新的 LLM Call。

它必须：

```text
新的 AgentStep
新的 LLM Observation
新的 LLM Usage
新的 Decision
```

不能覆盖旧 Decision。

例如：

```text
step 0
  Decision TOOL_CALL
  Tool Result

step 1
  Decision FINAL
```

Resume 后：

```text
step 0
  replay

step 1
  new LLM call
```

---

# 二十六、Step Index

严格保持：

```text
0-based
```

例如：

```text
step 0 → first LLM decision
step 1 → second LLM decision
step 2 → third LLM decision
```

Tool Result：

```text
same step_index
```

不要使用：

```text
round
```

代替。

---

# 二十七、Resume 不改变 run_id

必须：

```text
original run_id
      =
resume run_id
```

不能：

```text
resume → new run_id
```

否则：

```text
AgentCheckpoint
AgentStep
AgentToolResult
AgentDecision
```

无法形成同一个 Agent Run。

---

# 二十八、Resume 的 request_id

Resume 是一次新的 Runtime request。

因此：

```text
run_id = unchanged
request_id = new
```

不能复用旧：

```text
request_id
```

原因：

```text
request_id = request/assistant execution correlation
run_id = Agent execution identity
```

---

# 二十九、Conversation 不参与 Resume

Resume API 不允许：

```text
conversation_id
turn_id
idempotency_key
```

成为 Agent Resume 主键。

Resume：

```text
run_id
```

是唯一业务身份。

Conversation 只作为外部调用上下文。

Agent Runtime 不得：

```text
query Conversation
create ConversationTurn
modify ConversationTurn
```

---

# 三十、Resume Entry

建议增加一个独立 Runtime 入口：

```python
resume(run_id: str, ...)
```

或者：

```python
resume_agent_run(run_id: str, ...)
```

具体命名遵循当前项目结构。

不要修改：

```text
run()
```

现有语义。

建议：

```text
run()
    → 新 Agent Run

resume(run_id)
    → 已存在 Agent Run
```

---

# 三十一、Resume 不重新构造原始 Prompt

这是非常重要的。

Resume：

**不依赖重新获取原始 User Prompt。**

不能：

```text
run_id
 ↓
Conversation
 ↓
重新获取 messages
 ↓
重新调用 LLM
```

本阶段恢复依赖：

```text
Checkpoint
Decision Store
Tool Result Store
```

---

# 三十二、Context 处理

P-6 不实现完整历史 Context 恢复。

只恢复：

```text
Agent control state
```

即：

```text
Decision
Tool Result
Step position
```

如果下一次 LLM 调用需要 context：

必须复用当前 Agent Runtime 已有的安全 context 输入机制。

不得偷偷：

```text
查询 Conversation
查询 DB
查询历史消息
```

---

# 三十三、Checkpoint 状态转换

P-5 已经有：

```text
ACTIVE
TERMINAL
```

P-6 使用：

### Tool Result 已持久化

```text
ACTIVE
next_action = LLM_DECISION
pending_decision_ref = ...
```

Resume 后：

```text
Decision replay
↓
Tool Result replay
↓
next LLM
```

---

# 三十四、Resume 完成后的 Checkpoint

如果 Agent 完成：

```text
next_action = COMPLETED
status = TERMINAL
```

如果失败：

```text
next_action = FAILED
status = TERMINAL
```

必须通过：

```text
state_version
claim_token
```

进行更新。

不能：

```text
blind UPDATE
```

---

# 三十五、并发 Resume

P-5 已经实现：

```text
state_version
claim_token
```

P-6 必须使用它。

如果两个 Worker 同时：

```text
resume(run_id)
```

要求：

```text
Worker A → state_version update success
Worker B → conflict
```

Worker B：

```text
FAIL CLOSED
```

错误：

```text
AGENT_RESUME_CONFLICT
```

禁止：

```text
retry
takeover
claim rotation
```

---

# 三十六、Resume Idempotency

同一个：

```text
run_id
```

重复 Resume。

要求：

### 已 TERMINAL

直接返回历史 terminal result。

不要：

```text
LLM
Tool
```

---

### ACTIVE

只有一个 Resume 可以成功获得状态更新权。

其他：

```text
conflict
```

---

# 三十七、Terminal Result

当前 AgentRun 只保存：

```text
status
error_code
limit_reason
counts
```

并没有保存：

```text
final answer
```

因此：

**本阶段不要为了 Resume 返回最终 answer 而修改 AgentRun schema。**

如果现有系统已经可以从：

```text
AgentStep / Decision Store
```

安全恢复 FINAL content：

可以使用。

否则：

```text
terminal result available
but final content unavailable
```

应明确返回：

```text
AGENT_FINAL_RESULT_UNAVAILABLE
```

不要重新调用 LLM。

不要修改历史 AgentRun。

---

# 三十八、AgentStep Replay

AgentStep 是：

```text
Runtime State + Audit Projection
```

P-6 不新增：

```text
REPLAY
RESUMED
```

等状态。

保持：

```text
OK
FAILED
```

Step persistence 只记录实际发生的 Step。

Replay 本身：

**不新增一个“假的 Step”。**

---

# 三十九、AgentToolResult Replay

Replay 不创建新的：

```text
AgentToolResult
```

否则会污染：

```text
(run_id, step_index)
```

历史执行事实。

Replay：

```text
read only
```

---

# 四十、AgentRun Replay

Resume 不创建新的：

```text
AgentRun
```

使用原：

```text
run_id
```

---

# 四十一、LLM Observation

Resume 读取旧 Decision：

```text
LLM calls = 0
```

因为没有新的 LLM。

Tool Replay：

```text
Tool handler calls = 0
```

因为没有新的 Tool。

只有 Resume 继续到下一次真实 LLM 时：

```text
LLM calls +1
```

---

# 四十二、严格安全边界

Resume 期间：

禁止：

```text
重新执行历史 Tool
重新执行历史 LLM
重新执行 SQL
重新执行 HTTP
重新执行 destructive action
```

除非是：

```text
恢复后新的明确 LLM Decision
```

产生新的 Tool Call。

---

# 四十三、测试设计

新增：

```text
tests/test_agent_resume.py
```

以及：

```text
tests/test_agent_decision_store.py
tests/test_agent_resume_integration.py
```

如果现有结构更合适，可以调整。

---

# 四十四、Decision Store 单元测试

至少：

```text
create decision
get decision
duplicate decision
wrong run
wrong step
invalid decision type
invalid arguments
oversized arguments
unsafe arguments
missing decision
```

---

# 四十五、Decision Persistence 测试

验证：

```text
LLM Decision
 ↓
Decision Store
 ↓
Checkpoint.pending_decision_ref
```

一致。

---

# 四十六、Resume FINAL 测试

场景：

```text
LLM
 ↓
FINAL
 ↓
persist Decision
 ↓
Checkpoint
 ↓
process crash
```

Resume：

```text
Decision replay
↓
FINAL
```

要求：

```text
LLM calls = 0
Tool calls = 0
```

---

# 四十七、Resume TOOL_CALL 成功测试

构造：

```text
Decision TOOL_CALL
Tool Result success
Checkpoint ACTIVE
```

Resume：

```text
Decision replay
↓
Tool Result replay
↓
new LLM
↓
FINAL
```

验证：

```text
historical Tool handler = 0
new LLM = 1
```

---

# 四十八、Resume TOOL_CALL failure

构造：

```text
Decision TOOL_CALL
Tool Result success=false
```

Resume：

```text
replay failure
↓
FAILED
```

验证：

```text
Tool handler = 0
LLM = 0
```

---

# 四十九、Missing Decision

```text
Checkpoint.pending_decision_ref
        ↓
Decision Store missing
```

必须：

```text
FAILED
AGENT_DECISION_NOT_FOUND
```

验证：

```text
LLM = 0
Tool = 0
```

---

# 五十、Missing Tool Result

```text
Decision = TOOL_CALL
Tool Result missing
```

必须：

```text
FAILED
AGENT_TOOL_RESULT_NOT_FOUND
```

验证：

```text
Tool = 0
LLM = 0
```

---

# 五十一、Mismatch

测试：

```text
decision.run_id != checkpoint.run_id
```

以及：

```text
decision.step_index != checkpoint.pending_step_index
```

必须：

```text
AGENT_CHECKPOINT_DECISION_MISMATCH
```

---

# 五十二、Concurrent Resume

两个并发 Resume：

```text
A success
B conflict
```

验证：

```text
只允许一个继续
```

不能：

```text
两个 LLM
两个 Tool
```

---

# 五十三、Terminal Resume

已经：

```text
COMPLETED
```

再次 Resume：

```text
LLM = 0
Tool = 0
```

已经：

```text
FAILED
```

再次 Resume：

同样：

```text
LLM = 0
Tool = 0
```

---

# 五十四、Original Run Id

验证：

```text
original run_id
==
resume run_id
```

同时：

```text
original request_id
!=
resume request_id
```

---

# 五十五、Replay Security

构造 Tool Result：

```text
{
  "data": {
      "password": "...",
      "authorization": "...",
      "database_url": "..."
  }
}
```

验证：

```text
Agent Resume
```

不能把敏感信息重新写入：

```text
Decision
Checkpoint
AgentStep
AgentRun
```

---

# 五十六、No Blind Rerun

这是 P-6 最关键的测试。

模拟：

```text
Tool execution state = UNKNOWN
```

如果现有状态无法证明：

```text
Tool Result persisted
```

必须：

```text
FAIL CLOSED
```

而不是：

```text
rerun Tool
```

---

# 五十七、DB Integration

新增：

```text
tests/test_agent_resume_integration.py
```

使用：

```text
RUN_DB_TESTS=1
```

验证真实 PostgreSQL：

```text
agent_decision
agent_checkpoint
agent_tool_result
agent_step
agent_run
```

完整恢复链路。

---

# 五十八、DB Residue

所有 DB Integration Test 完成后：

```text
agent_decision = 0
agent_checkpoint = 0
agent_tool_result = 0
agent_step = 0
agent_run = 0
```

必须：

```text
DB residue = 0
```

不得：

```text
DELETE production data
```

只允许：

```text
test fixture cleanup
```

---

# 五十九、现有 Regression

必须执行：

```powershell
python -m pytest -q
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

如果完整 DB regression 仍然存在之前的：

```text
3 个 / 6 个 order-dependent failures
```

必须明确区分：

```text
Pre-existing
```

不能为了 P-6 通过而修改 baseline。

---

# 六十、compile / lint

执行：

```powershell
python -m compileall backend tests scripts
```

如果项目已有 lint：

继续运行。

要求：

```text
compileall = PASS
lint = PASS
```

---

# 六十一、Architecture Audit

必须检查：

```text
backend/app/agent/runtime.py
```

不能出现：

```text
sqlalchemy
backend.app.db
Session
Engine
Repository
```

AgentRuntime 仍然必须：

```text
DB dependency = 0
```

允许：

```text
Injected Port / Sink
```

---

# 六十二、Decision Store Architecture Audit

Decision Store 必须：

```text
DB access
    ↓
Repository
```

而不是：

```text
AgentRuntime
    ↓
SQLAlchemy
```

AgentRuntime：

```text
DecisionStore Protocol
CheckpointSink
ToolResultSink
```

只依赖抽象。

---

# 六十三、Orchestrator Boundary

Orchestrator 可以：

```text
create request_id
resolve Agent capability
call run/resume
record AgentRun
```

但是：

不能：

```text
实现 Agent Loop
```

也不能：

```text
解析 Tool Call
```

---

# 六十四、Tool Boundary

ToolExecutionService 不得知道：

```text
AgentCheckpoint
AgentDecision
AgentResume
```

Tool Framework 不知道 Agent persistence。

---

# 六十五、Conversation Boundary

Conversation Runtime 不得：

```text
读取 agent_decision
读取 agent_checkpoint
执行 Tool Replay
```

Resume：

```text
Agent Runtime capability
```

---

# 六十六、Evidence Boundary

不得：

```text
AgentDecision → Evidence
AgentCheckpoint → Evidence
AgentToolResult → Evidence
```

本阶段不增加 Evidence。

---

# 六十七、Observability Boundary

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

职责：

```text
LLMUsageRecord = LLM-level observation
ToolExecutionRecord = Tool-level observation
AgentStep = Agent execution projection
AgentToolResult = Tool replay data
AgentCheckpoint = Recovery control state
AgentDecision = LLM decision replay data
AgentRun = terminal execution summary
```

不要重复字段。

---

# 六十八、不要新增 Resume 状态

P-6 不实现：

```text
RESUMING
REPLAYING
RESUMED
```

保持：

```text
ACTIVE
TERMINAL
```

Resume 是动作，不是新的持久化生命周期状态。

---

# 六十九、错误码

至少定义：

```text
AGENT_DECISION_PERSISTENCE_FAILURE
AGENT_DECISION_NOT_FOUND
AGENT_CHECKPOINT_DECISION_MISMATCH
AGENT_TOOL_RESULT_NOT_FOUND
AGENT_RESUME_CONFLICT
AGENT_FINAL_RESULT_UNAVAILABLE
```

如果现有错误体系已有对应稳定码：

优先复用。

不要建立重复错误码。

---

# 七十、性能

本阶段不做性能优化。

只记录必要：

```text
resume lookup count
```

不要新增：

```text
cache
Redis
memory cache
```

---

# 七十一、真实 DeepSeek

本阶段默认：

```text
DeepSeek calls = 0
```

所有测试：

```text
Fake LLM
```

完成离线 Resume 验证后，如果项目已有 real LLM smoke：

可以单独人工运行。

但：

**不得为了 P-6 自动调用真实 DeepSeek。**

---

# 七十二、禁止修改现有 Agent 行为

尤其：

```text
AgentRuntime.run()
```

原有：

```text
FINAL
TOOL_CALL
LIMIT_REACHED
FAILED
```

语义必须保持。

P-6 新增：

```text
resume()
```

而不是修改现有：

```text
run()
```

---

# 七十三、Git Diff 检查

完成后：

```powershell
git status --short
git diff --stat
git diff
```

必须确认：

```text
没有 Prompt 修改
没有 Router 策略修改
没有 TextToSQL 修改
没有 RAG 修改
没有 Tool Framework 修改
没有 SQL Validator 修改
没有 SQL Executor 修改
没有 Conversation Idempotency 修改
没有 Evidence 修改
没有无关依赖
没有 secrets
```

---

# 七十四、建议文件

原则上新增：

```text
backend/app/db/models/agent_decision.py
backend/app/db/agent_decision_repository.py

tests/test_agent_decision_store.py
tests/test_agent_resume.py
tests/test_agent_resume_integration.py
```

如果当前架构存在更合适位置，可以调整。

不要为了文件数量强行新增。

---

# 七十五、提交

所有测试通过后：

```powershell
git status --short
git diff --stat
git add backend tests docs
git commit -m "feat: Phase 5 agent resume and replay"
```

不要：

```text
git push
git merge
git rebase
git reset
```

---

# 七十六、最终报告格式

完成后严格报告：

```text
【Phase 5 P-6 COMPLETE】

1. Decision Store
- table:
- repository:
- decision identity:
- security boundary:

2. Checkpoint Integration
- pending_decision_ref:
- state_version:
- claim_token:

3. Resume
- run_id:
- request_id:
- resume entry:

4. Decision Replay
- FINAL:
- TOOL_CALL:

5. Tool Replay
- success:
- failure:
- missing result:

6. Fail Closed
- missing decision:
- decision mismatch:
- missing tool result:
- concurrent resume:

7. Tests
- offline:
- DB:
- full regression:

8. DB Residue
- agent_decision:
- agent_checkpoint:
- agent_tool_result:
- agent_step:
- agent_run:

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
- HITL: NOT STARTED
- Cancellation: NOT STARTED
- Agent Read API: NOT STARTED

Phase 5 P-6 STOP
```

---

# 七十七、强制 STOP

完成 P-6 后：

**立即停止。**

不要进入：

```text
P-7 HITL
P-7 Cancellation
Agent Read API
Agent UI
Streaming
Workflow Runtime
Multi-Agent
LangGraph
```

下一阶段必须由用户明确下达。

# 最终架构

完成 P-6 后应达到：

```text
                    AgentRuntime
                         │
                         ▼
                    LLM Decision
                         │
                         ▼
                 AgentDecisionStore
                         │
                         ▼
                  AgentCheckpoint
                         │
              ┌──────────┴──────────┐
              │                     │
          TOOL_CALL               FINAL
              │                     │
              ▼                     ▼
      ToolExecutionService       Answer
              │
              ▼
       AgentToolResult
              │
              ▼
          Checkpoint
              │
              ▼
         Next LLM Call
```

恢复：

```text
resume(run_id)
      │
      ▼
AgentCheckpoint
      │
      ▼
AgentDecisionStore
      │
      ├── FINAL
      │     └── return
      │
      └── TOOL_CALL
             │
             ▼
       AgentToolResult
             │
             ├── success → replay → new LLM
             │
             └── failure → FAILED
```

核心保证：

```text
已完成 LLM
    → 不重复调用

已完成 Tool
    → 不重复执行

状态不确定
    → Fail Closed

Resume
    → 同一个 run_id

Resume request
    → 新 request_id

并发 Resume
    → state_version + claim_token

AgentRuntime
    → DB dependency = 0
```

**P-6 完成后立即 STOP。**
