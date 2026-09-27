# Phase 3.10.21：Usage Analytics Application Read Facade

## 一、阶段目标

Phase 3.10.20 已经完成：

```text
LLMUsageRecordView
        ↓
Usage Aggregation
        ↓
Usage Analytics
```

但是目前上层调用者仍然需要自己组合：

```text
Query Runtime
    +
Aggregation
    +
Analytics
```

本阶段只解决一个问题：

**建立一个稳定的 Application Read Facade，让上层只需要调用一个入口即可获得 Usage Analytics。**

目标架构：

```text
PostgreSQL
    ↓
Query Repository
    ↓
Query Service
    ↓
Query Runtime
    ↓
LLMUsageRecordView
    ↓
Usage Aggregation
    ↓
Usage Analytics
    ↓
Usage Analytics Read Facade
    ↓
Future API / Dashboard / Admin / AI Ops
```

注意：

**本阶段仍然不开发 API、Dashboard、Billing、前端。**

---

# 二、开始编码前必须阅读

先阅读真实代码：

```text
backend/app/services/llm_usage_query_service.py
backend/app/services/llm_usage_query_runtime.py
backend/app/services/llm_usage_aggregation_service.py
backend/app/services/llm_usage_analytics_service.py
backend/app/db/llm_usage_repository.py

tests/test_llm_usage_query.py
tests/test_llm_usage_query_runtime.py
tests/test_llm_usage_aggregation.py
tests/test_llm_usage_analytics.py

docs/architecture.md
docs/evaluation/Phase 3.10.20 — Usage Analytics Contract.md
```

确认真实：

1. Query Runtime 的参数
2. Query Filter 的真实结构
3. Pagination 的真实限制
4. Analytics 的真实接口
5. Aggregation 的真实接口
6. DTO 的真实字段
7. 错误类型
8. 当前 Query Runtime 的 async 边界

**不要假设接口。**

---

# 三、严格范围

## 允许

新增：

```text
backend/app/services/llm_usage_analytics_facade.py
tests/test_llm_usage_analytics_facade.py
docs/evaluation/Phase 3.10.21 — Usage Analytics Read Facade.md
```

必要时：

```text
docs/architecture.md
```

增加 §8.21。

---

## 禁止

不得修改：

```text
LLM Client
Observation
Accounting
Persistence
Idempotency
Query Repository
Query Service
Aggregation Service
Analytics Service
Router
RAG
Tool
Text-to-SQL
SQL Validator
SQL Executor
```

不得新增：

```text
HTTP API
FastAPI route
Dashboard
Frontend
Billing
Cost
Pricing
Currency
Cache
Redis
Queue
Worker
Outbox
WebSocket
```

不得增加数据库表。

不得增加数据库字段。

不得修改数据库 Schema。

---

# 四、Facade 的职责

新增：

```text
LLMUsageAnalyticsReadFacade
```

它是：

**Application Read Boundary**

不是新的 Analytics Engine。

它只负责组合已有能力。

---

# 五、推荐接口

先根据真实代码调整名称，但职责保持：

```python
class LLMUsageAnalyticsReadFacade:
    async def query_snapshot(...):
        ...

    async def query_summary(...):
        ...

    async def query_by_provider(...):
        ...

    async def query_by_model(...):
        ...

    async def query_by_provider_model(...):
        ...
```

如果当前项目更适合：

```python
async def analytics(...)
```

也可以采用单入口：

```python
async def snapshot(...)
```

但必须优先遵循当前项目已有命名风格。

---

# 六、核心执行链

Facade 的内部逻辑应该是：

```text
query
 ↓
LLMUsageQueryRuntime
 ↓
tuple[LLMUsageRecordView]
 ↓
LLMUsageAnalyticsService
 ↓
LLMUsageAnalyticsSnapshot
```

即：

```python
records = await query_runtime.list_records_async(...)
snapshot = analytics_service.snapshot(records)
return snapshot
```

---

# 七、最重要的架构约束

Facade：

### 可以依赖

```text
LLMUsageQueryRuntime
LLMUsageAnalyticsService
LLMUsageQueryFilter
LLMUsageAnalyticsSnapshot
```

### 不可以直接依赖

```text
SQLAlchemy Session
Repository
Engine
Connection
SQL
PostgreSQL
```

因此：

```text
Facade → Query Runtime
```

是允许的。

而：

```text
Facade → Repository
```

禁止。

---

# 八、不要复制 Analytics 逻辑

Facade 不允许重新实现：

```text
sum
count
grouping
sorting
NULL handling
known flags
provider grouping
model grouping
provider+model grouping
```

全部由：

```text
LLMUsageAggregationService
LLMUsageAnalyticsService
```

完成。

Facade 只是：

```text
Query → Analytics
```

---

# 九、Snapshot 是默认推荐返回值

优先设计：

```python
await facade.query_snapshot(...)
```

返回：

```text
LLMUsageAnalyticsSnapshot
```

内部：

```text
total
by_provider
by_model
by_provider_model
```

这样上层不需要重复查询数据库四次。

---

# 十、必须避免重复数据库查询

例如：

```text
❌ query_summary()
❌ query_by_provider()
❌ query_by_model()
❌ query_by_provider_model()
```

如果上层需要完整 Analytics：

**不得执行四次 DB Query。**

应该：

```text
DB Query = 1 次

        ↓

records snapshot

        ↓

Analytics Snapshot

        ├── summary
        ├── provider
        ├── model
        └── provider + model
```

---

# 十一、Pagination 边界

必须阅读当前：

```text
LLMUsageQueryFilter
```

和：

```text
pagination constants
```

不要重新定义分页限制。

Facade 应该：

