# Phase 5 P-5 — Agent Checkpoint Persistence

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段目标

在已经完成：

```text
AgentRun Persistence       READY
Agent Tool Observation     READY
AgentStep Persistence      READY
Tool Result Store          READY
Tool Result Write Point    READY

Tool Idempotency           DECIDED
Tool Replay                DECIDED
```

的基础上，实现：

> **Agent Checkpoint Persistence。**

本阶段只解决：

```text
Agent 当前执行到了哪里？
下一步应该做什么？
当前状态版本是多少？
谁拥有继续执行这个 Checkpoint 的 claim？
```

形成：

```text
AgentRun
   │
   ├── AgentStep
   │
   ├── AgentToolResult
   │
   └── AgentCheckpoint
          │
          ├── state_version
          ├── claim_token
          ├── next_action
          ├── pending_step_index
          ├── pending_tool_call_id
          └── references
```

---

# 二、非常重要：本阶段不是 Resume

本阶段：

```text
Checkpoint = READY
Resume      = NOT STARTED
```

禁止实现：

```text
resume()
restore()
replay()
retry
HITL
cancel
approval
```

也禁止：

```text
自动恢复 Agent
自动重新调用 LLM
自动重新执行 Tool
```

本阶段只建立：

> **可被未来 Resume 使用的 durable checkpoint。**

---

# 三、为什么现在需要 Checkpoint

当前 P-4 已经证明：

```text
Tool
 ↓
Tool Result
 ↓
agent_tool_result durable
```

但是目前仍然没有：

```text
当前 Agent 控制状态
```

因此进程在：

```text
Tool Result persisted
        ↓
AgentStep persisted
        ↓
AgentRun persisted
```

之间崩溃时：

我们知道：

```text
Tool Result 已存在
```

但是不知道：

```text
Agent 下一步应该做什么
```

因此 P-5 只建立这个控制状态。

未来 P-6 才使用：

```text
Checkpoint
+
AgentToolResult
+
saved LLM Decision
```

进行 Resume。

---

# 四、先阅读真实代码

开始修改前必须阅读：

```text
backend/app/agent/runtime.py
backend/app/agent/ids.py
backend/app/agent/recorder.py
backend/app/agent/capability.py

backend/app/db/models/agent_run.py
backend/app/db/models/agent_step.py
backend/app/db/models/agent_tool_result.py

backend/app/db/agent_run_repository.py
backend/app/db/agent_step_repository.py
backend/app/db/agent_tool_result_repository.py

backend/app/services/tool_execution_service.py

backend/app/services/ai_orchestrator_service.py
backend/app/services/project_orchestrator_factory.py
backend/app/api/orchestrator_chat.py
```

以及：

```text
tests/test_agent_runtime.py
tests/test_agent_run_persistence.py
tests/test_agent_step_persistence.py
tests/test_agent_tool_result_store.py
tests/test_agent_tool_result_store_db.py
tests/test_agent_tool_result_integration.py
tests/test_agent_run_scope.py
```

同时搜索：

```text
run_id
step_index
state_version
claim_token
checkpoint
WAITING_APPROVAL
CANCELLED
LIMIT_REACHED
```

**必须以真实代码为准。**

---

# 五、严格范围

## 允许新增

推荐：

```text
backend/app/db/models/agent_checkpoint.py
backend/app/db/agent_checkpoint_repository.py
```

以及：

```text
backend/app/agent/checkpoint.py
```

如果当前架构有更自然的位置，可以调整。

测试：

```text
tests/test_agent_checkpoint.py
tests/test_agent_checkpoint_db.py
```

必要时增加：

```text
tests/test_agent_checkpoint_integration.py
```

---

# 六、禁止修改

本阶段禁止修改：

```text
ToolExecutionContext
ToolExecutionService contract
Tool Registry
Tool Handler
LLM Provider
LLM Adapter
Prompt
Conversation
Conversation Idempotency
Evidence
RAG
Text-to-SQL
SQL Validator
SQL Executor
AgentRun schema
AgentStep schema
AgentToolResult schema
ToolExecutionRecord schema
LLMUsageRecord schema
AssistantOutcomeRecord schema
API contract
```

尤其禁止：

```text
❌ 修改 AgentRun
❌ 修改 AgentStep
❌ 修改 AgentToolResult
❌ 把 checkpoint 字段塞进 AgentRun
❌ 把 checkpoint 字段塞进 AgentStep
❌ 把 checkpoint 字段塞进 ToolExecutionContext
```

