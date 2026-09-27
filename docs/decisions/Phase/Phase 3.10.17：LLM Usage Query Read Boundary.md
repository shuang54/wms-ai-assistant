你现在开始实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.10.17：LLM Usage Query Read Boundary

## 一、阶段目标

本阶段开始建立：

**LLM Usage Persistence 的只读查询边界。**

Phase 3.10.14～3.10.16 已经完成：

```text
LLM
 ↓
Observation
 ↓
Accounting
 ↓
Runtime Bridge
 ↓
Persistence
 ↓
Idempotency
 ↓
PostgreSQL
```

现在增加：

```text
PostgreSQL
 ↓
LLMUsageRepository
 ↓
LLMUsageQueryService
 ↓
Usage Query DTO
```

最终形成：

```text
                 ┌── Write
LLM Request ─────┤
                 │
                 └── Observation
                         ↓
                     Accounting
                         ↓
                     Persistence
                         ↓
                    PostgreSQL
                         ↑
                         │
                   Query Service
                         ↑
                         │
                    Query DTO
```

本阶段不建立 HTTP API。

不建立 Dashboard。

不建立 Billing。

不建立 Metrics Platform。

---

# 二、核心目标

本阶段只解决：

> **应用内部如何安全、稳定、可测试地读取 `ai_ops.llm_usage_record`。**

需要形成两个边界：

```text
Repository
```

负责：

```text
SQL / Session / DB
```

以及：

```text
Query Service
```

负责：

```text
查询参数
↓
Repository
↓
安全 DTO
```

最终业务层不能直接：

```text
Session.query()
Session.execute()
ORM Model
```

访问 Usage Persistence。

---

# 三、开始编码前必须先阅读

不要假设当前接口。

先阅读：

```text
backend/app/db/models/llm_usage_record.py
backend/app/db/llm_usage_repository.py
backend/app/services/llm_usage_persistence_service.py
backend/app/services/llm_usage_persistence_runtime.py
```

同时阅读：

```text
backend/app/db/
backend/app/services/
backend/app/llm/
```

寻找项目当前已有的：

```text
Repository
Query Service
DTO
Filter
Pagination
```

实现模式。

重点确认：

1. Session factory 的现有模式
2. Repository Error 的现有模式
3. DTO 使用 dataclass / Pydantic / frozen dataclass 的方式
4. 当前项目是否已有分页 DTO
5. 当前项目是否已有 QueryFilter
6. 当前项目是否已有时间范围类型
7. 当前项目的 timezone 约定
8. 当前测试 Fake Session / Fake Repository 模式

**优先复用已有模式。**

不要为了 Usage Query 新造一套完全不同的 Repository Framework。

---

# 四、严格范围

## 允许

允许：

```text
新增 Usage Query DTO
新增 Usage Query Filter
新增 Repository 查询方法
新增 Query Service
新增单元测试
新增 PostgreSQL 集成测试
新增 evaluation 文档
修改 docs/architecture.md
```

必要时允许：

```text
修改 llm_usage_repository.py
```

但只增加：

```text
READ / SELECT
```

相关能力。

## 禁止

不要修改：

```text
AI Router
AI Orchestrator
RAG
Tool Framework
Text-to-SQL
SQL Validator
SQL Executor
RelevantTableSelector
Business Semantic
Schema Explorer
Project Context
Prompt
LLM Observation
LLM Accounting
Persistence Runtime
Persistence Idempotency
```

不要增加：

```text
FastAPI API
HTTP Endpoint
Dashboard
Frontend
Billing
Cost Aggregation
Metrics Platform
Tracing Platform
Redis
Kafka
Queue
Worker
Outbox
Celery
```

不要新增数据库表。

不要修改：

```text
public schema
```

不要把：

```text
ai_ops.llm_usage_record
```

移动到：

```text
public
```

---

# 五、当前数据库结构

继续使用：

```text
ai_ops.llm_usage_record
```

字段：

```text
id
request_id
provider
model
prompt_tokens
completion_tokens
total_tokens
created_at
```

本阶段：

**不要增加字段。**

---

# 六、Query DTO

不要直接把：

```python
LLMUsageRecord
```

暴露给上层。

