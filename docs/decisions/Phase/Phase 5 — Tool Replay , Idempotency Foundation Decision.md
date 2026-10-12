# Phase 5 — Tool Replay / Idempotency Foundation Decision

你现在开始执行：

**Phase 5 Tool Replay / Idempotency Foundation Decision**

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段目标

本阶段**只做架构调查 + 决策冻结**。

不要实现 Checkpoint。

不要实现 Resume。

不要实现 HITL。

不要实现 Cancellation。

不要新增 Read API。

不要修改 AgentRuntime 主循环。

不要修改 Tool Registry。

不要修改现有 Tool Handler。

不要修改 Conversation / Idempotency-Key。

---

本阶段只解决两个此前明确延期的问题：

```text
P-2 Tool Idempotency / Dedup Contract

P-3 Replayable Tool Result Store
```

最终必须回答：

```text
Agent Tool Call
      ↓
tool_call_id
      ↓
是否已经执行？
      ↓
Tool Result 是否已经可靠保存？
      ↓
Resume 时：
    ├── 已完成 → Replay
    └── 未完成 → 根据安全边界决定是否重新执行
```

核心问题：

> 如果 Agent 在 Tool 执行完成之后、Checkpoint 持久化之前发生进程崩溃，下一次 Resume 如何避免错误地重复执行 Tool？

---

# 二、必须先阅读

不要假设当前实现。

先阅读：

```text
backend/app/agent/runtime.py
backend/app/agent/models.py
backend/app/agent/decision.py
backend/app/agent/capability.py
backend/app/agent/recorder.py
backend/app/agent/llm.py
backend/app/agent/ids.py

backend/app/services/tool_execution_service.py
backend/app/services/ai_orchestrator_service.py

backend/app/db/models/agent_run.py
backend/app/db/models/agent_step.py
backend/app/db/agent_run_repository.py
backend/app/db/agent_step_repository.py

backend/app/db/models/tool_execution_record.py
backend/app/db/models/llm_usage_record.py
backend/app/db/models/assistant_outcome_record.py

tests/test_agent_runtime.py
tests/test_agent_tool_observation.py
tests/test_agent_run_persistence.py
tests/test_agent_step_persistence.py
tests/test_agent_step_persistence_db.py

docs/decisions/Phase/Phase 5 — Scope & Architecture Decision.md
docs/decisions/Phase/Phase 5 — Agent State & Resume Boundary Decision.md
```

同时搜索：

```text
tool_call_id
ToolExecutionContext
ToolExecutionRecord
ToolExecutionObserver
tool execution
idempotency
dedup
request_id
run_id
step_index
AgentStep
AgentRun
```

---

# 三、必须确认当前真实状态

先输出一份调查结果。

至少回答：

## 1. Tool Call Identity

当前唯一能够识别一次 Agent Tool 调用的字段是什么？

重点确认：

```text
tool_call_id
run_id
step_index
request_id
```

分别是什么语义。

不能把：

```text
run_id
```

当成：

```text
tool_call_id
```

也不能把：

```text
Conversation Idempotency-Key
```

当成 Tool Idempotency Key。

---

# 四、明确三个 Identity

必须冻结：

```text
request_id
    = AI Runtime request correlation

run_id
    = 一次 Agent execution identity

tool_call_id
    = Provider / LLM 提出的某一次 Tool Call identity
```

如果当前真实实现与此不同：

先报告。

不要直接修改。

---

# 五、当前 ToolExecutionRecord 能否承担 Replay Store？

重点分析：

```text
tool_execution_record
```

当前是否具备：

```text
tool_call_id
request_id
run_id
round
tool_name
project_id
success
duration
```

以及：

**是否保存 Tool Result 本身。**

必须明确：

```text
ToolExecutionRecord
    =
Observation

还是

ToolExecutionRecord
    =
Replayable Result Store
```

不能因为已经存在 ToolExecutionRecord 就默认认为它可以用于 Resume。

---

# 六、禁止把 Observation 当 State

明确保持：

