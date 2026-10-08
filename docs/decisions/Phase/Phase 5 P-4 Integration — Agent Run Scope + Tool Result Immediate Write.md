# Phase 5 P-4 Integration — Agent Run Scope + Tool Result Immediate Write

项目：

```text
D:\coding\ai\wms-ai-assistant
```

## 一、阶段目标

上一阶段已经完成：

```text
AgentRun Persistence       READY
Agent Tool Observation     READY
AgentStep Persistence      READY
Tool Result Store          READY
Tool Idempotency           DECIDED
Tool Replay                DECIDED
```

但发现：

```text
P-4 Integration Boundary = INSUFFICIENT
```

当前原因已经确认：

```text
AgentRuntime
    有 run_id
       ↓
ToolExecutionService
    有 Tool Result
       ↓
两者没有共同的 Agent Run identity
```

本阶段只解决：

> **让 Agent Tool Result 在 Tool 执行完成后，能够使用 `(run_id, step_index)` 立即写入 `agent_tool_result`。**

采用已经讨论的：

# α 方案

```text
AgentRuntime.run()
    ↓
agent_run_scope(run_id)
    ↓
ToolExecutionService
    ↓
Agent Tool Result Recorder
    ↓
current_agent_run_id()
    ↓
agent_tool_result
```

---

# 二、严格范围

## 允许修改

只允许涉及：

```text
backend/app/agent/ids.py
backend/app/agent/runtime.py
backend/app/agent/recorder.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/project_orchestrator_factory.py
backend/app/api/orchestrator_chat.py
```

以及：

```text
tests/
```

如果 composition root 的实际位置不同，遵循真实项目结构。

---

# 三、禁止修改

本阶段禁止修改：

```text
ToolExecutionContext
ToolExecutionService 的公共 contract
Tool Registry
Tool Handler
Provider
LLM Adapter
Conversation
Conversation Idempotency
AgentRun schema
AgentStep schema
ToolExecutionRecord schema
LLMUsageRecord schema
Prompt
API contract
Database schema
```

尤其禁止：

```text
❌ 把 run_id 加入 ToolExecutionContext
❌ 把 Agent 专属字段塞进公共 Tool DTO
❌ 重构 AgentRuntime 主循环
❌ 修改 Tool Handler
❌ 修改 ToolExecutionService 业务语义
❌ 修改 Tool Registry
❌ 新增第二套 Tool execution framework
```

---

# 四、先阅读真实代码

先阅读：

```text
backend/app/agent/runtime.py
backend/app/agent/recorder.py
backend/app/agent/ids.py
backend/app/agent/capability.py

backend/app/services/tool_execution_service.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/project_orchestrator_factory.py
backend/app/api/orchestrator_chat.py

backend/app/db/agent_tool_result_repository.py
backend/app/db/models/agent_tool_result.py

tests/test_agent_runtime.py
tests/test_agent_tool_result_store.py
tests/test_agent_tool_result_store_db.py
tests/test_agent_tool_observation.py
tests/test_agent_step_persistence.py
tests/test_agent_run_persistence.py
```

确认上一阶段真实接口。

**不要根据任务书假设接口。**

---

# 五、Agent Run Scope

新增一个极小的 Agent Run Context。

优先放置：

```text
backend/app/agent/ids.py
```

或者当前项目最合适的位置。

使用：

```python
ContextVar[str | None]
```

语义：

```text
agent_run_scope(run_id)
current_agent_run_id()
```

---

# 六、Scope Contract

必须满足：

```text
outside scope
    ↓
current_agent_run_id()
    ↓
None
```

进入：

```text
with agent_run_scope("run-001"):
    current_agent_run_id()
    # "run-001"
```

退出：

```text
current_agent_run_id()
    ↓
None
```

必须使用：

```text
token = ContextVar.set(...)
try:
    yield
finally:
    ContextVar.reset(token)
```

禁止：

```text
global mutable variable
```

禁止：

```text
current_run_id = run_id
```

避免 async / nested request 污染。

---

# 七、Scope 不负责生成 run_id

非常重要：

```text
agent_run_scope(run_id)
```

只接受已有：

```text
run_id
```

绝对不能：

```text
agent_run_scope()
```

内部自动生成。

Run ID 仍然由：

```text
AgentRuntime.run()
```

现有逻辑生成。

保持：

```text
run_id = new_agent_run_id()
```

语义不变。

---

# 八、AgentRuntime 接入 Scope

