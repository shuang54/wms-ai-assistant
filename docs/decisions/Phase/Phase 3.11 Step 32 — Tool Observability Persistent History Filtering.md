你现在开始执行：

# Phase 3.11 Step 32 — Tool Observability Persistent History Filtering

## 一、阶段目标

在 Phase 3.11 Step 31 已完成的基础上：

**只为 Persistent Tool Execution History 增加过滤能力。**

当前 API：

```text
GET /api/observability/tools/history?limit=100&offset=0
```

Step 32 增加：

```text
project_id
tool_name
success
```

最终：

```text
GET /api/observability/tools/history
    ?limit=100
    &offset=0
    &project_id=project-a
    &tool_name=get_inventory
    &success=true
```

本阶段只解决：

> 从 Persistent Tool Execution History 中按照已有 ToolExecutionRecord 字段进行精确过滤。

---

# 二、严格范围

## 允许

允许修改：

```text
backend/app/db/tool_execution_repository.py
backend/app/services/tool_execution_persistent_query_service.py
backend/app/api/tool_observability.py
```

允许新增：

```text
tests/test_tool_observability_history_filtering_db.py
docs/evaluation/Phase 3.11.32 — Tool Observability Persistent History Filtering.md
```

允许扩展：

```text
tests/test_tool_execution_repository.py
tests/test_tool_execution_persistent_query_service.py
tests/test_tool_observability_api.py
tests/test_tool_observability_persistence_architecture.py
```

允许更新：

```text
docs/api.md
docs/architecture.md
docs/decisions/ADR-3.11.26-tool-observability-persistence-boundary.md
```

---

# 三、明确禁止

本阶段禁止修改：

```text
ToolExecutionService
ToolExecutionRecord
ToolExecutionObserver
CompositeToolExecutionObserver

ToolExecutionPersistenceAdapter
ToolExecutionPersistenceService

InMemoryToolExecutionCollector
ToolObservabilityQueryService

Tool Registry
AIOrchestrator
Router
RAG
Text-to-SQL
ORM Model
```

除非发现明确的已有 bug。

禁止：

```text
COUNT
SUM
AVG
success_rate
total_count

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

全文搜索
LIKE "%keyword%"
ILIKE
模糊匹配
正则匹配
```

禁止修改 Runtime API：

```text
GET /api/observability/tools
GET /api/observability/tools/metrics
```

禁止 Runtime → Persistent fallback。

禁止 Persistent → Runtime fallback。

禁止 Runtime / Persistent merge。

---

# 四、Filtering API

保持：

```http
GET /api/observability/tools/history
```

现有参数：

```text
limit
offset
```

新增：

```text
project_id
tool_name
success
```

例如：

```text
GET /api/observability/tools/history?project_id=project-a
```

```text
GET /api/observability/tools/history?tool_name=get_inventory
```

```text
GET /api/observability/tools/history?success=true
```

组合：

```text
GET /api/observability/tools/history
    ?project_id=project-a
    &tool_name=get_inventory
    &success=true
    &limit=20
    &offset=0
```

---

# 五、参数语义

## 1. project_id

类型：

```text
str | None
```

精确匹配：

```sql
project_id = :project_id
```

不要：

```sql
LIKE
ILIKE
```

不要自动：

```text
lower()
trim()
fuzzy match
```

保持数据库实际值语义。

---

## 2. tool_name

类型：

```text
str | None
```

精确匹配：

```sql
tool_name = :tool_name
```

例如：

```text
get_inventory
get_work_order
```

不要：

```text
contains
startswith
LIKE
ILIKE
regex
```

---

## 3. success

类型：

```text
bool | None
```

语义：

```text
success=true
    ↓
WHERE success = TRUE
```

```text
success=false
    ↓
WHERE success = FALSE
```

未提供：

```text
success=None
    ↓
不增加 WHERE 条件
```

注意：

不能把：

```text
success=false
```

当成：

```text
参数未提供
```

这是一个重要测试点。

---

# 六、参数组合

过滤条件之间使用：

```text
AND
```

例如：

```text
project_id = 'project-a'
AND tool_name = 'get_inventory'
AND success = true
```

