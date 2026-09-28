你现在开始执行：

# Phase 3.11 Step 31 — Tool Observability Persistent History Pagination

## 一、阶段目标

在 Phase 3.11 Step 30 已完成的基础上：

**只为 Persistent Tool Execution History API 增加分页能力。**

当前：

```text
GET /api/observability/tools/history?limit=100
```

Step 31 目标：

```text
GET /api/observability/tools/history?limit=100&offset=0
```

形成：

```text
HTTP API
    ↓
PersistentQueryService
    ↓
Repository
    ↓
PostgreSQL
```

支持：

```text
limit
offset
```

但仍然保持：

```text
Runtime History
        ≠
Persistent History
```

不合并、不 fallback。

---

# 二、开始编码前必须先阅读

先阅读真实代码：

```text
backend/app/api/tool_observability.py

backend/app/services/tool_execution_persistent_query_service.py

backend/app/db/tool_execution_repository.py

backend/app/db/models/tool_execution_record.py

tests/test_tool_observability_api.py
tests/test_tool_execution_persistent_query_service.py
tests/test_tool_execution_repository.py
tests/test_tool_observability_history_api_db.py

tests/test_tool_observability_persistence_architecture.py

docs/architecture.md
docs/api.md
docs/decisions/ADR-3.11.26-tool-observability-persistence-boundary.md
```

重点确认当前：

```text
limit
started_at DESC
id DESC
PersistentQueryService
History API
```

的真实实现。

**不要假设接口。**

---

# 三、严格范围

## 本阶段允许

允许：

* Persistent History API 增加 offset
* Repository 增加最小分页查询能力
* PersistentQueryService 透传分页参数
* API Response 增加必要的分页元信息
* API / Service / Repository 测试
* DB-gated 分页测试
* Architecture Contract C37
* 更新 architecture / API / ADR / evaluation 文档

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

ToolExecutionPersistentQueryService 的既有职责
Tool Registry
AIOrchestrator
Router
RAG
Text-to-SQL
```

除非发现 Step 30 已存在的明确 bug。

---

禁止：

```text
Metrics
COUNT
SUM
AVG
success_rate

Filtering
project_id
tool_name
success
time range
keyword

Retention
TTL
Cleanup
Archive

Dashboard
Prometheus
OpenTelemetry

Redis
Kafka
Cache
Background Worker
Queue

Runtime / Persistent merge
Runtime fallback
Persistent fallback
```

---

# 四、分页 API

保持：

```http
GET /api/observability/tools/history
```

增加：

```text
limit
offset
```

例如：

```http
GET /api/observability/tools/history?limit=100&offset=0
```

第二页：

```http
GET /api/observability/tools/history?limit=100&offset=100
```

第三页：

```http
GET /api/observability/tools/history?limit=100&offset=200
```

---

# 五、参数契约

## limit

保持 Step 30：

```text
default = 100
min = 1
max = 1000
```

---

## offset

新增：

```text
default = 0
min = 0
```

建议：

```text
offset: int = 0
```

并使用现有 FastAPI/Pydantic Query 参数校验机制。

不要自行手写：

```python
if offset < 0:
```

如果项目现有 API 已经使用 Query：

优先保持同样风格。

---

## 非法参数

例如：

```text
limit=0
limit=-1
limit=1001

offset=-1
offset=-100

offset=abc
limit=abc
```

全部必须在进入 endpoint/service 前被拒绝。

保持：

```text
HTTP 422
```

并且：

```text
PersistentQueryService 不被调用
Repository 不被调用
DB 不被触达
```

---

# 六、Repository

在：

```text
backend/app/db/tool_execution_repository.py
```

增加最小分页能力。

例如：

```python
list_recent(
    *,
    limit: int = 100,
    offset: int = 0,
)
```

但：

**先根据当前真实 Repository API 判断最自然的兼容方式。**

不要为了分页重构 Repository。

---

# 七、分页 SQL

保持当前：

```sql
ORDER BY started_at DESC, id DESC
```

然后增加：

```sql
LIMIT :limit
OFFSET :offset
```

最终逻辑：

```text
SELECT explicit columns
FROM ai_ops.tool_execution_record
ORDER BY started_at DESC, id DESC
LIMIT :limit
OFFSET :offset
```

必须继续：

```text
SELECT only
explicit columns
```

禁止：

```text
SELECT *
```

---

# 八、排序绝对不能改变

分页最重要的一点：

**所有页面必须使用同一套确定性排序。**

保持：

```text
started_at DESC
id DESC
```

不要：

```text
ORDER BY id DESC
```

替代。

不要：

```text
ORDER BY started_at DESC
```

替代。

不要在 Service / API 层重新排序。

原因：

```text
Page 1
    ↓
Page 2
    ↓
