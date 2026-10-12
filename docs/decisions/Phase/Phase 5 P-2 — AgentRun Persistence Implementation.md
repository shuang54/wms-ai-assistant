你现在开始执行：

# Phase 5 P-2 — AgentRun Persistence Implementation

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、目标

根据已经完成的：

```text
Phase 5 Agent Persistence Architecture Decision
```

正式实现 **AgentRun Run-level Persistence MVP**。

已经冻结的架构：

```text
ConversationRuntime
        ↓
AIOrchestrator
        ↓
AgentRuntime
        ↓
AgentRuntimeResult
        ↓
BestEffort AgentRunRecorder
        ↓
ai_ops.agent_run
```

核心原则：

```text
AgentRuntime = 纯内存执行
AgentRunRecorder = 持久化边界
agent_run = Run-level state
AgentStep = 暂不持久化
LLMUsageRecord = 继续负责 LLM-level observation
ToolExecutionRecord = 继续负责 Tool-level observation
Conversation = 不归 Agent 所有
```

本阶段只实现：

> AgentRun 持久化。

不要进入 AgentStep / Resume / HITL / Cancellation。

---

# 二、必须先阅读

先阅读：

```text
backend/app/agent/
backend/app/services/ai_orchestrator_service.py
backend/app/db/
backend/app/db/models/
backend/app/db/init_db.py
backend/app/db/llm_usage_repository.py
backend/app/db/tool_execution_repository.py
backend/app/db/models/assistant_outcome_record.py
backend/app/services/chat_application_service.py
```

以及：

```text
docs/decisions/Phase/Phase 5 — Scope & Architecture Decision.md
docs/architecture.md
```

重点确认当前：

```text
SQLAlchemy Base
ai_ops schema
ORM model registration
init_db DDL
repository pattern
observation best-effort write pattern
request_id correlation
```

不要假设现有实现。

---

# 三、严格按照已经关闭的 Decision

已经冻结：

### OD-P5-25

Conversation Relationship：

```text
AgentRun.request_id
        ↕
ConversationTurn.assistant_request_id
        ↓
conversation_id
```

没有 FK。

没有 ownership。

---

### OD-P5-26

新增：

```text
ai_ops.agent_run
```

MVP：

```text
AgentRun = persisted
AgentStep = NOT persisted
```

---

### OD-P5-27

AgentStep：

```text
in-memory only
```

保留未来迁移路径。

---

### OD-P5-28

Retention：

```text
append-only
no TTL
no cross-boundary cascade
```

---

# 四、禁止修改的边界

本阶段禁止修改：

```text
AgentRuntime loop semantics
AgentLLM
DeepSeekProvider
OpenAICompatibleClient
LLMProvider contract
Router
RAG
Text-to-SQL
SQL Validator
SQL Executor
Tool Registry
ToolExecutionService semantics
Conversation idempotency
ConversationTurn schema
Evidence
LLMUsageRecord schema
ToolExecutionRecord schema
AssistantOutcomeRecord schema
Prompt
API contract
```

不要修改：

```text
RouteType
AgentDecision
AgentRuntimePolicy
```

除非编译/类型集成绝对必要。

如果发现架构冲突：

**停止并报告，不要现场重设计。**

---

# 五、Database Model

新增：

```text
backend/app/db/models/agent_run.py
```

模型：

```text
AgentRun
```

对应：

```text
ai_ops.agent_run
```

字段严格按照 Decision：

