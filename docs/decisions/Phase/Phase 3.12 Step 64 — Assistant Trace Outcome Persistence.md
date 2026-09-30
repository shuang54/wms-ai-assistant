# Phase 3.12 Step 64 — Assistant Trace Outcome Persistence

## 一、阶段目标

基于：

```text
Phase 3.12 Step 62
→ Assistant Outcome Contract

Phase 3.12 Step 63
→ Runtime metadata.outcome
```

本阶段只完成：

> **将 Assistant-level Outcome 持久化，并接入 Assistant Trace Read Model。**

最终目标：

```text
/api/ai/chat
      ↓
AIOrchestrator
      ↓
metadata.outcome
      ↓
Assistant Outcome Persistence
      ↓
Assistant Trace
      ↓
GET /api/observability/assistant-trace/{assistant_request_id}
```

Trace 最终能够回答：

```text
这次 AI 请求最终是什么结果？
```

例如：

```json
{
  "assistant_request_id": "...",
  "outcome": "SUCCESS",
  "llm_usage": [],
  "tool_executions": [],
  "rag_executions": []
}
```

---

# 二、严格范围

## 允许

允许修改：

```text
backend/app/services/assistant_trace_service.py
backend/app/api/assistant_trace.py
backend/app/db/
backend/app/services/
backend/app/dto/
tests/
docs/architecture.md
```

具体文件必须以现有真实结构为准。

允许新增：

```text
Assistant Outcome persistence model
Assistant Outcome repository
Assistant Outcome persistence service
Assistant Outcome trace query integration
```

但坚持最小实现。

---

# 三、明确禁止

本阶段禁止：

```text
Pagination
Cursor
Offset
Unified Timeline
Event Store
Conversation
Memory
Agent
MCP
OpenTelemetry
Prometheus
Grafana
Langfuse
Dashboard
Cost Tracking
Billing
```

禁止修改：

```text
RAG core
Tool Framework core
TextToSQL core
Validator
Executor
LLM Provider
LLM Client
Router strategy
Project Context
Prompt
```

禁止修改：

```text
LLM Usage persistence schema
Tool Execution persistence schema
RAG Execution persistence schema
```

不要修改 Step 63 的：

```text
metadata.outcome
```

契约。

---

# 四、开始前必须阅读

先阅读：

```text
backend/app/services/assistant_trace_service.py
backend/app/api/assistant_trace.py

backend/app/services/ai_orchestrator_service.py
backend/app/dto/assistant_outcome.py

backend/app/db/
backend/app/db/models/

tests/test_assistant_trace_service.py
tests/test_assistant_trace_api.py
tests/test_assistant_outcome.py
tests/test_assistant_trace_multi_path_e2e.py
```

如果实际文件名称不同，以项目真实结构为准。

同时搜索：

```text
assistant_request_id
Assistant Trace
LLMUsage
ToolExecution
RagExecution
metadata.outcome
AssistantOutcome
```

---

# 五、先审计现有 Persistence 能力

不要直接新增表。

先确认当前是否已经存在：

```text
Assistant request-level record
Execution record
Trace root record
```

如果已经存在可以承载：

```text
assistant_request_id
outcome
```

优先复用。

只有确认没有合适的 request-level persistence boundary，才新增表。

---

# 六、推荐的最小 Persistence Model

如果现有架构确实没有合适的 request-level record：

新增：

```text
ai_ops.assistant_outcome_record
```

最小字段：

```text
id
assistant_request_id
outcome
created_at
```

其中：

```text
assistant_request_id
```

必须唯一。

建议：

```text
UNIQUE(assistant_request_id)
```

不要加入：

```text
question
prompt
content
answer
SQL
RAG chunks
Tool arguments
Tool results
exception message
stacktrace
API key
Authorization
DATABASE_URL
```

本表只是：

> Assistant request-level terminal outcome。

---

# 七、为什么不能复用 LLM Usage

不要把 Outcome 写进：

```text
llm_usage_record
```

因为：

```text
LLM request
≠
Assistant request
```

一个 Assistant request 可能产生：

```text
LLM call 1
LLM call 2
LLM call 3
```

但只有：

```text
Assistant Outcome = 1
```

所以必须保持：

```text
Assistant-level
```

与：

```text
LLM-level
```

分离。

---

# 八、为什么不能写进 Tool Execution

同样禁止：

```text
ToolExecutionRecord.outcome
```

因为：

```text
Tool success
≠
Assistant success
```

Step 62 已经确认：

```text
LLM success
Tool success
RAG success
Executor success
```

都不是最终 Assistant Outcome。

