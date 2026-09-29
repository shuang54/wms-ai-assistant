你现在开始执行：

# Phase 3.12 Step 46：RAG Persistent Execution Record

## 一、阶段目标

Step 45 已经完成 RAG Persistent Observation Contract。

现在只实现：

> **将当前已经生产接线的 RAG Runtime Observation 持久化到 PostgreSQL。**

目标链路：

```text
POST /api/ai/chat
        ↓
AIOrchestrator
        ↓
AIRouter
        ↓
RagService
        ↓
RagExecutionObservation
        ↓
CompositeRagExecutionObserver
        ├── InMemoryRagExecutionCollector
        └── Persistence Adapter
                ↓
          Persistence Service
                ↓
            Repository
                ↓
          PostgreSQL
```

本阶段只负责：

```text
RAG Runtime Observation
        ↓
PostgreSQL Persistence
```

**不要接 Assistant Trace。**

---

# 二、严格范围

## 允许修改

原则上只允许：

```text
backend/app/services/
backend/app/db/
tests/
docs/
```

与 RAG Persistence 直接相关的文件。

可以新增：

```text
backend/app/db/models/rag_execution_record.py

backend/app/db/rag_execution_repository.py

backend/app/services/rag_execution_persistence_service.py

backend/app/services/rag_execution_persistence_adapter.py

backend/app/services/composite_rag_execution_observer.py
```

实际命名必须先参考现有：

```text
ToolExecution
LLMUsage
```

的真实命名风格。

如果项目已有可复用结构，**优先复用，不要复制出第二套模式。**

---

# 三、明确禁止

本阶段禁止修改：

```text
TextToSQL
SQL Validator
SQL Executor
AI Router
AI Orchestrator
VectorSearch
Embedding
Reranker
ContextBuilder
Prompt
Tool Execution
LLM Usage
Assistant Trace
Assistant Trace API
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
Metrics API
RAG HTTP API
```

禁止新增：

```text
RAG Query API
RAG Metrics API
```

禁止修改：

```text
RagExecutionObservation
```

的 13 字段契约。

禁止增加：

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
credentials
project_id
```

---

# 四、先阅读现有 Persistence 实现

开始修改前必须先阅读：

```text
backend/app/db/models/llm_usage_record.py
backend/app/db/llm_usage_repository.py
backend/app/services/llm_usage_persistence_service.py
backend/app/services/database_llm_accounting_sink.py

backend/app/db/models/tool_execution_record.py
backend/app/db/tool_execution_repository.py
backend/app/services/tool_execution_persistence_service.py
backend/app/services/tool_execution_persistence_adapter.py
```

以及：

```text
backend/app/db/base.py
backend/app/db/session.py
backend/app/main.py
```

确认：

1. ORM Model 命名方式
2. schema 命名
3. table naming
4. 主键方式
5. timestamp 类型
6. JSON / ARRAY 字段方式
7. Repository 参数绑定方式
8. explicit column selection
9. Repository Error 类型
10. Persistence Service 职责
11. Persistence Adapter 职责
12. migration / init_db 当前机制
13. 测试数据库初始化方式

**不要凭经验假设。**

---

# 五、数据库表

建议表：

```text
ai_ops.rag_execution_record
```

最终字段严格根据 Step 45 Contract。

建议：

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

但：

**必须根据项目现有 ORM / PostgreSQL 类型风格确认最终实现。**

特别注意：

```text
chunk_ids
document_ids
```

优先使用项目已经存在的 PostgreSQL ARRAY / JSON 类型方案。

不要为了 RAG 引入新的序列化框架。

---

# 六、主键

沿用：

```text
id
```

数据库自增。

不要加入：

```text
record_id
uuid
observation_id
```

除非现有 DB 架构强制要求。

Step 45 已经明确：

```text
Observation DTO 不持有 DB primary key
```

保持这个边界。

---

# 七、Index

第一版只需要：

```text
request_id
started_at
```

索引。

命名沿用现有：

```text
ix_<table>_<column>
```

不要新增：

```text
chunk_id
document_id
project_id
query
similarity
```

索引。

不要做 composite index，除非现有项目已经有明确同构模式。

---

# 八、Repository

新增：

```text
RagExecutionRepository
```

职责严格限定：

```text
insert(record)
get_by_request_id(request_id)
```

如果项目已有 repository 方法命名规范，遵循现有规范。

要求：

### insert

只写：

```text
ai_ops.rag_execution_record
```

### get_by_request_id

使用：

```text
WHERE request_id = :request_id
ORDER BY id ASC
```

必须：

* 参数绑定
* explicit columns
* 不使用 `SELECT *`
* 不动态拼接 SQL
* 不暴露 SQLAlchemy Session / Connection

---

# 九、Repository Error

新增：

```text
RagExecutionRepositoryError
```

或者项目已有统一 Domain Repository Error 风格，则沿用。

语义：

```text
DB success → normal result

