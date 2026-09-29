你现在开始执行：

# Phase 3.12 Step 48：Assistant Trace RAG Integration

## 一、阶段目标

Phase 3.12 Step 47 已完成设计审计。

现在正式实现：

```text
Assistant Trace
    ├── LLM Usage
    ├── Tool Execution
    └── RAG Execution
```

完整读取链：

```text
GET /api/observability/assistant-trace/{assistant_request_id}
        ↓
AssistantTraceQueryService
        ├── LLMUsageQueryService
        ├── ToolExecutionPersistentQueryService
        └── RagExecutionPersistentQueryService
                                      ↓
                              ai_ops.rag_execution_record
```

本阶段只完成：

1. RAG Persistent Read 接入 Assistant Trace
2. `rag_executions` HTTP Response 字段
3. RAG Trace DTO
4. `RagExecutionRepositoryError → HTTP 502`
5. 完整 E2E / Security / Regression

完成后立即 STOP。

---

# 二、严格范围

## 允许修改

只允许修改与 Assistant Trace RAG Read Integration 直接相关的：

```text
backend/app/services/assistant_trace_query_service.py
backend/app/api/assistant_trace.py
```

以及：

```text
backend/app/services/
```

中确实需要新增的 RAG Trace DTO。

测试：

```text
tests/test_assistant_trace*
tests/test_rag_execution_persistence*
```

文档：

```text
docs/architecture.md
docs/evaluation/
```

---

# 三、禁止修改

绝对不要修改：

```text
RagService
RagExecutionObservation
RagExecutionPersistentQueryService
RagExecutionPersistenceService
RagExecutionPersistenceAdapter
CompositeRagExecutionObserver
InMemoryRagExecutionCollector

AI Router
AI Orchestrator

LLM Usage
Tool Execution

VectorSearch
Embedding
Reranker
ContextBuilder

TextToSQL
SQL Validator
SQL Executor

Prompt
Project Context
```

禁止：

```text
Conversation
Memory
Agent
MCP
OpenTelemetry
Streaming
Dashboard
Metrics
```

禁止：

```text
新增数据库表
新增 migration
修改 rag_execution_record schema
新增 RAG HTTP API
```

---

# 四、Step 1：先阅读真实代码

必须先阅读：

```text
backend/app/services/assistant_trace_query_service.py
backend/app/api/assistant_trace.py
```

以及：

```text
backend/app/services/rag_execution_persistent_query_service.py
backend/app/db/rag_execution_repository.py
```

同时阅读：

```text
tests/test_assistant_trace*
tests/test_rag_execution_persistence*
tests/test_assistant_trace_rag_integration_contract.py
```

不要根据 Step 47 报告猜接口。

---

# 五、RAG Trace DTO

在当前项目实际 DTO 风格下新增最小 Trace DTO。

推荐：

```text
RagExecutionTraceResponse
```

如果现有命名规范更适合 `RagExecutionTrace`，遵循现有项目。

HTTP 暴露：

```text
request_id
started_at
finished_at
duration_ms
result_count
used_chunks_count
top_k
context_truncated
context_chars
reranker_used
rerank_elapsed_ms
chunk_ids
document_ids
```

共：

```text
13 fields
```

但是：

**不要暴露数据库主键 `id`。**

数据库：

```text
id
```

只用于内部稳定排序：

```text
ORDER BY id ASC
```

---

# 六、显式 Mapping

必须建立：

```text
RagExecutionRecordRow
        ↓
RagExecutionTraceResponse
```

显式逐字段映射。

禁止：

```text
vars()
__dict__
asdict()
model_dump()
自动 ORM 序列化
```

不要直接：

```text
return RagExecutionRecordRow
```

---

# 七、AssistantTraceResponse

将当前：

```json
{
  "assistant_request_id": "...",
  "llm_usage": [],
  "tool_executions": []
}
```

扩展为：

```json
{
  "assistant_request_id": "...",
  "llm_usage": [],
  "tool_executions": [],
  "rag_executions": []
}
```

要求：

### 字段名

必须：

```text
rag_executions
```

不要使用：

```text
rag
rag_usage
rag_trace
retrievals
retrieval_executions
```

---

# 八、Backward Compatibility

这是 additive response change：

```text
旧字段：
assistant_request_id
llm_usage
tool_executions

全部保持不变。

新增：
rag_executions
```

不要：

```text
rename
delete
reorder semantics
```

空数据：

```text
rag_executions = []
```

仍然：

```text
HTTP 200
```

FastAPI response model 应继续承担明确的响应 schema / serialization / field filtering；不要绕过现有 response model 机制。