---

# 九、为什么不能写进 RAG Execution

同理：

```text
RAG execution
```

只表示：

```text
RAG execution metadata
```

不能承担：

```text
Assistant outcome
```

---

# 十、Persistence Contract

建立最小接口：

```python
persist_outcome(
    assistant_request_id: str,
    outcome: AssistantOutcome,
) -> None
```

或者按照当前项目已有 Persistence Service 风格命名。

要求：

```text
request-level
idempotent
no prompt
no content
no SQL
no exception message
```

---

# 十一、Idempotency

同一个：

```text
assistant_request_id
```

不能产生多个 Outcome。

例如：

```text
persist(A, SUCCESS)
persist(A, SUCCESS)
```

数据库最终：

```text
1 row
```

不要：

```text
2 rows
```

---

# 十二、Conflict Policy

测试：

```text
persist(A, SUCCESS)
persist(A, FAILED)
```

必须明确策略。

推荐：

> **first-write-wins**

即：

```text
A → SUCCESS
```

后续：

```text
FAILED
```

不覆盖。

原因：

Assistant Outcome 是 terminal state。

如果出现：

```text
SUCCESS → FAILED
```

通常意味着：

```text
重复 request
```

或者：

```text
生命周期 bug
```

本阶段不要引入状态机。

---

# 十三、Persistence Failure Isolation

这是本阶段非常重要的要求。

如果：

```text
Outcome DB INSERT
```

失败：

**不能让业务请求失败。**

例如：

```text
AIOrchestrator
→ SUCCESS
→ persist outcome
→ DB error
```

最终：

```text
HTTP 200
metadata.outcome=SUCCESS
```

而不是：

```text
HTTP 500
```

原则：

> Observability / trace persistence failure must never become Assistant business failure.

---

# 十四、写入时机

优先：

```text
AIOrchestrator
    ↓
determine outcome
    ↓
construct AIOrchestrationResult
    ↓
persist outcome
    ↓
return result
```

但如果当前代码结构更适合：

```text
API boundary
```

也可以考虑。

不过：

> 不要让 API 层重新推断 Outcome。

Step 63 已经确定：

```text
AIOrchestratorService
```

是唯一 Outcome 判定点。

因此：

```text
Orchestrator determines
API does not determine
Persistence only stores
```

---

# 十五、异常路径

当前 4xx / 5xx：

```text
detail
```

保持不变。

本阶段可以持久化：

```text
FAILED
```

但是：

**不要修改错误响应结构。**

例如：

```text
T2SQL retry exhausted
→ persist FAILED
→ HTTP 500
→ detail unchanged
```

同样：

```text
RAG failure
→ persist FAILED
→ HTTP 500
```

以及：

```text
capability disabled
→ persist FAILED
→ HTTP 403
```

---

# 十六、Tool Failure

特殊场景：

```text
HTTP 200
tool_success=false
outcome=FAILED
```

必须持久化：

```text
FAILED
```

最终 Trace：

```text
outcome=FAILED
tool_executions[0].success=false
```

HTTP 仍然：

```text
200
```

---

# 十七、RAG Empty

特殊场景：

```text
route=rag
rag_used_chunks=0
outcome=EMPTY
```

持久化：

```text
EMPTY
```

最终 Trace：

```text
outcome=EMPTY
rag_executions[0].result_count=0
```

如果当前空检索路径已有 RAG execution record，则直接复用。

不要新增第二条 RAG 记录。

---

# 十八、Refusal

当前：

```text
route=text_to_sql
refused=true
outcome=REFUSED
```

持久化：

```text
REFUSED
```

最终：

```text
outcome=REFUSED
```

保持：

```text
LLM usage = 1
Validator = 0
Executor = 0
```

---

# 十九、T2SQL Retry

例如：

```text
attempt 1 → invalid
attempt 2 → valid
```

最终：

```text
Assistant Outcome = SUCCESS
```

只持久化：

```text
SUCCESS
```

不要：

```text
RETRY
```

不要记录：

```text
attempt_count
```

到 Outcome 表。

LLM Usage 已经负责记录每次真实 LLM request。

---

# 二十、Trace Read Model

扩展：

```text
AssistantTraceView
```

从：

```text
assistant_request_id
llm_usage[]
tool_executions[]
rag_executions[]
```

变为：

```text
assistant_request_id
outcome
llm_usage[]
tool_executions[]
rag_executions[]
```

其中：

```text
outcome: AssistantOutcome | None
```

---

# 二十一、旧 Trace 数据兼容

非常重要：

历史 Assistant Trace 可能没有 Outcome。

