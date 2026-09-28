你现在开始执行：

# Phase 3.12 Step 45：RAG Persistent Observation Boundary Audit

## 一、阶段目标

Step 44 已经完成：

```text
/api/ai/chat
    ↓
AIOrchestrator
    ↓
AIRouter
    ↓
RagService
    ↓
RagExecutionObserver
    ↓
InMemoryRagExecutionCollector
```

现在不要立即实现 PostgreSQL Persistence。

本阶段唯一目标：

> **确定 RAG Runtime Observation 如何安全、稳定地进入 Persistent Read Boundary，为后续 Step 46 做最小契约准备。**

本阶段只做：

```text
Audit
↓
Contract Design
↓
Read/Write Boundary Design
↓
Security Review
↓
Contract Tests
↓
Documentation
↓
STOP
```

---

# 二、严格禁止

本阶段禁止修改：

```text
RagService 核心业务逻辑
VectorSearch
Embedding
Reranker
ContextBuilder
AI Router
AI Orchestrator
LLM Usage
Tool Execution
Assistant Trace API
Assistant Trace Query
Text-to-SQL
SQL Validator
SQL Executor
Prompt
```

禁止：

```text
新增数据库表
新增 migration
新增 Repository
新增 SQLAlchemy Model
新增 DB Session
新增 PostgreSQL 写入
新增 PostgreSQL 查询
```

禁止：

```text
DeepSeek
SiliconFlow
网络请求
生产数据库
真实 LLM
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
```

不要修改：

```text
phase 3.12 Step 44 baseline
```

---

# 三、先阅读真实实现

先检查：

```text
backend/app/services/rag_execution_observation.py
backend/app/services/in_memory_rag_execution_collector.py
backend/app/services/rag_observability_query_service.py
backend/app/services/rag_observability_runtime.py

backend/app/db/
backend/app/services/llm_usage*
backend/app/services/tool_execution*
backend/app/services/assistant_trace*
```

重点比较：

```text
LLM Usage Persistent Boundary
Tool Execution Persistent Boundary
RAG Runtime Observation
```

不要重新设计已经存在的 Tool / LLM Persistence 模式。

---

# 四、确认当前 RAG Observation Contract

冻结 Step 44 当前的 13 个字段：

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

逐字段判断：

```text
是否适合持久化
是否需要 nullable
是否需要长度限制
是否需要索引
是否可能泄露业务数据
```

特别注意：

```text
chunk_ids
document_ids
```

允许作为引用 ID 保存。

禁止持久化：

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
```

---

# 五、设计 Persistent Record Contract

只设计 DTO / contract，不实现 ORM。

建议形成：

```text
RagExecutionPersistentRecord
```

或者项目已有统一 Snapshot / Record 命名方式，则遵循现有风格。

至少确定：

```text
record_id
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

但：

**不要机械新增 record_id。**

先检查 Tool Execution / LLM Usage 是否已有统一主键模式。

如果现有 persistence record 使用数据库自增 id，则沿用。

---

# 六、确定 request_id 策略

必须明确：

```text
assistant_request_id
```

还是：

```text
request_id
```

不要新增第二套 correlation id。

对照当前：

```text
LLMUsageRecord.assistant_request_id
ToolExecutionRecord.request_id
RagExecutionObservation.request_id
```

给出最终结论：

```text
RAG Persistent Record.request_id
=
Assistant request_id
```

如果当前项目已有更准确的字段命名，则遵循现有架构。

---

# 七、Persistence Boundary

设计：

```text
RagExecutionObservation
        ↓
Persistence Adapter
        ↓
Persistent Record
        ↓
Repository
```

但本阶段：

**只写接口/契约设计，不真正创建 Adapter / Repository。**

明确：

### Write Side

未来应该由：

```text
RagExecutionObserver
```

还是：

```text
Composite Observer
```

负责持久化。

必须比较当前 Tool / LLM 的做法。

不要为了 RAG 新造一套完全不同的生命周期。

---

# 八、Observer Failure Policy

沿用 Step 43/44：

```text
RAG Observation failure
        ↓
WARNING
        ↓
RAG business result unaffected
```

未来 DB 写入失败：

**不能导致 `/api/ai/chat` RAG 请求失败。**

但是：

**不能静默吞掉持久化错误。**

应该：

```text
业务成功
+
observability warning
```

具体错误处理方式必须和：

```text
LLM Usage
Tool Execution
```

保持一致。

---

# 九、Read Boundary

设计未来的：

```text
RagExecutionPersistentQueryService
```

但本阶段不要实现。

至少需要支持：

```text
list_by_request_id(request_id)
```

并明确：

```text
empty → []
DB failure → typed repository/query error
```

不能：

```text
DB failure → []
```

因为这会把：

```text
没有记录
```

和：

```text
数据库查询失败
```

混在一起。

---

# 十、查询字段安全

未来 Persistent Read Boundary：