DB failure → RagExecutionRepositoryError
```

绝不能：

```text
DB failure → []
```

---

# 十、Persistent Service

新增：

```text
RagExecutionPersistenceService
```

职责：

```text
RagExecutionObservation
        ↓
validation / mapping
        ↓
Repository.insert()
```

不要在 Service 中：

```text
创建 SQL
创建 Session
直接操作 ORM query
```

保持：

```text
Service
 ↓
Repository
```

---

# 十一、Persistence Adapter

新增：

```text
RagExecutionPersistenceAdapter
```

实现现有：

```text
RagExecutionObserver
```

接口。

输入：

```text
RagExecutionObservation
```

然后：

```text
PersistenceAdapter
    ↓
PersistenceService
    ↓
Repository
```

---

# 十二、Failure Isolation

必须严格保持 Step 43 / Step 44 的原则：

```text
RAG Business Result
        ↓
成功
```

即使：

```text
PostgreSQL unavailable
Repository insert failure
Persistence Service failure
```

也不能让：

```text
/api/ai/chat
```

RAG 请求失败。

行为：

```text
Persistence failure
        ↓
warning log
        ↓
RAG result remains successful
```

但是：

**不能静默吞掉。**

日志至少包含：

```text
error_type
request_id
```

禁止日志输出：

```text
query
answer
content
credentials
database_url
SQL
```

---

# 十三、Composite Observer

当前：

```text
RagService
    ↓
InMemoryRagExecutionCollector
```

现在变成：

```text
RagService
    ↓
CompositeRagExecutionObserver
       ├── InMemory Collector
       └── Persistence Adapter
```

要求：

### 1. 两个 observer 都执行

Persistence failure：

```text
Memory observation 仍然成功
```

Memory failure：

```text
Persistence 不应该被静默跳过
```

具体 fan-out 语义必须参考现有 Tool Composite Observer。

---

# 十四、Production Composition

修改：

```text
backend/app/services/rag_observability_runtime.py
```

从：

```text
RagService(observer=InMemoryCollector)
```

变为：

```text
RagService(
    observer=CompositeRagExecutionObserver(
        InMemoryCollector,
        RagExecutionPersistenceAdapter
    )
)
```

必须保持：

```text
一个进程
    ↓
一个 Collector
    ↓
一个 Persistence Adapter
    ↓
一个 RagService
```

不能每 request 创建：

```text
Repository
Service
Adapter
Collector
```

除非现有项目的 DB session 生命周期明确要求 per-operation 创建 session。

---

# 十五、数据库初始化

必须按照项目现有机制增加：

```text
ai_ops.rag_execution_record
```

不要：

```text
手工执行 CREATE TABLE
```

不要修改生产 DB。

先检查项目当前：

```text
init_db
metadata.create_all
migration
fixture
```

采用已有方式。

如果项目当前没有 migration framework：

**不要自行引入 Alembic。**

---

# 十六、DB Tests

新增：

```text
tests/test_rag_execution_persistence_db.py
```

至少测试：

### 1. insert

Observation：

```text
request_id=A
```

成功写入。

### 2. read

```text
get_by_request_id(A)
```

得到 1 条。

### 3. multiple records

```text
A
A
B
```

查询：

```text
A → 2
B → 1
```

顺序：

```text
id ASC
```

### 4. empty

```text
unknown-request
→ []
```

### 5. invalid request_id

例如：

```text
""
"   "
None
>128 chars
```

必须：

```text
validation error
```

且 Repository 不执行 DB query。

### 6. failure semantics

Repository DB failure：

```text
RagExecutionRepositoryError
```

不能：

```text
[]
```

---

# 十七、Persistence Mapping Tests

新增：

```text
tests/test_rag_execution_persistence.py
```

测试：

```text
Observation
    ↓