Checkpoint 必须是：

> **独立持久化对象。**

---

# 七、Checkpoint 与 AgentRun 的关系

不要建立数据库 FK。

保持现有架构：

```text
AgentRun
   │
   │ run_id
   ▼
AgentCheckpoint
```

使用：

```text
run_id
```

逻辑关联。

不要：

```text
FOREIGN KEY
```

不要级联删除。

原因：

当前项目已经明确：

```text
AgentRun = execution record
Checkpoint = recovery control state
```

二者职责不同。

---

# 八、Checkpoint 最小 Schema

新增：

```text
ai_ops.agent_checkpoint
```

建议最小字段：

```text
id
run_id
state_version
claim_token
status
next_action
pending_step_index
pending_tool_call_id
pending_decision_ref
pending_tool_result_ref
created_at
updated_at
```

推荐类型：

```text
id                    BIGINT PRIMARY KEY

run_id                VARCHAR(64) NOT NULL

state_version         BIGINT NOT NULL

claim_token            VARCHAR(128) NOT NULL

status                VARCHAR(32) NOT NULL

next_action           VARCHAR(32) NOT NULL

pending_step_index    INTEGER NULL

pending_tool_call_id  VARCHAR(128) NULL

pending_decision_ref  VARCHAR(128) NULL

pending_tool_result_ref VARCHAR(128) NULL

created_at            TIMESTAMPTZ NOT NULL DEFAULT now()

updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
```

---

# 九、Checkpoint 必须是可更新对象

与：

```text
AgentRun
AgentStep
AgentToolResult
```

不同。

Checkpoint 是：

> **当前恢复控制状态。**

因此：

```text
UPDATE agent_checkpoint
```

是允许的。

本阶段必须支持：

```text
create
update
get
```

最小能力。

---

# 十、唯一身份

Checkpoint：

```text
UNIQUE(run_id)
```

即：

```text
一个 Agent Run
    ↓
最多一个 current checkpoint
```

不要：

```text
UNIQUE(run_id, state_version)
```

因为：

> `state_version` 是版本号，不是 Checkpoint 行身份。

---

# 十一、state_version

`state_version` 是：

> Checkpoint 当前状态版本。

创建：

```text
state_version = 1
```

每次成功更新：

```text
state_version = state_version + 1
```

禁止：

```text
state_version -= 1
```

禁止：

```text
随机 version
```

禁止：

```text
时间戳作为 version
```

---

# 十二、Optimistic Concurrency

这是本阶段最重要的部分。

更新必须使用：

```text
run_id
+
expected_state_version
```

例如：

```sql
UPDATE ai_ops.agent_checkpoint
SET
    state_version = state_version + 1,
    ...
WHERE
    run_id = :run_id
    AND state_version = :expected_state_version
```

要求：

```text
affected_rows == 1
```

才表示：

```text
update success
```

如果：

```text
affected_rows == 0
```

则：

```text
CHECKPOINT_VERSION_CONFLICT
```

---

# 十三、为什么必须 state_version

避免：

```text
Worker A
   ↓
read version=5

Worker B
   ↓
read version=5

Worker A
   ↓
write version=6

Worker B
   ↓
write version=6
```

造成：

```text
lost update
```

因此：

```text
expected version
```

必须进入 UPDATE 条件。

---

# 十四、claim_token

`claim_token` 表示：

> 当前执行者持有的 Checkpoint claim。

它不是：

```text
request_id
run_id
tool_call_id
conversation idempotency key
```

也不是：

```text
LLM request id
```

---

# 十五、claim_token 生成

可以使用：

```python
secrets.token_urlsafe(...)
```

或者当前项目已有安全 token 工具。

必须：

```text
cryptographically random
```

不要：

```text
uuid.uuid4()
```

也不要：

```text
hash(run_id)
```

也不要：

```text
timestamp
```

---

# 十六、claim_token 不对外作为业务 ID

不要把：

```text
claim_token
```

返回给用户。

不要：

```text
API response
conversation metadata
AIOrchestrationResult
```

暴露它。

它只是：

```text
internal concurrency / ownership token
```

---

# 十七、claim 更新规则

更新 Checkpoint 时：

必须同时满足：

```text
run_id
expected_state_version
claim_token
```

即：

```sql
UPDATE ...
WHERE
    run_id = :run_id
    AND state_version = :expected_state_version
    AND claim_token = :claim_token
```