然后：

```text
ORDER BY started_at DESC, id DESC
LIMIT :limit
OFFSET :offset
```

完整顺序保持：

```text
SELECT explicit columns
FROM ai_ops.tool_execution_record
WHERE ...
ORDER BY started_at DESC, id DESC
LIMIT ...
OFFSET ...
```

---

# 七、Repository

修改：

```text
backend/app/db/tool_execution_repository.py
```

把当前：

```text
list_recent(limit, offset)
```

扩展成最小过滤能力。

推荐逻辑：

```python
list_recent(
    *,
    limit=100,
    offset=0,
    project_id=None,
    tool_name=None,
    success=None,
)
```

但：

**先阅读当前真实 Repository 实现，再采用最小兼容修改。**

不要重构 Repository。

---

# 八、SQL 构造

继续保持：

```text
explicit columns
SELECT only
```

禁止：

```text
SELECT *
```

条件：

```text
project_id IS NOT NULL
    → project_id = :project_id

tool_name IS NOT NULL
    → tool_name = :tool_name

success IS NOT NULL
    → success = :success
```

没有条件时：

```text
WHERE
```

不要生成空 WHERE。

---

# 九、SQL Injection 安全

所有过滤值必须：

```text
bound parameters
```

禁止：

```python
f"... WHERE project_id = '{project_id}'"
```

禁止：

```python
text(...)
```

拼接用户输入。

必须验证：

```text
project_id
tool_name
```

都不会直接进入 SQL 文本。

测试：

```text
project_id = "' OR 1=1 --"
tool_name = "get_inventory' OR 1=1 --"
```

必须被当作普通字符串参数。

不能扩大查询结果。

---

# 十、Filtering + Pagination

过滤必须发生在：

```text
LIMIT/OFFSET
```

之前。

即：

```text
WHERE filters
    ↓
ORDER BY
    ↓
LIMIT
    ↓
OFFSET
```

不能：

```text
LIMIT
    ↓
OFFSET
    ↓
Python filter
```

不能在 Service / API 层：

```text
filter()
```

不能：

```text
list comprehension
```

来完成数据库过滤。

---

# 十一、PersistentQueryService

修改：

```text
backend/app/services/tool_execution_persistent_query_service.py
```

扩展：

```python
list_recent(
    *,
    limit=100,
    offset=0,
    project_id=None,
    tool_name=None,
    success=None,
)
```

职责保持：

```text
validate
    ↓
Repository
    ↓
Row → Snapshot
```

Service：

**不执行 SQL。**

**不做 Python filtering。**

**不排序。**

**不聚合。**

**不 deduplicate。**

---

# 十二、API

修改：

```text
backend/app/api/tool_observability.py
```

History endpoint 新增：

```text
project_id: str | None = None
tool_name: str | None = None
success: bool | None = None
```

继续：

```text
limit: 1~1000
offset: >=0
```

推荐保持当前项目 Query 参数风格。

FastAPI 对 query 参数可以直接进行类型转换和约束验证，并自动反映到 OpenAPI。

---

# 十三、空字符串

需要明确处理：

```text
project_id=""
tool_name=""
```

先检查项目当前 API 对字符串 Query 参数的既有处理方式。

优先采用一致行为。

如果没有现有约定：

**不要在本阶段自行引入复杂 normalization。**

建议最小语义：

```text
"" = 普通字符串值
```

即：

```sql
project_id = ''
```

而不是自动转换成：

```text
None
```

---

# 十四、不存在的 Filter 值

例如：

```text
project_id=does-not-exist
```

返回：

```json
{
  "items": [],
  "limit": 100,
  "offset": 0
}
```

HTTP：

```text
200
```

不是：

```text
404
```

因为：

```text
没有匹配记录
```

不是：

```text
资源不存在
```

---

# 十五、Filter + Pagination 核心测试

准备 synthetic 数据：

```text
A:
project-a
get_inventory
success=true

B:
project-a
get_inventory
success=false

C:
project-a
get_work_order
success=true

D:
project-b
get_inventory
success=true

E:
project-b
get_work_order
success=false
```

测试：

### project filter

```text
project_id=project-a
```

得到：

