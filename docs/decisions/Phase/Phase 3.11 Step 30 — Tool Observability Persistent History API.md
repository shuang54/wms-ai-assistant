你现在开始执行：

# Phase 3.11 Step 30 — Tool Observability Persistent History API

## 一、阶段目标

在 Phase 3.11 Step 29 已完成的基础上：

**只新增 Persistent Tool Execution History 的 HTTP Read API。**

本阶段目标链路：

```text
ToolExecution
      ↓
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
ToolExecutionPersistentQueryService
      ↓
ToolExecutionSnapshot
      ↓
HTTP History API
```

最终形成：

```text
Runtime History
    → GET /api/observability/tools

Persistent History
    → GET /api/observability/tools/history
```

两个 API 是：

```text
parallel
```

不是：

```text
fallback
```

不是：

```text
merge
```

不是：

```text
runtime + database dedup
```

---

# 二、开始前必须先阅读

先阅读真实代码：

```text
backend/app/api/tool_observability.py

backend/app/api/orchestrator_chat.py

backend/app/services/tool_observability_query_service.py
backend/app/services/tool_execution_persistent_query_service.py

backend/app/services/tool_observability_snapshot.py
backend/app/services/tool_observability_serialization.py

backend/app/db/tool_execution_repository.py
backend/app/db/models/tool_execution_record.py

tests/test_tool_observability_api.py
tests/test_tool_execution_persistent_query_service.py
tests/test_tool_execution_repository.py

docs/architecture.md
docs/decisions/ADR-3.11.26-tool-observability-persistence-boundary.md
docs/evaluation/
```

同时检查：

```text
backend/app/main.py
```

确认现有 Router 注册方式。

**不要假设 API / Service / DTO 接口。**

---

# 三、严格范围

## 本阶段允许

允许：

* 新增 Persistent History HTTP GET endpoint
* 新增 API Response DTO（如果现有 Snapshot 不能直接作为 response）
* 新增少量 API mapping / serialization
* 新增 API unit tests
* 新增 DB-gated API integration test
* 更新 architecture / ADR / evaluation 文档

---

## 本阶段禁止

禁止修改：

```text
ToolExecutionService
ToolExecutionRecord
ToolExecutionObserver
CompositeToolExecutionObserver
ToolExecutionPersistenceAdapter
ToolExecutionPersistenceService

InMemoryToolExecutionCollector
ToolObservabilityQueryService

ToolExecutionPersistentQueryService
ToolExecutionRepository
ToolExecutionRecord ORM Model

Tool Registry
AIOrchestrator
Router
RAG
Text-to-SQL
```

除非测试明确发现 Step 29 已存在的 bug。

如果发现现有实现缺陷：

**先停止并报告，不要为了让 API 测试通过而修改底层架构。**

---

禁止：

```text
Metrics
Pagination
Filtering
Retention
Dashboard
Prometheus
OpenTelemetry
Redis
Kafka
Background Worker
Queue
Cache
```

禁止：

```text
Runtime + Persistent merge
```

禁止：

```text
Persistent → Runtime fallback
```

禁止：

```text
Runtime → Persistent fallback
```

禁止：

```text
DB write through HTTP API
```

本 API 必须是：

```text
GET only
read only
```

---

# 四、API 设计

新增：

```http
GET /api/observability/tools/history
```

Response：

```json
{
  "items": [
    {
      "request_id": "...",
      "round": 1,
      "tool_name": "get_inventory",
      "started_at": "...",
      "finished_at": "...",
      "duration_ms": 12.34,
      "success": true,
      "project_id": "project-a",
      "tool_call_id": "...",
      "error_code": null,
      "error_type": null
    }
  ]
}
```

注意：

Response 中只能出现：

```text
ToolExecutionSnapshot 11 fields
```

以及最外层：

```text
items
```

---

# 五、API Query 参数

本阶段只允许：

```text
limit
```

例如：

```http
GET /api/observability/tools/history?limit=20
```

默认：

```text
limit = 100
```

最大：

```text
1000
```

必须与 Step 29 Persistent Query Service 的契约一致。

---

## 禁止新增：