如果：

```text
version 不匹配
```

或者：

```text
claim_token 不匹配
```

都：

```text
CHECKPOINT_CONFLICT
```

---

# 十八、claim_token 是否每次更新变化

本阶段：

> **每次成功 checkpoint update 不重新生成 claim_token。**

即：

```text
run_id = A
claim_token = X
version = 1

update

run_id = A
claim_token = X
version = 2
```

保持：

```text
claim_token = X
```

未来 P-7 如果引入 lease / ownership，可以单独重新设计。

---

# 十九、next_action

定义最小枚举：

```text
NONE
LLM_DECISION
TOOL_EXECUTION
WAITING_APPROVAL
COMPLETED
FAILED
```

本阶段建议只真正使用：

```text
LLM_DECISION
TOOL_EXECUTION
COMPLETED
FAILED
```

预留：

```text
WAITING_APPROVAL
```

但：

> 不实现 HITL。

不要在本阶段增加：

```text
APPROVED
REJECTED
CANCELLED
```

---

# 二十、status

Checkpoint status 建议：

```text
ACTIVE
PAUSED
TERMINAL
```

本阶段：

```text
ACTIVE
TERMINAL
```

可以预留：

```text
PAUSED
```

但不要实现 HITL。

---

# 二十一、pending_step_index

表示：

> 当前需要继续处理的 Agent Step。

例如：

```text
next_action = TOOL_EXECUTION
pending_step_index = 2
```

表示未来 Resume：

```text
继续 step 2
```

不要使用：

```text
round
```

---

# 二十二、pending_tool_call_id

只有：

```text
next_action = TOOL_EXECUTION
```

时允许存在。

必须来自：

```text
Provider ToolCall.id
```

禁止：

```text
uuid.uuid4()
```

重新生成。

如果：

```text
next_action != TOOL_EXECUTION
```

则：

```text
pending_tool_call_id = NULL
```

---

# 二十三、pending_decision_ref

不要保存：

```text
完整 LLM message
完整 prompt
完整 Tool Call
完整 reasoning
```

只保存：

> 已经持久化的 Decision 的引用。

例如：

```text
decision_ref
```

但本阶段：

**如果当前项目还没有独立 Decision Store，不要为了 P-5 新增 Decision Store。**

因此可以：

```text
pending_decision_ref = NULL
```

并明确：

```text
LLM Decision Persistence = P-6 prerequisite
```

---

# 二十四、pending_tool_result_ref

P-4 已经有：

```text
agent_tool_result
```

本阶段可以使用稳定引用：

```text
run_id + step_index
```

但是：

**不要新增 result_ref API。**

可以记录：

```text
pending_tool_result_ref
```

为：

```text
"{run_id}:{step_index}"
```

前提是当前系统已有这种逻辑引用方式。

如果没有：

```text
pending_tool_result_ref = NULL
```

并在报告说明。

不要修改 P-4 schema。

---

# 二十五、Checkpoint 不保存大数据

禁止保存：

```text
prompt
messages
conversation context
RAG chunks
tool definitions
tool arguments
tool result body
SQL
LLM response body
```

Checkpoint 只保存：

```text
control state
references
identifiers
```

这与 P-4 Tool Result Store 的职责分离保持一致。大型状态应单独持久化，而 checkpoint 保持小型控制状态。

---

# 二十六、Checkpoint Repository

新增：

```text
backend/app/db/agent_checkpoint_repository.py
```

至少提供：

```text
create()
get(run_id)
update()
delete()
```

但：

### delete

本阶段：

> 不允许业务路径使用 delete。

可以为了测试/cleanup 提供：

```text
delete_for_test()
```

如果项目已有测试 cleanup 模式，优先复用。

生产 Repository API 不需要 delete。

---

# 二十七、Repository 安全验证

写入前必须检查：

```text
run_id length
claim_token length
state_version >= 1
pending_step_index >= 0
enum values
```

禁止：

```text
negative version
negative step
empty claim token
unknown status
unknown next_action
```

---

# 二十八、Atomic Update

Repository 的 update 必须是：

```text
single SQL UPDATE
```

不要：

```text
SELECT version
    ↓
Python compare
    ↓
UPDATE
```

因为这不是安全的 optimistic concurrency。

必须：

```text
UPDATE ... WHERE version = expected
```

---

# 二十九、Create 语义