**复用现有 Query Filter / Pagination Contract。**

不得出现第二套：

```text
MAX_PAGE_SIZE
DEFAULT_PAGE_SIZE
```

---

# 十二、Snapshot 与 Pagination 的语义

必须明确：

本阶段 Facade 返回的是：

```text
当前 Query Filter 对应的记录集合
```

的 Analytics。

因此：

```text
page_size = 100
```

意味着：

```text
Analytics = 当前这 100 条记录的 Analytics
```

而不是：

```text
整个数据库的 Analytics
```

不要偷偷增加：

```text
COUNT(*)
```

或：

```text
total_count
```

---

# 十三、测试设计

新增：

```text
tests/test_llm_usage_analytics_facade.py
```

建议至少覆盖：

## 1. Snapshot

```text
query
 ↓
runtime
 ↓
analytics
 ↓
snapshot
```

验证：

```text
total
by_provider
by_model
by_provider_model
```

全部正确。

---

## 2. Query Runtime 调用一次

Fake Runtime：

```text
call_count = 0
```

调用：

```text
query_snapshot()
```

之后：

```text
call_count == 1
```

确保没有：

```text
summary → DB
provider → DB
model → DB
provider_model → DB
```

---

# 十四、Generator / Iterable

如果 Runtime 返回：

```text
list
tuple
generator
```

必须遵循现有 Analytics Service 的：

```text
Iterable → tuple snapshot
```

不要在 Facade 再实现一套 snapshot 逻辑。

---

# 十五、NULL Semantics

Facade 不得改变 Phase 3.10.19 / 3.10.20 的规则。

例如：

```text
100
NULL
200
```

仍然：

```text
sum = 300
known = False
```

而：

```text
0
```

仍然：

```text
sum = 0
known = True
```

---

# 十六、Ordering

Facade 不得重新排序。

必须保持：

```text
key ASC
None LAST
```

由 Analytics / Aggregation Service 负责。

---

# 十七、Error Propagation

Facade 不应该吞掉底层错误。

例如：

```text
Query Runtime Error
        ↓
Facade
        ↓
same error
```

不要：

```python
except Exception:
    return None
```

不要制造：

```text
GenericAnalyticsError
```

除非现有项目已经有明确的 Application Boundary Error Contract。

---

# 十八、Immutability

Facade 返回：

```text
LLMUsageAnalyticsSnapshot
```

必须保持：

```text
frozen
tuple
immutable
```

Facade 不允许：

```python
dict(...)
list(...)
```

重新包装成可变结构。

---

# 十九、安全检查

Facade 返回值不得暴露：

```text
Session
Connection
Engine
SQL
Database URL
Password
API Key
Authorization Header
Prompt
Raw LLM messages
LLM response
```

只允许返回已有：

```text
LLMUsageRecordView
LLMUsageAggregate
ProviderUsageAggregate
ModelUsageAggregate
ProviderModelUsageAggregate
LLMUsageAnalyticsSnapshot
```

---

# 二十、测试依赖

默认：

```text
DB Access = 0
Network = 0
```

Facade 单元测试：

**Fake Query Runtime**

不要连接真实 PostgreSQL。

本阶段重点是：

```text
Application Composition
```

不是 DB Integration。

---

# 二十一、不要新增 Fake Framework

优先检查：

```text
tests/
```

当前已有：

```text
Fake Repository
Fake Runtime
Fake Service
Fake LLM
```

如果存在：

**直接复用。**

不要新建大型 Mock Framework。

---

# 二十二、测试数量

目标：

```text
15～25 tests
```

不要为了数量写大量重复测试。

重点测试：

```text
Facade → Runtime
Facade → Analytics
Single Query
Snapshot
Error propagation
Pagination propagation
Filter propagation
Immutability
Security
No DB
No Network
```

---

# 二十三、第一步只实现 Python + Tests

本 Step：

只允许：

```text
backend/app/services/llm_usage_analytics_facade.py
tests/test_llm_usage_analytics_facade.py
```

暂时不要修改：

```text
docs/architecture.md
```

暂时不要写 Evaluation 文档。

---

# 二十四、第一步测试

执行：

```powershell
cd D:\coding\ai\wms-ai-assistant

python -m pytest -q tests/test_llm_usage_analytics_facade.py
```

目标：

```text
15～25 passed
0 failed
```

然后：

```powershell
python -m pytest -q `
  tests/test_llm_usage_analytics_facade.py `
  tests/test_llm_usage_analytics.py `
  tests/test_llm_usage_aggregation.py
```

确认：

```text
Facade PASS
Analytics PASS
Aggregation PASS
```

---

# 二十五、发现问题的处理原则

如果发现：

```text
Query Runtime 接口不适合 Facade
Analytics 接口不适合组合
Pagination Contract 不明确
```

不要直接修改旧模块。

先停止并报告：

```text
发现问题：
涉及模块：
当前行为：
Facade 所需行为：
建议修改：
```

只有确认属于本阶段必要的最小修复，才可以修改。

---

# 二十六、完成标准

第一步必须达到：

```text
Facade implemented
Tests PASS
Analytics unchanged
Aggregation unchanged
Query Runtime unchanged
DB Access = 0
Network = 0
```

---

# 二十七、第一步完成后立即停止

完成后只报告：

```text
Step 1 COMPLETE

Facade = implemented
Tests = XX passed
Analytics = unchanged
Aggregation = unchanged
Query Runtime = unchanged
DB Access = 0
Network = 0
```

**立即停止。**

不要：

```text
修改 architecture.md
创建 Evaluation
运行全量测试
运行 DB tests
开发 API
开发 Dashboard
开发 Billing
```

等待下一步指令。