```text
A B C
```

---

### tool filter

```text
tool_name=get_inventory
```

得到：

```text
A B D
```

---

### success=true

得到：

```text
A C D
```

---

### success=false

得到：

```text
B E
```

---

### combination

```text
project_id=project-a
tool_name=get_inventory
success=true
```

得到：

```text
A
```

---

# 十六、Filter + Pagination

这是本阶段非常重要的测试。

例如：

```text
project-a
```

有：

```text
A B C D E
```

请求：

```text
limit=2
offset=0
```

得到：

```text
A B
```

请求：

```text
limit=2
offset=2
```

得到：

```text
C D
```

请求：

```text
limit=2
offset=4
```

得到：

```text
E
```

确认：

```text
filter → order → limit → offset
```

而不是：

```text
limit → filter
```

---

# 十七、Ordering

继续保持：

```text
ORDER BY started_at DESC, id DESC
```

Filtering 不能改变排序。

不要：

```text
ORDER BY project_id
```

不要：

```text
ORDER BY tool_name
```

不要：

```text
ORDER BY success
```

不要在 Python 中排序。

---

# 十八、Unit Tests

### Repository

至少增加：

```text
无 filter
project_id filter
tool_name filter
success=true
success=false
三个 filter AND
filter + limit
filter + offset
filter + limit + offset
empty result
```

---

### SQL Assertions

验证：

```text
project_id = :project_id
tool_name = :tool_name
success = :success
```

并验证：

```text
ORDER BY started_at DESC, id DESC
LIMIT
OFFSET
```

禁止：

```text
SELECT *
```

禁止：

```text
COUNT(
```

---

# 十九、SQL Injection Tests

至少：

```text
project_id="' OR 1=1 --"
tool_name="get_inventory' OR 1=1 --"
```

验证：

```text
不会扩大查询
不会执行额外 SQL
不会出现 multi-statement
```

重点验证：

```text
用户输入不进入 SQL 结构
```

---

# 二十、API Tests

至少：

```text
/history
/history?project_id=project-a
/history?tool_name=get_inventory
/history?success=true
/history?success=false
/history?project_id=project-a&tool_name=get_inventory
/history?project_id=project-a&tool_name=get_inventory&success=true
```

并继续测试：

```text
limit
offset
filter + pagination
```

---

# 二十一、Invalid Query Tests

继续保持：

```text
limit=0
limit=-1
limit=1001
limit=abc

offset=-1
offset=abc
```

全部：

```text
422
```

并且：

```text
Service = 0 calls
Repository = 0 calls
DB = 0 calls
```

---

# 二十二、Runtime Isolation

必须继续验证：

```text
GET /api/observability/tools
```

不受 Persistent Filter 影响。

例如：

```text
DB filter:
project-a
```

不能改变：

```text
Runtime Collector
```

同样：

```text
GET /api/observability/tools/metrics
```

仍然只读取：

```text
InMemoryCollector
```

---

# 二十三、DB-gated Tests

新增：

```text
tests/test_tool_observability_history_filtering_db.py
```

或者如果现有：

```text
tests/test_tool_observability_history_api_db.py
```

结构适合，则扩展它。

优先遵循当前项目测试组织方式。

使用：

```text
synthetic records only
```

禁止：

```text
真实 WMS inventory
真实 work order
生产数据
```

至少验证：

```text
project_id
tool_name
success=true
success=false
组合过滤
filter + pagination
empty result
stable ordering
SQL injection input
```

---

# 二十四、DB residue

完成 DB 测试后：

```text
ai_ops.tool_execution_record = 0
```

必须。

同时：

```text
ai_ops.llm_usage_record
```

不得修改。

不要：

```text
TRUNCATE ai_ops.llm_usage_record
```

---

# 二十五、Architecture Contract C38

新增：

```text
C38 — Persistent History Filtering Boundary
```

至少：

### C38.1

History API 支持：

```text
limit
offset
project_id
tool_name
success
```

---

### C38.2

Runtime API 不增加上述参数。

---

### C38.3

Filtering 在 Repository / SQL 层完成。

---

### C38.4

Service 不执行 Python filtering。

---

### C38.5