```text
id
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

# 六、字段约束

## id

```text
BIGINT
PRIMARY KEY
```

使用当前项目既有 ID 策略。

不要自行引入 UUID。

---

## run_id

```text
VARCHAR(64)
NOT NULL
UNIQUE
```

Agent Runtime 已经有：

```text
run_id
```

持久化时直接使用。

禁止：

```text
uuid.uuid4()
```

在 Recorder 中重新生成。

---

## request_id

```text
VARCHAR(64)
NOT NULL
INDEX
```

来自：

```text
AIOrchestrator
```

不要在 AgentRunRecorder 中生成。

---

## project_id

```text
VARCHAR
NULL
```

使用当前 ProjectContext / Orchestrator 实际 project_id。

---

## status

```text
VARCHAR(32)
NOT NULL
```

保存：

```text
RUNNING
COMPLETED
FAILED
CANCELLED
LIMIT_REACHED
WAITING_APPROVAL
```

不要创建新的 status。

---

## error_code

```text
VARCHAR
NULL
```

只保存：

```text
稳定错误码
```

禁止保存：

```text
exception message
stack trace
prompt
tool result
SQL
```

---

## limit_reason

```text
VARCHAR
NULL
```

只有：

```text
LIMIT_REACHED
```

相关原因才填写。

没有则：

```text
NULL
```

不要写：

```text
"max steps exceeded because ..."
```

只允许稳定 code。

---

# 七、Counters

保存：

```text
step_count
tool_call_count
llm_call_count
```

全部：

```text
INT NOT NULL
```

最低允许：

```text
0
```

不能为 NULL。

来源：

```text
AgentRuntimeResult
```

不要重新计算。

---

# 八、Token Usage

字段：

```text
token_usage_unverifiable BOOLEAN NOT NULL
```

来源：

```text
AgentRuntimeResult.token_budget_unverifiable
```

不要在 AgentRun 中增加：

```text
prompt_tokens
completion_tokens
total_tokens
cost
price
currency
```

因为：

```text
LLMUsageRecord
```

已经承担 LLM-level usage。

---

# 九、Timing

字段：

```text
started_at
finished_at
duration_ms
created_at
```

必须使用：

```text
AgentRuntimeResult / Runtime execution timing
```

如果当前 AgentRuntime 没有可靠提供 started_at：

采用最小实现：

```text
Recorder begin time
Recorder finish time
```

但：

**不要修改 AgentRuntime 只是为了增加持久化字段。**

如果 duration 已经存在：

直接复用。

不要制造两个不同 duration 定义。

---

# 十、AgentRun Recorder

新增：

```text
backend/app/agent/recorder.py
```

定义：

```text
AgentRunRecorder
```

职责：

```text
AgentRuntimeResult
        ↓
AgentRun persistence DTO
        ↓
Repository
```

要求：

```text
best-effort
```

即：

```text
Recorder failure
    ≠
Agent execution failure
```

例如：

```text
AgentRuntime COMPLETED
        ↓
agent_run INSERT fails
        ↓
业务结果仍然 COMPLETED
```

必须：

```text
log / diagnostic
```

但不能：

```text
raise
```

到用户业务层。

---

# 十一、Recorder Ownership

Recorder 可以由：

```text
AIOrchestrator
```

调用。

推荐：

```text
AgentCapability
        ↓
AgentRuntime
        ↓
AgentRuntimeResult
        ↓
AgentRunRecorder
```

但是：

**AgentRuntime 本身禁止依赖 DB。**

因此：

```text
AgentRuntime
    ❌ import AgentRunRecorder
```

而是：

```text
Orchestrator
    → agent_runtime.run()
    → recorder.record(...)
```

---

# 十二、Repository

新增：

```text
backend/app/db/agent_run_repository.py
```

提供最小能力：

```text
create_agent_run(...)
```

必要时：

```text
find_by_run_id(...)
```

但本阶段没有 Agent Run Read API。

因此：

如果 `find_by_run_id()` 没有实际调用方：

**不要为了完整而新增。**

MVP 只实现：

```text
insert
```

---

# 十三、Idempotent Insert

必须保持：

```text
run_id UNIQUE
```

Repository 写入必须：

```text
ON CONFLICT DO NOTHING
```

或者等价的现有项目 best-effort 写入方式。

但注意：

```text
run_id UNIQUE
```

只是：

```text
persistence safety
```

不是 Conversation Idempotency。

不要改变已有：

```text
Idempotency-Key
```

语义。

---

# 十四、Retry / Duplicate Boundary

必须验证：

### Duplicate Conversation Request

```text
Conversation idempotency replay
        ↓
