# Phase 5 P-4 — Agent Tool Result Store 实现

你现在开始实现：

**Phase 5 P-4 — Agent Tool Result Store**

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段目标

基于已经冻结的：

```text
Phase 5 — Tool Replay & Idempotency Decision
```

现在只实现：

```text
ai_ops.agent_tool_result
```

用于保存 Agent Tool Call 的**可安全 Replay 结果**。

本阶段不实现：

```text
Checkpoint
Resume
HITL
Cancellation
Read API
```

也不修改：

```text
AgentRuntime 主循环
ToolExecutionService
Tool Registry
Tool Handler
Provider
Conversation
Conversation Idempotency
AgentRun
AgentStep
ToolExecutionRecord
LLMUsageRecord
Prompt
```

---

# 二、已经冻结的核心语义

严格遵守：

```text
Tool Execution Identity
    =
(run_id, step_index)
```

不是：

```text
tool_call_id
request_id
Conversation Idempotency-Key
```

其中：

```text
run_id
    = Agent execution identity

step_index
    = run 内 Tool execution step

tool_call_id
    = LLM protocol correlation
```

---

# 三、Replay 语义

严格遵守：

### SUCCEEDED

```text
已有持久化结果
        ↓
Replay
        ↓
handler = 0
```

### FAILED

```text
已有持久化失败结果
        ↓
Replay failure
        ↓
handler = 0
```

### UNKNOWN / IN-FLIGHT

```text
无法证明 Tool 已完成
        ↓
FAIL CLOSED
        ↓
handler = 0
```

### Result 未持久化但 Tool 可能已经执行

```text
FAIL CLOSED
```

本阶段不要实现任何 blind rerun。

---

# 四、先阅读真实代码

先阅读：

```text
backend/app/agent/runtime.py
backend/app/agent/models.py
backend/app/agent/decision.py
backend/app/agent/capability.py
backend/app/agent/recorder.py

backend/app/services/tool_execution_service.py

backend/app/db/models/agent_run.py
backend/app/db/models/agent_step.py
backend/app/db/agent_run_repository.py
backend/app/db/agent_step_repository.py
backend/app/db/models/tool_execution_record.py
backend/app/db/models/llm_usage_record.py

tests/test_agent_runtime.py
tests/test_agent_tool_observation.py
tests/test_agent_run_persistence.py
tests/test_agent_step_persistence.py
tests/test_agent_step_persistence_db.py

docs/decisions/Phase/Phase 5 — Scope & Architecture Decision.md
docs/decisions/Phase/Phase 5 — Agent State & Resume Boundary Decision.md
docs/decisions/Phase/Phase 5 — Tool Replay & Idempotency Decision.md
```

同时搜索：

```text
ToolExecutionContext
ToolExecutionObserver
ToolExecutionRecord
ToolExecutionService
tool_call_id
run_id
step_index
AgentStepRecord
BestEffortAgentStepRecorder
```

---

# 五、Database Model

新增：

```text
backend/app/db/models/agent_tool_result.py
```

表：

```text
ai_ops.agent_tool_result
```

最小字段：

```text
id BIGINT PRIMARY KEY

run_id VARCHAR(64) NOT NULL

step_index INTEGER NOT NULL

tool_name VARCHAR(128) NOT NULL

project_id VARCHAR(128) NULL

success BOOLEAN NOT NULL

error_code VARCHAR(64) NULL

result_payload JSONB NULL

result_hash VARCHAR(64) NULL

result_size_bytes INTEGER NOT NULL

result_truncated BOOLEAN NOT NULL

result_unavailable BOOLEAN NOT NULL

result_ref VARCHAR(512) NULL

tool_call_id VARCHAR(128) NULL

created_at TIMESTAMPTZ NOT NULL DEFAULT now()
```

---

# 六、唯一约束

必须：

```text
UNIQUE(run_id, step_index)
```

例如：

```text
uq_agent_tool_result_run_id_step_index
```

这是：

> Tool Execution Identity 的数据库约束。

不是：

```text
tool_call_id UNIQUE
```

不要给：

```text
tool_call_id
```

增加唯一约束。

---

# 七、Foreign Key

当前阶段：

```text
NO FOREIGN KEY
```

保持与：

```text
agent_run
agent_step
```

当前设计一致。

使用：

```text
run_id
```

进行逻辑 correlation。

---

# 八、模型注册

将：

```text
AgentToolResult
```

注册到：

```text
backend/app/db/models/__init__.py
```

