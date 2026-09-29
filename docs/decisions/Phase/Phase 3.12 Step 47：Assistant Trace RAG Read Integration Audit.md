你现在开始执行：

# Phase 3.12 Step 47：Assistant Trace RAG Read Integration Audit

## 一、阶段目标

Phase 3.12 Step 46 已经完成：

```text
RAG Runtime Observation
        ↓
Composite Observer
        ├── InMemory
        └── PostgreSQL
              ↓
        rag_execution_record
```

当前 Assistant Trace 仍然只有：

```text
AssistantTrace
    ├── LLM Usage
    └── Tool Execution
```

本阶段不要直接修改 Assistant Trace API。

唯一目标：

> **审计并确定 RAG Persistent Read Model 如何安全地进入 Assistant Trace。**

本阶段只做：

```text
阅读现有 Trace
        ↓
比较 LLM / Tool / RAG Read Boundary
        ↓
设计 RAG Trace Contract
        ↓
安全边界
        ↓
离线 Contract Tests
        ↓
文档
        ↓
STOP
```

---

# 二、严格禁止

本阶段禁止：

```text
修改 /api/observability/assistant-trace/{request_id}
修改 AssistantTraceResponse
修改 AssistantTraceQueryService
修改 LLM Usage
修改 Tool Execution
修改 RAG Runtime
修改 RAG Persistence
修改 RagService
修改 AI Router
修改 AI Orchestrator
```

禁止：

```text
新增 HTTP API
新增数据库表
新增 migration
新增 Repository
新增 SQLAlchemy Model
新增 DB 写入
```

禁止：

```text
DeepSeek
SiliconFlow
真实网络
生产数据库
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

---

# 三、先阅读真实实现

必须先阅读：

```text
backend/app/services/assistant_trace_query_service.py
backend/app/services/assistant_trace_read_model.py
backend/app/api/assistant_trace.py
```

以及：

```text
backend/app/services/llm_usage_query_service.py
backend/app/services/tool_execution_persistent_query_service.py
backend/app/services/rag_execution_persistent_query_service.py
```

和：

```text
backend/app/db/llm_usage_repository.py
backend/app/db/tool_execution_repository.py
backend/app/db/rag_execution_repository.py
```

以及现有：

```text
tests/test_assistant_trace*
tests/test_*trace*
```

先理解真实 DTO 和 Read Boundary。

不要根据之前阶段报告猜接口。

---

# 四、当前 Trace Contract

先确认当前 HTTP Response：

```json
{
  "assistant_request_id": "...",
  "llm_usage": [...],
  "tool_executions": [...]
}
```

确认：

1. 字段名称
2. DTO 类型
3. 序列化方式
4. 空结果行为
5. 查询失败行为
6. HTTP error mapping
7. security filtering
8. ordering

不要修改这些行为。

---

# 五、RAG Persistent Read Boundary

Step 46 已经存在：

```text
RagExecutionPersistentQueryService
```

确认它实际支持：

```text
list_by_request_id(request_id)
```

确认：

```text
request_id validation
empty → []
DB failure → typed error
ORDER BY id ASC
explicit fields
```

不要新增第二个 RAG Query Service。

---

# 六、RAG Trace DTO 设计

本阶段只设计，不修改生产 DTO。

提出一个最小结构，例如：

```text
RagExecutionTrace
```

或者根据当前项目 DTO 命名规范选择。

建议只暴露：

```text
id
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

但必须重新检查：

> Assistant Trace 是否真的需要暴露全部 13 个字段。

不要机械复制 Persistent Record。

尤其评估：

```text
started_at
finished_at
duration_ms
```

是否属于 Trace 必需信息。

以及：

```text
chunk_ids
document_ids
```

是否需要暴露给 HTTP 客户端。

---

# 七、安全审计

Assistant Trace 是 HTTP Read API。

因此重新检查：

允许：

```text
request_id
timestamps
latency
result_count
used_chunks_count
top_k
context_truncated
context_chars
reranker_used
rerank_elapsed_ms
```