因此：

```text
outcome = None
```

是合法情况。

不要：

```text
根据历史 execution records 猜 SUCCESS
```

不要：

```text
根据 LLM usage 猜 SUCCESS
```

不要：

```text
根据 HTTP status 猜 SUCCESS
```

历史没有明确 Outcome：

```text
None
```

---

# 二十二、Trace API

保持：

```text
GET /api/observability/assistant-trace/{assistant_request_id}
```

HTTP contract 顶层增加：

```json
{
  "assistant_request_id": "...",
  "outcome": "SUCCESS",
  "llm_usage": [],
  "tool_executions": [],
  "rag_executions": []
}
```

或者：

```text
outcome = null
```

对于历史无记录。

不要新增：

```text
limit
offset
cursor
page
page_size
total
has_more
```

Step 59 已明确：

```text
Pagination = DEFER
```

---

# 二十三、不要新增 error_class

本阶段：

```text
AssistantOutcome
```

只有：

```text
SUCCESS
EMPTY
REFUSED
FAILED
```

禁止：

```text
LLM_ERROR
RAG_ERROR
TOOL_ERROR
TEXT_TO_SQL_ERROR
SQL_EXECUTION_ERROR
```

---

# 二十四、Security Boundary

Outcome persistence 只允许：

```text
assistant_request_id
outcome
created_at
```

Trace API 只允许：

```text
assistant_request_id
outcome
```

以及已经存在：

```text
llm_usage[]
tool_executions[]
rag_executions[]
```

禁止新加入：

```text
prompt
messages
answer
SQL
RAG chunks
Tool args
Tool result data
exception
stacktrace
headers
credentials
```

---

# 二十五、测试要求

新增：

```text
tests/test_assistant_outcome_persistence.py
```

至少覆盖：

### 1. SUCCESS persistence

```text
A → SUCCESS
```

### 2. EMPTY persistence

```text
A → EMPTY
```

### 3. REFUSED persistence

```text
A → REFUSED
```

### 4. FAILED persistence

```text
A → FAILED
```

### 5. Idempotency

```text
same request_id
same outcome
→ one row
```

### 6. Conflict

```text
SUCCESS
then FAILED
→ SUCCESS remains
```

### 7. Persistence failure

模拟：

```text
repository insert fails
```

确认：

```text
business result unchanged
```

### 8. Historical Trace

无 Outcome row：

```text
trace.outcome is None
```

### 9. Trace Integration

有 Outcome：

```text
trace.outcome == stored outcome
```

### 10. Security

Outcome row / Trace payload 不出现：

```text
prompt
SQL
credentials
exception message
```

---

# 二十六、真实 API E2E

至少覆盖：

```text
POST /api/ai/chat
```

然后：

```text
GET /api/observability/assistant-trace/{assistant_request_id}
```

验证：

### RAG success

```text
chat.outcome = SUCCESS
trace.outcome = SUCCESS
```

### RAG empty

```text
chat.outcome = EMPTY
trace.outcome = EMPTY
```

### Tool failure

```text
chat.outcome = FAILED
trace.outcome = FAILED
HTTP = 200
```

### Refusal

```text
chat.outcome = REFUSED
trace.outcome = REFUSED
HTTP = 200
```

### T2SQL success

```text
chat.outcome = SUCCESS
trace.outcome = SUCCESS
```

---

# 二十七、Failure API E2E

验证：

```text
T2SQL retry exhausted
```

得到：

```text
HTTP 500
detail unchanged
```

同时：

```text
trace.outcome = FAILED
```

同样验证：

```text
RAG runtime failure
```

以及：

```text
capability disabled
```

不要修改 HTTP error body。

---

# 二十八、Cross-request Isolation

至少：

```text
A → SUCCESS
B → FAILED
C → REFUSED
D → EMPTY
```

验证：

```text
Trace(A).outcome = SUCCESS
Trace(B).outcome = FAILED
Trace(C).outcome = REFUSED
Trace(D).outcome = EMPTY
```

不能串线。

---

# 二十九、Concurrency

至少：

```text
5 concurrent requests
```

每个：

```text
different assistant_request_id
```

验证：

```text
outcome(A) != incorrectly copied outcome(B)
```

特别覆盖：

```text
SUCCESS
FAILED
EMPTY
REFUSED
```

不要引入新的 global mutable state。

---

# 三十、DB Schema

如果需要新增：

```text
ai_ops.assistant_outcome_record
```

只允许：

```text
id
assistant_request_id
outcome
created_at
```

索引：

```text
UNIQUE assistant_request_id
```

不要新增：

