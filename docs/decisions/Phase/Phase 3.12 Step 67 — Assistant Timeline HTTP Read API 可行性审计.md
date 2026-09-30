# Phase 3.12 Step 67 — Assistant Timeline HTTP Read API 可行性审计

你现在开始执行：

**Phase 3.12 Step 67 — Assistant Timeline HTTP Read API 可行性审计**

---

# 一、阶段目标

基于：

```text
Phase 3.12 Step 65
Unified Timeline Audit

        ↓

Phase 3.12 Step 66
Assistant Timeline Grouped Projection
```

本阶段只回答一个问题：

> **当前 Grouped Timeline Projection 是否已经具备安全、稳定、值得暴露给 HTTP API 的条件？**

本阶段：

**只做 API Contract Audit + 最小测试。**

不要真正新增 HTTP Endpoint。

不要修改现有 Assistant Trace API。

不要实现 Unified Timeline。

---

# 二、当前已存在能力

当前已经存在：

```text
AssistantTimelineEvent
AssistantTimeline
AssistantTimelineQueryService
```

Grouped Projection：

```text
assistant_request_id
    │
    ├── llm_events[]
    ├── tool_events[]
    ├── rag_events[]
    └── outcome_event
```

当前明确：

```text
NO merged events[]
NO event_id
NO sequence
NO span_id
NO parent_event_id
NO cross-group ordering
```

这些限制必须保持。

---

# 三、开始前必须阅读

先阅读真实代码：

```text
backend/app/dto/assistant_timeline.py
backend/app/services/assistant_timeline_query_service.py

backend/app/api/assistant_trace.py
backend/app/services/assistant_trace_query_service.py

backend/app/main.py

backend/app/api/orchestrator_chat.py
```

以及：

```text
tests/test_assistant_timeline_projection.py
tests/test_assistant_trace_multi_path_e2e.py
tests/test_assistant_outcome_persistence.py
```

同时检查：

```text
docs/architecture.md
```

重点确认当前：

```text
API Router registration
Response DTO conventions
Error handling conventions
Path parameter validation
Pydantic DTO style
```

不要根据文件名猜测。

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
Router
Validator
Executor
Project Context
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
DB schema change
new index
new persistence table
```

禁止：

```text
Unified Timeline
event_id
sequence
span_id
parent_event_id
trace_id
OpenTelemetry
Langfuse
Prometheus
Tracing framework
```

禁止：

```text
Pagination implementation
Cursor implementation
Conversation
Memory
Agent
MCP
```

本阶段不解决这些问题。

---

# 五、核心问题：Grouped Timeline 是否适合 HTTP

需要审计：

```text
AssistantTimelineQueryService
        ↓
AssistantTimeline
        ↓
HTTP response
```

判断：

### 1. API Contract 是否稳定

检查：

```text
AssistantTimeline
AssistantTimelineEvent
```

字段是否适合外部 API。

### 2. API 是否泄露内部实现

检查：

```text
source
source_id
event_type
status
created_at
```

这些字段是否应该直接暴露。

特别检查：

```text
source_id
```

是否属于：

```text
内部 DB primary key
```

如果直接暴露存在明显内部实现泄露风险：

**只记录问题，不修改 DTO。**

---

# 六、API Response 设计 Audit

设计候选：

```text
GET /api/observability/assistant-timeline/{assistant_request_id}
```

但是：

**本阶段不实现。**

只设计 Response Contract。

候选：

```json
{
  "assistant_request_id": "...",
  "llm_events": [],
  "tool_events": [],
  "rag_events": [],
  "outcome_event": null
}
```

要求分析：

```text
是否需要 wrapper DTO
是否直接复用 AssistantTimeline
是否需要 API DTO
是否需要隐藏 source_id
```

不要直接拍板。

---

# 七、Unknown Request

明确：

```text
unknown assistant_request_id
```

当前 Service 行为：

```text
empty timeline
```

需要评估 HTTP 层应该：

```text
200 + empty
```

还是：

```text
404
```

本阶段只做：

```text
contract decision
```

不要修改现有 Service。

---

# 八、Validation

审计：

```text
assistant_request_id
```

是否需要：

```text
empty string reject
whitespace reject
max length
invalid characters
```

优先复用项目已有：

```text
AssistantRequestId
```

或者现有 ID validation。

不要创建新的 ID validation framework。

---

# 九、Security Audit

重点检查 HTTP 输出：

绝对不能包含：

```text
prompt
messages
system_prompt
SQL
query
RAG chunk content
embedding
similarity
tool arguments
tool result
API key
authorization
password
database URL
raw provider response
exception message
stack trace
```

允许：

```text
assistant_request_id
source
event_type
source_id
timestamps
duration
status
```

但：

**需要单独讨论 `source_id` 是否应该暴露。**

---

# 十、Ordering Contract

HTTP API 必须明确说明：

```text
llm_events
    created_at ASC, id ASC

tool_events
    id ASC

rag_events
    id ASC

outcome_event
    max 1
