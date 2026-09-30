# Phase 3.12 Step 71 — Assistant Trace / Timeline Outcome 一致性 Audit

## 一、阶段目标

在 Step 70 已证明：

```text
Grouped Timeline
    ↓
5 / 10 concurrent requests
    ↓
request-level isolation
    ↓
PostgreSQL consistency
```

之后，本阶段只做一个问题：

> **确认 Assistant Trace 与 Assistant Timeline 对同一个 assistant_request_id 是否表达一致的业务终态。**

本阶段仍然是：

```text
Audit / Contract Validation
```

不是功能扩展。

---

# 二、核心问题

当前存在两个 Read API：

```text
GET /api/observability/assistant-trace/{assistant_request_id}

GET /api/observability/assistant-timeline/{assistant_request_id}
```

Trace：

```text
assistant_request_id
outcome
llm_usage[]
tool_executions[]
rag_executions[]
```

Timeline：

```text
assistant_request_id
llm_events[]
tool_events[]
rag_events[]
outcome_event
```

本阶段验证：

```text
Trace.outcome
        ↕
Timeline.outcome_event.status
```

对于同一个 request：

**二者必须语义一致。**

---

# 三、开始前必须阅读

阅读真实实现：

```text
backend/app/api/assistant_trace.py
backend/app/api/assistant_timeline.py

backend/app/services/assistant_trace_query_service.py
backend/app/services/assistant_timeline_query_service.py
backend/app/services/assistant_outcome_query_service.py

backend/app/dto/assistant_trace.py
backend/app/dto/assistant_timeline_api.py
backend/app/dto/assistant_outcome.py

backend/app/db/models/assistant_outcome_record.py

tests/test_assistant_timeline_db_e2e.py
tests/test_assistant_timeline_concurrency_db_e2e.py
tests/test_assistant_trace_multi_path_e2e.py
```

不要假设字段。

---

# 四、严格范围

允许新增：

```text
tests/test_assistant_trace_timeline_outcome_consistency.py

docs/evaluation/
    phase-3.12-step-71-trace-timeline-outcome-audit.md
```

允许修改：

```text
docs/architecture.md
```

禁止修改：

```text
AIOrchestrator
AIRouter
RagService
ToolChatService
TextToSQLService
LLMClient
Validator
Executor

AssistantOutcome
AssistantTrace DTO
AssistantTimeline DTO
AssistantTraceQueryService
AssistantTimelineQueryService

AssistantOutcome persistence
LLM persistence
Tool persistence
RAG persistence
```

禁止：

```text
DB schema
migration
index
API contract
Unified Timeline
event_id
sequence
span_id
pagination
```

如果发现真实生产 bug：

**停止并报告，不要顺手修复。**

---

# 五、测试矩阵

至少验证以下状态：

| 场景                         | Trace outcome | Timeline outcome |
| -------------------------- | ------------- | ---------------- |
| RAG success                | SUCCESS       | SUCCESS          |
| RAG empty                  | EMPTY         | EMPTY            |
| Tool success               | SUCCESS       | SUCCESS          |
| Tool failure               | FAILED        | FAILED           |
| T2SQL success              | SUCCESS       | SUCCESS          |
| T2SQL retry success        | SUCCESS       | SUCCESS          |
| T2SQL execution failure    | FAILED        | FAILED           |
| Refusal                    | REFUSED       | REFUSED          |
| RAG upstream failure       | FAILED        | FAILED           |
| Historical missing outcome | null          | null             |

重点：

```text
Trace = Timeline
```

而不是：

```text
HTTP status
```

---

# 六、RAG Success

创建真实 DB request：

```text
POST /api/ai/chat
```

RAG 正常完成。

读取：

```text
GET /api/observability/assistant-trace/{id}

GET /api/observability/assistant-timeline/{id}
```

验证：

```text
trace.outcome == "SUCCESS"

timeline.outcome_event.status == "SUCCESS"
```

并且：

```text
timeline.outcome_event.source_id
```

必须等于：

```text
assistant_outcome_record.id
```