当前：

```text
AgentRuntime.run()
    ↓
run_id = new_agent_run_id()
```

保持。

只增加：

```text
with agent_run_scope(run_id):
```

让整个 Agent 执行过程处于：

```text
Agent Run Scope
```

范围。

---

# 九、非常重要：不要改变 Loop

不要重新设计：

```text
while ...
decision
tool_call
tool_execution
build_result
```

只做：

```text
run_id
    ↓
scope
    ↓
existing loop
```

禁止：

```text
新的状态机
新的 retry
新的 checkpoint
新的 replay
新的 resume
```

---

# 十、Scope 生命周期

Scope 必须覆盖：

```text
AgentRuntime.run()
    │
    ├── LLM Decision
    │
    ├── Tool Authorization
    │
    ├── Tool Execution
    │      │
    │      └── AgentToolResultRecorder
    │
    ├── AgentStep
    │
    └── final result
```

因此：

```text
Tool Execution
```

发生时：

```text
current_agent_run_id()
```

必须已经存在。

---

# 十一、Tool Result Recorder 接线

上一阶段已经存在：

```text
AgentToolResultRecord
AgentToolResultRecorder
BestEffortAgentToolResultRecorder
```

本阶段不要重新设计。

复用：

```text
record(...)
record_many(...)
arecord(...)
```

或者当前真实接口。

---

# 十二、Recorder 必须取得：

在 Tool execution 完成后：

```text
run_id
step_index
tool_name
project_id
tool_call_id
success
ToolResult
```

其中：

```text
run_id
```

来源：

```text
current_agent_run_id()
```

而不是：

```text
ToolExecutionContext.run_id
```

因为：

**ToolExecutionContext 不修改。**

---

# 十三、step_index

继续使用 AgentRuntime 当前语义：

```text
step_index
```

不要重新计算。

不要使用：

```text
round
```

直接传当前 Agent step index。

已有：

```text
round = step_index + 1
```

只用于 ToolExecutionContext。

两者不要混淆。

---

# 十四、project_id

继续使用现有 Agent Tool capability / authorized Tool scope。

不要从：

```text
ContextVar
```

重新获取。

如果当前 Tool Execution Context 已经包含：

```text
project_id
```

复用。

---

# 十五、tool_call_id

继续使用 Provider 给出的：

```text
tool_call_id
```

禁止：

```text
uuid.uuid4()
```

重新生成。

---

# 十六、最佳写入位置

最终执行链必须变成：

```text
AgentRuntime
   ↓
LLM Decision
   ↓
Tool Authorization
   ↓
ToolExecutionService.execute(...)
   ↓
Tool Handler
   ↓
Tool Result
   ↓
AgentToolResultRecorder
   ↓
agent_tool_result INSERT
   ↓
AgentRuntime continues
```

这是本阶段最关键的验证点。

---

# 十七、如何接入 ToolExecutionService

优先使用现有：

```text
ToolExecutionObserver
```

或当前 Tool execution extension point。

如果已有 observer 可以拿到：

```text
ToolExecutionContext
Tool result
success/failure
```

就在该边界增加 Agent Result Store recorder。

---

# 十八、如果 Observer 无法拿到 Agent Result

如果现有 Observer：

```text
只能观察
request_id
round
tool_call_id
success
duration
```

却拿不到：

```text
ToolResult payload
```

不要修改 ToolExecutionContext。

也不要修改 ToolExecutionRecord。

此时：

**允许在 AgentRuntime 调用 ToolExecutionService 的返回边界增加最小 recorder 调用。**

即：

```text
result = execute(...)
       ↓
AgentToolResultRecorder
       ↓
return result
```

关键要求：

```text
必须在 AgentRuntime 继续下一轮 LLM 之前写入。
```

---

# 十九、同步 / 异步

当前 AgentRuntime 如果是同步：

优先同步：

```text
recorder.record(...)
```

如果当前 runtime / ToolExecutionService 使用 async：

使用：

```text
await recorder.arecord(...)
```

不要因为 Result Store 而强行改整个 Agent Runtime 为 async。

---

# 二十、Best-effort 原则

Result Store 写入失败：

```text
DB down
Repository exception
serialization failure
```

当前 Agent 行为必须保持：

```text
Tool Result
    ↓
Agent continues
```

不得：

```text
Tool result store failed
    ↓
retry Tool
```

不得：

```text
Tool result store failed
    ↓
retry LLM
```