谨慎评估：

```text
chunk_ids
document_ids
```

禁止：

```text
query
answer
chunk content
document content
similarity
embedding
prompt
messages
raw LLM response
SQL
database connection
credentials
Authorization
API key
password
database URL
session
ORM
internal traceback
```

如果 chunk/document ID 可能暴露业务敏感信息：

必须记录这个风险。

不要擅自放宽。

---

# 八、Correlation

必须保持：

```text
Assistant Request
        ↓
request_id = A
```

三条记录：

```text
LLM Usage
    assistant_request_id = A

Tool Execution
    request_id = A

RAG Execution
    request_id = A
```

不得增加：

```text
rag_request_id
retrieval_request_id
trace_request_id
```

等新的 correlation ID。

---

# 九、Ordering

必须明确 Trace 中三个来源如何排序。

当前：

```text
LLM Usage
Tool Execution
RAG Execution
```

分别拥有自己的时间/id。

不要直接假设：

```text
LLM list + Tool list + RAG list
```

就是正确时间顺序。

需要确定：

### 方案 A

三个独立列表：

```text
{
  llm_usage: [],
  tool_executions: [],
  rag_executions: []
}
```

### 方案 B

新增统一 events：

```text
events: []
```

本阶段不要实现，只分析。

优先保持当前：

```text
llm_usage
tool_executions
```

结构不变。

评估 RAG 是否应该作为：

```text
rag_executions
```

第三个独立列表。

不要为了统一排序重构现有 Trace。

---

# 十、Failure Semantics

必须明确：

如果：

```text
LLM query success
Tool query success
RAG query failure
```

Assistant Trace 应该：

```text
业务请求是否失败？
```

以及：

```text
Trace API 返回什么？
```

对照当前：

```text
LLMUsageRepositoryError
ToolExecutionRepositoryError
```

的现有行为。

目标不是隐藏数据库错误。

必须保持：

```text
empty result != query failure
```

如果 RAG Persistent Query 抛：

```text
RagExecutionRepositoryError
```

不能：

```text
→ []
```

---

# 十一、Backward Compatibility

当前已有客户端可能只认识：

```json
{
  "assistant_request_id": "...",
  "llm_usage": [],
  "tool_executions": []
}
```

本阶段必须评估：

增加：

```json
"rag_executions": []
```

是否属于：

```text
backward-compatible additive change
```

原则：

```text
旧字段不改名
旧字段不删除
旧字段语义不改变
```

但：

**本阶段不要实际修改 HTTP Response。**

只形成设计结论。

---

# 十二、Trace Query Composition

设计未来：

```text
AssistantTraceQueryService
        ├── LLMUsageQueryService
        ├── ToolExecutionPersistentQueryService
        └── RagExecutionPersistentQueryService
```

确认：

### 不允许：

```text
AssistantTraceQueryService
    ↓
直接访问 SQLAlchemy
```

### 不允许：

```text
AssistantTraceQueryService
    ↓
rag_execution_record
```

直接查询。

必须保持：

```text
Read Model
    ↓
Query Service
```

---

# 十三、不要引入 Runtime Collector

非常重要：

Assistant Trace 查询必须：

```text
Persistent RAG Query Service
```

而不是：

```text
InMemoryRagExecutionCollector
```

否则：

```text
restart
multi-worker
```

情况下 Trace 会出现：

```text
LLM / Tool 有历史
RAG 没历史
```

因此本阶段明确：

```text
Assistant Trace RAG source = PostgreSQL
```

而不是 Runtime Memory。

---

# 十四、Project Isolation

检查：

```text
request_id
```

是否足以隔离：

```text
project-a
project-b
```

当前 RAG Persistent Record 没有：

```text
project_id
```

这是设计中的明确选择。

本阶段需要回答：

> Assistant request_id 是否已经是足够的安全访问边界？

如果当前 Trace API 没有 project-level authorization：

必须明确记录：

```text
当前 Trace API 依赖 request_id 作为查询键。
没有额外 project authorization。
```