---

# 七、RAG Empty

构造当前已有的 empty retrieval 行为。

验证：

```text
HTTP = 200

Trace:
outcome = EMPTY

Timeline:
outcome_event.status = EMPTY
```

同时：

```text
llm_usage = []
rag_executions = [...]
tool_executions = []
```

不要根据：

```text
HTTP 200
```

自行推导 EMPTY。

必须以已有 outcome persistence 为准。

---

# 八、Tool Success

执行：

```text
/api/ai/chat
```

Tool 路径。

验证：

```text
Trace.outcome = SUCCESS
Timeline.outcome_event.status = SUCCESS
```

同时：

```text
Trace.tool_executions
```

与：

```text
Timeline.tool_events
```

数量一致。

---

# 九、Tool Failure

构造现有 Tool handler failure。

当前语义：

```text
HTTP = 200
tool_success = false
outcome = FAILED
```

验证：

```text
Trace.outcome == FAILED

Timeline.outcome_event.status == FAILED
```

不要因为 HTTP 200 而判定 SUCCESS。

---

# 十、T2SQL Retry Success

Fake LLM：

```text
attempt 1 → invalid SQL
attempt 2 → valid SQL
```

验证：

```text
Trace.outcome = SUCCESS
Timeline.outcome_event.status = SUCCESS
```

同时：

```text
Trace.llm_usage count = 2
Timeline.llm_events count = 2
```

注意：

**不要新增 attempt 字段。**

---

# 十一、T2SQL Execution Failure

生成合法 SQL，但 Fake Executor 返回 failure。

验证：

```text
HTTP = 500

Trace.outcome = FAILED
Timeline.outcome_event.status = FAILED
```

如果当前 error response 不包含 outcome：

保持现状。

不要修改 HTTP error contract。

---

# 十二、Refusal

执行 destructive request。

验证：

```text
Trace.outcome = REFUSED
Timeline.outcome_event.status = REFUSED
```

同时：

```text
llm_usage = 1
timeline.llm_events = 1

validator = 0
executor = 0

tool = 0
rag = 0
```

---

# 十三、RAG Upstream Failure

构造 RAG upstream LLM failure。

验证：

```text
HTTP = 500

Trace.outcome = FAILED
Timeline.outcome_event.status = FAILED
```

同时保留当前真实 RAG observation persistence semantics。

不要因为本阶段修改：

```text
rag_execution_record
```

---

# 十四、Historical Missing Outcome

创建一个具有历史 Trace 数据但删除：

```text
assistant_outcome_record
```

模拟：

```text
historical request
```

读取两个 API。

要求：

```text
Trace.outcome == null

Timeline.outcome_event == null
```

不能：

```text
Trace → SUCCESS
Timeline → SUCCESS
```

也不能从：

```text
HTTP status
LLM events
Tool events
RAG events
```

推断 outcome。

---

# 十五、Outcome Source ID

对于存在 outcome 的 request：

```text
Trace.outcome
```

没有 source_id。

而：

```text
Timeline.outcome_event.source_id
```

必须：

```text
int
```

并且：

```text
source_id == assistant_outcome_record.id
```

不要让 Trace DTO 暴露 source_id。

---

# 十六、Cross-request Consistency

同时产生：

```text
A = RAG
B = Tool
C = Refusal
D = T2SQL
```

分别读取：

```text
Trace(A)
Timeline(A)

Trace(B)
Timeline(B)

Trace(C)
Timeline(C)

Trace(D)
Timeline(D)
```

验证：

```text
A Trace outcome == A Timeline outcome
B Trace outcome == B Timeline outcome
C Trace outcome == C Timeline outcome
D Trace outcome == D Timeline outcome
```

并且：

```text
A != B != C != D
```

不存在跨 request 污染。

---

# 十七、Count Consistency

对于同一个 request：

```text
Trace.llm_usage count
==
Timeline.llm_events count
```

```text
Trace.tool_executions count
==
Timeline.tool_events count
```

```text
Trace.rag_executions count
==
Timeline.rag_events count
```

