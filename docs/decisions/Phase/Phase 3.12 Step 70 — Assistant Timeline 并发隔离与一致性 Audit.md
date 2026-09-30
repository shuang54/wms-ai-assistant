# Phase 3.12 Step 70 — Assistant Timeline 并发隔离与一致性 Audit

## 一、阶段目标

在 Phase 3.12 Step 69 已完成真实 PostgreSQL E2E 的基础上，本阶段只验证：

> 多个 Assistant Request 并发产生 LLM / Tool / RAG / Outcome 数据时，Timeline 是否保持 request-level 隔离、一致性和完整性。

本阶段：

**只做 Audit / E2E Validation。**

不新增 Timeline 功能。

不实现 Unified Timeline。

不实现：

```text
event_id
sequence
span_id
parent_event_id
cross-group ordering
pagination
Conversation
Memory
Agent
MCP
OpenTelemetry
```

---

# 二、开始前必须阅读

先阅读真实实现：

```text
backend/app/api/assistant_timeline.py
backend/app/api/orchestrator_chat.py

backend/app/services/assistant_timeline_query_service.py
backend/app/services/assistant_trace_query_service.py

backend/app/services/llm_usage_persistence_service.py
backend/app/services/tool_execution_persistence_service.py
backend/app/services/rag_execution_persistence_service.py
backend/app/services/assistant_outcome_persistence_service.py

backend/app/db/models/llm_usage_record.py
backend/app/db/models/tool_execution_record.py
backend/app/db/models/rag_execution_record.py
backend/app/db/models/assistant_outcome_record.py

tests/test_assistant_timeline_db_e2e.py
tests/test_assistant_timeline_api.py
tests/test_assistant_timeline_projection.py
```

同时确认当前：

```text
assistant_request_id
request_id
provider request_id
source_id
```

四者的真实关系。

不要修改这些 Contract。

---

# 三、严格范围

## 允许

新增：

```text
tests/test_assistant_timeline_concurrency_db_e2e.py

docs/evaluation/
    phase-3.12-step-70-timeline-concurrency-audit.md
```

必要时只允许对：

```text
tests/
```

增加测试辅助代码。

---

## 禁止

禁止修改：

```text
AIOrchestrator
AIRouter
RagService
ToolChatService
TextToSQLService
LLMClient
LLM Provider
Validator
Executor

AssistantTimeline DTO
AssistantTimelineQueryService
AssistantTimeline API

LLM Usage Persistence
Tool Persistence
RAG Persistence
Assistant Outcome Persistence
```

禁止修改：

```text
DB schema
migration
index
persistence contract
API contract
```

如果测试发现真实生产 bug：

**停止修改，记录：**

```text
发现问题
影响
复现方式
根因候选
```

不要为了通过测试修改生产代码。

---

# 四、并发测试技术要求

不要使用：

```text
for request in requests:
    client.post(...)
```

这种串行方式模拟并发。

优先使用：

```text
httpx.AsyncClient
ASGITransport
asyncio.gather
```

或者项目现有等价并发测试基础设施。

FastAPI 官方文档说明，异步测试可以通过 HTTPX `AsyncClient` + `ASGITransport` 调用 ASGI 应用；异步测试函数使用 `pytest.mark.anyio`。

不要新增第三方依赖。

如果项目当前已经有 `httpx`，直接复用。

---

# 五、测试规模

不要做压力测试。

只验证：

```text
5 concurrent requests
```

以及：

```text
10 concurrent requests
```

即可。

本阶段不是：

```text
load test
stress test
benchmark
```

---

# 六、Case A：5 个 RAG 并发请求

构造：

```text
A1
A2
A3
A4
A5
```

每个 request 使用唯一：

```text
assistant_request_id
```

使用 Fake LLM / MockTransport。

要求：

```text
5 HTTP requests
    ↓
5 independent assistant_request_id
```

每个请求最终：

```text
route = rag
outcome = SUCCESS
llm_events = 1
rag_events = 1
tool_events = 0
```

验证：

```text
Timeline(A1) 只包含 A1
Timeline(A2) 只包含 A2
...
Timeline(A5) 只包含 A5
```

---

# 七、Case B：RAG + Tool 混合并发

同时启动：

```text
R1 = RAG
T1 = Tool
R2 = RAG
T2 = Tool
R3 = RAG
```

