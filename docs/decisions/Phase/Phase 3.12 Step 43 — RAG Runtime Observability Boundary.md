你现在开始执行：

# Phase 3.12 Step 43 — RAG Runtime Observability Boundary

## 一、阶段目标

基于 Phase 3.12 Step 42 的 Audit 结果，本阶段开始补充：

**RAG Runtime Observability Boundary。**

Step 42 已确认：

```text
POST /api/ai/chat
        ↓
AIOrchestrator
        ↓
assistant_request_id = A
        ↓
RAG
        ↓
Vector Search
        ↓
Reranker（可选）
        ↓
ContextBuilder
        ↓
LLM
```

RAG 执行期间：

```python
current_assistant_request_id()
```

已经能够读取：

```text
A
```

但当前没有 RAG execution observation record。

本阶段只建立：

```text
RAG
 ↓
Runtime Observation
 ↓
assistant_request_id
```

**不进入 PostgreSQL Persistence。**

---

# 二、严格范围

## 允许

允许新增：

```text
RAG Runtime Observation DTO
RAG Runtime Collector
RAG Runtime Query Boundary
少量 RAG Service instrumentation
RAG runtime tests
RAG observability audit/evaluation 文档
```

但必须优先检查项目是否已经存在可以复用的：

```text
ToolExecutionSnapshot
ToolObservabilityCollector
ToolObservabilityQueryService
contextvar
assistant_trace_scope
```

如果能够安全复用既有模式，可以参考并保持一致。

不要复制一整套复杂 Tool Framework。

---

## 禁止

本阶段禁止修改：

```text
RAG Retrieval 核心算法
Vector Search 算法
Embedding Model
Reranker 算法
ContextBuilder 核心算法
AI Router 核心路由行为
AI Orchestrator 核心行为
LLM Usage Persistence
Tool Execution Persistence
Assistant Trace Query
Assistant Trace API
SQL Validator
SQL Executor
```

禁止新增：

```text
RAG database table
RAG ORM Model
RAG Repository
RAG PersistenceService
OpenTelemetry
Span
TraceId
Conversation
Memory
Agent
MCP
Streaming
Dashboard
```

禁止：

```text
DeepSeek
真实外部 LLM
生产数据库写入
```

---

# 三、Step 1：先阅读现有 Tool Runtime Observability

重点阅读 Phase 3.11 已有实现：

```text
backend/app/services/tool_observability_collector.py
backend/app/services/tool_observability_query_service.py
backend/app/services/tool_execution_service.py
backend/app/services/tool_execution_persistent_query_service.py
```

以及：

```text
tests/test_tool_observability_collector.py
tests/test_tool_observability_query_service.py
```

确认：

```text
Collector
Snapshot
Query Boundary
线程/请求隔离
容量限制
```

但：

**不要直接复制 Tool 代码。**

先判断哪些模式适合 RAG。

---

# 四、Step 2：定义 RAG Runtime Observation

只记录 Runtime 诊断需要的数据。

建议最小字段：

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

但必须重新检查 Step 42 的 Security Audit。

其中：

### Trace-safe

允许：

```text
request_id
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

### 不允许

不要记录：

```text
query
answer
chunk content
document content
embedding vector
prompt
messages
raw LLM response
SQL
DB connection
credentials
```

特别注意：

```text
similarity
```

暂时**不要进入 Runtime Observation DTO**，除非现有安全边界证明它有明确必要性。

第一版保持最小。

---

# 五、Step 3：request_id 来源

不要给 RAG Service 增加：

```python
request_id: str
```

如果没有必要。

优先复用现有：

```python
current_assistant_request_id()
```

也就是说：

```text
AIOrchestrator
    ↓
assistant_trace_scope(A)
    ↓
RagService
    ↓
current_assistant_request_id()
    ↓
A
```

保持：

```text
RAG Service API contract
```

不变。

例如不要把：

```python
RagService.answer(
    query,
    request_id=A,
)
```

作为第一选择。

---

# 六、Step 4：Observation 生命周期

RAG 一次执行形成：

```text
RAG Observation
```

生命周期：

```text
start
 ↓
Vector Search
 ↓
Reranker（optional）
 ↓