遵循当前 `_MODELS` 注册方式。

继续使用当前项目：

```text
Base.metadata.create_all(checkfirst=True)
```

不要引入：

```text
Alembic
migration
ALTER TABLE
```

---

# 九、Repository

新增：

```text
backend/app/db/agent_tool_result_repository.py
```

至少提供：

```python
create(...)
create_many(...)
```

暂时不要提供：

```text
find
get
list
timeline
replay
resume
```

因为：

> P-4 只建立写入 Store。

Read / Replay API 留给后续 Checkpoint / Resume 阶段。

---

# 十、Repository Identity

Repository 必须以：

```text
(run_id, step_index)
```

作为 persistence identity。

重复：

```text
(run_id, step_index)
```

不能产生第二行。

使用：

```sql
ON CONFLICT (run_id, step_index) DO NOTHING
```

保持与 AgentStep Repository 一致。

---

# 十一、输入验证

进入 DB 前必须验证：

```text
run_id
```

长度：

```text
1..64
```

```text
step_index >= 0
```

```text
tool_name
```

长度：

```text
1..128
```

```text
project_id
```

如果存在：

```text
<= 128
```

```text
tool_call_id
```

如果存在：

```text
<= 128
```

```text
error_code
```

如果存在：

```text
<= 64
```

```text
result_size_bytes >= 0
```

```text
result_truncated ∈ {True, False}
result_unavailable ∈ {True, False}
```

---

# 十二、Result Hash

使用：

```text
SHA-256
```

结果：

```text
64 hex characters
```

hash 的输入必须是：

> 完整、安全序列化后的 Tool Result。

不要 hash：

```text
repr(object)
str(object)
```

如果 result 无法安全序列化：

```text
result_unavailable = true
result_payload = null
result_hash = null
```

不要强行序列化。

---

# 十三、JSON Safe Serialization

Tool Result 必须经过：

```text
JSON-safe serialization
```

目标：

```text
dict
list
str
int
float
bool
null
```

需要根据当前项目实际技术栈决定是否复用已有 JSON serializer。

优先：

```text
已有 helper
```

不要重新创建一套复杂 serializer。

---

# 十四、禁止保存对象本身

绝对禁止：

```text
SQLAlchemy model
Session
Connection
cursor
HTTP response
exception object
LLMResponse
ToolDefinition
```

进入：

```text
result_payload
```

必须是纯 JSON-compatible data。

---

# 十五、Result 大小策略

严格实现已经冻结的边界：

### ≤ 32 KiB

```text
result_payload = full JSON-safe result
result_hash = SHA256(full result)
result_size_bytes = size
result_truncated = false
result_unavailable = false
```

允许 Replay。

---

### > 32 KiB 且 ≤ 1 MiB

本阶段：

```text
result_payload = null
result_hash = SHA256(full JSON-safe result)
result_size_bytes = size
result_truncated = false
result_unavailable = true
result_ref = null
```

因为：

> 当前没有 artifact store。

所以：

```text
不可 Replay
```

但是仍然保存：

```text
hash
size
identity
success
```

用于证明结果曾经存在/计算过。

---

### > 1 MiB

不要保存 payload。

建议：

```text
result_payload = null
result_hash = SHA256(full JSON-safe result)
result_size_bytes = size
result_truncated = false
result_unavailable = true
result_ref = null
```

如果项目无法安全构造完整 hash：

则：

```text
result_hash = null
result_unavailable = true
```

不能伪造 hash。

---

# 十六、不要截断后继续 Replay

禁止：

```text
result[:32768]
```

然后：

```text
Replay
```

如果：

```text
result_truncated = true
```

则：

```text
Replay = unavailable
```

本阶段建议：

**不做 payload truncation。**

超出 inline limit 就：

```text
result_unavailable = true
```

---

# 十七、result_ref

当前阶段：

```text
result_ref = None
```

因为：

```text
artifact store
```

尚未实现。

不要创建：

```text
S3
MinIO
filesystem
blob
Redis
```

---

# 十八、Security Boundary

Tool Result 是：

```text
UNTRUSTED DATA
```

必须保持：

```text
Replay result
    ↓
role=tool
    ↓
【TOOL RESULT — UNTRUSTED DATA】
```

本阶段 Store 本身：

**不负责改变 Tool Result 的信任等级。**

不要增加：

```text
system message
policy message
trusted_context
```

---

# 十九、禁止保存内容

Result Store 禁止保存：