并发执行：

```text
5 requests
```

最终：

### RAG 请求

```text
llm_events = 1
rag_events = 1
tool_events = 0
outcome = SUCCESS
```

### Tool 请求

```text
llm_events = 0
rag_events = 0
tool_events = 1
outcome = SUCCESS
```

验证绝对不存在：

```text
R1 timeline contains T1 tool event
T1 timeline contains R1 rag event
```

---

# 八、Case C：并发 Text-to-SQL Retry

同时运行：

```text
T2S-A
T2S-B
T2S-C
```

每个：

```text
attempt 1 → invalid SQL
Validator → reject

attempt 2 → valid SQL
Executor → success
```

因此每个请求：

```text
llm_events = 2
outcome = SUCCESS
tool_events = 0
rag_events = 0
```

注意：

**不要断言 Timeline 中存在 attempt=1/2。**

当前 Timeline Contract 没有 attempt 字段。

只验证：

```text
2 LLM usage rows
```

以及：

```text
assistant_request_id
```

全部正确。

---

# 九、Case D：并发 Refusal

同时执行：

```text
REFUSE-A
REFUSE-B
REFUSE-C
```

每个：

```text
route = text_to_sql
outcome = REFUSED
llm_events = 1
tool_events = 0
rag_events = 0
```

同时验证：

```text
Validator = 0
Executor = 0
```

并确认：

```text
REFUSE-A
```

不会读取：

```text
REFUSE-B
```

的 LLM usage。

---

# 十、Cross-request Isolation

这是本阶段最重要的测试。

建立：

```text
A
B
C
D
E
```

五个 assistant_request_id。

查询：

```text
GET /api/observability/assistant-timeline/A
GET /api/observability/assistant-timeline/B
...
```

每一个 response：

```text
所有 event.assistant_request_id == 当前 ID
```

并且：

```text
source_id
```

必须属于当前 request 对应数据库记录。

禁止：

```text
A source_id
出现于 B Timeline
```

---

# 十一、DB-level Isolation

不仅检查 HTTP JSON。

直接读取数据库：

```text
llm_usage_record
tool_execution_record
rag_execution_record
assistant_outcome_record
```

按：

```text
assistant_request_id / request_id
```

检查。

要求：

```text
A DB rows ∩ B DB rows = ∅
```

对：

```text
LLM
Tool
RAG
Outcome
```

分别检查。

---

# 十二、Count Consistency

对于每个 request：

## RAG

```text
DB LLM count == timeline.llm_events count
DB RAG count == timeline.rag_events count
DB Tool count == timeline.tool_events count
DB Outcome count == 1
```

## Tool

```text
DB LLM count == 0
DB RAG count == 0
DB Tool count == 1
DB Outcome count == 1
```

## T2SQL Retry

```text
DB LLM count == 2
DB Tool count == 0
DB RAG count == 0
DB Outcome count == 1
```

## Refusal

```text
DB LLM count == 1
DB Tool count == 0
DB RAG count == 0
DB Outcome count == 1
```

---

# 十三、Timeline Read Consistency

并发请求完成后：

第一次读取：

```text
timeline_1
```

立即再次读取：

```text
timeline_2
```

要求：

```text
timeline_1 == timeline_2
```

至少比较：

```text
assistant_request_id
llm_events
tool_events
rag_events
outcome_event
```

不要比较：

```text
JSON serialization whitespace
```

---

# 十四、No Cross-request Mutation

测试：

```text
timeline_a = GET(A)
timeline_b = GET(B)
```

确认：

```text
timeline_a
```

不会因为：

```text
GET(B)
```

而发生变化。

可以：

```python
snapshot_a = deepcopy(timeline_a)
GET(B)
assert timeline_a == snapshot_a
```

如果 API 每次重新构造 DTO，则直接比较 JSON。

---

# 十五、Source ID Isolation

对于每个 Timeline：

```text
llm_events[].source_id
tool_events[].source_id
rag_events[].source_id
outcome_event.source_id
```

必须：

```text
isinstance(source_id, int)
```

并且：

```text
source_id ∈ 当前 request 对应 DB PK
```

禁止：

```text
UUID
hash
array index
sequence
人工生成 ID
```

---

# 十六、Ordering

继续保持 Step 66 Contract：

### LLM