ContextBuilder
 ↓
LLM
 ↓
result
 ↓
finish
```

必须保证：

### 正常成功

产生：

```text
success observation
```

### 空检索

即：

```text
Vector Search = []
```

也应该能够形成：

```text
RAG observation
```

因为：

```text
空检索 ≠ 没有 RAG 执行
```

### Reranker disabled

仍然形成 observation：

```text
reranker_used = false
```

### 异常

如果 RAG 抛异常：

Observation 至少能够安全结束。

不要吞掉原始异常。

例如：

```text
RAG error
 ↓
record observation
 ↓
re-raise original error
```

---

# 七、Step 5：chunk/document ID

当前：

```text
RagSource
```

已经有：

```text
chunk_id
document_id
```

可以从已有结果中提取：

```text
chunk_ids
document_ids
```

要求：

### 去重

同一个：

```text
chunk_id
```

不要重复记录。

### 保持顺序

按照实际 retrieval/source 顺序。

不要：

```text
set(...)
```

导致顺序不稳定。

建议：

```text
first occurrence order
```

---

# 八、Step 6：Runtime Collector

新增最小 Collector。

建议能力：

```text
record(observation)
list()
get_by_request_id(request_id)
clear()
```

如果项目现有 Collector API 更合适：

优先遵循现有风格。

要求：

```text
max size
```

不要无限增长。

如果参考 Tool Collector 的：

```text
1000
```

可以保持一致。

但是：

**不要为了 RAG 新增复杂 retention 系统。**

---

# 九、Step 7：Query Boundary

新增：

```text
RagObservabilityQueryService
```

或者按照项目实际命名选择等价名称。

职责：

```text
Runtime Collector
        ↓
Query Service
        ↓
RAGObservationSnapshot
```

Query Service 必须：

```text
request_id 非空
strip
长度 ≤ 128
```

并且：

**validation 必须发生在 Collector 查询之前。**

非法：

```text
None
""
"   "
>128
```

必须直接拒绝。

不要访问 Collector。

---

# 十、Step 8：不要修改 Assistant Trace

本阶段：

```text
/api/observability/assistant-trace/{request_id}
```

**保持不变。**

不要加入：

```text
rag
retrieval
sources
```

本阶段只是建立：

```text
RAG Runtime Observability Boundary
```

下一阶段是否进入 Assistant Trace，由后续阶段单独决定。

---

# 十一、Step 9：不要新增 HTTP API

本阶段：

**不要新增：**

```text
/api/observability/rag
/api/observability/rag/history
/api/observability/rag/metrics
```

Runtime Query Boundary 只用于：

```text
内部测试
内部后续组合
```

避免现在就扩大 API surface。

---

# 十二、Step 10：RAG Service 最小 instrumentation

只允许在：

```text
backend/app/services/rag_service.py
```

增加最小 observation instrumentation。

不要修改：

```text
VectorSearchService
RerankerClient
ContextBuilder
```

优先在 RagService 这一层收集：

```text
started_at
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

如果 RagService 当前无法可靠取得某个字段：

```text
不要修改下游核心模块来“补数据”
```

记录：

```text
unavailable
```

或根据现有 DTO 设计省略。

---

# 十三、异常语义

必须遵守：

```text
Observation failure
```

不能覆盖：

```text
RAG original failure
```

即：

```python
try:
    ...
except Exception:
    record_observation(...)
    raise
```

如果 observation 自身失败：

**不得影响 RAG 主链路。**

例如：

```text
RAG success
Observation failure
```

不能变成：

```text
RAG failure
```

所以建议 observation 属于：

```text
best-effort diagnostics
```

---

# 十四、测试要求

新增：

```text
tests/test_rag_runtime_observability.py
```

如果已有测试结构更合适，遵循现有结构。

至少覆盖：

## 1. Normal RAG

```text
RAG execute
→ observation exists
```

---

## 2. request_id

确认：

```text
observation.request_id == metadata.request_id
```

---

## 3. Empty retrieval

```text
retrieval = []
```

仍然有 observation。

---

## 4. Reranker disabled

确认：

```text
reranker_used = false
```

---

## 5. Chunk IDs

例如：