不得：

```text
Tool result store failed
    ↓
fallback
```

不得：

```text
Tool result store failed
    ↓
Agent FAILED
```

因为当前 Recorder 是：

```text
BestEffort
```

---

# 二十一、但必须记录 failure

Best-effort failure：

```text
warning
+
error_type
```

保持上一阶段 recorder 风格。

不要输出：

```text
result payload
prompt
SQL
secret
```

日志中也不要打印敏感数据。

---

# 二十二、AgentRun Recorder 顺序

当前已经冻结：

```text
Steps
 ↓
Run
```

本阶段增加 Tool Result 后：

```text
Tool Result
 ↓
Step
 ↓
Run
```

注意：

**不要求三者一个数据库事务。**

只是生命周期顺序。

---

# 二十三、为什么必须 Tool Result 在前

必须增加测试证明：

```text
Tool Result Store write
```

发生在：

```text
AgentRun terminal write
```

之前。

这是为了未来：

```text
Tool Result persisted
Checkpoint not persisted
process crashes
```

Resume 可以根据：

```text
(run_id, step_index)
```

找到历史结果。

---

# 二十四、不能声称 Exactly Once

本阶段不能写：

```text
exactly-once execution
```

当前只是：

```text
execution identity
+
result persistence
+
future replay boundary
```

真正的 atomic claim / state_version / claim_token：

留到 Checkpoint / Resume 阶段。

---

# 二十五、Nested Agent Scope

增加测试：

```text
with agent_run_scope("run-A"):
    assert current_agent_run_id() == "run-A"

    with agent_run_scope("run-B"):
        assert current_agent_run_id() == "run-B"

    assert current_agent_run_id() == "run-A"
```

验证：

```text
ContextVar reset
```

正确。

---

# 二十六、Concurrent Scope

增加 async / context isolation 测试。

例如：

```text
task A → run-A
task B → run-B
```

确认：

```text
task A ≠ run-B
task B ≠ run-A
```

如果项目已有 async test infrastructure，复用。

不要新增复杂 concurrency framework。

---

# 二十七、Agent Tool Result Integration Test

新增：

```text
tests/test_agent_tool_result_integration.py
```

至少验证：

```text
Agent
 ↓
Tool
 ↓
Result
 ↓
AgentToolResultRecorder
```

Recorder 收到：

```text
run_id
step_index
tool_name
project_id
tool_call_id
result
```

正确。

---

# 二十八、Persistence Integration Test

新增/扩展：

```text
tests/test_agent_tool_result_store_db.py
```

验证：

```text
real Agent
 ↓
real Tool Execution
 ↓
real AgentToolResultRecorder
 ↓
real Repository
 ↓
PostgreSQL
```

如果现有 DB fixture 允许。

不要调用 DeepSeek。

使用：

```text
Fake LLM
Fake / existing read-only Tool
```

---

# 二十九、关键 Crash Window Test

模拟：

```text
Tool execution
    ↓
Tool result recorder
    ↓
persist result
    ↓
simulate checkpoint boundary
```

至少证明：

```text
agent_tool_result
```

已经存在。

不要真的 kill process。

不需要实现 Checkpoint。

测试语义：

> 如果之后 Checkpoint 没写，Tool Result 已经 durable。

---

# 三十、Duplicate Integration Test

执行同一个：

```text
run_id
step_index
```

两次 recorder。

确认：

```text
DB rows = 1
```

第二次：

```text
handler 不重复执行
```

注意：

本阶段不实现 runtime-level dedup。

这里只证明：

```text
persistence identity
```

已经有效。

---

# 三十一、No Agent Run Scope Test

直接调用：

```text
AgentToolResultRecorder
```

但没有：

```text
agent_run_scope
```

则：

```text
run_id = None
```

必须：

```text
不写 DB
```

并保持 best-effort。

不要猜：

```text
request_id → run_id
```

绝对禁止这种 fallback。

---

# 三十二、Non-Agent Tool Path

普通：

```text
ToolChatService
```

调用 Tool：

不应该自动产生：

```text
agent_tool_result
```

因为：

```text
没有 Agent Run Scope
```

确认：

```text
agent_tool_result = 0
```

保持现有普通 Tool 行为。

---

# 三十三、Security

增加：

```text
Agent Run Scope
```

本身不得泄露：

```text
prompt
context
tool result
credentials
```

ContextVar 只存：

```text
run_id
```

---