不要自行新增 project_id。

---

# 十五、Contract Tests

新增：

```text
tests/test_assistant_trace_rag_integration_contract.py
```

纯离线。

至少测试：

### 1. RAG field mapping

Persistent Record：

```text
→ Trace DTO
```

字段不丢失或明确裁剪。

### 2. Security

Trace DTO 不包含：

```text
query
content
answer
similarity
embedding
prompt
messages
raw_response
sql
credentials
```

### 3. Correlation

```text
request_id = assistant_request_id
```

### 4. Empty

```text
no RAG record
→ rag_executions = []
```

### 5. Error

```text
RagExecutionRepositoryError
```

不能伪装成：

```text
[]
```

### 6. Ordering

明确 RAG records：

```text
id ASC
```

保持。

### 7. Backward compatibility

确认：

```text
llm_usage
tool_executions
```

语义没有变化。

---

# 十六、文档

新增：

```text
docs/evaluation/Phase 3.12 Step 47 — Assistant Trace RAG Integration Audit.md
```

内容：

## 1. Current State

```text
Assistant Trace
    ├── LLM Usage
    └── Tool Execution
```

## 2. Proposed State

```text
Assistant Trace
    ├── LLM Usage
    ├── Tool Execution
    └── RAG Execution
```

## 3. Read Sources

```text
LLM → LLMUsageQueryService
Tool → ToolExecutionPersistentQueryService
RAG → RagExecutionPersistentQueryService
```

## 4. Correlation

```text
assistant_request_id
```

## 5. Security

完整允许/禁止字段。

## 6. Error Semantics

```text
empty != failure
```

## 7. Backward Compatibility

说明新增 `rag_executions` 的兼容性。

## 8. Runtime vs Persistent

明确：

```text
Assistant Trace
    ↓
Persistent only
```

不读取 Runtime Collector。

## 9. Project Authorization Limitation

如果存在，明确记录。

## 10. Deferred

```text
HTTP API modification deferred
Implementation deferred
```

---

# 十七、验证

运行：

```powershell
python -m pytest -q tests/test_assistant_trace_rag_integration_contract.py
```

然后：

```powershell
python -m pytest -q tests/test_rag_execution_persistence.py
python -m pytest -q tests/test_rag_runtime_observability.py
```

最后：

```powershell
python -m pytest -q
python -m compileall -q backend tests
```

要求：

```text
0 failed
0 compile errors
```

本阶段：

```text
DB migration = 0
DB writes = 0
DeepSeek = 0
Network = 0
```

---

# 十八、Git Diff

原则上：

```text
Production runtime code = 0
```

允许：

```text
tests/
docs/
```

如果发现当前 Trace DTO 必须修改才能表达设计：

**先停止并报告，不要直接修改。**

---

# 十九、最终报告

严格输出：

```text
Phase 3.12 Step 47 COMPLETE

1. Current Assistant Trace
2. RAG Read Boundary
3. Proposed RAG Trace DTO
4. Correlation
5. Ordering
6. Security
7. Failure Semantics
8. Backward Compatibility
9. Runtime vs Persistent
10. Project Authorization
11. Contract Tests
12. Full Regression
13. compileall
14. DB
15. Git Diff
16. Deferred Implementation
```

最终必须明确：

```text
RAG Persistence = YES
RAG Persistent Read Boundary = YES
Assistant Trace RAG Integration = DESIGN ONLY
Assistant Trace HTTP API = UNCHANGED
Production Code = 0
DB Schema = UNCHANGED
DB Writes = 0
DeepSeek = 0
Network = 0
```

然后：

# STOP

不要实现 `rag_executions` HTTP 字段。
不要修改 AssistantTraceResponse。
不要修改 AssistantTraceQueryService。
不要接入 RAG Query Service。
不要开发 Conversation。
不要开发 Memory。
不要开发 Agent。
不要开发 MCP。
不要开发 OpenTelemetry。
不要开发 Dashboard。

等待下一步指令。
