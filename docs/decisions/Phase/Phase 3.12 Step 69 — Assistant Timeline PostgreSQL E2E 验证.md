# Phase 3.12 Step 69 — Assistant Timeline PostgreSQL E2E 验证

你现在开始执行：

**Phase 3.12 Step 69 — Assistant Timeline PostgreSQL E2E 验证**

---

# 一、阶段目标

验证 Phase 3.12 Step 68 已实现的：

```text
GET /api/observability/assistant-timeline/{assistant_request_id}
```

在真实 PostgreSQL 环境下能够正确读取：

```text
LLM Usage
Tool Execution
RAG Execution
Assistant Outcome
```

并正确形成：

```text
AssistantTimeline
```

本阶段：

**只做真实 DB E2E 验证。**

不新增 Timeline 架构。

不新增 Timeline 字段。

不实现 Unified Timeline。

---

# 二、当前链路

```text
/api/ai/chat
      ↓
assistant_request_id
      ↓
LLM / Tool / RAG / Outcome persistence
      ↓
PostgreSQL
      ↓
AssistantTimelineQueryService
      ↓
GET /api/observability/assistant-timeline/{id}
      ↓
AssistantTimelineResponse
```

本阶段需要验证完整链路。

---

# 三、开始前必须阅读

先阅读：

```text
backend/app/api/assistant_timeline.py
backend/app/dto/assistant_timeline_api.py
backend/app/services/assistant_timeline_query_service.py

backend/app/services/llm_usage_query_service.py
backend/app/services/tool_execution_persistent_query_service.py
backend/app/services/rag_execution_persistent_query_service.py
backend/app/services/assistant_outcome_query_service.py

backend/app/db/models/llm_usage_record.py
backend/app/db/models/tool_execution_record.py
backend/app/db/models/rag_execution_record.py
backend/app/db/models/assistant_outcome_record.py

tests/test_assistant_timeline_api.py
tests/test_assistant_timeline_projection.py

tests/conftest.py
```

同时检查：

```text
RUN_DB_TESTS
```

当前项目真实 DB fixture / cleanup 方式。

**优先复用已有 DB fixture。**

---

# 四、严格禁止

本阶段禁止修改：

```text
AIOrchestrator
RagService
ToolChatService
TextToSQLService
LLMClient
LLMProvider
AIRouterService
SQLValidator
SQLExecutor
```

禁止修改：

```text
AssistantTimeline
AssistantTimelineEvent
AssistantTimelineQueryService
AssistantTimelineResponse
AssistantTimelineEventResponse
```

除非真实 DB E2E 发现明确 bug。

如果发现 bug：

**先停止并报告。**

不要为了让测试通过而修改生产逻辑。

---

# 五、禁止数据库结构修改

绝对禁止：

```text
migration
new table
new column
new index
ALTER TABLE
DROP TABLE
CREATE TABLE
```

本阶段只使用当前已有：

```text
ai_ops.llm_usage_record
ai_ops.tool_execution_record
ai_ops.rag_execution_record
ai_ops.assistant_outcome_record
```

---

# 六、禁止真实 DeepSeek

本阶段：

```text
Real LLM = OFF
```

不要调用：

```text
DeepSeek
SiliconFlow
OpenAI
```

使用：

```text
Fake / Mock LLM
```

但：

**数据库必须是真实 PostgreSQL。**

即：

```text
Fake LLM
      ↓
真实 persistence
      ↓
真实 PostgreSQL
      ↓
真实 Query Service
      ↓
真实 Timeline API
```

---

# 七、测试数据策略

使用测试专用：

```text
assistant_request_id
```

例如：

```text
step69-rag
step69-tool
step69-t2sql
step69-empty
```

具体 ID 根据现有测试习惯生成。

不要使用：

```text
production request id
```

不要写入生产数据库。

---

# 八、Case A：完整 RAG Timeline

构造一个真实 DB RAG execution record + LLM usage + outcome。

要求最终数据库中：

```text
LLM = 1
RAG = 1
Tool = 0
Outcome = SUCCESS
```

然后：

```text
GET /api/observability/assistant-timeline/{request_id}
```

验证：

```text
HTTP 200

llm_events = 1
rag_events = 1
tool_events = 0
outcome_event != null
```

验证：

```text
source_id
```

必须等于真实 DB primary key。

验证：

```text
created_at
```

