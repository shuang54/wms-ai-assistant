你现在开始执行：

# Phase 3.11 Step 33：Tool Observability Persistent Metrics

## 一、阶段目标

在 Step 32 已经完成：

```text
Persistent History
    ↓
limit / offset
    ↓
project_id / tool_name / success 精确过滤
```

的基础上，只增加：

> **Persistent Tool Execution Metrics Read Model**

目标：

让系统能够从 PostgreSQL 中只读计算 Tool Execution 的基础统计指标。

本阶段仍然属于：

```text
Tool Observability
```

的收尾工作。

**不要进入 Dashboard、Prometheus、OpenTelemetry、Retention。**

---

# 二、严格范围

允许修改：

```text
backend/app/db/tool_execution_repository.py
backend/app/services/tool_execution_persistent_query_service.py
backend/app/api/tool_observability.py

tests/test_tool_execution_repository.py
tests/test_tool_execution_persistent_query_service.py
tests/test_tool_observability_api.py
tests/test_tool_observability_persistence_architecture.py
```

允许新增：

```text
tests/test_tool_observability_persistent_metrics_db.py

docs/evaluation/Phase 3.11.33 — Tool Observability Persistent Metrics.md
```

如果当前项目已有更合适的测试文件结构，可以遵循现有结构。

不要为了凑文件新增无意义代码。

---

# 三、明确禁止

本阶段禁止修改：

```text
ToolExecutionService
ToolExecutionRecord
ToolExecutionContext
ToolExecutionObserver
CompositeToolExecutionObserver
PersistenceAdapter
PersistenceService
InMemoryCollector
ToolObservabilityQueryService
ToolExecutionSnapshot
ToolExecution ORM Model
Tool Execution API runtime behavior
AIOrchestrator
ToolRegistry
Router
RAG
Text-to-SQL
LLM Usage
```

禁止：

```text
Dashboard
Prometheus
OpenTelemetry
Grafana
Redis
Kafka
background worker
Celery
定时任务
Retention
TTL
Cleanup
Archive
```

禁止新增数据库表。

禁止数据库写操作。

禁止修改现有历史 baseline。

---

# 四、Metrics DTO

新增一个 Persistent Metrics Read Model。

如果项目已经存在：

```text
ToolExecutionMetricsSnapshot
```

可以复用现有 DTO，但必须确认它的语义。

不要让 Persistent Metrics 和 Runtime Metrics 混淆。

建议最终 Persistent Metrics 至少包含：

```text
total_count
success_count
failure_count

success_rate
failure_rate

total_duration_ms
average_duration_ms
max_duration_ms
```

一共：

```text
8 fields
```

如果项目已有完全一致的 frozen DTO：

**直接复用，不重复创建。**

---

# 五、Metrics 计算语义

使用 PostgreSQL 聚合。

统计：

```text
total_count
    = COUNT(*)

success_count
    = COUNT(*) FILTER (WHERE success = true)

failure_count
    = COUNT(*) FILTER (WHERE success = false)

total_duration_ms
    = SUM(duration_ms)

average_duration_ms
    = AVG(duration_ms)

max_duration_ms
    = MAX(duration_ms)
```

然后：

```text
success_rate
    = success_count / total_count

failure_rate
    = failure_count / total_count
```

要求：

### 空数据

如果：

```text
total_count == 0
```

则：

```text
success_rate = None
failure_rate = None
average_duration_ms = None
max_duration_ms = None
```

不要返回：

```text
0
```

冒充真实统计结果。

---

# 六、Filter

Persistent Metrics 必须支持与 Step 32 History 一致的三个精确过滤条件：

```text
project_id
tool_name
success
```

例如：

```text
GET /api/observability/tools/metrics/persistent
```

支持：

```text
?project_id=project-a
?tool_name=get_inventory
?success=true
```

也支持组合：

```text
?project_id=project-a&tool_name=get_inventory&success=true
```

过滤语义必须与 History 完全一致：

```text
project_id → exact equality
tool_name  → exact equality
success    → true / false exact equality
None       → 不追加 WHERE
```

禁止：

```text
LIKE
ILIKE
contains
startswith
regex
fuzzy
```

不要增加：

```text
time range
keyword
search
```

---

# 七、Repository

在：

```text
backend/app/db/tool_execution_repository.py
```

增加一个专门的 metrics query。

例如：

```text
build_metrics_select(
    *,
    project_id=None,
    tool_name=None,
    success=None,
)
```

以及：

```text
get_metrics(
    *,
    project_id=None,
    tool_name=None,
    success=None,
)
```

具体命名根据当前 Repository 风格调整。

要求：

1. 只读 SQL。
2. SQLAlchemy 聚合。
3. 所有过滤值使用 bound parameters。
4. 不使用字符串拼接。
5. 不使用 `text()` 拼接用户输入。
6. 不查询 ORM Entity。
7. 不暴露 SQLAlchemy Result。
8. 返回专用 immutable Row / DTO。
9. 不进行 Python 全量扫描后再统计。

---

# 八、SQL 结构

SQL 逻辑必须类似：