```text
offset
cursor
page
project_id
tool_name
success
from
to
start_time
end_time
keyword
```

这些属于后续 Filtering / Pagination 阶段。

---

# 六、API → Service 边界

HTTP API：

```text
tool_observability.py
        ↓
ToolExecutionPersistentQueryService
        ↓
ToolExecutionRepository
```

API **不得直接访问**：

```text
Session
SQLAlchemy
ORM Model
Repository
```

API 不允许：

```python
session.execute(...)
```

也不允许：

```python
ToolExecutionRepository(...)
```

API 只能依赖：

```text
ToolExecutionPersistentQueryService
```

---

# 七、Response DTO

先检查当前项目 API DTO 风格。

如果项目已经存在适合的 Pydantic DTO：

优先复用。

如果没有：

新增最小 DTO，例如：

```text
ToolExecutionHistoryItemResponse
ToolExecutionHistoryResponse
```

要求：

```text
ToolExecutionHistoryItemResponse
```

严格对应：

```text
ToolExecutionSnapshot
```

11 fields：

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

不要加入：

```text
id
created_at
database_name
arguments
data
sql
prompt
llm_response
exception_message
traceback
```

---

# 八、Serialization Boundary

继续复用 Step 24：

```text
tool_observability_serialization.py
```

如果当前 API serialization 已经存在：

优先复用。

不要重新实现：

```text
datetime.isoformat()
```

不要：

```python
snapshot.__dict__
```

不要：

```python
vars(snapshot)
```

不要：

```python
dataclasses.asdict(snapshot)
```

必须继续保持：

```text
Snapshot
    ↓
explicit serialization
    ↓
API response
```

---

# 九、Persistent Query Service

Step 30：

**不得修改其内部逻辑。**

直接调用：

```python
persistent_query_service.list_recent(limit=limit)
```

例如：

```text
HTTP
 ↓
PersistentQueryService.list_recent()
 ↓
list[ToolExecutionSnapshot]
 ↓
serialization
 ↓
response
```

不要在 API 层：

```text
sort
filter
aggregate
deduplicate
paginate
```

顺序必须完全保持 Step 29 Repository 返回的：

```text
started_at DESC
id DESC
```

---

# 十、Composition Root

检查：

```text
backend/app/api/orchestrator_chat.py
```

目前已有：

```text
collector
persistence_service
persistence_adapter
composite_observer
```

Step 29 新增：

```text
PersistentQueryService
```

Step 30 需要决定：

**PersistentQueryService 应该在哪里创建。**

优先遵循当前项目：

```text
module-level composition root
```

不要：

```text
每个 HTTP request new Repository
每个 HTTP request new Service
```

推荐：

```text
module-level repository
        ↓
module-level persistent query service
        ↓
API accessor
```

如果当前 Repository 本身是轻量无状态对象，则按现有 Repository composition 风格复用。

不要新增全局 singleton framework。

---

# 十一、API Router

继续使用：

```text
backend/app/api/tool_observability.py
```

不要新建：

```text
persistent_tool_observability.py
history_api.py
tool_history.py
```

除非当前项目结构明确要求拆分。

现有：

```http
GET /api/observability/tools
GET /api/observability/tools/metrics
```

保持完全不变。

新增：

```http
GET /api/observability/tools/history
```

---

# 十二、错误处理

严格遵循现有 API error convention。

### 1. 正常

```text
200
```

即使：

```text
items = []
```

也必须：

```text
200
```

因为：

```text
empty database
```

不是异常。

---

### 2. limit 非法

例如：

```text
limit=0
limit=-1
limit=1001
limit=abc
```

必须返回项目当前约定的：

```text
4xx
```

不要让它变成：

```text
500
```

具体 HTTP status：

**先检查现有 API 对 Pydantic/query 参数错误的真实处理方式，再保持一致。**

不要自行创造新的错误格式。

---

### 3. Database failure

如果：

```text
ToolExecutionPersistentQueryService
        ↓
Repository
        ↓
DB failure
```

Repository 已经定义：

```text
ToolExecutionRepositoryError
```

API 层按照现有项目 convention 转换为内部服务错误。

不要：

```text
return []
```

不要吞掉异常。