```text
system_prompt
user_prompt
conversation_context
RAG chunks
tool definitions
headers
authorization
API key
password
database URL
database credentials
raw SQL credentials
```

特别注意：

如果 Tool Result 本身包含：

```text
password
Bearer ...
sk-...
DATABASE_URL
```

不能假设：

```text
result_payload JSONB
```

就可以直接保存。

至少建立一个最小的安全序列化检查。

如果发现敏感 token：

```text
result_unavailable = true
result_payload = null
```

不要写入敏感原文。

---

# 二十、敏感内容检查

不要实现复杂 DLP。

本阶段只建立：

```text
最小明确禁止 token 检查
```

例如检查：

```text
api_key
authorization
password
database_url
Bearer 
sk-
```

如果当前项目已有 secret sanitizer：

优先复用。

不要重新发明复杂 regex framework。

---

# 二十一、Failure Result

Tool 执行失败时：

可以保存：

```text
success = false
error_code = stable code
result_payload = null
```

禁止：

```text
exception message
stack trace
raw exception
```

例如：

```text
AGENT_TOOL_EXECUTION_FAILURE
```

或者使用当前真实 stable error code。

必须复用当前错误码体系。

不要创建几十个新 error code。

---

# 二十二、成功 Result

Tool 成功：

```text
success = true
error_code = null
```

并按照大小策略处理 result。

---

# 二十三、未知 Result

如果：

```text
ToolExecutionService
```

返回无法判断成功/失败的异常状态：

不要猜。

使用：

```text
success = false
result_unavailable = true
```

并记录稳定错误码。

---

# 二十四、Recorder

扩展：

```text
backend/app/agent/recorder.py
```

新增：

```text
AgentToolResultRecord
AgentToolResultRecorder
BestEffortAgentToolResultRecorder
```

但注意：

### Runtime 仍然不能直接访问 DB。

Recorder 负责：

```text
AgentRuntime result
       ↓
Recorder
       ↓
Repository
```

或者：

```text
Orchestrator / Capability boundary
       ↓
Tool Result Recorder
       ↓
Repository
```

根据当前真实调用结构选择。

---

# 二十五、最佳接线位置

非常重要：

Tool Result Store 必须在：

```text
Tool handler 成功/失败之后
```

立即保存。

而不是：

```text
AgentRun 完成之后
```

才保存。

目标：

```text
Tool Call
   ↓
ToolExecutionService
   ↓
Tool result
   ↓
AgentToolResultRecorder
   ↓
DB
   ↓
Agent continues
```

这样才能覆盖：

```text
Tool result persisted
Checkpoint not yet persisted
process crashes
```

这个关键恢复窗口。

---

# 二十六、但是不要改变 AgentRuntime 主循环

本阶段不要重写：

```text
AgentRuntime.run()
```

只允许通过已有 capability / recorder boundary 注入。

如果当前结构无法在不修改 Runtime 主循环的情况下接入：

先停止并报告：

```text
P-4 integration boundary insufficient
```

不要为了接线而重构 Runtime。

---

# 二十七、AgentStep / AgentRun 不修改

本阶段明确：

```text
agent_run schema = 0 change
agent_step schema = 0 change
```

不要增加：

```text
tool_result_id
last_tool_call
result_available
```

等字段。

Checkpoint 将来自己保存 reference。

---

# 二十八、ToolExecutionRecord 不修改

保持：

```text
ToolExecutionRecord
=
Observation
```

不要增加：

```text
result
run_id
step_index
UNIQUE
```

不要破坏现有 observation semantics。

---

# 二十九、LLMUsageRecord 不修改

保持：

```text
LLMUsageRecord
=
LLM observation
```

不复制：

```text
provider
model
tokens
```

---

# 三十、测试

新增：

```text
tests/test_agent_tool_result_store.py
tests/test_agent_tool_result_store_db.py
```

至少覆盖：

## 1. Small success

```text
≤32 KiB
→ payload saved
→ hash saved
→ replayable=true
```

## 2. Small failure

```text
success=false
→ error_code
→ no exception text
```

## 3. Medium result

```text
32 KiB < size ≤ 1 MiB
→ payload=null
→ hash present
→ unavailable=true
```

## 4. Large result

```text
>1 MiB
→ payload=null
→ unavailable=true
```

## 5. Duplicate

```text
same run_id + step_index
→ one row
```

## 6. Different runs

```text
run-A step-0
run-B step-0
→ two rows
```

## 7. tool_call_id

确认：

```text
tool_call_id
```

