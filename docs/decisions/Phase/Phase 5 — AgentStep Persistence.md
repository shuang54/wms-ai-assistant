你现在开始执行：

# Phase 5 — AgentStep Persistence

## 一、阶段目标

在已经完成：

```text
Phase 5 Agent Core Loop
Phase 5 Provider-native Tool Calling
Phase 5 Agent Integration
Phase 5 AgentRun Persistence
Phase 5 Agent Tool Observation
Phase 5 Agent State / Resume Boundary Decision
```

的基础上，实现：

> **AgentStep Persistence**

本阶段只把当前 `AgentRuntimeResult.step_records` 中已经存在的安全 Step 状态，持久化为 `ai_ops.agent_step`。

本阶段：

**不是 Resume。**

**不是 Checkpoint。**

**不是 HITL。**

**不是 Cancellation。**

---

# 二、已冻结的架构结论

严格遵守：

```text
AgentRun
    =
terminal execution record
```

```text
AgentStep
    =
Runtime State + Audit Projection
```

```text
Checkpoint
    =
future independent recoverable state
```

因此：

```text
AgentRun ≠ AgentStep
AgentStep ≠ Checkpoint
```

不要把 AgentStep 做成 checkpoint。

---

# 三、开始前必须阅读

先阅读：

```text
backend/app/agent/models.py
backend/app/agent/runtime.py
backend/app/agent/decision.py
backend/app/agent/recorder.py
backend/app/agent/ids.py

backend/app/db/models/agent_run.py
backend/app/db/agent_run_repository.py

backend/app/services/ai_orchestrator_service.py

backend/app/services/tool_execution_service.py

tests/test_agent_runtime.py
tests/test_agent_orchestrator_integration.py
tests/test_agent_run_persistence.py
tests/test_agent_tool_observation.py
tests/test_agent_run_persistence_db.py

docs/decisions/Phase/Phase 5 — Scope & Architecture Decision.md
docs/decisions/Phase/Phase 5 — Agent State & Resume Boundary Decision.md

docs/architecture.md
```

必须确认当前真实：

```text
AgentStep
step_records
step_index
step_type
status
tool_name
error
elapsed_ms
run_id
```

不要根据旧报告猜代码。

---

# 四、数据库模型

新增：

```text
backend/app/db/models/agent_step.py
```

对应：

```text
ai_ops.agent_step
```

第一版字段严格保持最小：

```text
id BIGINT PRIMARY KEY

run_id VARCHAR(64) NOT NULL

step_index INTEGER NOT NULL

step_type VARCHAR(32) NOT NULL

status VARCHAR(32) NOT NULL

tool_name VARCHAR(128) NULL

error_code VARCHAR(64) NULL

elapsed_ms DOUBLE PRECISION NULL

created_at TIMESTAMPTZ NOT NULL DEFAULT now()
```

如果现有 Decision 中已经明确了不同字段类型：

**以 Decision 为准，不自行修改。**

---

# 五、Step Identity

必须建立：

```text
(run_id, step_index)
```

唯一性。

数据库：

```text
UNIQUE(run_id, step_index)
```

不要只依赖：

```text
step_index
```

因为不同 AgentRun 可以拥有相同：

```text
step_index = 0
```

---

# 六、AgentRun Relationship

第一版：

```text
AgentRun
    │
    └── run_id
          │
          └── AgentStep.run_id
```

是否建立 FK：

**按照 Phase 5 Decision 已冻结的 persistence boundary 执行。**

如果 Decision 明确要求：

```text
no FK
```

则：

```text
NO FOREIGN KEY
```

不要自行增加。

不要修改：

```text
agent_run
```

表。

---

# 七、AgentStep 不保存业务正文

绝对禁止持久化：

```text
prompt
messages
user question
system prompt
context
RAG chunks
tool arguments
tool result
SQL
final answer
headers
API key
password
database URL
exception message
stack trace
raw LLM response
```

AgentStep 只保存：

```text
step identity
step type
status
tool name
stable error code
timing
```

---

# 八、tool_name

只保存：