不要：

```text
fallback → memory collector
```

这是非常重要的边界：

```text
Persistent DB failure
        ↓
HTTP error
```

而不是：

```text
Persistent DB failure
        ↓
Runtime memory fallback
```

---

# 十三、Security

API response 严格禁止泄露：

```text
API key
password
DATABASE_URL
connection string
SQL
tool arguments
ToolResult.data
LLM prompt
LLM response
exception message
traceback
ORM model
SQLAlchemy session
```

只允许：

```text
ToolExecutionSnapshot
```

11 fields。

---

# 十四、Runtime / Persistent Isolation

新增测试：

```text
test_persistent_history_api_does_not_read_memory_collector
```

构造：

```text
collector:
    record A

database:
    record B
```

调用：

```http
GET /api/observability/tools/history
```

必须：

```text
response == B
```

不能出现：

```text
A
```

---

新增测试：

```text
test_runtime_history_api_does_not_read_database
```

保证原：

```http
GET /api/observability/tools
```

仍然只读取：

```text
InMemoryToolExecutionCollector
```

不读取 PostgreSQL。

---

# 十五、API Unit Tests

至少覆盖：

### 1. Empty

```text
PersistentQueryService → []
API → 200
items == []
```

### 2. One record

```text
Service → one Snapshot
API → one item
```

### 3. Multiple records

确认：

```text
顺序完全保持 Service 返回顺序
```

### 4. limit

测试：

```text
limit=1
limit=100
limit=1000
```

---

### 5. Invalid limit

测试：

```text
0
-1
1001
abc
```

---

### 6. DB failure

Mock：

```text
ToolExecutionRepositoryError
```

确认：

```text
不会 fallback 到 collector
```

---

### 7. Security whitelist

Response 不得出现：

```text
id
arguments
sql
prompt
database_url
exception_message
```

---

### 8. Runtime isolation

验证：

```text
persistent endpoint != runtime collector
```

---

# 十六、DB-gated API E2E

新增：

```text
tests/test_tool_observability_history_api_db.py
```

只在：

```text
RUN_DB_TESTS=1
```

执行。

使用：

```text
synthetic ToolExecutionRecord
```

例如：

```text
request_id = step30-test-001
tool_name = get_inventory
project_id = test-project
```

禁止：

```text
真实 WMS 数据
```

---

测试：

```text
INSERT synthetic record
        ↓
GET /api/observability/tools/history
        ↓
assert response
```

验证：

```text
request_id
round
tool_name
project_id
success
duration_ms
timestamps
```

---

# 十七、DB residue

测试完成后：

```text
ai_ops.tool_execution_record
```

必须：

```text
residue = 0
```

同时确认：

```text
ai_ops.llm_usage_record
```

没有被修改。

禁止：

```text
TRUNCATE ai_ops.llm_usage_record
```

---

# 十八、Architecture Contract C36

新增：

```text
C36 — Persistent History HTTP API Boundary
```

至少包含：

### C36.1

HTTP API 可以依赖：

```text
ToolExecutionPersistentQueryService
```

### C36.2

HTTP API 不允许 import：

```text
SQLAlchemy
Session
ToolExecutionRecordModel
ToolExecutionRepository
```

### C36.3

HTTP API 不允许依赖：

```text
ToolExecutionService
ToolRegistry
Tool Handler
```

### C36.4

Persistent History API 不允许 import：

```text
InMemoryToolExecutionCollector
ToolObservabilityQueryService
```

### C36.5

Persistent History API 不修改 Runtime API 行为。

### C36.6

Response 只能暴露 Snapshot 11 fields。

### C36.7

不存在 Runtime → Persistent fallback。

### C36.8

不存在 Persistent → Runtime fallback。

### C36.9

API 不允许：

```text
POST
PUT
PATCH
DELETE
```

### C36.10

Persistent History API 只能通过：

```text
PersistentQueryService
```

读取。

---

# 十九、API Contract

必须保持：

```text
GET /api/observability/tools
```

不变。

```text
GET /api/observability/tools/metrics
```

不变。

新增：

```text
GET /api/observability/tools/history
```

不要修改：

```text
main.py
```