新增一个只读 DTO，例如：

```text
LLMUsageRecordView
```

或者遵循项目现有命名规范。

DTO 至少包含：

```text
id
request_id
provider
model
prompt_tokens
completion_tokens
total_tokens
created_at
```

要求：

```text
immutable / frozen
```

如果项目现有 DTO 使用 Pydantic，则遵循现有模式。

如果项目现有 DTO 使用 frozen dataclass，则继续使用。

**不要为了这个阶段引入新的 DTO framework。**

---

# 七、安全 DTO 边界

Query DTO 只能包含：

```text
id
request_id
provider
model
prompt_tokens
completion_tokens
total_tokens
created_at
```

不得出现：

```text
prompt
messages
response
SQL
RAG context
tool arguments
API key
password
authorization
database URL
connection
Session
Engine
cost
price
currency
```

尤其注意：

**不能把 ORM Model 原对象直接返回给上层。**

---

# 八、Query Filter

建立最小 Query Filter。

建议支持：

```text
request_id
provider
model
created_at_from
created_at_to
limit
offset
```

全部可选。

概念：

```text
UsageQueryFilter(
    request_id=None,
    provider=None,
    model=None,
    created_at_from=None,
    created_at_to=None,
    limit=50,
    offset=0,
)
```

具体类型必须根据当前项目已有规范决定。

---

# 九、不要增加复杂查询条件

本阶段暂时不要支持：

```text
user_id
project_id
tenant_id
session_id
trace_id
status
cost
currency
token_range
```

因为当前 `llm_usage_record` 本身没有这些字段。

不要为了 Query 而修改数据库结构。

---

# 十、默认查询行为

如果：

```text
request_id = None
provider = None
model = None
created_at_from = None
created_at_to = None
```

则查询：

```text
全部 Usage Records
```

但是必须有：

```text
ORDER BY created_at DESC, id DESC
```

确保结果稳定。

SQLAlchemy 官方文档说明，带 `LIMIT/OFFSET` 的查询如果没有稳定的 `ORDER BY`，返回顺序可能是不确定的，因此本阶段分页查询必须明确排序。

---

# 十一、分页

本阶段使用简单：

```text
limit
offset
```

即可。

例如：

```text
limit = 50
offset = 0
```

表示：

```text
第一页
```

下一页：

```text
limit = 50
offset = 50
```

SQLAlchemy 的 `Select.limit()` / `Select.offset()` 正好提供这一能力。

---

# 十二、分页参数安全边界

必须防止：

```text
limit = -1
offset = -1
```

以及异常大：

```text
limit = 999999999
```

建议：

```text
limit:
1 ~ 100
```

默认：

```text
50
```

`offset`：

```text
>= 0
```

最大值是否限制，需要根据项目已有规范决定。

如果项目没有统一规范，可以采用：

```text
offset >= 0
```

并避免额外设计复杂分页系统。

---

# 十三、时间范围

支持：

```text
created_at_from
created_at_to
```

语义：

```text
created_at >= created_at_from
created_at <= created_at_to
```

如果项目已有：

```text
datetime
timezone-aware datetime
```

规范：

**严格复用。**

不要在本阶段设计新的时间类。

---

# 十四、时间范围非法情况

至少测试：

```text
created_at_from > created_at_to
```

必须：

```text
reject
```

不要：

```text
自动交换
```

不要：

```text
自动修正
```

不要：

```text
静默返回空结果
```

错误应该在 Query Service / Filter validation 边界被明确处理。

---

# 十五、Repository

当前：

```text
backend/app/db/llm_usage_repository.py
```

已经负责：

```text
INSERT
```

本阶段增加只读方法。

例如：

```text
get_by_request_id()
list()
```

或者根据当前项目命名规范设计。

Repository 负责：

```text
SQL
Session
Result
ORM → raw record
```

Repository 不负责：

```text
业务规则
DTO
HTTP
权限
Dashboard
统计
```

---

# 十六、get_by_request_id

实现：

```text
get_by_request_id(request_id)
```

语义：

```text
存在
 ↓
return record

不存在
 ↓
return None
```

因为 Phase 3.10.16 已经保证：

```text
request_id IS NOT NULL
```