不是唯一键。

---

# 三十一、Security Tests

必须测试：

```text
prompt
messages
RAG chunk
SELECT * FROM secret_users
sk-live-...
password
Bearer ...
DATABASE_URL
```

不会进入数据库 payload。

---

# 三十二、Hash Integrity

测试：

```text
same result
→ same hash

different result
→ different hash
```

并且：

```text
hash != encryption
```

不把 hash 当安全存储。

---

# 三十三、Replay Availability

测试：

```text
small result
→ replayable

medium/large unavailable
→ not replayable
```

但：

**本阶段不要新增 Replay API。**

这里只测试：

```text
stored metadata
```

---

# 三十四、Best-effort Failure Isolation

Repository 模拟：

```text
DB unavailable
```

验证：

```text
Tool business result 不变
Agent business result 不变
```

不得：

```text
retry Tool
retry LLM
fallback
```

Observation / Store failure：

> 不能变成 Agent business failure。

但是：

如果未来 Checkpoint/Resume 要求 durable result 才能安全恢复：

那是未来恢复层的 gating 问题。

本阶段不改变当前运行语义。

---

# 三十五、DB Tests

使用：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_agent_tool_result_store_db.py
```

必须验证：

```text
table exists
columns
types
unique(run_id, step_index)
FK = 0
insert
duplicate
different run
payload security
cleanup
```

最终：

```text
agent_tool_result residue = 0
```

并检查：

```text
agent_step = 0
agent_run = 0
tool_execution_record = 0
llm_usage_record = 0
assistant_outcome_record = 0
rag_execution_record = 0
```

不要修改现有 residue guard。

---

# 三十六、Offline Regression

运行：

```powershell
python -m pytest -q tests/test_agent_tool_result_store.py
python -m pytest -q tests/test_agent_step_persistence.py tests/test_agent_run_persistence.py tests/test_agent_tool_observation.py
python -m pytest -q tests/test_agent_runtime.py
python -m pytest -q
```

要求：

```text
new tests = PASS
existing offline regression = PASS
```

已知 DB order-dependent 的 6 个历史失败不要修改。

---

# 三十七、Compile

运行：

```powershell
python -m compileall -q backend tests scripts
```

必须：

```text
0 errors
```

---

# 三十八、Git Diff

执行：

```powershell
git status --short
git diff --stat
git diff --check
git diff
```

确认：

```text
AgentRuntime 主循环没有重构
ToolExecutionService 没有语义修改
Tool Registry 没有修改
Tool Handler 没有修改
AgentRun 没有 schema 修改
AgentStep 没有 schema 修改
ToolExecutionRecord 没有 schema 修改
LLMUsageRecord 没有 schema 修改
Conversation 没有修改
Prompt 没有修改
Provider 没有修改
没有 migration
没有 secrets
```

允许：

```text
agent_tool_result.py
agent_tool_result_repository.py
recorder.py
相关生产接线
相关 tests
db model registration
```

---

# 三十九、Commit

只有全部通过：

```text
offline
DB
compileall
git diff --check
security
residue
```

才 commit：

```text
feat: Phase 5 agent tool result store
```

不要：

```text
push
PR
merge
rebase
```

---

# 四十、最终报告

严格输出：

```text
【Phase 5 AGENT TOOL RESULT STORE COMPLETE】

1. Database Model
2. Persistence Identity
3. Result Serialization
4. Hash
5. Size Boundary
6. Security Boundary
7. Success Result
8. Failure Result
9. Recorder
10. Write Timing
11. AgentRun Relationship
12. AgentStep Relationship
13. ToolExecutionRecord Relationship
14. Replay Availability
15. Failure Isolation
16. DB Tests
17. Offline Regression
18. DB Residue
19. compileall
20. Git Diff
21. Commit
22. Architecture Drift
23. Known Limitations

Final:

AgentRun Persistence      = READY
Agent Tool Observation   = READY
AgentStep Persistence    = READY
Tool Result Store        = READY

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

# 四十一、强制 STOP

完成后立即停止。

不要进入：

```text
Checkpoint
Resume
HITL
Cancellation
Read API
```

不要修改：

```text
AgentRuntime
ToolExecutionService
Tool Handler
Tool Registry
```

不要实现：

```text
blind rerun
Replay API
Checkpoint API
Resume API
```

本阶段唯一目标：

> **把 P-3 Replayable Tool Result Store 真正落地，同时保持当前 Agent Runtime 行为完全不变。**
