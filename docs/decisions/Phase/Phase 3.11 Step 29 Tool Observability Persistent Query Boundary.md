你现在开始执行：

# Phase 3.11 — Step 29

# Tool Observability Persistent Query Boundary

项目：

`D:\coding\ai\wms-ai-assistant`

---

# 一、阶段目标

Step 28 已经完成：

```text
AIOrchestrator
      ↓
ToolExecutionService
      ↓
ToolRegistry
      ↓
ToolResult
      ↓
ToolExecutionRecord
      ↓
CompositeObserver
      ├── InMemoryCollector
      └── PersistenceAdapter
              ↓
        PersistenceService
              ↓
        Repository
              ↓
        PostgreSQL
```

当前：

```text
Persistence = WRITE READY
```

但是：

```text
QueryService → PostgreSQL = NOT IMPLEMENTED
```

Step 29 只解决：

> **建立 Persistent Observability 的只读 Query Boundary。**

目标：

```text
PostgreSQL
    ↓
Repository
    ↓
Persistent Query Service
    ↓
Read Model / Snapshot
```

本阶段：

**不修改 HTTP API。**

---

# 二、严格范围

## 允许

新增：

```text
ToolExecutionPersistentQueryService
```

或者根据当前项目命名规范使用等价名称。

允许：

* Repository 增加最小只读查询方法
* Persistent Query Service
* DB-gated query tests
* Query DTO / Read Model
* Architecture Contracts
* Evaluation 文档
* ADR 更新

---

## 禁止

本阶段禁止：

```text
❌ 修改 ToolExecutionService
❌ 修改 ToolExecutionRecord
❌ 修改 Observer
❌ 修改 PersistenceAdapter
❌ 修改 PersistenceService
❌ 修改 InMemoryCollector
❌ 修改现有 ToolObservabilityQueryService 的 API
❌ 修改 Snapshot DTO 的既有字段
❌ 修改 Metrics DTO
❌ 修改 Serialization
❌ 修改 HTTP API
❌ 新增 History API
❌ 新增分页 API
❌ 新增过滤 API
❌ 新增 Dashboard
❌ 新增 Metrics API
❌ Retention
❌ TTL
❌ Delete / Cleanup
❌ Redis
❌ Kafka
❌ OpenTelemetry
❌ Prometheus
```

尤其：

**不要为了让数据库 Query Service 能被 API 使用，而提前修改 API。**

---

# 三、先阅读现有 Query 架构

开始编码前先阅读：

```text
backend/app/services/tool_observability_query_service.py
backend/app/services/tool_observability_snapshot.py
backend/app/services/tool_execution_record.py

backend/app/db/tool_execution_repository.py
backend/app/db/llm_usage_repository.py

backend/app/services/llm_usage_query_service.py
backend/app/services/llm_usage_analytics_service.py
```

如果项目实际名称不同，以真实代码为准。

重点理解：

```text
InMemory Query
Persistent Query
```

两者应该如何保持：

```text
Read Model compatible
```

---

# 四、核心原则

Step 29 必须建立：

```text
Runtime Query
```

与：

```text
Persistent Query
```

的明确边界。

当前：

```text
ToolObservabilityQueryService
        ↓
InMemoryCollector
```

未来：

```text
ToolObservabilityPersistentQueryService
        ↓
ToolExecutionRepository
        ↓
PostgreSQL
```

两者：

**并列存在。**

不要在本阶段强行合并。

---

# 五、不要直接让 QueryService 访问 SQLAlchemy

禁止：

```text
ToolObservabilityPersistentQueryService
        ↓
SQLAlchemy Session
```

必须：

```text
PersistentQueryService
        ↓
Repository
        ↓
SQLAlchemy
```

因此：

```text
Query Service = application read boundary
Repository = persistence boundary
```

---

# 六、Repository 查询能力

Step 27 目前只有：

```python
create()
get_by_request_id()
```

Step 29 可以增加**最小只读查询能力**。

第一版建议：

```python
list_recent(
    *,
    limit: int = 100,
) -> list[ToolExecutionRecordRow]
```

具体方法名根据项目已有 Repository 命名风格调整。

要求：

```text
ORDER BY id DESC
LIMIT N
```

