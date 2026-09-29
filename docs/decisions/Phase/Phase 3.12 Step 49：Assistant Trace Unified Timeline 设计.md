# Phase 3.12 Step 49：Assistant Trace Unified Timeline 设计

## 一、阶段目标

在 Phase 3.12 Step 48 已完成：

```text
Assistant Trace
├── LLM Usage
├── Tool Execution
└── RAG Execution
```

当前三个数据源已经能够通过同一个：

```text
assistant_request_id
```

关联。

但是当前 Trace API 仍然是：

```text
llm_usage[]
tool_executions[]
rag_executions[]
```

三个独立列表。

Step 49 只解决一个问题：

> **设计是否需要、以及应该如何提供 Assistant Request 的统一时间线。**

本阶段：

**只做 Design / Contract Audit。**

不要实现 Unified Events。

---

# 二、先阅读

先阅读真实实现：

```text
backend/app/services/assistant_trace_query_service.py
backend/app/api/assistant_trace.py

backend/app/services/llm_usage_query_service.py
backend/app/services/tool_execution_persistent_query_service.py
backend/app/services/rag_execution_persistent_query_service.py

backend/app/db/llm_usage_repository.py
backend/app/db/tool_execution_repository.py
backend/app/db/rag_execution_repository.py

tests/test_assistant_trace_api.py
tests/test_assistant_trace_query_service.py
tests/test_assistant_trace_rag_integration.py
tests/test_assistant_trace_rag_integration_e2e_db.py

docs/architecture.md
docs/evaluation/
```

确认当前真实字段和排序方式。

不要根据历史文档猜测。

---

# 三、当前事实

确认并记录：

### LLM

当前排序：

```text
created_at
+
stable id
```

### Tool

当前排序：

```text
id ASC
```

### RAG

当前排序：

```text
id ASC
```

三个来源都通过：

```text
assistant_request_id
```

关联。

---

# 四、设计目标

设计一个未来可选的：

```text
AssistantTraceEvent
```

概念。

例如：

```text
AssistantTraceEvent
    ├── event_type
    ├── occurred_at
    ├── duration_ms
    ├── source_id
    └── payload
```

但：

**不要直接采用这个字段设计。**

必须根据当前真实数据模型分析。

---

# 五、必须回答的问题

## 1. event_type

至少分析：

```text
llm
tool
rag
```

是否足够。

不要提前加入：

```text
router
embedding
reranker
validator
executor
database
```

除非当前已有持久化数据能够可靠支持。

---

## 2. occurred_at

分别分析：

### LLM

应该使用：

```text
created_at
```

还是其他已有时间字段。

### Tool

当前是否存在：

```text
started_at
finished_at
```

### RAG

当前：

```text
started_at
finished_at
```

明确选择一个统一的：

```text
event start time
```

不能凭空创建时间。

---

## 3. 同一时间

处理：

```text
A.started_at == B.started_at
```

必须定义稳定 tie-breaker。

例如：

```text
occurred_at
→ source priority
→ source id
```

但具体优先级必须先基于当前数据结构分析。

---

## 4. duration

分析是否统一暴露：

```text
duration_ms
```

以及：

```text
None
```

如何处理。

不要重新计算已经存在的 duration。

---

# 六、关键问题：不要破坏现有 API

当前：

```http
GET /api/observability/assistant-trace/{assistant_request_id}
```

已经存在：

```json
{
  "assistant_request_id": "...",
  "llm_usage": [],
  "tool_executions": [],
  "rag_executions": []
}
```

Step 49：

**不要修改现有字段语义。**

如果认为需要增加：

```text
events
```

只进行设计：

```json
{
  "assistant_request_id": "...",
  "llm_usage": [],
  "tool_executions": [],
  "rag_executions": [],
  "events": []
}
```

但本阶段：

**不要真正增加 `events` 字段。**

---

# 七、Security Boundary

统一事件设计必须继续遵守现有安全边界。

允许考虑：

```text
event_type
occurred_at
duration_ms
request_id
provider
model
finish_reason
usage
tool name
tool execution status
RAG result_count
RAG used_chunks_count
chunk_ids
document_ids
```

禁止进入 Event：

```text
query
question
answer
content
prompt
messages
raw_response
SQL
database_url
password
api_key
authorization
embedding
similarity
ORM
Session
traceback
credentials
```

尤其注意：

> Unified Event 不能因为“方便调试”而重新暴露当前已经禁止的数据。

---

# 八、不要建立 Event Framework

本阶段明确禁止：

```text
EventBus
EventEmitter
Observer Framework
OpenTelemetry
Kafka
Redis
Celery
WebSocket
Streaming
```