---

# 九、AssistantTraceQueryService

修改：

```text
AssistantTraceQueryService.get_trace(request_id)
```

从：

```text
LLMUsageQueryService
ToolExecutionPersistentQueryService
```

扩展为：

```text
LLMUsageQueryService
ToolExecutionPersistentQueryService
RagExecutionPersistentQueryService
```

即：

```text
AssistantTraceQueryService
    ├── LLM Usage Query
    ├── Tool Execution Persistent Query
    └── RAG Execution Persistent Query
```

禁止：

```text
AssistantTraceQueryService
    ↓
SQLAlchemy
```

禁止：

```text
AssistantTraceQueryService
    ↓
rag_execution_record
```

直接查询。

必须经过：

```text
RagExecutionPersistentQueryService
```

---

# 十、Composition Root

检查当前：

```text
get_assistant_trace_query_service()
```

必须注入：

```text
get_rag_execution_persistent_query_service()
```

不要在 API 层创建：

```text
RagExecutionPersistentQueryService()
```

不要：

```text
Repository()
```

不要：

```text
Session()
```

API 继续只负责：

```text
HTTP
→
Composition Service
```

---

# 十一、Failure Semantics

这是本阶段必须修复的问题。

当前 Step 47 已确认：

```text
LLMUsageRepositoryError → 502
ToolExecutionRepositoryError → 502
RagExecutionRepositoryError → 500   ← BUG
```

必须改成：

```text
LLMUsageRepositoryError
ToolExecutionRepositoryError
RagExecutionRepositoryError
        ↓
HTTP 502
```

不要扩大到：

```text
所有 Exception → 502
```

只增加：

```text
RagExecutionRepositoryError
```

到现有 Repository Error 的 502 tuple。

其它异常语义保持不变：

```text
ValueError → 400
path validation → 422
known repository errors → 502
unexpected → 500
```

---

# 十二、Empty Semantics

当：

```text
RagExecutionPersistentQueryService.list_by_request_id(A)
```

返回：

```text
[]
```

Trace：

```json
"rag_executions": []
```

HTTP：

```text
200
```

不能：

```text
404
```

不能：

```text
502
```

不能：

```text
500
```

---

# 十三、Ordering

三个列表独立排序。

### LLM

保持现有：

```text
created_at ASC
id ASC
```

### Tool

保持：

```text
id ASC
```

### RAG

保持：

```text
id ASC
```

AssistantTraceQueryService：

**不要重新排序。**

不要建立：

```text
events[]
```

不要合并三个列表。

不要去重。

---

# 十四、Production Composition E2E

新增或扩展：

```text
tests/test_assistant_trace_rag_integration_e2e_db.py
```

使用：

```text
TestClient(create_app())
```

真实：

```text
FastAPI app
Assistant Trace API
AssistantTraceQueryService
LLMUsageQueryService
ToolExecutionPersistentQueryService
RagExecutionPersistentQueryService
PostgreSQL
```

只 Fake 外部边界。

---

# 十五、E2E Case 1：RAG Only

先通过真实 `/api/ai/chat` 产生：

```text
request_id=A
```

产生：

```text
rag_execution_record
```

然后：

```text
GET /api/observability/assistant-trace/A
```

验证：

```text
200
```

并：

```text
response.json()["assistant_request_id"] == A
len(response["rag_executions"]) >= 1
```

验证 RAG 字段：

```text
request_id == A
result_count
used_chunks_count
top_k
context_truncated
context_chars
reranker_used
rerank_elapsed_ms
chunk_ids
document_ids
```

---

# 十六、E2E Case 2：LLM + RAG

构造一个真实 RAG 请求，使 RAG 内部调用 LLM。

验证：

```text
llm_usage >= 1
rag_executions >= 1
```

并：

```text
LLM assistant_request_id == A
RAG request_id == A
```

确认 correlation 完整。

---

# 十七、E2E Case 3：Tool Only

产生：

```text
tool_execution
```

但不产生：

```text
rag_execution
```

Trace：

```json
{
  "llm_usage": [],
  "tool_executions": [...],
  "rag_executions": []
}
```

确认：

```text
rag_executions == []
```

---

# 十八、E2E Case 4：Empty Trace

request_id：

```text
unknown-request
```

返回：

```text
200
```

并：

```json
{
  "assistant_request_id": "unknown-request",
  "llm_usage": [],
  "tool_executions": [],
  "rag_executions": []
}
```

---

# 十九、E2E Case 5：Cross Request Isolation

创建：

```text
A
B
```

两次 RAG 请求。

查询：