```text
LLMUsageRecord
    = LLM-level observation

ToolExecutionRecord
    = Tool-level observation

AgentStep
    = Runtime State + Audit Projection

AgentRun
    = Agent Run terminal record

Checkpoint
    = Future recoverable execution state

ToolResult Store
    = Future replayable Tool result
```

不能混淆。

---

# 七、P-2：Tool Idempotency / Dedup

需要决定：

## Option A

```text
tool_call_id
    ↓
唯一 Tool execution identity
```

同一个：

```text
run_id + tool_call_id
```

最多允许一次实际 Tool Handler execution。

---

## Option B

```text
tool_call_id
    ↓
只作为 LLM protocol correlation
```

真正的 idempotency key 由：

```text
run_id + step_index
```

组成。

---

## Option C

其他设计。

如果选择其他设计：

必须说明：

```text
为什么
风险
与当前 AgentStep / ToolExecutionRecord 如何关联
```

---

# 八、强制分析 Provider 行为

必须检查当前：

```text
Provider ToolCall
```

是否保证：

```text
tool_call_id
```

存在。

以及：

```text
同一次 Tool Call
```

在 LLM 重试 / Resume / replay 场景下：

是否可能：

```text
重新产生新的 tool_call_id
```

或者：

```text
恢复时继续使用旧 tool_call_id
```

这个问题必须在最终决策里明确。

---

# 九、Tool Handler 副作用分类

检查当前真实 Tool。

至少：

```text
get_inventory
get_work_order
```

不要假设所有未来 Tool 都是只读。

建立一个最小分类：

```text
READ_ONLY
IDEMPOTENT_WRITE
NON_IDEMPOTENT_WRITE
UNKNOWN
```

当前 Agent Runtime：

```text
只允许 READ_ONLY
```

这一点保持不变。

---

# 十、关键问题：READ_ONLY 是否意味着可以安全 Replay？

不要简单回答：

```text
READ_ONLY = safe
```

必须分析。

例如：

```text
SELECT inventory
```

可能随着时间变化。

Replay 的语义不是：

```text
重新查询最新数据
```

而是：

> 恢复同一个已经完成的 Tool Call 的历史结果。

因此：

```text
Replay
```

与：

```text
Re-execute
```

必须明确区分。

---

# 十一、P-3：Replayable Tool Result Store

研究当前系统是否已经存在可以安全保存：

```text
tool_call_id
tool_name
project_id
run_id
step_index
success
result
error_code
created_at
```

的存储。

但是：

**不要直接决定把完整 Tool Result 写入 ToolExecutionRecord。**

先分析：

### Result 是否包含：

```text
业务数据
PII
敏感字段
大文本
SQL
内部异常
凭证
Prompt Injection
```

Tool Result 可能是不可信数据。

不能因为要 Replay 就把所有原始 Tool Result 无限制写入数据库。

---

# 十二、Result Storage 安全边界

必须研究：

```text
Tool Result
```

应该保存：

```text
完整结果
```

还是：

```text
安全序列化结果
```

还是：

```text
外部 artifact reference
```

还是：

```text
hash + reference
```

---

# 十三、大小限制

必须考虑 Tool Result 可能非常大。

例如：

```text
10MB
100MB
```

不能设计：

```text
agent_tool_result.result TEXT
```

然后无大小限制。

最终设计至少必须明确：

```text
small result
large result
```

如何处理。

---

# 十四、不要保存 Prompt / Context

无论最终采用什么方案：

禁止把以下内容放入 Replay Store：

```text
system prompt
user prompt
conversation context
RAG chunks
tool definitions
API key
authorization
password
database URL
headers
SQL credentials
```

---

# 十五、Tool Result 是否需要加密？

本阶段不要实现加密。

但是要做架构决策：

```text
是否属于敏感业务数据
是否需要依赖现有 DB encryption / application encryption
```

如果无法确认：

```text
记录为 OQ
```

不要凭空设计加密系统。

---

# 十六、P-2 Dedup 的核心语义

必须定义：

### Case A：第一次执行

```text
tool_call_id = tc_001

DB:
不存在

→ execute handler
→ save result
```