```

但是：

**绝对不要在 HTTP 层把四组重新排序。**

不要：

```text
sorted(
    llm + tool + rag + outcome
)
```

不要产生：

```text
sequence
```

不要假装是完整 Timeline。

API 名称虽然可以叫：

```text
assistant-timeline
```

但 response 必须明确是：

```text
grouped projection
```

---

# 十一、Cross-request Isolation

必须审计：

```text
GET timeline/A
```

不能返回：

```text
B
C
D
```

特别关注：

```text
LLMUsageQueryService
ToolExecutionPersistentQueryService
RagExecutionPersistentQueryService
AssistantOutcomeQueryService
```

是否都使用：

```text
assistant_request_id
```

作为边界。

---

# 十二、Historical Data

需要覆盖：

### Case A

完整数据：

```text
LLM
Tool
RAG
Outcome
```

### Case B

没有 Outcome：

```text
outcome_event = null
```

### Case C

没有 RAG：

```text
rag_events = []
```

### Case D

没有 Tool：

```text
tool_events = []
```

### Case E

没有 LLM：

```text
llm_events = []
```

### Case F

全部为空：

```text
empty grouped timeline
```

不能：

```text
HTTP 404
```

除非本阶段审计明确决定其他 Contract。

---

# 十三、API Error Boundary

分析：

```text
Query Service exception
```

HTTP 应该如何映射。

必须遵循当前项目已有错误约定。

不要重新设计全局异常系统。

重点检查：

```text
known error
unknown error
```

不能把：

```text
DB exception message
```

直接暴露给客户端。

---

# 十四、Payload Size Audit

不实现 Pagination。

但需要估算：

```text
10 events
50 events
100 events
```

情况下：

```text
JSON size
```

使用 synthetic DTO。

不要访问真实 DB。

目标只是回答：

> 当前 grouped projection 在什么规模下开始需要 pagination？

如果没有证据：

```text
DEFER
```

不要提前实现。

---

# 十五、权限边界 Audit

本项目当前是否存在：

```text
authentication
authorization
user_id
tenant_id
project_id
```

需要检查现有 API。

如果当前 API 没有 request-level authorization：

**只记录限制。**

不要本阶段新增认证系统。

特别注意：

```text
assistant_request_id
```

如果知道一个 request_id 是否可以读取另一个请求的数据？

这是必须记录的安全问题。

但：

**不要在本阶段实现权限系统。**

---

# 十六、测试

新增：

```text
tests/test_assistant_timeline_api_audit.py
```

注意：

**本阶段不要新增真正 HTTP endpoint。**

测试应该是：

```text
offline
Fake Query Service
Fake DTO
Contract Audit
```

至少覆盖：

1. response shape
2. field whitelist
3. source/event_type pairing
4. timestamp semantics
5. grouped ordering
6. no merged ordering
7. source_id exposure audit
8. unknown request
9. historical missing outcome
10. empty groups
11. cross-request isolation
12. sensitive field isolation
13. request_id validation
14. error mapping contract
15. payload size synthetic audit
16. no DB/network
17. no endpoint registration

建议：

```text
15~20 tests
```

不要为了数量重复。

---

# 十七、Documentation

新增：

```text
docs/evaluation/phase-3.12-step-67-timeline-api-audit.md
```

记录：

## 1. Current Projection

```text
AssistantTimeline
```

## 2. API Candidate

```text
GET /api/observability/assistant-timeline/{assistant_request_id}
```

标记：

```text
NOT IMPLEMENTED
```

## 3. Response Candidate

记录：

```text
assistant_request_id
llm_events
tool_events
rag_events
outcome_event
```

## 4. Security

重点：

```text
source_id
request_id
```

## 5. Ordering

明确：

```text
Grouped ordering only
```

## 6. Error Contract

记录：

```text
unknown request
query failure
invalid request id
```

## 7. Payload

记录 synthetic size audit。

## 8. Authorization

记录当前项目是否具备 request-level authorization。

## 9. Decision

最终只能出现：

```text
READY FOR API IMPLEMENTATION
```

或者：

```text
BLOCKED
```

如果发现 API contract 尚不稳定：

```text
BLOCKED
```

不要强行进入实现。

---

# 十八、测试命令

只运行：

```powershell
python -m pytest -q tests/test_assistant_timeline_api_audit.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

本阶段：

```text
NO full pytest
NO RUN_DB_TESTS
NO real LLM
NO real HTTP endpoint
```

---

# 十九、Git Diff

检查：

```powershell
git status --short
git diff --stat
git diff
```

必须确认：

```text
□ 没有 HTTP endpoint
□ 没有 Router registration
□ 没有 DB migration
□ 没有 production business changes
□ 没有 Trace API 修改
□ 没有 Timeline persistence
□ 没有 Pagination
□ 没有 Unified Timeline
□ 没有 OpenTelemetry
□ 没有 Agent
□ 没有 MCP
```

---

# 二十、最终报告

严格输出：

```text
Phase 3.12 Step 67 完成报告

1. Audit Scope
2. Current Grouped Timeline Contract
3. HTTP Candidate
4. Response Contract
5. Security
6. source_id Decision
7. Ordering
8. Historical Data
9. Error Contract
10. Payload Size
11. Authorization
12. Tests
13. compileall
14. Git Diff
15. Decision

READY / BLOCKED

Phase 3.12 Step 67 STOP
```

---

# 二十一、硬停止

完成后：

**立即 STOP。**

不要：

```text
实现 HTTP endpoint
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

全部等待下一步明确指令。