或者根据当前 Record 的时间语义：

```text
ORDER BY started_at DESC, id DESC
```

必须先根据真实 schema / existing conventions 选择。

---

# 七、Limit 必须有边界

Repository 不允许：

```python
list_recent(limit=999999999)
```

必须：

```text
limit >= 1
limit <= MAX_ALLOWED_LIMIT
```

MAX_ALLOWED_LIMIT 采用项目已有查询上限，如果没有：

建议：

```text
1000
```

但：

**不要为了这个功能新增全局配置。**

可以使用模块级常量。

---

# 八、不要实现复杂 Filter

Step 29：

只支持：

```text
recent records
```

不要新增：

```text
project_id filter
tool_name filter
success filter
time range
keyword search
pagination cursor
```

这些属于后续 Query API 阶段。

---

# 九、Repository 返回 Row

继续使用：

```text
ToolExecutionRecordRow
```

禁止返回：

```text
ORM Model
```

即：

```text
Repository
    ↓
ToolExecutionRecordRow
```

而不是：

```text
Repository
    ↓
ToolExecutionRecordModel
```

---

# 十、Persistent Query Service

新增：

```text
backend/app/services/tool_execution_persistent_query_service.py
```

或者遵循现有项目自然命名。

建议：

```python
class ToolExecutionPersistentQueryService:
    def __init__(
        self,
        repository: ToolExecutionRepository | None = None,
    ):
        ...

    def list_recent(
        self,
        *,
        limit: int = 100,
    ) -> list[ToolExecutionSnapshot]:
        ...
```

如果当前架构已有更自然的 DTO，则复用。

---

# 十一、Record → Snapshot

Persistent Query Service 可以：

```text
ToolExecutionRecordRow
        ↓
ToolExecutionSnapshot
```

但必须：

**显式字段映射。**

不要：

```text
vars()
asdict()
__dict__
model_dump()
```

继续遵守 Step 24 的 Serialization / Snapshot Contract。

---

# 十二、Snapshot 字段

最终 Snapshot 仍然严格保持 Step 22：

```text
request_id
round
tool_name
started_at
finished_at
duration_ms
success
project_id
tool_call_id
error_code
error_type
```

不要因为 PostgreSQL 查询而新增：

```text
id
created_at
database_name
```

如果数据库 Primary Key `id` 对排序有用：

可以：

```text
Repository 内部使用
```

但：

**不要泄漏到当前 Snapshot API。**

---

# 十三、数据库查询安全

Persistent Query 必须：

```text
SELECT only
```

禁止：

```text
INSERT
UPDATE
DELETE
TRUNCATE
DDL
```

Repository：

```text
explicit columns
```

继续禁止：

```sql
SELECT *
```

---

# 十四、Repository 查询异常

Repository 查询失败：

```text
SQLAlchemyError
    ↓
ToolExecutionRepositoryError
```

不要：

```text
return []
```

因为：

```text
DB failure ≠ empty database
```

必须区分：

```text
成功查询，没有数据
→ []

数据库查询失败
→ RepositoryError
```

---

# 十五、Persistent Query Service Exception

Service：

```text
RepositoryError
```

是否转换异常，必须参考当前：

```text
LLM Usage Query Service
```

如果现有 Query Service 直接传播 RepositoryError：

继续保持。

如果已有统一 application exception：

复用。

不要新增一套异常体系。

---

# 十六、Security

Persistent Query Service 不得返回：

```text
tool_arguments
tool_result
sql
prompt
llm_response
api_key
password
database_url
connection_string
exception_message
traceback
```

数据库已有：

```text
11 fields + id
```

最终 Read Model 仍然：

```text
11 fields
```

---

# 十七、Ordering

必须明确数据库 Query 的稳定排序。

推荐：

```text
started_at DESC
id DESC
```

原因：

```text
started_at
```

可能相同。

Primary Key：

```text
id
```

提供 deterministic tie-breaker。

但是：

**不要把 id 暴露给 Snapshot。**

测试必须验证：

```text
同一 started_at
→ id 越大越靠前
```

---

# 十八、Empty Result

数据库没有数据：

```text
[]
```

必须：

```text
not None
```

不要：

```text
None
```