---

### Case B：重复执行请求

```text
tool_call_id = tc_001

DB:
已有成功结果

→ 不执行 handler
→ replay historical result
```

---

### Case C：已有失败结果

必须决定：

```text
FAILED
```

是否：

```text
replay failure
```

还是：

```text
允许重新执行
```

不能模糊。

---

### Case D：执行中进程崩溃

这是最重要的情况。

例如：

```text
handler started
      ↓
handler actually completed
      ↓
process crashes
      ↓
result has NOT been persisted
```

此时：

```text
DB:
没有 completed record
```

Resume 无法证明 Tool 是否已经产生副作用。

必须决定：

```text
SAFE RERUN
```

还是：

```text
FAIL CLOSED
```

还是：

```text
DOWNSTREAM IDEMPOTENCY REQUIRED
```

---

# 十七、不能假设 Exactly Once

最终架构文档中必须明确：

不要声称：

```text
exactly-once Tool execution
```

除非有真实基础设施证明。

默认更合理的模型是：

```text
at-least-once possibility
+
idempotency / dedup
+
replay completed result
```

或者：

```text
fail-closed when execution boundary is uncertain
```

根据实际 Tool 类型决定。

---

# 十八、推荐重点考虑的模型

优先评估：

```text
Tool Call
    ↓
Execution Identity
    ↓
Execution State
    ├── NOT_STARTED
    ├── RUNNING
    ├── SUCCEEDED
    └── FAILED
```

但是：

**本阶段只做设计，不实现 State Machine。**

不要新增：

```text
RUNNING
```

等 production schema，除非最终决策明确要求。

---

# 十九、Checkpoint 与 Tool Result 的关系

最终必须画清：

```text
AgentRun
   │
   ├── AgentStep
   │
   └── Checkpoint
          │
          ├── current_step
          ├── next_action
          ├── pending_tool_call_id
          └── Tool Result Reference
```

以及：

```text
Tool Result Store
   │
   └── tool_call_id
```

Checkpoint：

> 保存“恢复需要知道什么”。

Tool Result Store：

> 保存“恢复后需要重放什么”。

AgentStep：

> 保存“已经发生过什么”的安全投影。

三者不能混为一谈。

---

# 二十、LLM Replay

继续遵守此前冻结的：

```text
Resume:
不重新调用已经完成的 LLM Decision
```

如果恢复时：

```text
Decision 已持久化
```

则：

```text
Replay Decision
```

如果：

```text
Decision 缺失
```

则：

```text
FAIL CLOSED
```

不能：

```text
自动重新问 LLM
```

本阶段只确认 Tool Replay 与这个规则如何衔接。

---

# 二十一、Conversation Idempotency 不参与 Tool Dedup

明确：

```text
Conversation Idempotency-Key
```

解决：

```text
重复用户请求
```

而：

```text
Tool Idempotency
```

解决：

```text
同一个 Agent Tool Call 是否重复执行
```

两者必须保持独立。

---

# 二十二、Concurrency

必须考虑：

```text
两个 Resume 同时到达
```

如果两者都看到：

```text
tool_call_id = tc_001
status = NOT_STARTED
```

不能同时执行 Tool。

因此最终决策必须说明：

```text
unique constraint
+
atomic claim
```

是否需要。

如果当前阶段不实现：

记录为未来 Checkpoint/Resume 的前置要求。

---

# 二十三、不要修改数据库

本阶段：

```text
NO MIGRATION
NO CREATE TABLE
NO ALTER TABLE
NO DB WRITE
```

可以：

```text
读取 schema
读取 ORM model
读取 repository
```

但是不要创建新表。

---

# 二十四、不要调用 DeepSeek

本阶段：

```text
LLM calls = 0
```

不需要真实 LLM。

---

# 二十五、不要调用真实 Tool

本阶段：

```text
Tool handler calls = 0
```

只分析现有 Tool Contract。

---

# 二十六、输出一个最终架构决策

新增：

```text
docs/decisions/Phase/Phase 5 — Tool Replay & Idempotency Decision.md
```