Page 3
```

必须来自同一个稳定排序窗口。

当前已经有：

```text
started_at DESC
id DESC
```

这个设计继续保持。

---

# 九、PersistentQueryService

修改：

```text
backend/app/services/tool_execution_persistent_query_service.py
```

让：

```python
list_recent(
    *,
    limit=100,
    offset=0,
)
```

返回：

```text
list[ToolExecutionSnapshot]
```

要求：

```text
Service 不排序
Service 不过滤
Service 不聚合
Service 不 deduplicate
Service 不访问 SQLAlchemy
Service 不访问 Session
Service 不访问 ORM
```

Service 只负责：

```text
validate boundary
        ↓
Repository
        ↓
Row → Snapshot
```

---

# 十、不要引入复杂 Page DTO

本阶段不要实现：

```text
Pagination framework
Page[T]
PageRequest
PageResponse
CursorPage
```

保持简单。

推荐 Response：

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
  ],
  "limit": 100,
  "offset": 0
}
```

如果当前 API DTO 风格有更自然的设计，可以按现有风格调整。

但是：

**不要增加 total_count。**

因为：

```text
total_count
```

意味着需要额外：

```sql
COUNT(*)
```

这属于后续 Metrics / Query metadata 范围。

本阶段不要做。

---

# 十一、空页语义

例如数据库：

```text
100 records
```

请求：

```text
offset=100
limit=100
```

返回：

```json
{
  "items": [],
  "limit": 100,
  "offset": 100
}
```

必须：

```text
HTTP 200
```

不是：

```text
404
```

---

# 十二、offset 超大

例如：

```text
offset=999999999
```

只要：

```text
offset >= 0
```

就属于合法分页参数。

不要人为创建：

```text
MAX_OFFSET
```

除非当前项目已有统一约定。

本阶段不要额外引入新的全局配置。

---

# 十三、Repository Error

保持 Step 30：

```text
DB failure
    ↓
ToolExecutionRepositoryError
    ↓
PersistentQueryService
    ↓
API
    ↓
502
```

不要：

```text
DB failure
    ↓
[]
```

不要：

```text
DB failure
    ↓
Runtime Collector
```

不要：

```text
DB failure
    ↓
fallback
```

---

# 十四、Runtime API 不变

以下 API：

```http
GET /api/observability/tools
GET /api/observability/tools/metrics
```

必须完全保持原行为。

尤其：

```text
/tools
```

仍然：

```text
InMemoryCollector
```

而不是：

```text
PostgreSQL
```

---

# 十五、分页一致性测试

这是 Step 31 的核心测试。

准备至少：

```text
5 records
```

例如：

```text
A
B
C
D
E
```

按：

```text
started_at DESC
id DESC
```

排序。

请求：

```text
limit=2 offset=0
```

得到：

```text
A B
```

请求：

```text
limit=2 offset=2
```

得到：

```text
C D
```

请求：

```text
limit=2 offset=4
```

得到：

```text
E
```

验证：

```text
A B C D E
```

与一次：

```text
limit=5 offset=0
```

得到的结果一致。

---

# 十六、Tie-breaker 测试

继续保留：

```text
started_at 相同
```

的情况。

例如：

```text
record A:
started_at = T
id = 10

record B:
started_at = T
id = 11
```

必须：

```text
id=11
```

排在：

```text
id=10
```

之前。

然后分页：

```text
limit=1 offset=0
limit=1 offset=1
```

必须分别得到：

```text
id=11
id=10
```

不能出现：

```text
重复
遗漏
顺序变化
```

---

# 十七、API Unit Tests

至少增加：

### 1. default

```text
GET /history
```

验证：

```text
limit=100
offset=0
```

---

### 2. limit

```text
limit=1
limit=100
limit=1000
```

---

### 3. offset

```text
offset=0
offset=1
offset=100
```

---

### 4. limit + offset

例如：

```text
limit=2
offset=2
```

---

### 5. invalid offset

```text
offset=-1
offset=-100
offset=abc
```

必须：

```text
422
```

且：

```text
Service calls = 0
```

---

### 6. invalid limit

继续验证 Step 30：

```text
0
-1
1001
abc
```

---

### 7. empty page

```text
offset > record count
```

返回：

```text
200
items=[]
```

---

### 8. DB failure

继续验证：

```text
502
```

且：

```text
no runtime fallback
```

---

# 十八、DB-gated Tests

新增或扩展：

```text
tests/test_tool_observability_history_api_db.py
```

使用 synthetic records。

不要使用真实 WMS 数据。

至少测试：

```text
5 records
```

然后：

```text
limit=2 offset=0
limit=2 offset=2
limit=2 offset=4
```

验证：

```text
no duplicate
no missing
stable ordering
```

---

# 十九、DB Residue

测试完成：

```text
ai_ops.tool_execution_record
```

必须：

```text
residue = 0
```

并确认：

```text
ai_ops.llm_usage_record
```

没有修改。

不要：

```text
TRUNCATE ai_ops.llm_usage_record
```

---

# 二十、Architecture Contract C37

新增：