具有：

```text
UNIQUE
```

所以：

```text
request_id
```

最多只能对应：

```text
1 row
```

不要：

```text
.first()
```

之后默默忽略多个结果。

应该利用数据库 Contract。

---

# 十七、list 查询

实现：

```text
list(...)
```

支持：

```text
request_id
provider
model
created_at_from
created_at_to
limit
offset
```

组合过滤。

例如：

```text
provider = deepseek
model = deepseek-chat
```

应该生成：

```text
WHERE provider = ...
  AND model = ...
```

而不是在 Python 中：

```text
SELECT all
↓
Python filter
```

必须：

**让 PostgreSQL 执行过滤。**

---

# 十八、不要 SELECT *

建议显式选择字段：

```text
id
request_id
provider
model
prompt_tokens
completion_tokens
total_tokens
created_at
```

不要：

```sql
SELECT *
```

这样未来数据库增加敏感字段时：

**Query Boundary 不会自动泄露新字段。**

---

# 十九、不要返回 ORM Object

禁止：

```python
return session.execute(...).scalar_one()
```

然后直接：

```text
ORM → Service
```

Query Repository 可以内部使用 ORM。

但是 Repository 对 Service 返回的应该是：

```text
Repository Record
```

或者项目已有的内部 DTO。

最终：

```text
Query Service
```

转换成：

```text
LLMUsageRecordView
```

---

# 二十、Query Service

新增：

```text
backend/app/services/llm_usage_query_service.py
```

职责：

```text
validate filter
      ↓
Repository
      ↓
Record
      ↓
DTO
```

不要：

```text
Service
 ↓
Session
```

Service 不应该直接创建：

```text
SQLAlchemy Session
```

Session 仍然由 Repository 管理。

---

# 二十一、Query Service 不做统计

本阶段：

```text
list()
get()
```

可以。

但：

```text
sum_tokens()
average_tokens()
cost()
daily_usage()
monthly_usage()
```

全部禁止。

原因：

这是：

```text
Analytics / Metrics
```

不是：

```text
Read Boundary
```

后续单独阶段再做。

---

# 二十二、Query Service 不计算 Cost

虽然项目已有：

```text
LLMCost
```

但是本阶段不要：

```text
Usage Query
 ↓
Pricing
 ↓
Cost
```

因为：

```text
Persistence
```

目前明确没有保存：

```text
price
currency
cost
```

本阶段保持：

```text
Usage Query = Usage Query
```

---

# 二十三、Query Service 不做权限系统

暂时不要增加：

```text
user permission
role
tenant
project ACL
```

因为当前目标只是：

**内部 Usage Read Boundary。**

未来接 API 时再建立权限边界。

---

# 二十四、Repository Session Isolation

继续保持 Phase 3.10.15 的：

```text
Session factory
        ↓
Repository operation
        ↓
close
```

不要创建：

```text
global Session
```

不要创建：

```text
module-level Connection
```

不要创建：

```text
global transaction
```

测试必须验证：

```text
5 query operations
↓
5 independent sessions
```

---

# 二十五、异常处理

保持现有 Repository Error 模式。

数据库异常：

```text
SQLAlchemy / DBAPI error
```

映射到：

```text
LLMUsageRepositoryError
```

不要直接把：

```text
SQLAlchemy exception
```

泄露到 Service。

Query Service 可以将 Repository Error 原样传播，或者按照当前项目 Service Error 规范转换。

**不要新造复杂异常体系。**

---

# 二十六、Query Failure 不修改数据库

Query 是：

```text
READ ONLY
```

测试必须证明：

```text
SELECT
```

不会产生：

```text
INSERT
UPDATE
DELETE
```

测试前：

```text
row_count_before
```

测试后：

```text
row_count_after
```

必须：

```text
row_count_before == row_count_after
```

---

# 二十七、真实 PostgreSQL Query Test

新增：

```text
tests/test_llm_usage_query.py
```

至少准备：

```text
5~10 rows
```

测试数据可以使用：

```text
Fake / fixture
```

或者当前项目已有 DB fixture。

优先复用现有机制。

不要：

```text
向开发数据库永久写入测试数据
```

测试结束：