```text
route
status
error_class
error_message
content
```

因为：

```text
route
```

已经属于 Orchestrator result；

```text
error_class
```

尚未冻结。

---

# 三十一、Migration / init_db

按照当前项目已有 DB migration / init_db 模式。

不要建立第二套 migration framework。

如果当前项目只有：

```text
init_db.py
```

就按照当前方式。

不要引入 Alembic。

---

# 三十二、No DB Write in Offline Tests

默认：

```text
pytest -q
```

必须：

```text
no DB
```

使用：

```text
Fake Repository
```

或现有 persistence test fixture。

DB-gated 才测试真实 PostgreSQL。

---

# 三十三、Architecture Documentation

修改：

```text
docs/architecture.md
```

新增：

```text
§8.69 Assistant Outcome Persistence
```

简洁描述：

```text
Assistant Request
      ↓
AIOrchestrator
      ↓
AssistantOutcome
      ↓
Outcome Persistence
      ↓
Assistant Trace
```

明确：

```text
Outcome is Assistant-level
LLM / Tool / RAG execution status remain independent
```

以及：

```text
Persistence failure does not affect business result
```

---

# 三十四、禁止过度设计

本阶段不要新增：

```text
State Machine
Event Sourcing
Event Store
Timeline
Workflow Run
Execution Graph
Conversation
Memory
Agent
MCP
```

不要把：

```text
AssistantOutcome
```

设计成复杂生命周期。

它就是：

```text
terminal request-level fact
```

---

# 三十五、测试命令

先：

```powershell
python -m pytest -q tests/test_assistant_outcome_persistence.py
```

然后：

```powershell
python -m pytest -q tests/test_assistant_outcome.py
```

然后：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

编译：

```powershell
python -m compileall -q backend tests scripts
```

---

# 三十六、Git Diff

完成后检查：

```powershell
git status --short
git diff --stat
git diff
```

必须确认：

```text
□ Outcome Contract 未改变
□ metadata.outcome 未改变
□ HTTP status 未改变
□ Tool 200 failure 保持
□ Refusal 200 保持
□ RAG empty 200 保持
□ Error body 未改变
□ 无 error_class
□ 无 Pagination
□ 无 Timeline
□ 无 Conversation
□ 无 Memory
□ 无 Agent
□ 无 MCP
□ 无 Prompt 修改
□ 无 RAG core 修改
□ 无 Tool core 修改
□ 无 T2SQL core 修改
□ 无 Validator 修改
□ 无 Executor 修改
□ 无 LLM Provider 修改
```

---

# 三十七、最终验收

必须满足：

```text
□ Assistant Outcome 有独立 request-level persistence
□ SUCCESS / EMPTY / REFUSED / FAILED 全部可持久化
□ assistant_request_id 唯一
□ Idempotent
□ first-write-wins
□ Persistence failure isolated
□ 历史 Trace 无 Outcome 时返回 None
□ 新 Trace 能返回 Outcome
□ Chat 与 Trace Outcome 一致
□ RAG success
□ RAG empty
□ Tool success
□ Tool failure
□ Refusal
□ T2SQL success
□ T2SQL retry success
□ T2SQL failure
□ SQL execution failure
□ Capability disabled
□ Cross-request isolation
□ Concurrent isolation
□ Security whitelist
□ 无 prompt / SQL / credentials 泄露
□ HTTP contract backward compatible
□ DB schema 最小
□ pytest 通过
□ DB regression 通过
□ compileall 通过
```

---

# 三十八、最终报告

严格按照：

```text
Phase 3.12 Step 64 完成报告

1. 修改文件
2. Assistant Outcome Persistence
3. DB Schema
4. Persistence Contract
5. Idempotency
6. Conflict Policy
7. Persistence Failure Isolation
8. Trace Integration
9. Historical Trace Compatibility
10. RAG
11. Tool
12. Refusal
13. T2SQL
14. Failure API
15. Security
16. Concurrency
17. Tests
18. DB Tests
19. compileall
20. Git Diff
21. Production Code Changes
22. DB Schema Changes
23. API Contract Changes
24. Current Limitations

Phase 3.12 Step 64 READY
Phase 3.12 Step 64 STOP
```

# 三十九、硬停止

完成后立即停止。

不要进入：

```text
Step 65 Unified Timeline
Step 66 Pagination
Step 67 Conversation
Step 68 Memory
Agent
MCP
OpenTelemetry
Dashboard
Cost Tracking
```

本阶段只完成：

```text
Runtime Outcome
        ↓
Persistent Outcome
        ↓
Assistant Trace
```

等待下一步指令。