```text
C37 — Persistent History Pagination Boundary
```

至少：

### C37.1

History API 允许：

```text
limit
offset
```

---

### C37.2

History API 不允许：

```text
filter
project_id
tool_name
success
time range
keyword
```

---

### C37.3

Repository 使用：

```text
ORDER BY started_at DESC, id DESC
LIMIT
OFFSET
```

---

### C37.4

PersistentQueryService 不自行排序。

---

### C37.5

API 不自行排序。

---

### C37.6

Runtime API 不使用 Persistent Pagination。

---

### C37.7

Persistent API 不读取 Collector。

---

### C37.8

不存在 fallback。

---

### C37.9

不存在 total_count / COUNT。

---

### C37.10

不存在 Metrics aggregation。

---

# 二十一、Security

分页本身不能突破现有数据边界。

Response 仍然只能：

```text
ToolExecutionSnapshot 11 fields
```

不能出现：

```text
id
arguments
sql
prompt
LLM response
password
API key
DATABASE_URL
exception message
traceback
```

---

# 二十二、文档

新增：

```text
docs/evaluation/Phase 3.11.31 — Tool Observability Persistent History Pagination.md
```

记录：

```text
1. Scope
2. API
3. limit
4. offset
5. Ordering
6. Empty page
7. Error semantics
8. Security
9. Tests
10. DB residue
11. Limitations
```

更新：

```text
docs/api.md
```

记录：

```text
GET /api/observability/tools/history
```

现在支持：

```text
limit
offset
```

更新：

```text
docs/architecture.md
```

增加：

```text
§8.49
```

说明：

```text
Persistent History
        ↓
PersistentQueryService
        ↓
Repository
        ↓
ORDER BY started_at DESC, id DESC
        ↓
LIMIT + OFFSET
```

更新：

```text
ADR-3.11.26-tool-observability-persistence-boundary.md
```

Implementation Status：

```text
Step 31 — Persistent History Pagination implemented
```

明确：

```text
Filtering = deferred
Metrics = deferred
Retention = deferred
Dashboard = deferred
Prometheus = deferred
OpenTelemetry = deferred
```

---

# 二十三、不要做这些事情

绝对不要：

```text
❌ total_count
❌ COUNT(*)
❌ Metrics
❌ Filtering
❌ project_id filter
❌ tool_name filter
❌ success filter
❌ time range
❌ keyword
❌ Cursor Pagination
❌ Keyset Pagination
❌ Retention
❌ Cleanup
❌ Dashboard
❌ Prometheus
❌ OpenTelemetry
❌ Redis
❌ Kafka
❌ Cache
❌ 修改 Runtime API
❌ Runtime/Persistent merge
❌ fallback
❌ 修改 ToolExecutionService
❌ 修改 Collector
❌ 修改 Persistence Adapter
❌ 修改 Persistence Service
❌ 修改 ORM
```

注意：

本阶段只实现：

```text
LIMIT + OFFSET
```

不要为了“未来性能”提前实现 Cursor/Keyset。

---

# 二十四、测试命令

先：

```powershell
python -m pytest -q tests/test_tool_execution_repository.py
```

然后：

```powershell
python -m pytest -q tests/test_tool_execution_persistent_query_service.py
```

然后：

```powershell
python -m pytest -q tests/test_tool_observability_api.py
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

编译：

```powershell
python -m compileall -q backend tests
```

如果项目已经存在 lint：

继续使用现有 lint。

不要新增 lint 工具。

---

# 二十五、成功标准

必须满足：

```text
Persistent History Pagination = PASS
limit = PASS
offset = PASS
stable ordering = PASS
page consistency = PASS
empty page = PASS
DB error = PASS
security whitelist = PASS
Runtime isolation = PASS
Architecture C37 = PASS
DB residue = 0
```

并且：

```text
ToolExecutionService = unchanged
Collector = unchanged
PersistenceAdapter = unchanged
PersistenceService = unchanged
ORM = unchanged
Runtime API = unchanged
```

---

# 二十六、最终汇报格式

完成后严格按照：

```text
【Phase 3.11 Step 31 COMPLETE】

1. Pagination
2. API
3. Repository
4. PersistentQueryService
5. Ordering
6. Page consistency
7. Error semantics
8. Security
9. Architecture C37
10. Unit Tests
11. DB Tests
12. DB residue
13. 全量测试
14. compile / lint
15. API 是否变化
16. 修改文件
17. 新增文件
18. 当前限制
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
LIMIT + OFFSET
      ↓
GET /api/observability/tools/history
```

明确：

```text
Runtime ≠ Persistent
Persistent ≠ Runtime
No fallback
No merge
No total_count
No filtering
```

---

# 二十七、STOP

完成 Phase 3.11 Step 31 后：

**立即停止。**

不要进入 Step 32。

不要实现：

```text
Metrics
Filtering
Retention
Dashboard
Prometheus
OpenTelemetry
```

等待下一步指令。