创建：

```text
run_id
state_version=1
claim_token=random
status=ACTIVE
next_action=...
```

如果：

```text
run_id already exists
```

必须：

```text
CHECKPOINT_ALREADY_EXISTS
```

不要静默覆盖。

---

# 三十、Checkpoint DTO

建议新增：

```text
AgentCheckpoint
```

要求：

```text
frozen / immutable
```

至少：

```text
run_id
state_version
claim_token
status
next_action
pending_step_index
pending_tool_call_id
pending_decision_ref
pending_tool_result_ref
created_at
updated_at
```

注意：

内部 DTO 可以包含 claim_token。

但是：

**任何 API / 用户输出 DTO 都不得包含 claim_token。**

---

# 三十一、Checkpoint Recorder

建议：

```text
backend/app/agent/checkpoint.py
```

提供：

```text
AgentCheckpointRecorder
```

但：

> 不要把 DB Repository 直接注入 AgentRuntime。

保持：

```text
AgentRuntime
    ↓
Checkpoint Port
    ↓
Recorder
    ↓
Repository
```

---

# 三十二、AgentRuntime 是否本阶段接入

本阶段：

### 可以接入最小 checkpoint write point

但只能：

```text
Tool Result durable
    ↓
Checkpoint update
```

不能改变 Agent 主循环。

---

# 三十三、推荐 Checkpoint 写点

至少支持：

### Tool Result 完成后

```text
Tool Result Store
      ↓
Checkpoint
```

状态：

```text
status = ACTIVE
next_action = LLM_DECISION
pending_step_index = next step
```

表示：

> Tool 已经完成，下一步应该重新进入 LLM Decision。

---

# 三十四、Agent 初始 Checkpoint

本阶段可以不在 Agent 启动时创建 checkpoint。

即：

```text
Agent starts
    ↓
LLM
    ↓
Tool
    ↓
Tool Result
    ↓
Checkpoint
```

这是为了避免本阶段引入：

```text
LLM Decision persistence
```

---

# 三十五、Terminal Checkpoint

当 Agent：

```text
COMPLETED
```

可以写：

```text
status=TERMINAL
next_action=COMPLETED
```

当：

```text
FAILED
```

可以：

```text
status=TERMINAL
next_action=FAILED
```

但是：

> 不修改 AgentRun 语义。

AgentRun 仍然是最终 execution record。

---

# 三十六、LIMIT_REACHED

本阶段必须考虑：

```text
AgentRuntime status = LIMIT_REACHED
```

Checkpoint：

```text
status=TERMINAL
next_action=FAILED
```

或者如果当前设计有更合适映射，必须保持：

```text
Checkpoint terminal
```

不要新增：

```text
LIMIT_REACHED
```

作为新的 checkpoint next_action，除非真实代码已有统一枚举体系。

---

# 三十七、WAITING_APPROVAL

本阶段：

可以定义：

```text
next_action=WAITING_APPROVAL
```

但：

**不允许真正产生该状态。**

HITL 属于后续阶段。

---

# 三十八、Checkpoint 更新顺序

Tool 路径：

```text
Tool Handler
    ↓
AgentToolResult
    ↓
Checkpoint
    ↓
AgentStep
    ↓
next LLM
```

如果当前已有：

```text
Tool Result
→ AgentStep
→ AgentRun
```

本阶段不需要为了 Checkpoint 大规模重构。

只保证：

> Tool Result 已 durable 后，Checkpoint 才能引用它。

---

# 三十九、Crash Window

新增测试：

```text
Tool Result persisted
      ↓
Checkpoint persisted
      ↓
simulate crash
```

检查：

```text
agent_tool_result exists
agent_checkpoint exists
```

而：

```text
AgentRun
```

可以：

```text
0
```

因为 Agent 还没有 terminal state。

---

# 四十、Checkpoint Result Reference

如果：

```text
pending_tool_result_ref
```

存在：

必须可以逻辑定位：

```text
agent_tool_result
```

禁止：

```text
reference points to another run
```

禁止：

```text
reference points to another step
```

至少增加纯函数验证：

```text
same run_id
same step_index
```

---

# 四十一、Concurrency Test

至少模拟：

```text
Worker A
expected_version=1
claim_token=A

Worker B
expected_version=1
claim_token=B
```

A 成功：

```text
version=2
```

B 必须：

```text
CHECKPOINT_CONFLICT
```

即使 B 使用正确的：