```text
created_at ASC
id ASC
```

### Tool

```text
id ASC
```

### RAG

```text
id ASC
```

### Outcome

```text
最多 1 条
```

并发测试中：

**不要要求不同 request 之间存在全局顺序。**

例如禁止断言：

```text
A event < B event < C event
```

因为当前 Contract 没有全局 sequence。

---

# 十七、Security Regression

对并发产生的全部 Timeline JSON 做扫描。

禁止出现：

```text
prompt
messages
system_prompt
user_prompt
sql
query
content
embedding
similarity
arguments
tool_result
api_key
authorization
password
database_url
postgresql://
sk-
Bearer
raw_response
traceback
exception
```

并加入：

```text
每个 request 的唯一 sentinel
```

确保：

```text
sentinel_A
```

不会出现在：

```text
Timeline(B)
```

---

# 十八、Failure Isolation

至少构造：

```text
成功请求 A
失败请求 B
成功请求 C
```

例如：

```text
A = RAG success
B = RAG upstream failure
C = Tool success
```

要求：

```text
A timeline unaffected
B timeline = FAILED semantics
C timeline unaffected
```

并且：

```text
B failure
```

不能污染：

```text
A/C
```

---

# 十九、Cleanup

测试必须严格跟踪：

```text
assistant_request_id
```

所有测试数据只删除：

```text
step70-
```

前缀或本测试创建的精确 request IDs。

禁止：

```text
TRUNCATE
DELETE all
DROP
```

测试结束必须：

```text
llm_usage_record = 0 residue
tool_execution_record = 0 residue
rag_execution_record = 0 residue
assistant_outcome_record = 0 residue
```

不得触碰历史数据。

---

# 二十、测试文件

新增：

```text
tests/test_assistant_timeline_concurrency_db_e2e.py
```

建议：

```text
10～15 个 DB-gated tests
```

重点不是数量，而是：

```text
真正并发
真实 PostgreSQL
真实 API
真实 Timeline QueryService
真实 Repository
```

---

# 二十一、Offline 行为

默认：

```powershell
python -m pytest -q tests/test_assistant_timeline_concurrency_db_e2e.py
```

应该：

```text
全部 SKIP
```

并且：

```text
DB writes = 0
network = 0
```

---

# 二十二、DB 测试命令

执行：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_assistant_timeline_concurrency_db_e2e.py
```

要求：

```text
0 failed
```

---

# 二十三、Compile

执行：

```powershell
python -m compileall -q backend tests scripts
```

要求：

```text
0 errors
```

---

# 二十四、不要运行 Full Regression

本阶段：

**不要运行：**

```text
python -m pytest -q
```

只运行：

```text
Step 70 concurrency DB tests
compileall
```

原因：

本阶段只是并发 E2E Audit。

---

# 二十五、禁止修改生产代码

Git Diff 必须确认：

```text
AIOrchestrator = 0
AIRouter = 0
RagService = 0
ToolChatService = 0
TextToSQLService = 0
LLMClient = 0
Validator = 0
Executor = 0

Timeline DTO = 0
Timeline QueryService = 0
Timeline API = 0

DB schema = 0
migration = 0
index = 0
persistence contract = 0
```

允许：

```text
tests/
docs/
```

---

# 二十六、完成报告

严格按照：

```text
Phase 3.12 Step 70 完成报告

1. 测试范围
2. 并发模型
3. RAG 并发
4. Tool 并发
5. T2SQL Retry 并发
6. Refusal 并发
7. Cross-request Isolation
8. DB-level Isolation
9. Count Consistency
10. Read Consistency
11. Source ID Isolation
12. Ordering
13. Security
14. Failure Isolation
15. Tests
16. Compileall
17. DB Residue
18. Git Diff
19. 发现的问题
20. 当前限制

Phase 3.12 Step 70 READY
Phase 3.12 Step 70 STOP
```

---

# 二十七、强制停止

完成后立即停止。

不要：

```text
Unified Timeline
event_id
sequence
span_id
parent_event_id
pagination
Conversation
Memory
Agent
MCP
OpenTelemetry
```

本阶段只证明：

> **当前 Grouped Assistant Timeline 在并发请求下是否保持 request-level isolation 和数据一致性。**

如果发现生产 bug：

**停止并报告，不要顺手修复。**