如果项目决策文档已经存在类似文件：

优先 append-only。

不要覆盖历史决策。

文档必须包含：

## 1. Problem

为什么 Resume 前必须解决 Tool Replay。

## 2. Current State

当前：

```text
AgentRun
AgentStep
ToolExecutionRecord
LLMUsageRecord
Conversation Idempotency
```

分别承担什么职责。

## 3. Tool Call Identity

明确：

```text
run_id
request_id
tool_call_id
step_index
```

的关系。

## 4. Idempotency Decision

选择：

```text
Option A / B / C
```

并解释。

## 5. Replay Decision

明确：

```text
success
failure
unknown/in-flight
```

分别怎么处理。

## 6. Tool Result Store Decision

明确：

```text
是否新增 Store
保存什么
不保存什么
大小边界
敏感数据边界
```

## 7. Checkpoint Boundary

明确：

```text
Checkpoint 保存什么
Tool Result Store 保存什么
AgentStep 保存什么
```

## 8. Concurrency

说明：

```text
atomic claim
unique identity
state_version
claim_token
```

哪些属于本阶段、哪些延期。

## 9. Security

明确：

```text
Prompt injection
PII
secrets
large result
untrusted tool output
```

处理边界。

## 10. Failure Model

明确：

```text
handler success + result persisted
handler success + process crash
handler failure + result persisted
handler failure + process crash
```

四种情况。

## 11. Resume Contract

明确未来 Resume 可以：

```text
Replay
```

不能：

```text
blind rerun
```

## 12. Migration Path

例如：

```text
P-2 Decision       ← current
P-3 Decision       ← current
Checkpoint         ← future
Resume             ← future
HITL               ← future
Cancellation       ← future
```

---

# 二十七、必须新增测试吗？

本阶段默认：

```text
NO production code
NO new runtime tests
```

但是可以新增：

```text
architecture decision verification test
```

如果项目现有架构测试适合验证：

```text
identity semantics
no DB schema changes
no runtime behavior changes
```

可以增加。

否则不要为了测试数量新增代码。

---

# 二十八、必须检查 Git Diff

执行：

```powershell
git status --short
git diff --stat
git diff
```

确认：

```text
生产 Agent Runtime = 0 改动
Tool Handler = 0 改动
Tool Registry = 0 改动
Conversation = 0 改动
DB schema = 0 改动
Prompt = 0 改动
Provider = 0 改动
```

允许：

```text
docs/decisions/Phase/Phase 5 — Tool Replay & Idempotency Decision.md
```

以及必要的 architecture verification test。

---

# 二十九、验证命令

如果没有代码变更：

```powershell
git diff --check
```

以及必要的静态检查。

如果增加测试：

```powershell
python -m pytest <targeted-tests> -q
```

不要运行 DeepSeek。

不要运行 DB integration。

---

# 三十、最终报告

完成后严格输出：

```text
【Phase 5 TOOL REPLAY / IDEMPOTENCY DECISION COMPLETE】

1. Problem
2. Current State
3. Tool Call Identity
4. Idempotency Decision
5. Replay Decision
6. Tool Result Store Decision
7. Checkpoint Boundary
8. Concurrency
9. Security Boundary
10. Failure Model
11. Resume Contract
12. Migration Path
13. Production Code Changes
14. Tests
15. DeepSeek Calls
16. Tool Handler Calls
17. DB Schema Changes
18. Git Diff
19. Architecture Drift
20. Open Questions

Final:

AgentRun Persistence      = READY
Agent Tool Observation   = READY
AgentStep Persistence    = READY

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

# 三十一、强制 STOP

完成本任务后：

**立即 STOP。**

不要：

```text
实现 Tool Idempotency
实现 Tool Result Store
实现 Checkpoint
实现 Resume
实现 HITL
实现 Cancellation
实现 Read API
修改 AgentRuntime
修改 ToolExecutionService
修改 Tool Handler
```

本阶段的目标只有：

> **把 Tool Replay / Idempotency 的边界彻底冻结，为下一阶段 Checkpoint / Resume 提供可靠前置条件。**