```text
run_id
```

也不能覆盖 A。

---

# 四十二、Wrong Claim Test

```text
run_id = same
expected_version = current
claim_token = wrong
```

必须：

```text
CHECKPOINT_CONFLICT
```

数据库状态不变。

---

# 四十三、Wrong Version Test

```text
run_id = same
claim_token = correct
expected_version = old
```

必须：

```text
CHECKPOINT_CONFLICT
```

数据库状态不变。

---

# 四十四、Successful Update Test

```text
version=1
claim=X
```

update：

```text
expected_version=1
claim=X
```

结果：

```text
version=2
claim=X
```

并且：

```text
updated_at
```

变化。

---

# 四十五、Concurrent Claim 不允许自动接管

本阶段：

如果：

```text
claim_token != current claim
```

禁止：

```text
自动 takeover
```

禁止：

```text
重新生成 claim
```

禁止：

```text
force update
```

未来 lease/ownership 单独设计。

---

# 四十六、AgentRuntime DB Isolation

AgentRuntime 当前已经通过：

```text
AgentRunRecorder
AgentStepRecorder
AgentToolResultWritePoint
```

保持：

```text
DB-free
```

Checkpoint 也必须：

> 通过 Port / Recorder 注入。

禁止：

```python
from backend.app.db...
```

进入 Runtime。

---

# 四十七、Architecture Audit

必须增加测试：

```text
agent/runtime.py
```

不能 import：

```text
sqlalchemy
backend.app.db
agent_checkpoint_repository
AgentCheckpoint ORM
Session
sessionmaker
```

允许：

```text
AgentCheckpoint Port
```

---

# 四十八、No Conversation Coupling

Checkpoint 不得 import：

```text
Conversation
ConversationTurn
Idempotency
```

不要把：

```text
conversation_id
turn_id
idempotency_key
```

放入 Checkpoint schema。

未来通过：

```text
request_id
```

或其他 correlation 进行外部关联。

---

# 四十九、No Evidence Coupling

Checkpoint：

```text
≠ Evidence
```

不得：

```text
evidence_id
```

进入 checkpoint schema。

---

# 五十、No Tool Result Duplication

Checkpoint 不保存：

```text
result_payload
result_hash
result_size_bytes
```

这些已经属于：

```text
AgentToolResult
```

---

# 五十一、No LLM Result Duplication

Checkpoint 不保存：

```text
prompt_tokens
completion_tokens
total_tokens
model
provider
finish_reason
LLM response body
```

LLM Observation / Usage 已负责。

---

# 五十二、Tests

新增：

```text
tests/test_agent_checkpoint.py
```

至少覆盖：

### DTO

```text
frozen
valid enum
invalid enum
version >= 1
step >= 0
claim_token required
```

### Repository

```text
create
get
update
duplicate run
wrong version
wrong claim
successful update
```

### Security

```text
no prompt
no SQL
no result payload
no secrets
no conversation
no evidence
no LLM usage
```

### Scope

```text
Checkpoint doesn't own AgentRun
Checkpoint doesn't own Conversation
```

---

# 五十三、DB Tests

新增：

```text
tests/test_agent_checkpoint_db.py
```

至少：

```text
schema exists
column types
UNIQUE(run_id)
indexes
no FK
create
get
update
duplicate
version conflict
claim conflict
concurrent update
cleanup
residue=0
```

---

# 五十四、Integration Test

新增：

```text
tests/test_agent_checkpoint_integration.py
```

至少验证：

```text
Agent
 ↓
Tool
 ↓
AgentToolResult
 ↓
Checkpoint
```

并确认：

```text
checkpoint.run_id == agent_run.run_id
checkpoint.pending_tool_result_ref
```

如果当前 P-4 result reference 不能可靠建立：

```text
不要强行实现
```

记录：

```text
pending_tool_result_ref = deferred
```

---

# 五十五、禁止真实 DeepSeek

本阶段：

```text
DeepSeek calls = 0
```

使用：

```text
Fake LLM
Fake Tool
```

完成测试。

---

# 五十六、禁止真实业务 Tool

不需要：

```text
get_inventory
get_work_order
```

真实业务数据验证。

P-5 是：

```text
state persistence
concurrency
```

不是业务 Tool smoke。

---

# 五十七、测试命令

先：

```powershell
python -m pytest -q tests/test_agent_checkpoint.py
```

然后：