Agent = 0
        ↓
AgentRun = 0 new rows
```

因为 Agent 根本没有再次执行。

---

### Conversation Retry

```text
same Idempotency-Key
USER exists
ASSISTANT missing
        ↓
new request_id
        ↓
Agent executes
        ↓
new run_id
        ↓
new agent_run row
```

---

# 十五、Orchestrator Integration

修改：

```text
backend/app/services/ai_orchestrator_service.py
```

只增加：

```text
AgentRunRecorder
```

调用点。

Agent 执行：

```text
agent_runtime.run()
```

之后：

```text
result = AgentRuntimeResult
```

然后：

```text
recorder.record(
    request_id=request_id,
    project_id=project_id,
    result=result,
)
```

注意：

```text
record()
```

失败：

```text
DO NOT FAIL THE REQUEST
```

---

# 十六、request_id

必须来自当前：

```text
AIOrchestrator
```

例如：

```text
request_id
```

不要：

```text
AgentRunRecorder.generate_request_id()
```

---

# 十七、run_id

来自：

```text
AgentRuntimeResult
```

如果当前 `AgentRuntimeResult` 已经保存 run_id：

直接使用。

如果当前 Result 没有 run_id：

先检查 Decision / 现有实现。

只有在：

```text
当前 Runtime 本来就已经有 run_id
```

但 Result 未暴露时，才允许做最小 DTO 暴露。

不要重新生成第二个 run_id。

---

# 十八、Status Mapping

必须原样保存：

```text
AgentRuntimeResult.status
```

特别：

```text
LIMIT_REACHED
```

必须保存：

```text
status = LIMIT_REACHED
```

不能变成：

```text
FAILED
```

数据库层不能改变 Agent 状态语义。

---

# 十九、Error Mapping

如果：

```text
AgentRuntimeResult.error
```

是稳定错误码：

保存。

如果是：

```text
Exception
```

禁止把 message 写入数据库。

只保存：

```text
type(exc).__name__
```

或者当前既有稳定错误码。

优先复用现有 Agent Error Code。

---

# 二十、安全边界

`agent_run` 禁止出现：

```text
prompt
messages
context
tool_result
tool_arguments
SQL
API key
Authorization
password
database_url
headers
stack_trace
exception_message
```

只允许：

```text
state
counts
stable codes
timestamps
correlation ids
project_id
```

---

# 二十一、不要建立 AgentStep Table

本阶段明确：

```text
NO agent_step table
NO AgentStep ORM
NO AgentStep repository
NO Step persistence
```

现有：

```text
AgentRuntimeResult.step_records
```

继续：

```text
in-memory
```

---

# 二十二、不要建立 Read API

禁止：

```text
GET /agent/runs
GET /agent/runs/{run_id}
GET /agent/steps
```

本阶段没有 Agent Run UI / API。

---

# 二十三、不要修改 Conversation Schema

不要增加：

```text
agent_run_id
```

到：

```text
conversation_turn
```

也不要增加 FK。

保持：

```text
request_id correlation
```

---

# 二十四、Observation 复用

确认：

```text
LLMUsageRecord
ToolExecutionRecord
AssistantOutcomeRecord
```

继续正常工作。

AgentRun 只增加：

```text
run-level record
```

不要复制：

```text
provider
model
tokens
tool arguments
tool result
```

---

# 二十五、测试

新增：

```text
tests/test_agent_run_persistence.py
```

至少覆盖：

### 1. Completed

```text
COMPLETED
→ agent_run row
```

### 2. Failed

```text
FAILED
→ row status FAILED
→ stable error_code
```

### 3. Limit

```text
LIMIT_REACHED
→ row status LIMIT_REACHED
→ limit_reason
```

### 4. Cancelled

如果当前 Runtime 能产生：

```text
CANCELLED
```

测试。

如果当前 unreachable：

只测试 DTO persistence mapping。

---

### 5. Waiting Approval

同上。

不要为了测试强行实现 HITL。

---

### 6. Counters

验证：

```text
step_count
tool_call_count
llm_call_count
```

来自 Runtime Result。

---

### 7. Token flag

验证：

```text
token_budget_unverifiable
```

原样保存。

---

### 8. Idempotent insert

同一个：

```text
run_id
```

写两次：

```text
row count = 1
```

---

### 9. Recorder failure isolation

模拟：

```text
Repository raises
```

确认：

```text
Agent business result unchanged
```

---

### 10. Security

确认数据库 row 不包含：

```text
prompt
context
tool args
tool result
secret
SQL
```

---

# 二十六、DB Tests

新增：

```text
tests/test_agent_run_persistence_db.py
```

如果项目已有 DB fixture：

优先复用。

测试：

```text
create_all / init_db
        ↓