```text
tool_name
```

例如：

```text
get_inventory
get_work_order
```

禁止保存：

```text
arguments
result
```

如果当前 Step 没有 Tool：

```text
tool_name = NULL
```

---

# 九、error_code

只保存稳定错误码：

例如：

```text
TOOL_EXECUTION_FAILURE
LLM_FAILURE
INVALID_DECISION
STEP_LIMIT_REACHED
```

不要保存：

```text
str(exception)
exception.args
stack trace
raw provider error
```

如果当前 Runtime 没有稳定错误码：

```text
NULL
```

不要临时发明大量新错误码。

---

# 十、Step Type

严格使用当前 Runtime 已存在的 Step Type。

当前已知：

```text
FINAL
TOOL_CALL
```

不要为了“完整”新增：

```text
LLM_DECISION
CHECKPOINT
APPROVAL
RESUME
```

尤其注意：

Phase State Decision 已记录：

```text
D-2：
AgentStep.type 当前只有 FINAL / TOOL_CALL
```

本阶段：

**不解决 D-2。**

不要修改 Runtime 的 Step 类型语义。

---

# 十一、Status

严格复用当前：

```text
AgentStep.status
```

不要重新设计状态机。

不要增加：

```text
WAITING_APPROVAL
CANCELLED
RESUMING
CHECKPOINTED
```

这些属于未来阶段。

---

# 十二、elapsed_ms

直接持久化当前：

```text
AgentStep.elapsed_ms
```

如果当前 Runtime 已经测量：

直接复用。

不要重新在 Repository 层测量。

要求：

```text
NULL
```

或者：

```text
>= 0
```

禁止：

```text
negative
NaN
Infinity
```

---

# 十三、Repository

新增：

```text
backend/app/db/agent_step_repository.py
```

只提供最小：

```text
create(...)
create_many(...)
```

如果当前 Runtime 产生多个 Step：

优先考虑：

```text
create_many()
```

减少：

```text
N 次 DB round trip
```

但：

**不要引入复杂 Unit of Work。**

---

# 十四、幂等

必须使用：

```text
(run_id, step_index)
```

作为 persistence identity。

重复写：

```text
same run_id
same step_index
```

不能产生第二条 Step。

可以采用：

```text
ON CONFLICT (run_id, step_index) DO NOTHING
```

或者当前项目已经使用的等价幂等方式。

注意：

这只是：

> Persistence safety

不是：

> Conversation Idempotency-Key

也不是：

> Resume Idempotency

不要混淆。

---

# 十五、Recorder

扩展当前：

```text
backend/app/agent/recorder.py
```

新增：

```text
AgentStepRecorder
```

或者按照当前 Recorder 架构增加等价接口。

必须保持：

```text
BestEffort
```

原则：

```text
AgentStep persistence failure
        ↓
warning
        ↓
Agent business result unchanged
```

不能因为：

```text
agent_step insert failed
```

导致：

```text
Agent FAILED
```

也不能：

```text
retry LLM
retry Tool
fallback
```

---

# 十六、最佳接线位置

不要让：

```text
AgentRuntime
```

直接访问：

```text
Repository
ORM
Session
```

继续保持：

```text
AgentRuntime
    =
DB-free
```

推荐：

```text
AgentRuntime
    ↓
AgentRuntimeResult.step_records
    ↓
Orchestrator / Agent Recorder
    ↓
AgentStepRecorder
    ↓
AgentStepRepository
```

如果现有 AgentRun Recorder 是 Orchestrator-side：

优先复用同一个持久化边界。

不要在 Runtime 内创建 DB recorder。

---

# 十七、AgentRun 与 AgentStep 写入顺序

必须认真处理。

当前 AgentRun 是：

```text
terminal record
```

AgentStep 是：

```text
step projection
```

推荐：

```text
Agent execution
      ↓
AgentRuntimeResult
      ↓
persist AgentStep records
      ↓
persist AgentRun terminal record
      ↓
return / raise business result
```

但：

**AgentStep persistence failure 不能改变 AgentRun/business result。**