仍然只存在于：

```text
LLM / Outcome
```

RAG 必须使用：

```text
started_at
finished_at
duration_ms
```

---

# 九、Case B：Tool Timeline

真实插入：

```text
ToolExecutionRecord
AssistantOutcomeRecord
```

然后：

```text
GET timeline
```

验证：

```text
tool_events = 1
llm_events = 0
rag_events = 0
outcome_event = SUCCESS
```

验证：

```text
tool_events[0].source_id
```

等于真实 Tool record primary key。

---

# 十、Case C：Text-to-SQL Retry

模拟：

```text
attempt 1
    ↓
invalid SQL
    ↓
Validator reject

attempt 2
    ↓
valid SQL
    ↓
Executor success
```

使用 Fake LLM。

真实 DB 最终至少产生：

```text
LLM usage row #1
LLM usage row #2
Assistant outcome = SUCCESS
```

Timeline：

```text
llm_events = 2
tool_events = 0
rag_events = 0
outcome = SUCCESS
```

必须验证：

```text
LLM events
    created_at ASC
    id ASC
```

但是：

**不能断言：**

```text
event #1 = attempt 1
event #2 = attempt 2
```

因为当前 DB 没有 attempt number。

也不能增加 attempt 字段。

---

# 十一、Case D：RAG Failure

模拟：

```text
RAG execution
    ↓
LLM failure
```

根据当前真实 persistence 行为：

如果：

```text
LLM usage = 0
RAG record = 1
Outcome = FAILED
```

则 Timeline 应：

```text
llm_events = []
rag_events = [1]
tool_events = []
outcome = FAILED
```

如果当前真实 failure path 不产生 RAG record：

则必须保持：

```text
rag_events = []
```

**不要为了测试补造记录。**

测试必须遵循当前真实 persistence 语义。

---

# 十二、Case E：Refusal

验证：

```text
refusal
```

最终：

```text
LLM usage = 1
Validator = 0
Executor = 0
Outcome = REFUSED
Tool = 0
RAG = 0
```

Timeline：

```text
llm_events = 1
tool_events = []
rag_events = []
outcome = REFUSED
```

---

# 十三、Case F：Historical Missing Outcome

只创建：

```text
LLM usage
```

或者：

```text
Tool execution
```

不创建：

```text
AssistantOutcomeRecord
```

GET Timeline：

```text
HTTP 200
outcome_event = null
```

禁止从：

```text
HTTP status
LLM usage
Tool success
```

推断 Outcome。

---

# 十四、Case G：Cross-request Isolation

建立：

```text
request A
request B
```

分别插入：

```text
LLM
Tool
RAG
Outcome
```

然后分别：

```text
GET timeline/A
GET timeline/B
```

必须：

```text
Timeline(A)
    only contains A

Timeline(B)
    only contains B
```

检查：

```text
assistant_request_id
source_id
event_type
status
```

均不得串线。

---

# 十五、Case H：Unknown Request

查询：

```text
step69-not-exist
```

必须：

```text
HTTP 200
```

响应：

```json
{
  "assistant_request_id": "step69-not-exist",
  "llm_events": [],
  "tool_events": [],
  "rag_events": [],
  "outcome_event": null
}
```

---

# 十六、真实 API 装配

本阶段必须使用：

```text
FastAPI TestClient
```

真实：

```text
main.py
router
API DTO
Query Service
Repository
PostgreSQL
```

不要直接：

```text
QueryService → assert
```

作为唯一测试。

最终必须：

```text
HTTP
 ↓
Query Service
 ↓
PostgreSQL
```

---

# 十七、数据库 Cleanup

这是本阶段最重要的安全要求之一。

测试前记录测试相关：

```text
request_id
```

测试后必须精确 cleanup。

优先复用：

```text
tests/conftest.py
```

当前已有的 cleanup / watermark 机制。

最终检查：

```text
step69-*
```

相关记录：

```text
LLM = 0
Tool = 0
RAG = 0
Outcome = 0
```

不能留下测试残留。

---

# 十八、数据库 Residue 检查

至少检查：

```text
ai_ops.llm_usage_record
ai_ops.tool_execution_record
ai_ops.rag_execution_record
ai_ops.assistant_outcome_record
```

最终：

```text
Step 69 residue = 0
```

不要修改其他历史测试数据。

---

# 十九、Security

