# Phase 3.12 Step 68 — Assistant Timeline HTTP Read API 实现

你现在开始执行：

**Phase 3.12 Step 68 — Assistant Timeline HTTP Read API**

---

# 一、阶段目标

基于 Phase 3.12 Step 66：

```text
AssistantTimeline
AssistantTimelineEvent
AssistantTimelineQueryService
```

以及 Step 67 的 API Audit：

```text
READY FOR API IMPLEMENTATION
```

本阶段只完成：

> 将现有 Assistant Timeline Grouped Projection 暴露为一个只读 HTTP API。

最终新增：

```text
GET /api/observability/assistant-timeline/{assistant_request_id}
```

---

# 二、严格边界

本阶段只做：

```text
Timeline Query Service
        ↓
API DTO
        ↓
FastAPI Router
        ↓
HTTP Response
```

禁止借此阶段扩展其他能力。

---

# 三、禁止修改

绝对禁止修改：

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
ProjectContext
Business Semantic
```

禁止修改：

```text
llm_usage_record
tool_execution_record
rag_execution_record
assistant_outcome_record
```

禁止：

```text
DB migration
DB schema
new index
Timeline persistence
```

禁止：

```text
Unified Timeline
merged events[]
event_id
sequence
span_id
parent_event_id
trace_id
OpenTelemetry
Langfuse
Prometheus
```

禁止：

```text
Pagination
Cursor
Conversation
Memory
Agent
MCP
```

---

# 四、开始前必须阅读

先阅读：

```text
backend/app/dto/assistant_timeline.py
backend/app/services/assistant_timeline_query_service.py

backend/app/api/assistant_trace.py
backend/app/services/assistant_trace_query_service.py
backend/app/api/orchestrator_chat.py
backend/app/main.py
```

以及：

```text
tests/test_assistant_timeline_projection.py
tests/test_assistant_timeline_api_audit.py
```

同时阅读：

```text
docs/architecture.md
```

必须复用当前 API 的：

```text
Router registration
Pydantic DTO
error mapping
path parameter validation
```

不要重新创建 API 基础设施。

---

# 五、API Endpoint

新增：

```text
GET /api/observability/assistant-timeline/{assistant_request_id}
```

Router：

```text
observability
```

必须按照当前项目既有：

```python
app.include_router(...)
```

方式注册。

不要修改现有：

```text
/api/observability/assistant-trace/{assistant_request_id}
```

---

# 六、API DTO

不要直接把内部：

```text
AssistantTimeline
AssistantTimelineEvent
```

作为 FastAPI response model。

新增专用 API DTO。

推荐：

```text
backend/app/dto/assistant_timeline_api.py
```

具体文件位置根据当前项目 DTO 结构决定。

建议：

```text
AssistantTimelineEventResponse
AssistantTimelineResponse
```

---

# 七、API Event 字段

API Event 明确只允许：

```text
assistant_request_id
source
event_type
source_id
started_at
finished_at
duration_ms
created_at
status
```

即：

```text
9 fields
```

不得增加：

```text
event_id
sequence
span_id
parent_event_id
trace_id
order
index
attempt
```

---

# 八、source_id 决策

按照 Step 67 建议：

**本阶段暴露 `source_id`。**

但 API 文档 / DTO docstring 必须明确：

```text
source_id:
内部事件标识。

仅用于同一次 assistant_request_id 内区分事件。

不作为跨请求、跨系统引用标识。
```

不能把它描述为：

```text
global event id
```

不能暗示：

```text
timeline sequence
```

---

# 九、Response Contract

最终：

```json
{
  "assistant_request_id": "req-123",
  "llm_events": [
    {
      "assistant_request_id": "req-123",
      "source": "llm_usage",
      "event_type": "LLM",
      "source_id": 1,
      "started_at": null,
      "finished_at": null,
      "duration_ms": null,
      "created_at": "2026-09-30T10:00:00+00:00",
      "status": null
    }
  ],
  "tool_events": [],
  "rag_events": [],
  "outcome_event": null
}
```

必须保持：

```text
llm_events[]
tool_events[]
rag_events[]
outcome_event
```

四段结构。

绝对不要增加：

```text
events[]
```

---

# 十、显式字段映射

API 层：

```text
AssistantTimeline
       ↓
AssistantTimelineResponse
```

必须：

**显式逐字段映射。**

不要：

```python
model_dump()
```

直接把内部 DTO 暴露出去。

不要：

```python
dataclasses.asdict()
```

作为 API response。

推荐：

```text
to_api_event(...)
to_api_timeline(...)
```

或者当前项目已有的等价 mapper 风格。

---

# 十一、Query Service

API 必须调用：

```text
AssistantTimelineQueryService
```

不能：

```text
API → Repository
```

不能：

```text
API → SQLAlchemy
```

不能：

```text
API → get_engine()
```

不能：

```text
API → Session
```

保持：

```text
API
 ↓
Query Service
 ↓
existing read boundaries
```

---

# 十二、Unknown Request

严格按照 Step 67：

```text
GET unknown assistant_request_id
```

返回：

```text
HTTP 200
```

body：

```json
{
  "assistant_request_id": "unknown",
  "llm_events": [],
  "tool_events": [],
  "rag_events": [],
  "outcome_event": null
}
```

不是：

```text
404
```

---

# 十三、Request ID Validation

复用现有：

```text
1 <= assistant_request_id length <= 128
```

规则。

至少：

```text
empty
whitespace
129 chars
non-string internal call
```

按照当前项目既有 API 错误约定处理。

预期：

```text
400
```

或当前项目已经确定的对应状态码。

不要创建新的 validation framework。

---

# 十四、Error Mapping

严格沿用 Step 67：

```text
ValueError
    ↓