```text
ai_ops.llm_usage_record = 0
```

---

# 二十八、Query Test Cases

至少覆盖：

## 1. Empty

```text
DB = 0 rows
query()
→ []
```

---

## 2. All

```text
DB = 5 rows
query()
→ 5 rows
```

---

## 3. request_id

```text
request_id = req-003
→ exactly 1
```

---

## 4. Not Found

```text
request_id = req-not-exist
→ None
```

---

## 5. provider

```text
provider = deepseek
→ only deepseek
```

---

## 6. model

```text
model = deepseek-chat
→ only matching model
```

---

## 7. Combined Filter

例如：

```text
provider = deepseek
model = deepseek-chat
```

只返回同时满足条件的数据。

---

## 8. Time Range

测试：

```text
from
to
```

验证边界：

```text
created_at == from
created_at == to
```

都符合预期。

---

## 9. Pagination

例如：

```text
limit = 2
offset = 0
```

然后：

```text
limit = 2
offset = 2
```

确认：

```text
page 1
page 2
```

不会重复。

---

# 二十九、Stable Ordering

测试必须验证：

相同：

```text
created_at
```

的记录仍然按照：

```text
id DESC
```

稳定排序。

最终排序：

```text
created_at DESC,
id DESC
```

不要只：

```text
ORDER BY created_at DESC
```

---

# 三十、DTO Security Test

构造：

```text
LLMUsageRecord
```

确认 DTO：

```text
has id
has request_id
has provider
has model
has prompt_tokens
has completion_tokens
has total_tokens
has created_at
```

同时确认不存在：

```text
Session
Connection
Engine
password
api_key
prompt
messages
SQL
cost
price
currency
```

---

# 三十一、Immutability Test

如果 DTO Contract 是 frozen：

测试：

```python
dto.provider = "xxx"
```

必须：

```text
reject
```

不要允许 Query DTO 被修改。

---

# 三十二、Repository / Service Boundary Test

验证：

```text
Repository
 ↓
record
 ↓
Query Service
 ↓
DTO
```

并确认：

```text
ORM object
```

不会直接成为：

```text
public return value
```

---

# 三十三、No Write Test

对 Query Service 执行：

```text
get
list
```

之后：

```text
count_before == count_after
```

同时确认：

```text
INSERT = 0
UPDATE = 0
DELETE = 0
```

如果当前测试设施可以监听 SQL，则可以增加 SQL-level assertion。

否则使用：

```text
DB state before / after
```

即可。

不要为了本测试引入新的 SQL instrumentation framework。

---

# 三十四、不要增加 API

本阶段结束后：

**不要创建：**

```text
GET /api/llm/usage
GET /api/llm/usage/{id}
```

也不要：

```text
FastAPI Router
```

原因：

API 属于下一层：

```text
Application / HTTP Boundary
```

当前只建立：

```text
Repository
 ↓
Service
 ↓
DTO
```

---

# 三十五、不要增加 Dashboard

本阶段不要出现：

```text
Usage Dashboard
Token Chart
Cost Chart
Provider Chart
Model Chart
```

即使数据已经可以查询：

**也不做 UI。**

---

# 三十六、不要增加 Aggregation

禁止：

```text
COUNT
SUM
AVG
GROUP BY
```

作为业务 Query API。

数据库内部允许：

```text
COUNT
```

仅用于：

```text
test verification
```

例如：

```text
确认测试清理完成
```

但不要建立：

```text
UsageAnalyticsService
```

---

# 三十七、Index / Performance

当前已有：

```text
created_at index
request_id unique partial index
```

本阶段：

**不要为了 provider/model 查询增加一堆 index。**

先观察真实 Query Pattern。

允许验证：

```text
request_id query
created_at ordered query
```

当前已有 index 是否能够满足。

不要做：

```text
premature optimization
```

---

# 三十八、Architecture 文档

修改：

```text
docs/architecture.md
```

新增：

```text
§8.17 LLM Usage Query Read Boundary
```

描述：

```text
LLM Usage Write:

Observation
 ↓
Accounting
 ↓
Runtime Bridge
 ↓
Persistence
 ↓
PostgreSQL


LLM Usage Read:

PostgreSQL
 ↓
Repository
 ↓
Query Service
 ↓
Immutable DTO
```