```text
trace/A
trace/B
```

确认：

```text
A.rag_executions 全部 request_id=A
B.rag_executions 全部 request_id=B
```

不得交叉。

---

# 二十、E2E Case 6：RAG Repository Failure

Mock：

```text
RagExecutionPersistentQueryService.list_by_request_id()
```

抛出：

```text
RagExecutionRepositoryError
```

调用：

```text
GET /api/observability/assistant-trace/A
```

必须：

```text
HTTP 502
```

并且：

```text
response
```

不能包含：

```text
traceback
SQL
database_url
credentials
internal path
```

---

# 二十一、Security Tests

HTTP Response 禁止：

```text
id
query
answer
content
similarity
embedding
prompt
messages
raw_response
sql
password
api_key
authorization
database_url
session
ORM
traceback
```

允许：

```text
request_id
timestamps
duration
result_count
used_chunks_count
top_k
context_truncated
context_chars
reranker_used
rerank_elapsed_ms
chunk_ids
document_ids
```

特别验证：

```text
chunk content
document content
```

绝不进入 Trace response。

---

# 二十二、RAG HTTP API 不变

确认：

```text
/api/rag/answer
```

不增加：

```text
rag_executions
request_id
trace
```

Step 48 只修改：

```text
/api/observability/assistant-trace/{request_id}
```

---

# 二十三、Assistant Trace 不读取 Runtime

必须增加静态/契约测试：

```text
AssistantTraceQueryService
```

不能 import：

```text
InMemoryRagExecutionCollector
RagObservabilityQueryService
```

只能使用：

```text
RagExecutionPersistentQueryService
```

---

# 二十四、测试文件

建议：

```text
tests/test_assistant_trace_rag_integration.py
tests/test_assistant_trace_rag_integration_e2e_db.py
```

如果现有测试结构已有对应文件，优先扩展，不重复创建。

Unit 至少覆盖：

```text
RAG mapping
empty
ordering
security
correlation
failure
```

DB/E2E 至少覆盖：

```text
RAG only
LLM + RAG
Tool only
empty
A/B isolation
repository failure
```

---

# 二十五、DB Residue

DB 测试前记录：

```text
rag_execution_record count
```

测试后必须：

```text
residue = 0
```

只允许删除：

```text
本阶段测试生成的 request_id
```

禁止：

```text
TRUNCATE
```

禁止修改真实 WMS 数据。

---

# 二十六、测试命令

先：

```powershell
python -m pytest -q tests/test_assistant_trace_rag_integration.py
```

然后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_assistant_trace_rag_integration_e2e_db.py
```

然后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

最后：

```powershell
python -m compileall -q backend tests
```

要求：

```text
0 failed
0 compile errors
```

默认测试：

```text
DeepSeek = 0
SiliconFlow = 0
Network = 0
```

---

# 二十七、Git Diff

允许：

```text
Assistant Trace DTO
AssistantTraceQueryService
Assistant Trace API error mapping
Composition Root
Tests
Docs
```

禁止：

```text
RAG Runtime changes
RAG Persistence write changes
AI Router changes
AI Orchestrator changes
LLM Usage changes
Tool Execution changes
Text-to-SQL changes
DB schema changes
```

---

# 二十八、最终报告

严格：

```text
Phase 3.12 Step 48 COMPLETE

1. Assistant Trace DTO
2. RAG Persistent Read Integration
3. Composition Root
4. HTTP API
5. Error Mapping
6. RAG Only E2E
7. LLM + RAG E2E
8. Tool Only E2E
9. Empty Trace
10. Cross Request Isolation
11. Failure Isolation
12. Security
13. Runtime vs Persistent
14. Tests
15. Full DB Regression
16. compileall
17. DB Residue
18. Git Diff
19. Current Limitations
```

最终必须：

```text
RAG Persistence = YES
RAG Persistent Read Boundary = YES
Assistant Trace RAG Integration = YES
rag_executions HTTP field = YES
RAG HTTP API = UNCHANGED
LLM Usage = UNCHANGED
Tool Execution = UNCHANGED
AI Router = UNCHANGED
AI Orchestrator = UNCHANGED
RAG Core = UNCHANGED
DB Schema = UNCHANGED
DeepSeek = 0 in tests
Network = 0 in tests
DB writes = test-only
DB residue = 0
```

然后：

# STOP

不要进入 Step 49。

不要开发 Conversation。
不要开发 Memory。
不要开发 Agent。
不要开发 MCP。
不要开发 OpenTelemetry。
不要开发 Streaming。
不要开发 Dashboard。
不要开发 Metrics API。