因此：

```text
AgentStep write
    = best-effort

AgentRun write
    = existing best-effort semantics
```

不要为了强一致性引入：

```text
transaction across runtime + observations
```

---

# 十八、FAILED Runtime

如果 Agent：

```text
step 0
step 1
step 2
→ FAILED
```

要求：

```text
agent_step
0
1
2
```

全部能够持久化。

AgentRun：

```text
status = FAILED
step_count = 3
```

必须与现有 Runtime Result 保持一致。

---

# 十九、LIMIT_REACHED

必须特别验证：

```text
AgentRun.status
=
LIMIT_REACHED
```

AgentStep：

```text
已有实际执行 Step
```

正常持久化。

不要新增：

```text
LIMIT_REACHED step
```

除非当前 Runtime 已经真实产生这样的 Step。

不要人为制造。

---

# 二十、COMPLETED

例如：

```text
Step 0 FINAL
```

最终：

```text
agent_step:
run_id = ...
step_index = 0
step_type = FINAL
status = ...
tool_name = NULL
```

不能因为：

```text
FINAL
```

而丢弃 Step。

---

# 二十一、Tool Call

例如：

```text
Step 0 TOOL_CALL
tool = get_inventory
```

持久化：

```text
step_index = 0
step_type = TOOL_CALL
tool_name = get_inventory
```

不要持久化：

```text
arguments = {...}
```

---

# 二十二、Tool Execution Observation Relationship

保持：

```text
AgentStep
     │
     ├── tool_name
     │
     └── step timing
```

而：

```text
ToolExecutionRecord
     │
     ├── request_id
     ├── tool_call_id
     ├── round
     ├── project_id
     ├── success
     └── duration
```

二者不是同一个表。

不要复制：

```text
tool_call_id
arguments
result
```

到 AgentStep。

---

# 二十三、LLM Observation Relationship

保持：

```text
AgentStep
    ≠
LLMUsageRecord
```

不要把：

```text
provider
model
prompt_tokens
completion_tokens
total_tokens
request_id
```

复制到 AgentStep。

这些继续由：

```text
LLMUsageRecord
```

负责。

---

# 二十四、测试要求

新增：

```text
tests/test_agent_step_persistence.py
```

至少测试：

### 1

Single FINAL step：

```text
1 row
```

### 2

Single TOOL_CALL：

```text
1 row
tool_name correct
```

### 3

Multi-step：

```text
step 0
step 1
step 2
```

全部存在。

### 4

FAILED run：

Step records 仍然存在。

### 5

LIMIT_REACHED：

AgentRun 保持：

```text
LIMIT_REACHED
```

### 6

Duplicate:

```text
same run_id + step_index
```

只有：

```text
1 row
```

### 7

Sensitive data isolation：

确认：

```text
arguments
tool result
SQL
prompt
secret
exception message
```

不会落库。

### 8

Best-effort persistence failure：

模拟 Repository failure：

```text
Agent result unchanged
```

### 9

AgentRuntime DB isolation：

Runtime 不 import：

```text
sqlalchemy
repository
Session
```

### 10

No new Agent state semantics：

确认没有新增：

```text
WAITING_APPROVAL
CANCELLED
RESUMING
CHECKPOINTED
```

---

# 二十五、DB Tests

新增：

```text
tests/test_agent_step_persistence_db.py
```

只在：

```text
RUN_DB_TESTS=1
```

运行。

验证：

```text
table exists
columns
types
UNIQUE(run_id, step_index)
indexes
foreign keys = expected
insert
duplicate
cleanup
```

最终：

```text
agent_step residue = 0
```

---

# 二十六、Security DB Test

插入：

```text
prompt
messages
tool arguments
tool result
SQL
api_key
password
authorization
exception message
```

通过可观察字段构造测试对象。

验证这些内容：

**不会出现在 AgentStep 的任何持久化字段。**

---

# 二十七、不要修改 AgentRun

本阶段：

```text
agent_run
```

不得：

```text
ALTER
ADD COLUMN
DROP
INDEX CHANGE
```

