# Phase 3.12 Step 72 — Trace / Timeline 写入中读取一致性 Audit

## 一、阶段目标

在 Step 70 / Step 71 已完成：

```text
并发 request isolation
        +
Trace ↔ Timeline outcome consistency
```

之后，本阶段只验证：

> 当一个 Assistant Request 的 LLM / Tool / RAG / Outcome 数据正在陆续写入 PostgreSQL 时，同时读取 Trace / Timeline，是否出现不合理的跨 request 数据污染、重复、错误身份或违反当前 Contract 的状态。

本阶段仍然：

**只做 Audit / E2E Validation。**

不修改生产逻辑。

不实现 Unified Timeline。

---

# 二、重要边界

当前系统没有：

```text
event_id
sequence
transaction_id
global ordering
unified started_at
```

因此本阶段：

**不要尝试证明“读取到的是完整最终快照”。**

只验证：

```text
1. 不跨 request
2. 不出现不存在的 source_id
3. 不出现重复 source_id
4. Trace / Timeline 在同一读取时刻都遵守当前已有 Contract
5. 最终稳定状态仍与 Step 71 一致
```

不要新增新的“强一致性”语义。

---

# 三、开始前阅读

先阅读：

```text
backend/app/api/assistant_trace.py
backend/app/api/assistant_timeline.py

backend/app/services/assistant_trace_query_service.py
backend/app/services/assistant_timeline_query_service.py

backend/app/services/llm_usage_persistence_service.py
backend/app/services/tool_execution_persistence_service.py
backend/app/services/rag_execution_persistence_service.py
backend/app/services/assistant_outcome_persistence_service.py

backend/app/db/models/
```

以及：

```text
tests/test_assistant_timeline_db_e2e.py
tests/test_assistant_timeline_concurrency_db_e2e.py
tests/test_assistant_trace_timeline_outcome_consistency.py
```

---

# 四、严格范围

允许新增：

```text
tests/test_assistant_trace_timeline_read_during_write.py

docs/evaluation/
    phase-3.12-step-72-read-during-write-audit.md
```

允许：

```text
docs/architecture.md
```

增加简短 Audit 结论。

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

Trace DTO
Timeline DTO
Trace QueryService
Timeline QueryService

LLM Persistence
Tool Persistence
RAG Persistence
Outcome Persistence

DB schema
migration
index
API contract
```

如果发现真实生产 bug：

**停止并报告，不修。**

---

# 五、测试模型

本阶段不要做压力测试。

使用：

```text
1 request
```

和：

```text
3 concurrent requests
```

即可。

核心思想：

```text
Request A
    ↓
逐步产生：
LLM
RAG / Tool
Outcome

与此同时：
GET Trace(A)
GET Timeline(A)
```

---

# 六、Case A：LLM 写入后、Outcome 写入前

构造一个可控的测试流程。

让：

```text
LLM usage
```

已经持久化，但：

```text
assistant_outcome_record
```

尚未持久化。

此时读取：

```text
Trace
Timeline
```

允许：

```text
outcome = null
outcome_event = null
```

但是：

```text
llm_usage
```

与：

```text
llm_events
```

必须保持：

```text
数量一致
source_id 一致
request_id 一致
```

不能因为 Outcome 尚未写入而：

```text
推断 SUCCESS
推断 FAILED
推断 REFUSED
```

---

# 七、Case B：RAG 写入后、Outcome 写入前

构造：

```text
LLM usage
+
RAG execution record
```

已经存在。

Outcome 尚不存在。

读取：

```text
Trace
Timeline
```

允许：

```text
outcome = null
outcome_event = null
```

但：

```text
Trace.llm_usage
==
Timeline.llm_events
```

以及：

```text
Trace.rag_executions
==
Timeline.rag_events
```

仍然必须一致。

---

# 八、Case C：Tool 写入后、Outcome 写入前

构造：

```text
Tool execution record
```

已经存在。

Outcome 尚不存在。

读取：

```text
Trace
Timeline
```

要求：

```text
Trace.tool_executions count
==
Timeline.tool_events count
```

并且：

```text
Trace.tool_executions.success
==
Timeline.tool_events.status
```

不要推断：

```text
outcome = SUCCESS
```

因为 Outcome 尚未写入。

---

# 九、Case D：Outcome 写入后

Outcome 已经存在：

```text
SUCCESS
```

再读取：

```text
Trace
Timeline
```

要求：

```text
Trace.outcome == SUCCESS
Timeline.outcome_event.status == SUCCESS
```

这是最终稳定状态。

---

# 十、Case E：Outcome FAILED

构造：

```text
FAILED
```

读取：

```text
Trace
Timeline
```

要求：

```text
Trace.outcome == FAILED
Timeline.outcome_event.status == FAILED
```

不能因为某些前置 event 缺失而改变 Outcome。

---

# 十一、Case F：Outcome REFUSED

构造：

```text
REFUSED
```

要求：

```text
Trace.outcome == REFUSED
Timeline.outcome_event.status == REFUSED
```

不能：

```text
推断 SUCCESS
```

也不能：

```text
推断 EMPTY
```

---

# 十二、Case G：Outcome EMPTY

构造：

```text
EMPTY
```

验证：

```text
Trace.outcome == EMPTY
Timeline.outcome_event.status == EMPTY
```

同时允许：

```text
llm_usage = []
```

如果当前真实 EMPTY 语义如此。

不要修改现有行为。

---

# 十三、Case H：Cross-request Read During Write

同时：

```text
A = RAG
B = Tool
C = T2SQL
```

让三者处于不同的持久化阶段。

同时执行：

```text
GET Trace(A)
GET Timeline(A)