```text
SELECT
    COUNT(*) AS total_count,
    COUNT(*) FILTER (WHERE success = true) AS success_count,
    COUNT(*) FILTER (WHERE success = false) AS failure_count,
    SUM(duration_ms) AS total_duration_ms,
    AVG(duration_ms) AS average_duration_ms,
    MAX(duration_ms) AS max_duration_ms
FROM ai_ops.tool_execution_record
WHERE ...
```

如果没有过滤：

```text
不要生成 WHERE
```

如果有多个过滤：

```text
WHERE project_id = :project_id
  AND tool_name = :tool_name
  AND success = :success
```

不要：

```text
WHERE 1=1
```

也不要把过滤条件拼成字符串。

---

# 九、Rate 计算

优先在 SQL 中完成基础聚合，在 Service 层完成：

```text
success_rate
failure_rate
```

原因：

保持：

```text
Repository
    ↓
raw aggregate
    ↓
PersistentQueryService
    ↓
Metrics DTO
```

Repository 不应该知道 API response。

如果当前项目已有明确的 Metrics DTO / Metrics Service 约定，可以遵循现有设计。

---

# 十、PersistentQueryService

在：

```text
backend/app/services/tool_execution_persistent_query_service.py
```

增加：

```text
metrics(...)
```

职责：

```text
validate filters
    ↓
Repository.get_metrics()
    ↓
aggregate row
    ↓
calculate rates
    ↓
return Metrics DTO
```

禁止：

```text
Python filtering
Python sorting
Python full-record aggregation
ORM access
DB session access
```

Service 不直接创建数据库连接。

---

# 十一、API

新增一个独立的 Persistent Metrics endpoint：

```text
GET /api/observability/tools/metrics/persistent
```

Query 参数：

```text
project_id: str | None
tool_name: str | None
success: bool | None
```

不要增加：

```text
limit
offset
```

因为 Metrics 是聚合结果，不是 History 列表。

FastAPI 的 typed query parameters 会自动进行类型转换和校验，并进入 OpenAPI，因此继续使用当前项目已有的 query parameter 风格。

---

# 十二、API Response

Response 只返回 Metrics 需要的字段：

```json
{
  "total_count": 10,
  "success_count": 8,
  "failure_count": 2,
  "success_rate": 0.8,
  "failure_rate": 0.2,
  "total_duration_ms": 1200,
  "average_duration_ms": 120.0,
  "max_duration_ms": 300.0
}
```

如果没有数据：

```json
{
  "total_count": 0,
  "success_count": 0,
  "failure_count": 0,
  "success_rate": null,
  "failure_rate": null,
  "total_duration_ms": null,
  "average_duration_ms": null,
  "max_duration_ms": null
}
```

不要把：

```text
id
request_id
tool_call_id
args
SQL
LLM response
```

放进 Metrics。

---

# 十三、Runtime Metrics 与 Persistent Metrics 必须继续隔离

当前：

```text
GET /api/observability/tools/metrics
```

仍然表示：

```text
InMemoryCollector Metrics
```

不要修改。

新增：

```text
GET /api/observability/tools/metrics/persistent
```

表示：

```text
PostgreSQL Persistent Metrics
```

明确保持：

```text
Runtime Metrics ≠ Persistent Metrics
```

禁止：

```text
Runtime + Persistent merge
```

禁止：

```text
Persistent unavailable → Runtime fallback
```

如果 PostgreSQL 失败：

```text
HTTP 502
```

不要返回内存 metrics。

---

# 十四、参数校验

继续沿用 Step 32：

```text
project_id:
    None → no filter
    str  → exact filter
    non-str → ValueError / 422

tool_name:
    None → no filter
    str  → exact filter
    non-str → ValueError / 422

success:
    None → no filter
    True → success = true
    False → success = false
    non-bool → ValueError / 422
```

特别测试：

```text
success=false
```

不能被错误当成：

```text
success omitted
```

空字符串：

```text
project_id=""
```

仍然是合法字面值。

不要自动转换：

```text
"" → None
```

---

# 十五、Repository Tests

至少增加：

### Empty

```text
empty table
→ all counts 0
→ rates None
→ duration aggregate None
```

### All success

```text
3 records
→ total=3
→ success=3
→ failure=0
→ success_rate=1.0
→ failure_rate=0.0
```

### Mixed

```text
3 success
2 failure
→ total=5
→ success=3
→ failure=2
→ rates=0.6 / 0.4
```

### Filters

分别测试：

```text
project_id
tool_name
success=true
success=false
```

以及：

```text
project_id + tool_name
project_id + success
tool_name + success
project_id + tool_name + success
```

---

# 十六、Aggregation Tests

测试：

```text
duration_ms:
100
200
300
```

得到：

```text
total_duration_ms = 600
average_duration_ms = 200
max_duration_ms = 300
```

不要依赖浮点精度进行脆弱断言。

允许合理的：

```text
pytest.approx(...)
```

---

# 十七、SQL Security Tests

必须验证：

过滤值：

```text
"' OR 1=1 --"
```