---

# 十九、Unit Tests

新增：

```text
tests/test_tool_execution_persistent_query_service.py
```

至少：

### 1

repository returns records

### 2

RecordRow → Snapshot

### 3

all 11 fields preserved

### 4

None preserved

### 5

empty result

### 6

limit default

### 7

limit boundary

### 8

invalid limit

### 9

repository error propagated / mapped according to project convention

### 10

no ORM Model returned

### 11

no JSON serialization

### 12

deterministic mapping

---

# 二十、Repository Unit Tests

扩展：

```text
tests/test_tool_execution_repository.py
```

增加：

```text
list_recent()
```

测试：

### Case A

3 rows：

```text
id=1
id=2
id=3
```

limit=2：

```text
3
2
```

### Case B

same started_at：

```text
id=10
id=11
```

结果：

```text
11
10
```

### Case C

limit=1

### Case D

limit=max

### Case E

invalid limit

### Case F

empty

---

# 二十一、DB Integration

新增：

```text
tests/test_tool_execution_persistent_query_service_db.py
```

使用：

```text
RUN_DB_TESTS=1
```

测试：

### Case 1

insert synthetic records

query recent

验证排序。

### Case 2

limit=2

只返回 2。

### Case 3

same timestamp

验证 deterministic ordering。

### Case 4

nullable fields

验证：

```text
NULL → None
```

### Case 5

empty table

返回：

```text
[]
```

### Case 6

repository query failure

验证：

```text
RepositoryError
```

---

# 二十二、禁止使用生产数据

所有 DB tests：

只能使用：

```text
synthetic ToolExecutionRecord
```

例如：

```text
request_id = "step29-test-001"
project_id = "test-project"
tool_name = "get_inventory"
```

不要：

```text
查询真实库存
查询真实工单
查询生产 WMS
```

---

# 二十三、DB Residue

测试结束：

```text
ai_ops.tool_execution_record = 0
```

不得：

```text
TRUNCATE ai_ops.llm_usage_record
```

不得删除：

```text
public.*
```

不得删除：

```text
ai_ops schema
```

---

# 二十四、Architecture Contracts

新增：

```text
C35
```

至少：

### C35.1

Persistent Query Service 可以依赖 Repository。

### C35.2

Persistent Query Service 不 import：

```text
SQLAlchemy
Session
ORM Model
```

### C35.3

Repository 可以执行 SELECT。

### C35.4

Repository Query 不执行：

```text
INSERT
UPDATE
DELETE
DDL
```

### C35.5

Persistent Query Service 不 import：

```text
Collector
```

### C35.6

Persistent Query Service 不 import：

```text
ToolExecutionService
ToolRegistry
Tool Handler
```

### C35.7

Persistent Query Service 不修改 Collector。

### C35.8

HTTP API 当前不依赖 Persistent Query Service。

### C35.9

Snapshot 字段仍然严格 11 个。

### C35.10

ORM Model 不泄漏到 Service / API。

---

# 二十五、当前 HTTP API 保持不变

必须确认：

```text
GET /api/observability/tools
GET /api/observability/tools/metrics
```

仍然：

```text
InMemoryCollector
```

不能改成：

```text
PostgreSQL
```

也不要增加：

```text
/api/observability/tools/history
```

或者：

```text
/api/observability/tools/persistent
```

---

# 二十六、为什么本阶段不修改 HTTP API

在 Evaluation 文档中明确：

```text
Runtime View
    = recent process-local observations

Persistent View
    = long-term database history
```

两者目前：

```text
并列
```

不是：

```text
fallback
```

也不是：

```text
merged
```

这样避免：

```text
Memory + DB
```

产生重复、排序、窗口、分页语义冲突。

---

# 二十七、不要实现 Metrics

本阶段禁止：

```text
Persistent Metrics
```

不要实现：

```text
COUNT
SUM
AVG
success_rate
failure_rate
```

数据库 Metrics 属于后续阶段。

本阶段只验证：

```text
DB rows → Snapshot
```

---

# 二十八、不要实现 Pagination

不要：

```text
offset
cursor
page
page_size
next_cursor
```

只允许：

```text
limit
```

并且：

```text
single query
```

---