Outcome：

```text
Trace.outcome != null
    ↔
Timeline.outcome_event != null
```

---

# 十八、Source ID Consistency

检查：

### LLM

```text
Trace.llm_usage[].id
==
Timeline.llm_events[].source_id
```

### Tool

```text
Trace.tool_executions[].id
==
Timeline.tool_events[].source_id
```

### RAG

```text
Trace.rag_executions[].id
==
Timeline.rag_events[].source_id
```

### Outcome

```text
Timeline.outcome_event.source_id
==
assistant_outcome_record.id
```

这是本阶段的重要验证。

---

# 十九、Ordering Consistency

比较：

```text
Trace.llm_usage
```

与：

```text
Timeline.llm_events
```

必须保持：

```text
same source_id order
```

Tool：

```text
Trace.tool_executions
==
Timeline.tool_events
```

RAG：

```text
Trace.rag_executions
==
Timeline.rag_events
```

不要要求跨组全局排序。

---

# 二十、Security

同时扫描：

```text
Trace JSON
Timeline JSON
```

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

并确认：

```text
Trace
```

和：

```text
Timeline
```

都不会暴露内部异常 message。

---

# 二十一、API Contract Lock

必须确认：

### Assistant Trace

仍然只有：

```text
assistant_request_id
outcome
llm_usage
tool_executions
rag_executions
```

### Assistant Timeline

仍然只有：

```text
assistant_request_id
llm_events
tool_events
rag_events
outcome_event
```

本阶段不得增加：

```text
timeline
events
sequence
event_id
trace_id
span_id
```

---

# 二十二、Tests

新增：

```text
tests/test_assistant_trace_timeline_outcome_consistency.py
```

建议：

```text
10～15 个 DB-gated tests
```

默认：

```powershell
python -m pytest -q tests/test_assistant_trace_timeline_outcome_consistency.py
```

应该：

```text
全部 SKIP
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_assistant_trace_timeline_outcome_consistency.py
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

# 二十四、DB Residue

必须严格清理：

```text
llm_usage_record
tool_execution_record
rag_execution_record
assistant_outcome_record
```

只删除：

```text
step71-
```

或当前测试创建的精确 request IDs。

禁止：

```text
TRUNCATE
DELETE all
DROP
```

最终：

```text
llm = 0
tool = 0
rag = 0
outcome = 0
```

---

# 二十五、不要运行 Full Regression

本阶段只运行：

```text
Step 71 tests
compileall
```

不要运行：

```text
python -m pytest -q
```

不要调用：

```text
DeepSeek
```

不要连接生产 DB。

---

# 二十六、Git Diff

确认：

```text
backend/ = 0 production code changes

DB schema = 0
migration = 0
index = 0
persistence = 0

Trace API = 0
Timeline API = 0
Timeline DTO = 0
Timeline QueryService = 0
Outcome = 0
```

允许：

```text
tests/
docs/
```

---

# 二十七、完成报告

严格：

```text
Phase 3.12 Step 71 完成报告

1. 测试范围
2. RAG SUCCESS
3. RAG EMPTY
4. Tool SUCCESS
5. Tool FAILED
6. T2SQL SUCCESS
7. T2SQL FAILED
8. REFUSED
9. RAG FAILED
10. Historical Missing Outcome
11. Cross-request Consistency
12. Count Consistency
13. Source ID Consistency
14. Ordering Consistency
15. Security
16. API Contract
17. Tests
18. Compileall
19. DB Residue
20. Git Diff
21. 发现的问题
22. 当前限制

Phase 3.12 Step 71 READY
Phase 3.12 Step 71 STOP
```

---

# 二十八、强制停止

完成后立即 STOP。

不要进入：

```text
Unified Timeline
event_id
sequence
span_id
pagination
Conversation
Memory
Agent
MCP
OpenTelemetry
```

本阶段只证明：

> **Assistant Trace 与 Grouped Assistant Timeline 对同一个 request 的业务终态和事件集合是否保持一致。**