除非当前 Router registration 的确需要修改。

如果 `tool_observability.py` 已经注册，则不需要新 Router。

---

# 二十、文档

新增：

```text
docs/evaluation/Phase 3.11.30 — Tool Observability Persistent History API.md
```

记录：

```text
1. Scope
2. Endpoint
3. Runtime vs Persistent
4. Request
5. Response
6. Error semantics
7. Security boundary
8. Tests
9. DB residue
10. Current limitations
```

更新：

```text
docs/architecture.md
```

增加：

```text
§8.48
```

内容重点：

```text
Runtime History API
        ↓
InMemory QueryService

Persistent History API
        ↓
Persistent QueryService
        ↓
Repository
        ↓
PostgreSQL
```

明确：

```text
parallel
not fallback
not merged
```

更新：

```text
ADR-3.11.26-tool-observability-persistence-boundary.md
```

Implementation Status：

```text
Step 30 — Persistent History HTTP API implemented
```

并明确：

```text
Metrics = deferred
Pagination = deferred
Filtering = deferred
Retention = deferred
Dashboard = deferred
Prometheus = deferred
OpenTelemetry = deferred
```

---

# 二十一、不要做这些事情

本阶段绝对不要：

```text
❌ Metrics API
❌ Persistent metrics SQL
❌ COUNT
❌ SUM
❌ AVG
❌ success_rate
❌ Pagination
❌ Offset
❌ Cursor
❌ project filter
❌ tool filter
❌ success filter
❌ time filter
❌ keyword search
❌ retention
❌ cleanup
❌ dashboard
❌ Redis
❌ Kafka
❌ Prometheus
❌ OpenTelemetry
❌ Runtime/Persistent merge
❌ Runtime fallback
❌ Persistent fallback
❌ 修改 ToolExecutionService
❌ 修改 Collector
❌ 修改 Persistence Adapter
❌ 修改 Persistence Service
❌ 修改 Repository 查询逻辑
❌ 修改 ORM
```

---

# 二十二、测试命令

先运行：

```powershell
python -m pytest -q tests/test_tool_observability_api.py
```

然后：

```powershell
python -m pytest -q tests/test_tool_execution_persistent_query_service.py
```

然后：

```powershell
python -m pytest -q tests/test_tool_observability_persistence_architecture.py
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_tool_observability_history_api_db.py
```

最后：

```powershell
python -m pytest -q
```

以及：

```powershell
python -m compileall backend tests
```

如果项目存在 lint：

继续使用现有 lint。

不要为了本阶段新增 lint 工具。

---

# 二十三、成功标准

必须满足：

```text
Persistent History API = PASS
Runtime History API = PASS
Runtime/Persistent isolation = PASS
Security whitelist = PASS
DB error semantics = PASS
Architecture C36 = PASS
DB residue = 0
```

并且：

```text
ToolExecutionService = unchanged
Collector = unchanged
PersistenceAdapter = unchanged
PersistenceService = unchanged
Repository query semantics = unchanged
ORM = unchanged
```

---

# 二十四、最终汇报格式

完成后严格按照：

```text
【Phase 3.11 Step 30 COMPLETE】

1. Persistent History API
2. Endpoint
3. Response DTO
4. Runtime / Persistent Isolation
5. Error Semantics
6. Security
7. Architecture C36
8. Unit Tests
9. DB Tests
10. DB residue
11. 全量测试
12. compile / lint
13. API 是否变化
14. 修改文件
15. 新增文件
16. 当前限制
```

最后输出：

```text
Architecture:

Runtime:

ToolExecution
      ↓
InMemoryCollector
      ↓
Runtime QueryService
      ↓
GET /api/observability/tools


Persistent:

ToolExecution
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
GET /api/observability/tools/history
```

明确：

```text
Runtime ≠ Persistent
Persistent ≠ Runtime
No fallback
No merge
```

---

# 二十五、STOP

完成 Phase 3.11 Step 30 后：

**立即停止。**

不要进入 Step 31。

不要实现：

```text
Metrics
Pagination
Filtering
Retention
Dashboard
Prometheus
OpenTelemetry
```

等待下一步指令。