不要增加：

```text
agent_step_count
```

因为已有：

```text
step_count
```

继续使用 AgentRuntimeResult。

---

# 二十八、不要实现 Read API

禁止：

```text
GET /agent/runs/{run_id}/steps
GET /agent/runs/{run_id}/timeline
```

也不要：

```text
find_steps()
list_steps()
timeline_service()
```

Read Model 是后续阶段。

---

# 二十九、不要实现 Resume

明确禁止：

```text
resume(run_id)
checkpoint
replay
recovery
state_version
claim_token
```

虽然未来 Decision 已经定义：

```text
state_version
claim_token
```

但本阶段不能实现。

---

# 三十、不要实现 HITL

禁止：

```text
approval
approval_id
WAITING_APPROVAL
approve
reject
```

AgentStep 当前只保存真实 Runtime 已产生的状态。

---

# 三十一、不要修改 D-2

当前：

```text
AgentStep.type = FINAL | TOOL_CALL
```

保持。

不要新增：

```text
LLM_DECISION
```

如果发现未来 Resume 确实需要它：

记录：

```text
D-2 follow-up architecture issue
```

但不要修。

---

# 三十二、完整测试

针对性：

```powershell
python -m pytest -q tests/test_agent_step_persistence.py
python -m pytest -q tests/test_agent_run_persistence.py
python -m pytest -q tests/test_agent_tool_observation.py
```

全量：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

编译：

```powershell
python -m compileall -q backend tests scripts
```

---

# 三十三、DB Residue

最终必须确认：

```text
agent_step = 0
agent_run = 0
tool_execution_record = 0
llm_usage_record = 0
assistant_outcome_record = 0
rag_execution_record = 0
```

如果全量测试出现之前已经确认的：

```text
PRE_EXISTING
```

失败：

继续标记为：

```text
PRE_EXISTING
```

不得：

```text
修改 baseline
关闭 guard
TRUNCATE
弱化测试
```

---

# 三十四、Git Diff

执行：

```powershell
git status --short
git diff --check
git diff --stat
git diff
```

确认：

```text
No AgentRun schema change
No Checkpoint
No Resume
No HITL
No Cancellation
No Conversation change
No Evidence change
No LLMUsageRecord change
No ToolExecutionRecord schema change
No Prompt change
No Provider change
No Tool Registry change
No API
No migration
No secrets
```

---

# 三十五、Commit

只有：

```text
Target tests PASS
Offline full PASS
DB tests PASS except known PRE_EXISTING
compileall PASS
DB residue = 0
git diff --check PASS
```

才 commit：

```text
feat: Phase 5 agent step persistence
```

不要：

```text
push
PR
merge
rebase
```

---

# 三十六、最终报告

严格输出：

```text
【Phase 5 AGENT STEP PERSISTENCE COMPLETE】

1. AgentStep Definition
2. Database Model
3. Repository
4. Recorder
5. Runtime Boundary
6. AgentRun Relationship
7. ToolExecutionRecord Relationship
8. LLMUsageRecord Relationship
9. Step Types
10. Status
11. Security Boundary
12. Best-effort Failure Isolation
13. Idempotency
14. DB Tests
15. Offline Regression
16. DB Residue
17. compileall
18. Git Diff
19. Commit
20. Architecture Drift
21. Known Limitations

最终状态：

AgentRun Persistence     = READY
Agent Tool Observation  = READY
AgentStep Persistence    = READY

Checkpoint               = NOT STARTED
Resume                   = NOT STARTED
HITL                     = NOT STARTED
Cancellation             = NOT STARTED
Read API                 = NOT STARTED

STOP
```

---

# 三十七、强制 STOP

完成后立即停止。

不要进入：

```text
Checkpoint
Resume
HITL
Cancellation
Read API
Timeline API
Streaming
Memory
Planning
Multi-Agent
LangGraph
MCP
```

本阶段只完成：

```text
AgentRuntime.step_records
        ↓
AgentStepRecorder
        ↓
ai_ops.agent_step
```

**不要扩大范围。**