API 不执行 Python filtering。

---

### C38.6

过滤值必须使用 bound parameters。

---

### C38.7

过滤发生在 LIMIT/OFFSET 之前。

---

### C38.8

排序仍然：

```text
started_at DESC, id DESC
```

---

### C38.9

不允许：

```text
COUNT
SUM
AVG
Metrics
```

---

### C38.10

不允许：

```text
LIKE
ILIKE
regex
fuzzy
keyword search
```

---

### C38.11

不存在 Runtime/Persistent fallback。

---

### C38.12

Snapshot 仍严格 11 fields。

---

# 二十六、Security

Response：

```text
items
limit
offset
```

items：

```text
ToolExecutionSnapshot 11 fields
```

禁止：

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

Filtering 参数不得改变字段白名单。

---

# 二十七、文档

新增：

```text
docs/evaluation/Phase 3.11.32 — Tool Observability Persistent History Filtering.md
```

记录：

```text
1. Scope
2. Filter parameters
3. Exact-match semantics
4. Filter + pagination
5. Ordering
6. SQL safety
7. Error semantics
8. Runtime isolation
9. Tests
10. DB residue
11. Limitations
```

更新：

```text
docs/api.md
```

History API：

```text
limit
offset
project_id
tool_name
success
```

更新：

```text
docs/architecture.md
```

增加：

```text
§8.50
```

结构：

```text
Persistent History API
        ↓
PersistentQueryService
        ↓
Repository
        ↓
WHERE filters
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
Step 32 — Persistent History Filtering implemented
```

明确：

```text
Metrics = deferred
Retention = deferred
Dashboard = deferred
Prometheus = deferred
OpenTelemetry = deferred
```

---

# 二十八、不要做这些事情

绝对不要：

```text
❌ COUNT
❌ total_count
❌ SUM
❌ AVG
❌ success_rate
❌ Metrics
❌ LIKE
❌ ILIKE
❌ regex
❌ fuzzy search
❌ keyword search
❌ time-range filtering
❌ full-text search
❌ retention
❌ cleanup
❌ archive
❌ dashboard
❌ Prometheus
❌ OpenTelemetry
❌ Redis
❌ Kafka
❌ cache
❌ background worker
❌ Runtime/Persistent merge
❌ fallback
❌ 修改 Runtime API
❌ 修改 ToolExecutionService
❌ 修改 Collector
❌ 修改 Persistence Adapter
❌ 修改 Persistence Service
❌ 修改 ORM
```

本阶段只实现：

```text
Exact Filtering
+
Pagination
+
Stable Ordering
```

---

# 二十九、测试命令

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
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_tool_observability_history_filtering_db.py
```

最后：

```powershell
python -m pytest -q
```

编译：

```powershell
python -m compileall -q backend tests
```

如果已有 lint：

继续使用现有 lint。

不要新增 lint 工具。

---

# 三十、成功标准

必须：

```text
Filtering = PASS
project_id = PASS
tool_name = PASS
success=true = PASS
success=false = PASS
combination = PASS
Filtering + Pagination = PASS
Stable Ordering = PASS
SQL Injection Safety = PASS
Runtime Isolation = PASS
Architecture C38 = PASS
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

# 三十一、最终汇报格式

完成后严格：

```text
【Phase 3.11 Step 32 COMPLETE】

1. Filtering
2. API
3. Repository
4. PersistentQueryService
5. Filter semantics
6. Pagination + Filtering
7. SQL safety
8. Ordering
9. Runtime isolation
10. Architecture C38
11. Unit Tests
12. DB Tests
13. DB residue
14. 全量测试
15. compile / lint
16. API 是否变化
17. 修改文件
18. 新增文件
19. 当前限制
```

最后：

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
WHERE exact filters
      ↓
ORDER BY started_at DESC, id DESC
      ↓
LIMIT + OFFSET
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
No metrics
No fuzzy search
```

---

# 三十二、STOP

完成 Phase 3.11 Step 32 后：

**立即停止。**

不要进入 Step 33。

不要实现：

```text
Metrics
Retention
Dashboard
Prometheus
OpenTelemetry
```

等待下一步指令。