400

Path validation failure
    ↓
422

Persistent query boundary failure
    ↓
502

Unexpected exception
    ↓
500
```

错误响应不得泄露：

```text
DB exception message
SQL
database URL
filesystem path
stack trace
credentials
```

使用当前项目既有固定 detail 文案。

---

# 十五、Historical Data

必须保持：

### 无 Outcome

```text
outcome_event = null
```

不要推断：

```text
HTTP 200 → SUCCESS
```

---

### 无 RAG

```text
rag_events = []
```

---

### 无 Tool

```text
tool_events = []
```

---

### 无 LLM

```text
llm_events = []
```

---

### 全空

返回合法：

```text
200
```

空 Timeline。

---

# 十六、Ordering

API 不得重新排序。

必须直接使用：

```text
AssistantTimeline
```

已经确定的组内顺序：

```text
llm_events:
    created_at ASC, id ASC

tool_events:
    id ASC

rag_events:
    id ASC

outcome_event:
    max 1
```

禁止：

```python
sorted(llm + tool + rag)
```

禁止：

```text
sequence
```

禁止：

```text
event_id
```

禁止声称：

```text
complete timeline
```

API 文档名称虽然叫：

```text
assistant-timeline
```

但内部/文档描述必须明确：

```text
Grouped Timeline Projection
```

---

# 十七、Security Boundary

API response 必须确保绝对不存在：

```text
prompt
messages
system_prompt
user_prompt
SQL
query
RAG chunk content
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

可以存在：

```text
assistant_request_id
source
event_type
source_id
timestamps
duration_ms
status
```

---

# 十八、Authorization

本阶段：

**不新增认证 / 授权系统。**

保持当前项目行为。

但是 API 文档必须明确：

```text
当前接口没有 request-level authorization。

知道 assistant_request_id 的调用方可以读取对应 Timeline。
```

不要隐藏这个事实。

不要实现：

```text
JWT
OAuth
API Key
tenant isolation
user ownership
```

---

# 十九、测试

新增：

```text
tests/test_assistant_timeline_api.py
```

建议使用：

```text
FastAPI TestClient
Fake Query Service
```

但不要调用真实 DB。

至少测试：

### 1

正常完整 Timeline：

```text
LLM
Tool
RAG
Outcome
```

---

### 2

LLM-only。

---

### 3

Tool-only。

---

### 4

RAG-only。

---

### 5

Outcome-only。

---

### 6

Historical missing Outcome。

---

### 7

Unknown request。

---

### 8

Cross-request isolation。

---

### 9

source/event_type mapping。

---

### 10

source_id exposed。

---

### 11

9-field whitelist。

---

### 12

Sensitive field isolation。

---

### 13

Invalid request ID。

---

### 14

Path length validation。

---

### 15

Query Service known error → 502。

---

### 16

Unexpected error → 500。

---

### 17

Error response 不泄露内部异常。

---

### 18

API 不进行 DB / network。

---

### 19

现有 Assistant Trace API Contract 未变化。

---

### 20

main.py Router registration 正确。

目标：

```text
20~25 tests
```

不要为了数量重复。

---

# 二十、Regression

先运行：

```powershell
python -m pytest -q tests/test_assistant_timeline_api.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

本阶段不要主动运行：

```text
full pytest
RUN_DB_TESTS
real DeepSeek
```

如果目标测试发现明显影响现有 API 的问题，再针对相关测试文件定向回归。

---

# 二十一、Git Diff

完成：

```powershell
git status --short
git diff --stat
git diff
```

确认：

```text
□ 只有 Timeline API DTO / Router / tests / docs
□ 没有 AI Core 修改
□ 没有 RAG 修改
□ 没有 Tool 修改
□ 没有 T2SQL 修改
□ 没有 Validator 修改
□ 没有 Executor 修改
□ 没有 DB migration
□ 没有 DB schema change
□ 没有 persistence change
□ 没有 Pagination
□ 没有 Unified Timeline
□ 没有 OpenTelemetry
□ 没有 Agent
□ 没有 MCP
```

---

# 二十二、Documentation

更新：

```text
docs/architecture.md
```

增加：

```text
§8.73 Assistant Timeline HTTP Read API
```

明确：

```text
GET /api/observability/assistant-timeline/{assistant_request_id}
```

并说明：

```text
这是 Grouped Timeline Projection。

不是 Unified Timeline。

没有：
event_id
sequence
span_id
parent_event_id
global ordering
pagination
```

同时记录：

```text
source_id
```

是：

```text
内部事件标识
仅用于同一 assistant_request_id 内区分事件
```

---

# 二十三、不要修改现有 Assistant Trace API

这是非常重要的边界。

不要修改：

```text
GET /api/observability/assistant-trace/{assistant_request_id}
```

不能添加：

```text
timeline
```

不能改变：

```text
outcome
llm_usage
tool_executions
rag_executions
```

现有 Trace API 保持完全兼容。

---

# 二十四、最终报告

严格输出：

```text
Phase 3.12 Step 68 完成报告

1. 新增文件
2. 修改文件
3. Endpoint
4. API DTO
5. Response Contract
6. source_id
7. Unknown Request
8. Error Mapping
9. Ordering
10. Security
11. Authorization
12. Tests
13. compileall
14. Git Diff
15. Existing Trace API Regression
16. 当前限制

Phase 3.12 Step 68 READY
Phase 3.12 Step 68 STOP
```

---

# 二十五、硬停止

完成后：

**立即 STOP。**

不要进入：

```text
Unified Timeline
event_id
sequence
span_id
parent span
Pagination
Conversation
Memory
Agent
MCP
OpenTelemetry
```

等待下一步明确指令。