# 三十四、Architecture Audit

增加静态审计：

```text
Agent Runtime
```

仍然不能 import：

```text
sqlalchemy
backend.app.db
repository
Session
sessionmaker
```

如果：

```text
recorder.py
```

通过延迟 import 访问 repository：

允许。

---

# 三十五、不要改变 ToolExecutionContext

必须有测试：

```text
ToolExecutionContext fields
```

仍然只有原有字段。

不要出现：

```text
run_id
```

---

# 三十六、不要改变 ToolExecutionRecord

必须有测试/审计：

```text
tool_execution_record schema
```

没有：

```text
result
run_id
step_index
```

保持：

```text
Observation
```

---

# 三十七、数据库 Schema

本阶段：

```text
agent_tool_result = 已存在
```

不再新增表。

不要：

```text
CREATE TABLE
```

以外的 schema 改造。

不增加：

```text
agent_run
agent_step
tool_execution_record
```

任何字段。

---

# 三十八、测试命令

先：

```powershell
python -m pytest -q tests/test_agent_tool_result_integration.py
```

然后：

```powershell
python -m pytest -q tests/test_agent_tool_result_store.py
python -m pytest -q tests/test_agent_step_persistence.py tests/test_agent_run_persistence.py tests/test_agent_tool_observation.py
python -m pytest -q tests/test_agent_runtime.py
```

然后：

```powershell
python -m pytest -q
```

---

# 三十九、DB

运行：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_agent_tool_result_store_db.py
```

然后完整 DB regression：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

保留已知的：

```text
3 个 order-dependent / baseline guard failures
```

不要修改 guard。

---

# 四十、Compile

运行：

```powershell
python -m compileall -q backend tests scripts
```

必须：

```text
0 errors
```

---

# 四十一、Git Diff

执行：

```powershell
git status --short
git diff --stat
git diff --check
git diff
```

确认禁止区：

```text
ToolExecutionContext = unchanged
ToolExecutionService contract = unchanged
Tool Registry = unchanged
Tool Handler = unchanged
AgentRun schema = unchanged
AgentStep schema = unchanged
ToolExecutionRecord schema = unchanged
LLMUsageRecord schema = unchanged
Conversation = unchanged
Prompt = unchanged
Provider = unchanged
API contract = unchanged
```

---

# 四十二、Commit

只有：

```text
offline PASS
DB PASS / 仅已知 guard failure
compileall PASS
git diff --check PASS
security PASS
residue = 0
```

才 commit：

```text
feat: Phase 5 agent tool result integration
```

不要：

```text
push
PR
merge
rebase
```

---

# 四十三、最终报告

严格：

```text
【Phase 5 AGENT TOOL RESULT INTEGRATION COMPLETE】

1. Integration Decision
2. Agent Run Scope
3. Scope Lifecycle
4. Tool Execution Boundary
5. Tool Result Write Timing
6. Persistence Identity
7. AgentRun Relationship
8. AgentStep Relationship
9. ToolExecutionRecord Relationship
10. Best-effort Failure Isolation
11. Non-Agent Tool Isolation
12. Security Boundary
13. Crash Window Coverage
14. Tests
15. DB Tests
16. DB Residue
17. Offline Regression
18. compileall
19. Git Diff
20. Commit
21. Architecture Drift
22. Known Limitations

Final:

AgentRun Persistence      = READY
Agent Tool Observation   = READY
AgentStep Persistence    = READY
Tool Result Store        = READY
Tool Result Write Point  = READY

Tool Idempotency         = DECIDED
Tool Replay              = DECIDED

Checkpoint               = NOT STARTED
Resume                   = NOT STARTED
HITL                     = NOT STARTED
Cancellation             = NOT STARTED
Read API                 = NOT STARTED

STOP
```

---

# 四十四、强制 STOP

完成本阶段立即停止。

不要实现：

```text
Checkpoint
Resume
HITL
Cancellation
Read API
```

不要修改：

```text
ToolExecutionContext
ToolExecutionService contract
Tool Registry
Tool Handler
```

不要实现：

```text
atomic claim
state_version
claim_token
blind rerun
Replay API
Resume API
```

这些属于后续 Checkpoint / Resume 阶段。

本阶段唯一目标：

> **用最小的 Agent Run Scope 解决当前真实 integration boundary，使 Tool Result 在执行完成后立即进入已冻结的 `agent_tool_result` Store，同时不改变现有 Tool Runtime 行为。**