# 二十九、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.11.29 — Tool Observability Persistent Query Boundary.md
```

内容：

## 1. Goal

## 2. Current Runtime Query

```text
QueryService
 ↓
Collector
```

## 3. Persistent Query

```text
PersistentQueryService
 ↓
Repository
 ↓
PostgreSQL
```

## 4. Read Model

```text
Row
 ↓
Snapshot
```

## 5. Ordering

## 6. Limit

## 7. Security

## 8. Error Semantics

## 9. Tests

## 10. DB Residue

## 11. HTTP API

明确：

```text
HTTP API = unchanged
```

## 12. Deferred

```text
History API
Pagination
Filtering
Metrics
Retention
Dashboard
```

---

# 三十、ADR 更新

更新：

```text
docs/decisions/ADR-3.11.26-tool-observability-persistence-boundary.md
```

Implementation Status：

```text
Step 27:
ORM + Repository = implemented

Step 28:
PersistenceService = implemented
PersistenceAdapter = implemented
Observer Integration = implemented

Step 29:
Persistent Query Boundary = implemented

HTTP History API = Deferred
Metrics = Deferred
Pagination = Deferred
Filtering = Deferred
Retention = Deferred
Dashboard = Deferred
```

不要改变 Step 26 的 Boundary Decision。

---

# 三十一、测试命令

Unit：

```powershell
python -m pytest -q tests/test_tool_execution_repository.py
```

Persistent Query Service：

```powershell
python -m pytest -q tests/test_tool_execution_persistent_query_service.py
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_tool_execution_persistent_query_service_db.py
```

Architecture：

```powershell
python -m pytest -q tests/test_tool_observability_persistence_architecture.py
```

最后：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

Compile：

```powershell
python -m compileall backend tests
```

LSP：

```text
0 diagnostics
```

---

# 三十二、成功标准

必须：

```text
✓ Repository 增加最小只读 recent query
✓ Persistent Query Service
✓ Row → Snapshot 显式映射
✓ 11 个 Snapshot 字段保持不变
✓ deterministic ordering
✓ limit 有上限
✓ empty = []
✓ DB failure ≠ empty result
✓ SELECT-only
✓ ORM 不泄漏
✓ Security contract PASS
✓ Unit tests PASS
✓ DB tests PASS
✓ C35 PASS
✓ DB residue = 0
✓ Runtime Execution 不变
✓ Observer 不变
✓ Persistence Adapter 不变
✓ HTTP API 不变
✓ 无 Metrics
✓ 无 Pagination
✓ 无 Filtering
✓ 无 Retention
```

---

# 三十三、最终报告

严格使用：

```text
【Phase 3.11 Step 29 COMPLETE】

1. Persistent Query Boundary
2. Repository Query
3. Persistent Query Service
4. Row → Snapshot
5. Ordering
6. Limit
7. Error Semantics
8. Security
9. Unit Tests
10. DB Tests
11. Architecture Contract C35
12. DB residue
13. Runtime 是否变化
14. API 是否变化
15. 新增文件
16. 修改文件
17. 当前限制
```

最后输出：

```text
Architecture:

Runtime:

ToolExecution
      ↓
ToolExecutionRecord
      ↓
CompositeObserver
      ↓
InMemoryCollector
      ↓
Runtime QueryService
      ↓
HTTP API


Persistent:

ToolExecutionRecord
      ↓
PersistenceAdapter
      ↓
PersistenceService
      ↓
Repository
      ↓
PostgreSQL
      ↓
PersistentQueryService
      ↓
ToolExecutionSnapshot
```

明确：

```text
HTTP History API = NOT IMPLEMENTED
Metrics = NOT IMPLEMENTED
Pagination = NOT IMPLEMENTED
Filtering = NOT IMPLEMENTED
Retention = NOT IMPLEMENTED
Dashboard = NOT IMPLEMENTED
```

---

# 三十四、立即停止

完成 Step 29 后：

**立即停止。**

不要进入 Step 30。

不要：

```text
修改 HTTP API
实现 History API
实现 Pagination
实现 Filtering
实现 Persistent Metrics
实现 Retention
实现 Dashboard
接入 Prometheus
接入 OpenTelemetry
```

等待下一步指令。