明确：

```text
Read Boundary
≠
Analytics
≠
Billing
≠
Dashboard
```

---

# 三十九、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.10.17 — Usage Query Read Boundary.md
```

记录：

## 1. Query Contract

```text
get_by_request_id
list
```

## 2. Filters

```text
request_id
provider
model
created_at_from
created_at_to
```

## 3. Pagination

```text
limit
offset
```

## 4. Ordering

```text
created_at DESC
id DESC
```

## 5. DTO

记录：

```text
fields
immutability
security
```

## 6. Read-only

记录：

```text
INSERT = 0
UPDATE = 0
DELETE = 0
```

## 7. Tests

记录：

```text
Empty = PASS
All = PASS
request_id = PASS
Not Found = PASS
Provider = PASS
Model = PASS
Combined Filter = PASS
Time Range = PASS
Pagination = PASS
Stable Ordering = PASS
DTO Security = PASS
Immutability = PASS
Session Isolation = PASS
```

---

# 四十、测试命令

先：

```powershell
python -m pytest -q tests/test_llm_usage_query.py
```

然后：

```powershell
python -m pytest -q tests/test_llm_usage_persistence.py tests/test_llm_usage_persistence_runtime.py tests/test_llm_usage_persistence_idempotency.py tests/test_llm_usage_query.py
```

完整：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

Compile：

```powershell
python -m compileall backend tests scripts
```

如果项目已有：

```text
LSP
lint
```

继续执行。

要求：

```text
0 failed
0 diagnostics
```

---

# 四十一、成功标准

必须全部：

```text
[PASS] Query DTO
[PASS] Query Filter
[PASS] get_by_request_id
[PASS] list
[PASS] provider filter
[PASS] model filter
[PASS] request_id filter
[PASS] time range
[PASS] pagination
[PASS] stable ordering
[PASS] empty result
[PASS] not found
[PASS] immutable DTO
[PASS] security boundary
[PASS] Repository Session isolation
[PASS] Repository error mapping
[PASS] READ ONLY
[PASS] DB rows unchanged
[PASS] no new table
[PASS] no public schema change
[PASS] no new dependency
```

---

# 四十二、如果发现问题

如果发现：

```text
Repository Query 问题
Session 问题
DTO 泄露
分页不稳定
时间范围问题
```

只允许在：

```text
Usage Query
Repository
DTO
```

范围内修复。

禁止为了测试通过修改：

```text
LLM Client
Observation
Accounting
Persistence Runtime
Idempotency
RAG
Router
Tool
T2S
Validator
Executor
```

如果发现数据库 schema 不满足 Query：

**先报告，不要直接修改数据库结构。**

---

# 四十三、最终报告格式

完成后严格按照：

```text
【Phase 3.10.17 COMPLETE】

1. 新增文件
2. 修改文件
3. Query DTO
4. Query Filter
5. Repository
6. Query Service
7. request_id Query
8. Provider / Model Filter
9. Time Range
10. Pagination
11. Stable Ordering
12. Security
13. Read-only
14. Session Isolation
15. 测试结果
16. DB writes
17. 发现并修复的问题
18. 当前限制
```

最后：

```text
LLM Usage Architecture:

                 ┌── Observation
LLM Request ─────┤
                 │
                 └── Accounting
                        ↓
                  Runtime Bridge
                        ↓
                   Persistence
                        ↓
                  Idempotency
                        ↓
                  PostgreSQL
                        ↑
                        │
                  Usage Repository
                        ↑
                        │
                  Query Service
                        ↑
                        │
                 Immutable DTO
```

并明确：

```text
Usage Query = PASS
Analytics = NOT IMPLEMENTED
Billing = NOT IMPLEMENTED
Dashboard = NOT IMPLEMENTED
HTTP API = NOT IMPLEMENTED
```

最后：

**立即停止。**

不要进入 Analytics。

不要进入 Billing。

不要进入 Dashboard。

不要开发 Chat API。

不要开发 Queue / Worker / Outbox。

不要修改 RAG / Router / Tool / Text-to-SQL。

等待下一阶段指令。