只能返回安全字段：

```text
record_id
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

禁止返回：

```text
query
answer
content
similarity
embedding
prompt
messages
raw_response
SQL
session
connection
password
api_key
authorization
database_url
```

必须继续采用：

**显式字段映射。**

不要：

```text
vars()
__dict__
asdict()
model_dump()
```

自动暴露内部对象。

---

# 十一、索引设计

只做设计，不 migration。

未来 PostgreSQL 表至少评估：

```text
request_id
started_at
```

是否需要索引。

重点回答：

```text
Assistant Trace 查询主要按什么字段？
```

当前已知：

```text
request_id
```

是第一优先级。

不要提前添加：

```text
query
document_id
chunk_id
project_id
```

等没有明确查询需求的索引。

---

# 十二、Retention / Capacity

Step 44 当前 runtime：

```text
deque(maxlen=1000)
```

Persistence 不应该简单复制：

```text
max 1000
```

先明确：

```text
Runtime capacity ≠ Persistent retention
```

本阶段只记录：

```text
未来需要 retention policy
```

但不要实现：

```text
TTL
cron
cleanup worker
background task
partition
```

---

# 十三、Assistant Trace 暂不修改

非常重要。

Step 45：

**不要修改 Assistant Trace。**

即：

```text
AssistantTraceQueryService
AssistantTraceResponse
/api/observability/assistant-trace/{request_id}
```

全部保持不变。

文档中明确：

```text
Step 45 only defines RAG persistence boundary.
Assistant Trace integration deferred.
```

---

# 十四、Contract Tests

新增一个纯离线测试文件：

```text
tests/test_rag_persistent_observation_contract.py
```

测试：

### 1. Observation → Persistent Record 字段一致

13 个字段不能丢失。

### 2. Forbidden fields

确保 contract 不包含：

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
password
api_key
authorization
database_url
```

### 3. request_id

验证：

```text
request_id
```

就是当前 assistant correlation id。

### 4. Serialization

确保：

```text
chunk_ids
document_ids
```

保持确定性顺序。

### 5. Error semantics

测试设计契约：

```text
empty result != persistence failure
```

如果现有项目已有统一 Error DTO，则复用。

---

# 十五、Architecture 文档

新增：

```text
docs/evaluation/Phase 3.12 Step 45 — RAG Persistence Boundary Audit.md
```

记录：

## 1. Current State

```text
RAG Runtime Observation
    ↓
InMemory Collector
```

## 2. Target State

```text
RAG Runtime Observation
    ↓
Persistence Adapter
    ↓
PostgreSQL
```

## 3. Fields

完整字段表。

## 4. Forbidden Fields

完整安全列表。

## 5. Correlation

```text
assistant_request_id
```

## 6. Failure Policy

```text
Observability failure
≠
Business failure
```

## 7. Read Boundary

```text
list_by_request_id()
```

## 8. Index

```text
request_id
started_at
```

## 9. Deferred

明确：

```text
No DB table
No migration
No Repository
No Assistant Trace integration
No HTTP API
```

---

# 十六、验证

运行：

```powershell
python -m pytest -q tests/test_rag_persistent_observation_contract.py
```

然后：

```powershell
python -m pytest -q tests/test_rag_runtime_observability.py
python -m pytest -q tests/test_rag_runtime_observability_e2e.py
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

不要运行：

```text
DeepSeek
SiliconFlow
DB migration
```

---

# 十七、Git Diff 审查

最终确认：

允许：

```text
tests/
docs/
```

生产代码原则上：

```text
0 production runtime changes
```

如果为了 contract 必须增加极少量纯 DTO 类型：

先停下来报告，不要擅自扩大范围。

---

# 十八、最终报告

严格输出：

```text
Phase 3.12 Step 45 COMPLETE

1. Current RAG Observation Contract
2. Persistent Record Contract
3. request_id Correlation
4. Forbidden Fields
5. Write Boundary
6. Read Boundary
7. Error Semantics
8. Index Design
9. Retention Design
10. Assistant Trace
11. Tests
12. Full Regression
13. compileall
14. DB
15. Git Diff
16. Deferred Items
```

最终必须明确：

```text
RAG Persistence = NOT IMPLEMENTED
RAG Persistent Contract = DEFINED
DB Schema = UNCHANGED
Migration = 0
DB writes = 0
DeepSeek = 0
Network = 0
Assistant Trace = UNCHANGED
HTTP API = UNCHANGED
```

然后：

**立即 STOP。**

不要进入 Step 46。
不要创建 PostgreSQL 表。
不要创建 Repository。
不要接入 Assistant Trace。
不要开发 Conversation / Memory / Agent / MCP / OpenTelemetry。

```

这一步的价值是先把 **RAG 持久化的数据契约和安全边界**固定下来，再做真正的 DB 实现，避免后面同时改 Schema、Repository、Trace 导致范围失控。
```