不要新增：

```text
ai_ops.assistant_trace_event
```

不要新增数据库表。

---

# 九、不要改变三个已有 Read Boundary

Step 49 只研究组合层：

```text
LLMUsageQueryService
        ↓
ToolExecutionPersistentQueryService
        ↓
RagExecutionPersistentQueryService
        ↓
AssistantTraceQueryService
```

禁止修改：

```text
LLM Usage Repository
Tool Execution Repository
RAG Execution Repository
RAG Persistence
Tool Persistence
LLM Usage Persistence
```

---

# 十、重点分析：是否真的需要 Unified Timeline

必须给出事实分析：

### 优点

例如：

```text
一个数组即可观察整个 Assistant Request
```

### 缺点

例如：

```text
三个数据源时间字段不同
并发调用顺序不一定等于开始时间
LLM / Tool / RAG 生命周期不同
```

尤其分析：

```text
并行 Tool / RAG
```

情况下：

```text
created_at
started_at
finished_at
```

是否足以恢复真实因果顺序。

不要假设：

```text
时间排序 = 因果排序
```

---

# 十一、并发场景

至少设计以下例子：

### Case A

```text
LLM
 ↓
RAG
 ↓
LLM
```

### Case B

```text
LLM
 ↓
Tool
 ↓
LLM
```

### Case C

```text
LLM
 ↓
RAG + Tool
 ↓
LLM
```

分析：

```text
events
```

是否能够正确表达。

特别关注：

```text
同一 timestamp
overlap
parallel execution
```

---

# 十二、Correlation

确认未来 Unified Event 不新增 Trace ID。

仍然：

```text
assistant_request_id = A
```

作为：

```text
Assistant Trace Root
```

所有 Event：

```text
event.request_id == A
```

不要新增：

```text
trace_id
span_id
event_id
```

除非设计分析证明确实必要。

本阶段不实现。

---

# 十三、输出 Design Document

新增：

```text
docs/evaluation/Phase 3.12 Step 49 — Assistant Trace Unified Timeline Design.md
```

内容必须包括：

```text
1. Current State

2. Existing Read Boundaries

3. Proposed Event Concept

4. Event Types

5. Timestamp Semantics

6. Tie-breaking

7. Duration Semantics

8. Correlation

9. Security Boundary

10. Concurrency Analysis

11. API Compatibility

12. Persistence Impact

13. Runtime Impact

14. Recommendation
```

---

# 十四、Recommendation

最后只能给出：

```text
IMPLEMENT
```

或者：

```text
DEFER
```

但不要因为“看起来更高级”就自动选择 IMPLEMENT。

如果当前三个独立列表已经能够满足主要 Trace 查询需求：

可以明确：

```text
DEFER
```

如果未来确实需要完整调用时间线：

说明：

```text
为什么需要
最小实现是什么
哪些能力仍然不应该进入 Timeline
```

---

# 十五、测试

本阶段不需要新增生产测试。

只需要：

```text
设计审计测试 / 文档一致性检查
```

如果没有必要：

```text
0 new tests
```

也是允许的。

不要为了测试数量新增无意义测试。

---

# 十六、禁止事项

本阶段禁止：

```text
❌ 修改 AssistantTraceResponse
❌ 增加 events 字段
❌ 修改 AssistantTraceQueryService 行为
❌ 修改 LLM Usage
❌ 修改 Tool Execution
❌ 修改 RAG Persistence
❌ 修改 RAG Runtime
❌ 新增 DB Table
❌ 新增 Event Bus
❌ OpenTelemetry
❌ Streaming
❌ WebSocket
❌ Conversation
❌ Memory
❌ Agent
❌ MCP
```

---

# 十七、验证

执行：

```powershell
python -m compileall -q backend tests
```

并检查：

```powershell
git status --short
git diff --stat
```

确认：

```text
生产代码 = 0 修改
DB schema = 0 修改
Runtime = 0 修改
API contract = 0 修改
```

---

# 十八、最终报告

完成后只报告：

```text
Phase 3.12 Step 49 COMPLETE

1. Current Trace Architecture
2. Existing Read Boundaries
3. Unified Event Proposal
4. Timestamp Semantics
5. Concurrency Analysis
6. Correlation
7. Security
8. API Compatibility
9. Persistence Impact
10. Recommendation
11. Files
12. Tests
13. compileall
14. Git Diff
15. Phase 3.12 Step 49 是否需要实施
```

然后：

**立即 STOP。**

不要进入 Step 50。

不要实现 Unified Timeline。

等待下一步指令。