```powershell
python -m pytest -q tests/test_agent_checkpoint_db.py
```

DB：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_agent_checkpoint_db.py
```

Integration：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_agent_checkpoint_integration.py
```

然后：

```powershell
python -m pytest -q
```

---

# 五十八、DB Regression

执行：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

允许保留当前已知的：

```text
order-dependent / baseline guard failures
```

但：

> 不得新增新的失败。

必须记录：

```text
before
after
```

---

# 五十九、Compile

```powershell
python -m compileall -q backend tests scripts
```

要求：

```text
0 errors
```

---

# 六十、Lint

如果项目已有 lint：

```powershell
<existing lint command>
```

要求：

```text
0 diagnostics
```

---

# 六十一、Residue

测试完成后：

```text
agent_checkpoint = 0
agent_tool_result = 0
agent_step = 0
agent_run = 0
tool_execution_record = 0
llm_usage_record = 0
assistant_outcome_record = 0
rag_execution_record = 0
```

全部：

```text
0
```

---

# 六十二、Git Diff

执行：

```powershell
git status --short
git diff --stat
git diff --check
git diff
```

必须确认：

```text
ToolExecutionContext        = unchanged
ToolExecutionService       = unchanged
Tool Registry              = unchanged
Tool Handler               = unchanged
AgentRun schema            = unchanged
AgentStep schema           = unchanged
AgentToolResult schema     = unchanged
ToolExecutionRecord        = unchanged
LLMUsageRecord             = unchanged
Conversation               = unchanged
Prompt                     = unchanged
Provider                   = unchanged
API contract               = unchanged
```

---

# 六十三、Commit

只有全部通过：

```text
offline PASS
DB PASS
compile PASS
lint PASS
residue 0
security PASS
concurrency PASS
git diff --check PASS
```

才 commit：

```text
feat: Phase 5 agent checkpoint persistence
```

不要：

```text
push
PR
merge
rebase
```

---

# 六十四、最终报告

严格使用：

```text
【Phase 5 AGENT CHECKPOINT COMPLETE】

1. Checkpoint Architecture
2. Schema
3. Repository
4. state_version
5. claim_token
6. Optimistic Concurrency
7. next_action
8. pending references
9. AgentRuntime Integration
10. AgentRun Relationship
11. AgentStep Relationship
12. AgentToolResult Relationship
13. Security Boundary
14. Crash Window
15. Concurrency Tests
16. Tests
17. DB Tests
18. DB Residue
19. Offline Regression
20. compileall
21. lint
22. Git Diff
23. Commit
24. Architecture Drift
25. Known Limitations

Final:

AgentRun Persistence       = READY
Agent Tool Observation    = READY
AgentStep Persistence     = READY
Tool Result Store         = READY
Tool Result Write Point   = READY
Checkpoint Persistence    = READY

Tool Idempotency          = DECIDED
Tool Replay               = DECIDED

Resume                    = NOT STARTED
HITL                      = NOT STARTED
Cancellation              = NOT STARTED
Read API                  = NOT STARTED

STOP
```

---

# 六十五、强制 STOP

完成 P-5 后立即停止。

禁止进入：

```text
P-6 Resume
HITL
Cancellation
Read API
Replay API
```

禁止新增：

```text
LLM Decision Store
Tool Replay Engine
atomic claim
blind rerun
lease takeover
```

除非本阶段测试明确发现 Checkpoint 自身存在真实 bug，否则不要修改：

```text
AgentRuntime loop
ToolExecutionService
ToolExecutionContext
Tool Registry
Tool Handler
```

---

# 六十六、最终架构

完成后应该形成：

```text
                 AgentRun
                    │
                    │ run_id
                    ▼
             AgentCheckpoint
                    │
          ┌─────────┼─────────┐
          │         │         │
          ▼         ▼         ▼
    state_version claim    next_action
                            │
                     ┌──────┴──────┐
                     │             │
                     ▼             ▼
                Tool Result    LLM Decision
                Reference       Reference
                     │
                     ▼
              AgentToolResult
```

职责必须保持：

```text
AgentRun
= 已经发生了什么

AgentStep
= 每一步发生了什么

AgentToolResult
= Tool 的历史结果是什么

AgentCheckpoint
= 下一次恢复应该从哪里继续

Conversation
= 用户对话是什么

Evidence
= 信息来源是什么
```

不要混淆这六个概念。

**Phase 5 P-5 完成后立即 STOP。**