真实 PostgreSQL → API → JSON 全链路检查：

响应不得出现：

```text
prompt
messages
system_prompt
user_prompt
SQL
query
chunk content
embedding
similarity
tool arguments
tool result
api_key
authorization
password
database_url
raw response
exception message
stack trace
```

同时检查：

```text
postgresql://
sk-
Bearer
```

等 sentinel。

---

# 二十、Source ID

每个事件：

```text
source_id
```

必须来自：

```text
真实 DB primary key
```

禁止：

```text
uuid4()
hash()
enumerate()
index
```

不要生成新的事件身份。

---

# 二十一、Ordering

真实 DB 测试至少创建两个同类事件。

例如：

```text
LLM id=100
LLM id=101
```

确认：

```text
llm_events
    created_at ASC
    id ASC
```

Tool/RAG：

```text
id ASC
```

禁止测试：

```text
LLM + Tool + RAG
```

被合并成：

```text
events[]
```

禁止：

```text
global sequence
```

---

# 二十二、API Contract Regression

确认 Step 68：

```text
GET /api/observability/assistant-timeline/{id}
```

OpenAPI 仍然：

```text
response = AssistantTimelineResponse
```

并且：

```text
5 top-level fields
9 event fields
```

同时确认：

```text
GET /api/observability/assistant-trace/{id}
```

仍然保持：

```text
5 fields
```

不能因为本阶段测试而改变。

---

# 二十三、测试文件

新增：

```text
tests/test_assistant_timeline_db_e2e.py
```

建议至少：

```text
10~15 DB tests
```

覆盖：

```text
A RAG
B Tool
C T2SQL retry
D RAG failure
E refusal
F missing outcome
G cross-request
H unknown request
source_id
ordering
security
API contract
cleanup
```

如果现有测试结构适合合并，也可以合理组织。

不要为了数量重复。

---

# 二十四、运行方式

先：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_assistant_timeline_db_e2e.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

本阶段**不要求**：

```text
full pytest
real DeepSeek
production DB
```

如果 DB E2E 失败：

**先定位问题。**

不要修改生产代码让测试通过。

---

# 二十五、失败处理

如果出现：

```text
Timeline API failure
Query Service failure
Persistence failure
DB cleanup failure
```

按照：

```text
API
 ↓
Query Service
 ↓
Read Boundary
 ↓
Repository
 ↓
PostgreSQL
```

逐层定位。

如果发现真实 bug：

报告：

```text
问题：
影响：
根因：
是否修改：
```

只有明确属于 Step 69 必要修复的问题才允许最小修改。

如果是测试 fixture / cleanup 问题：

优先修改：

```text
tests/
```

不要修改生产业务逻辑。

---

# 二十六、Git Diff

完成：

```powershell
git status --short
git diff --stat
git diff
```

必须确认：

```text
□ 无 AI Core 修改
□ 无 RAG 修改
□ 无 Tool 修改
□ 无 T2SQL 修改
□ 无 Validator 修改
□ 无 Executor 修改
□ 无 Timeline DTO 修改
□ 无 Timeline Query Service 修改
□ 无 DB schema 修改
□ 无 migration
□ 无新的 index
□ 无 persistence contract 修改
□ 无 Unified Timeline
□ 无 sequence
□ 无 event_id
□ 无 Pagination
□ 无 Agent
□ 无 MCP
```

允许：

```text
tests/
docs/
```

以及：

```text
必要的测试 fixture cleanup
```

---

# 二十七、最终报告

严格输出：

```text
Phase 3.12 Step 69 完成报告

1. 测试范围
2. PostgreSQL 环境
3. Case A — RAG
4. Case B — Tool
5. Case C — T2SQL Retry
6. Case D — RAG Failure
7. Case E — Refusal
8. Case F — Missing Outcome
9. Case G — Cross-request
10. Case H — Unknown Request
11. Source ID
12. Ordering
13. Security
14. API Contract
15. Tests
16. compileall
17. DB residue
18. Git Diff
19. 发现的问题
20. 当前限制

Phase 3.12 Step 69 READY
Phase 3.12 Step 69 STOP
```

---

# 二十八、硬停止

完成后：

**立即 STOP。**

不要进入：

```text
Unified Timeline
event_id
sequence
span_id
Pagination
Conversation
Memory
Agent
MCP
OpenTelemetry
```

等待下一步明确指令。