agent_run table
        ↓
insert
        ↓
query
```

确认：

```text
UNIQUE(run_id)
INDEX(request_id)
INDEX(started_at)
```

存在。

---

# 二十七、DB Residue

测试结束：

```text
agent_run
```

必须：

```text
0 residue
```

使用项目既有 cleanup / transaction fixture。

不要修改：

```text
DB residue baseline
```

不要：

```text
skip
xfail
delete test
```

来绕过 residue。

---

# 二十八、Retention

不要实现：

```text
DELETE
TTL
cleanup job
cascade
```

本阶段：

```text
append-only
```

---

# 二十九、Regression

至少执行：

```powershell
python -m pytest tests/test_agent_run_persistence.py -q
python -m pytest tests/test_agent_run_persistence_db.py -q
python -m pytest tests/test_agent_orchestrator_integration.py -q
```

然后：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

compile：

```powershell
python -m compileall -q backend tests scripts
```

---

# 三十、Scope Audit

检查：

```text
git diff --stat
git diff --check
```

确认：

```text
AgentRuntime
    unchanged DB dependency = 0

AgentStep table
    0

Conversation schema
    unchanged

Evidence
    unchanged

Observation tables
    unchanged

Prompt
    unchanged

API
    unchanged
```

---

# 三十一、禁止修复历史 DB Regression

如果出现：

```text
TestMatrixExecutionBaseline
test_db_residue_is_zero
test_baseline_matches_step89_actual_execution
```

等之前已经确认的：

```text
order-dependent
observation-table residue
```

不要修改：

```text
baseline
guard
cleanup policy
```

记录为：

```text
PRE_EXISTING
```

但本阶段新增：

```text
agent_run
```

自己的 residue 必须独立为：

```text
0
```

---

# 三十二、Git Commit

如果全部通过：

```text
feat: Phase 5 agent run persistence
```

不要：

```text
push
PR
merge
rebase
```

如果测试发现架构冲突：

停止，不 commit 生产修改。

---

# 三十三、最终报告

严格：

```text
【Phase 5 AGENT RUN PERSISTENCE COMPLETE】

1. Database Model
2. Repository
3. AgentRunRecorder
4. Orchestrator Integration
5. AgentRuntime DB Dependency
6. Status Mapping
7. Counters
8. Token Usage
9. Idempotency
10. Observation Relationship
11. Conversation Relationship
12. Evidence Relationship
13. Security
14. DB Tests
15. Offline Regression
16. DB Regression
17. DB Residue
18. compileall
19. Git Diff
20. Commit
21. Known Limitations
22. Next Boundary

Conclusion:

AGENT RUN PERSISTENCE = READY

AgentStep Persistence = NOT STARTED
Agent Resume = NOT STARTED
HITL = NOT STARTED

STOP
```

---

# 三十四、强制 STOP

完成后立即停止。

不要进入：

```text
AgentStep Persistence
Agent Resume
HITL
Cancellation
Agent Observability
Streaming
Memory
Planning
Multi-Agent
LangGraph
MCP
```

**本阶段只完成 AgentRun Run-level Persistence。**