不会造成：

```text
SQL injection
```

例如：

```text
project_id="' OR 1=1 --"
```

应该：

```text
total_count = 0
```

而不是返回全部数据。

验证：

```text
compile().params
```

中存在 bound parameter。

SQL 文本中不能出现：

```text
OR 1=1
```

不要出现：

```text
;
```

多语句。

---

# 十八、API Tests

至少：

```text
GET /metrics/persistent
```

测试：

1. no filter
2. project_id
3. tool_name
4. success=true
5. success=false
6. combination
7. empty result
8. invalid success
9. injection string
10. DB failure → 502
11. unexpected failure → 500
12. response schema
13. no sensitive fields
14. runtime metrics unchanged

---

# 十九、DB-gated Tests

新增：

```text
tests/test_tool_observability_persistent_metrics_db.py
```

使用 synthetic records。

不要读取真实 WMS 数据。

测试矩阵：

```text
A:
project-a / get_inventory / success

B:
project-a / get_inventory / failure

C:
project-a / get_work_order / success

D:
project-b / get_inventory / success

E:
project-b / get_work_order / failure
```

验证：

```text
all
project-a
project-b
get_inventory
get_work_order
success=true
success=false
project-a + get_inventory
project-a + success=true
project-b + get_work_order + success=false
```

最终检查：

```sql
SELECT COUNT(*)
FROM ai_ops.tool_execution_record;
```

必须：

```text
0
```

测试必须 rollback / cleanup。

不要：

```text
TRUNCATE
```

不要删除真实数据。

---

# 二十、Architecture Contract C39

新增：

```text
C39
```

至少保证：

### C39.1

Persistent Metrics API 存在。

### C39.2

Runtime Metrics API 不改变。

### C39.3

Persistent Metrics 只依赖 PersistentQueryService。

### C39.4

PersistentQueryService 不访问 ORM / Session。

### C39.5

Repository 执行 SQL aggregation。

### C39.6

过滤发生在 SQL 层。

### C39.7

没有 Python 全量 aggregation。

### C39.8

没有：

```text
COUNT(*) 之外的无关查询
```

### C39.9

没有：

```text
LIKE
ILIKE
regex
fuzzy
```

### C39.10

没有：

```text
fallback
merge
runtime metrics
```

### C39.11

Metrics response 不包含 Snapshot 之外的敏感字段。

### C39.12

Persistent Metrics 不支持：

```text
limit
offset
```

---

# 二十一、Docs

更新：

```text
docs/api.md
docs/architecture.md
docs/decisions/ADR-3.11.26-...
```

新增：

```text
docs/evaluation/Phase 3.11.33 — Tool Observability Persistent Metrics.md
```

明确说明：

```text
Runtime Metrics
    ↓
InMemoryCollector

Persistent Metrics
    ↓
PostgreSQL
```

以及：

```text
Runtime ≠ Persistent
No fallback
No merge
```

---

# 二十二、测试命令

先运行：

```powershell
python -m pytest tests/test_tool_execution_repository.py -q
python -m pytest tests/test_tool_execution_persistent_query_service.py -q
python -m pytest tests/test_tool_observability_api.py -q
python -m pytest tests/test_tool_observability_persistence_architecture.py -q
```

然后：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

编译：

```powershell
python -m compileall -q backend tests
```

---

# 二十三、失败处理

如果发现：

```text
Repository
Service
API
```

已有真实 bug：

不要扩大范围。

先记录：

```text
问题：
影响：
根因：
是否属于 Step 33：
```

只有 Step 33 必需的最小修复才允许修改。

不要修改：

```text
ToolExecutionService
AIOrchestrator
ToolRegistry
```

---

# 二十四、完成标准

必须达到：

```text
Persistent Metrics = PASS

Filters = PASS
Aggregation = PASS
Security = PASS
Runtime Isolation = PASS
DB Tests = PASS
DB writes = 0
compileall = PASS
LSP = 0 diagnostics
```

如果 lint 不存在：

明确：

```text
lint unavailable
```

不要安装新工具。

---

# 二十五、最终报告格式

完成后严格报告：

```text
【Phase 3.11 Step 33 COMPLETE】

1. Persistent Metrics
2. Metrics DTO
3. Repository
4. PersistentQueryService
5. API
6. Filtering
7. Runtime / Persistent Isolation
8. Security
9. Architecture C39
10. Unit Tests
11. DB Tests
12. 全量测试
13. compile / LSP / lint
14. DB residue
15. 修改文件
16. 新增文件
17. 当前限制
18. Architecture
```

最后：

```text
Runtime:

ToolExecution
    ↓
InMemoryCollector
    ↓
Runtime Metrics
    ↓
GET /api/observability/tools/metrics


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
Persistent Metrics
    ↓
PersistentQueryService
    ↓
GET /api/observability/tools/metrics/persistent
```

然后：

**立即停止。**

不要进入 Step 34。

不要开发：

```text
Dashboard
Prometheus
OpenTelemetry
Retention
TTL
Cleanup
Grafana
Kafka
Redis
```

等待下一步指令。