Persistence Record
```

13 字段完整保留。

特别验证：

```text
rerank_elapsed_ms=None
```

不会变成：

```text
0
```

以及：

```text
chunk_ids
document_ids
```

顺序保持。

---

# 十八、Security Tests

必须验证：

数据库 Record / Repository DTO 不包含：

```text
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
project_id
```

并且：

```text
chunk_ids
document_ids
```

只保存 ID。

---

# 十九、Production E2E

新增或扩展：

```text
tests/test_rag_runtime_observability_e2e_db.py
```

使用：

```text
TestClient(create_app())
```

真实：

```text
Composition Root
AIOrchestrator
AIRouter
RagService
Composite Observer
Persistence Adapter
Persistence Service
Repository
PostgreSQL
```

只 Fake：

```text
Embedding / Vector Search boundary
LLM transport
```

请求：

```text
POST /api/ai/chat
```

验证：

```text
200
route=rag
metadata.request_id=A
```

然后直接查询：

```text
ai_ops.rag_execution_record
```

确认：

```text
request_id=A
```

---

# 二十、Cross Request Isolation

执行：

```text
POST RAG → A
POST RAG → B
```

数据库：

```text
A → only A records
B → only B records
```

并确认：

```text
A != B
```

---

# 二十一、Non-RAG Isolation

执行 Tool：

```text
POST /api/ai/chat
```

确认：

```text
route=tool
```

并确认：

```text
ai_ops.rag_execution_record
```

没有新增 RAG record。

---

# 二十二、Legacy API

继续确认：

```text
/api/rag/answer
/api/chat
```

仍然：

```text
200
```

并且：

```text
RAG persistence records = 0
```

不要给旧 API 人为生成 request_id。

---

# 二十三、Observer Failure Isolation E2E

模拟：

```text
RagExecutionPersistenceAdapter.record()
```

抛出：

```text
RuntimeError
```

确认：

```text
POST /api/ai/chat
→ 200
→ route=rag
→ content 正常
→ metadata.request_id 存在
```

同时：

```text
warning log
```

存在。

---

# 二十四、Restart-like Persistence Test

这是本阶段重要测试。

流程：

```text
POST RAG → A
```

然后：

```text
clear InMemoryRagExecutionCollector
```

创建新的：

```text
RagExecutionPersistentQueryService
```

查询 PostgreSQL：

```text
A
```

仍然应该存在。

证明：

```text
Persistent Observation
≠
Runtime Memory
```

---

# 二十五、Assistant Trace

**绝对不要修改。**

以下保持：

```text
AssistantTraceQueryService
AssistantTraceResponse
/api/observability/assistant-trace/{request_id}
```

不读取 RAG 表。

本阶段完成后：

```text
RAG Persistence = YES
Assistant Trace = UNCHANGED
```

后续另一个独立 Step 再决定是否整合。

---

# 二十六、默认测试安全

默认：

```text
DeepSeek = 0
SiliconFlow = 0
Network = 0
```

DB-gated：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest ...
```

必须使用测试 PostgreSQL。

禁止：

```text
生产数据库
真实 WMS 数据
```

---

# 二十七、测试命令

先：

```powershell
python -m pytest -q tests/test_rag_persistent_observation_contract.py
```

然后：

```powershell
python -m pytest -q tests/test_rag_execution_persistence.py
```

然后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_rag_execution_persistence_db.py
```

然后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_rag_runtime_observability_e2e_db.py
```

再：

```powershell
python -m pytest -q
```

最后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

以及：

```powershell
python -m compileall -q backend tests
```

---

# 二十八、DB Residue

测试前后检查：

```text
ai_ops.rag_execution_record
```

要求：

```text
测试结束后 = 0
```

如果使用事务 rollback：

优先复用现有机制。

禁止：

```text
TRUNCATE
```

如果必须清理：

只允许：

```text
本阶段测试生成的 request_id
```

进行定向 DELETE。

最终报告必须：

```text
DB writes = test-only
DB residue = 0
```

---

# 二十九、Git Diff

完成后确认：

允许：

```text
RAG Persistence files
RAG Runtime Composition
DB Model
Repository
Tests
Docs
```

禁止出现：

```text
AI Router changes
AI Orchestrator changes
RAG core changes
VectorSearch changes
LLM Usage changes
Tool Execution changes
Assistant Trace changes
TextToSQL changes
```

---

# 三十、最终报告

严格按照：

```text
Phase 3.12 Step 46 COMPLETE

1. PostgreSQL Table
2. ORM Model
3. Repository
4. Persistence Service
5. Persistence Adapter
6. Composite Observer
7. Production Wiring
8. Persistence E2E
9. Cross Request Isolation
10. Non-RAG Isolation
11. Legacy API
12. Failure Isolation
13. Restart-like Test
14. Security
15. Tests
16. Full Regression
17. compileall
18. DB Residue
19. Git Diff
20. Assistant Trace
21. Current Limitations
```

最后必须明确：

```text
RAG Runtime Observation = production wired
RAG Persistence = YES
RAG Persistent Read Boundary = YES
Assistant Trace Integration = NO
RAG HTTP API = UNCHANGED
AI Router = UNCHANGED
AI Orchestrator = UNCHANGED
Text-to-SQL = UNCHANGED
DB Schema = one new rag_execution_record table
DeepSeek = 0 in tests
Network = 0 in tests
DB writes = test-only
DB residue = 0
```

然后：

# STOP

不要进入 Step 47。

不要接入 Assistant Trace。

不要开发 Conversation。

不要开发 Memory。

不要开发 Agent。

不要开发 MCP。

不要开发 OpenTelemetry。

不要开发 Dashboard。