```text
sources:
chunk-1
chunk-2
chunk-1
```

最终：

```text
chunk_ids:
chunk-1
chunk-2
```

保持第一次出现顺序。

---

## 6. Document IDs

同样：

```text
document_ids
```

去重并保持顺序。

---

## 7. Invalid request_id

测试：

```text
None
""
"   "
>128
```

必须：

```text
reject
```

并且：

```text
collector not accessed
```

---

## 8. Runtime isolation

```text
request A
request B
```

确认：

```text
A ≠ B
```

---

## 9. Collector clear

```text
record
clear
list = []
```

---

## 10. Capacity

如果设置：

```text
max_records = 1000
```

至少测试：

```text
1001
```

不会无限增长。

---

## 11. Error path

模拟：

```text
RAG exception
```

确认：

```text
original exception preserved
observation safely recorded
```

---

## 12. Observation failure isolation

模拟 Collector 本身异常。

确认：

```text
RAG result remains successful
```

如果 RAG 本身成功。

---

# 十五、Security Tests

严格检查 Runtime Observation 不包含：

```text
content
query
prompt
messages
raw_response
embedding
sql
password
api_key
authorization
database_url
session
connection
traceback
```

不要使用：

```text
vars()
__dict__
asdict()
model_dump()
```

做无边界序列化。

必须使用显式字段映射。

---

# 十六、Determinism

同一个 Fake RAG：

```text
run A
run A
```

观察结果：

```text
chunk_ids
document_ids
flags
counts
```

应该稳定。

不要把：

```text
duration_ms
timestamp
```

纳入 equality。

---

# 十七、不要做 DB

本阶段：

```text
NO DATABASE
NO MIGRATION
NO TABLE
NO INSERT
NO DELETE
```

只测试：

```text
Runtime Memory
```

因此：

```text
RUN_DB_TESTS=1
```

不是本阶段必要条件。

---

# 十八、文档

新增：

```text
docs/evaluation/Phase 3.12 Step 43 — RAG Runtime Observability.md
```

记录：

## 1. Goal

```text
RAG Runtime Observation
```

## 2. Data Model

列出安全字段。

## 3. Lifecycle

```text
RAG start
 ↓
Retrieval
 ↓
Rerank
 ↓
Context
 ↓
LLM
 ↓
Observation
```

## 4. Correlation

```text
assistant_request_id
```

## 5. Security

哪些字段：

```text
safe
forbidden
```

## 6. Runtime Only

明确：

```text
No PostgreSQL
No Persistence
```

## 7. API

明确：

```text
No new HTTP API
```

---

# 十九、Regression

先运行：

```powershell
python -m pytest -q tests/test_rag_runtime_observability.py
```

然后：

```powershell
python -m pytest -q
```

编译：

```powershell
python -m compileall -q backend tests
```

LSP：

```text
0 error
0 warning
```

lint：

如果没有：

```text
ruff / flake8
```

保持：

```text
lint unavailable
```

不要安装新工具。

---

# 二十、Git Diff

执行：

```powershell
git diff --stat
git diff --name-only
```

确认：

允许修改：

```text
RagService
Runtime RAG Observability
Tests
Docs
```

不允许出现：

```text
Assistant Trace API
LLM Usage
Tool Persistence
Database Schema
Router Core
Text-to-SQL
```

---

# 二十一、最终报告

完成后严格：

```text
Phase 3.12 Step 43 COMPLETE

1. RAG Runtime Observation
2. Correlation
3. Observation Fields
4. Collector
5. Query Boundary
6. Error Isolation
7. Security
8. Runtime Tests
9. Full Tests
10. compile / LSP / lint
11. DB
12. Git Diff
13. 当前限制
```

明确：

```text
RAG Runtime Observation = YES
RAG Persistence = NO
RAG Trace API = NO
Assistant Trace modification = NO
Database schema = unchanged
DeepSeek calls = 0
Network calls = 0
```

最后：

# STOP

**完成 Step 43 后立即停止。**

不要进入 Step 44。

不要做：

```text
RAG Persistence
Assistant Trace RAG integration
Conversation
Memory
Agent
MCP
OpenTelemetry
Streaming
Dashboard
```