GET Trace(B)
GET Timeline(B)

GET Trace(C)
GET Timeline(C)
```

验证：

```text
A response 不出现 B/C request_id
B response 不出现 A/C request_id
C response 不出现 A/B request_id
```

同时：

```text
source_id
```

不得属于其它 request。

---

# 十四、No Fake Completeness

非常重要。

本阶段明确禁止测试：

```text
Trace / Timeline 一定在任何时刻完整
```

也禁止要求：

```text
HTTP 200
→ 一定有 outcome
```

因为当前系统允许：

```text
outcome = null
```

例如：

```text
historical request
write in progress
```

测试应该验证：

> **partial state 是合法的，只要它没有违反已有字段关系和 request isolation。**

---

# 十五、Read Consistency

对于同一个稳定阶段：

```text
LLM only
LLM + RAG
LLM + Tool
Outcome
```

连续读取：

```text
Trace 1
Trace 2

Timeline 1
Timeline 2
```

如果中间没有任何新的写入：

要求：

```text
Trace1 == Trace2
Timeline1 == Timeline2
```

如果中间发生了新写入：

**不要要求 equality。**

只要求 Contract validity。

---

# 十六、Source ID Validation

所有返回：

```text
source_id
```

必须：

```text
isinstance(source_id, int)
```

并且属于：

```text
当前 request 对应 DB PK
```

禁止：

```text
UUID
hash
array index
fake sequence
```

---

# 十七、Security

对所有 read-during-write response 执行 JSON security scan。

禁止：

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
raw_response
api_key
authorization
password
database_url
postgresql://
sk-
Bearer
traceback
exception
```

失败响应：

```text
{"detail": "..."}
```

继续保持当前 error contract。

---

# 十八、不要测试事务内部状态

禁止：

```text
直接读取 SQLAlchemy Session 内部 transaction
```

禁止：

```text
修改 transaction isolation level
```

禁止：

```text
SELECT FOR UPDATE
```

禁止：

```text
手动改变数据库 isolation
```

本阶段测试真实应用层读写行为即可。

---

# 十九、测试实现建议

如果需要控制写入时机，可以使用：

```text
threading.Event
asyncio.Event
```

或现有 Fake / test seam。

例如：

```text
LLM persist
    ↓
pause
    ↓
读取 Trace / Timeline
    ↓
resume
    ↓
Outcome persist
```

注意：

不要修改 production code。

如果现有代码没有安全的 test seam：

**不要为了测试强行改生产代码。**

可以直接构造真实 persistence records，按照阶段逐步写入。

---

# 二十、测试文件

新增：

```text
tests/test_assistant_trace_timeline_read_during_write.py
```

建议：

```text
10～15 个 DB-gated tests
```

目标：

```text
真正 PostgreSQL
真正 Trace API
真正 Timeline API
真正 Query Service
真正 Repository
```

---

# 二十一、Offline

默认：

```powershell
python -m pytest -q tests/test_assistant_trace_timeline_read_during_write.py
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

# 二十二、DB

执行：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_assistant_trace_timeline_read_during_write.py
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

本阶段只执行：

```text
Step 72 tests
compileall
```

不要：

```text
python -m pytest -q
```

不要：

```text
DeepSeek
```

不要：

```text
production DB
```

---

# 二十五、DB Cleanup

只清理：

```text
step72-
```

或测试精确创建的 request_id。

禁止：

```text
TRUNCATE
DELETE all
DROP
```

最终：

```text
llm_usage_record = 0
tool_execution_record = 0
rag_execution_record = 0
assistant_outcome_record = 0
```

---

# 二十六、Git Diff

必须确认：

```text
backend/ = 0 production code changes

Trace DTO = 0
Timeline DTO = 0
Trace QueryService = 0
Timeline QueryService = 0

Persistence = 0
DB schema = 0
migration = 0
index = 0
API contract = 0
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
Phase 3.12 Step 72 完成报告

1. 测试范围
2. LLM-only Partial State
3. RAG Partial State
4. Tool Partial State
5. Outcome SUCCESS
6. Outcome FAILED
7. Outcome REFUSED
8. Outcome EMPTY
9. Cross-request Read During Write
10. No Fake Completeness
11. Read Consistency
12. Source ID Validation
13. Security
14. Tests
15. Compileall
16. DB Residue
17. Git Diff
18. 发现的问题
19. 当前限制

Phase 3.12 Step 72 READY
Phase 3.12 Step 72 STOP
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

本阶段只回答：

> **当前 Trace / Grouped Timeline 在“写入进行中”读取时，是否保持合法的 partial state、request isolation 和已有 Contract。**
