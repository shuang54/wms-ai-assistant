# Phase 3.10.21 — Step 1：Usage Analytics Read Facade

## 目标

Phase 3.10.20 已经完成并封板。

不要重新执行 Phase 3.10.20。

不要修改 Usage Analytics 已有实现。

本 Step 只新增一个：

```text
Application Read Facade
```

用于把：

```text
Query Runtime
    ↓
Usage Analytics
```

封装成一个上层只读入口。

---

# 一、开始前必须阅读真实代码

先阅读：

```text
backend/app/services/llm_usage_query_service.py
backend/app/services/llm_usage_query_runtime.py
backend/app/services/llm_usage_aggregation_service.py
backend/app/services/llm_usage_analytics_service.py

tests/test_llm_usage_query_runtime.py
tests/test_llm_usage_aggregation.py
tests/test_llm_usage_analytics.py
```

确认真实接口后再编码。

特别确认：

1. `LLMUsageQueryRuntime` 的真实方法名和参数
2. `LLMUsageQueryFilter` 的真实结构
3. Pagination 的真实参数
4. `LLMUsageAnalyticsService` 的真实构造方式
5. `snapshot()` 的真实签名
6. `LLMUsageAnalyticsSnapshot` 的真实类型

**禁止凭猜测创建接口。**

---

# 二、新增文件

只允许新增：

```text
backend/app/services/llm_usage_analytics_facade.py
tests/test_llm_usage_analytics_facade.py
```

本 Step：

**不要修改 architecture.md。**

**不要创建 Evaluation 文档。**

---

# 三、Facade 职责

新增：

```python
LLMUsageAnalyticsReadFacade
```

它只负责：

```text
Query Runtime
      ↓
records
      ↓
Analytics Service
      ↓
LLMUsageAnalyticsSnapshot
```

核心思想：

```text
Query = 获取数据

Aggregation = 数学聚合

Analytics = 组合 Analytics View

Facade = Application Read Boundary
```

Facade **不实现任何新的聚合算法**。

---

# 四、推荐 API

根据真实项目接口调整名称，但原则保持：

```python
class LLMUsageAnalyticsReadFacade:

    async def query_snapshot(...):
        ...
```

优先只提供：

```text
query_snapshot()
```

不要为了“完整”而强行增加：

```text
query_summary()
query_by_provider()
query_by_model()
query_by_provider_model()
```

因为：

**Snapshot 已经包含这四个结果。**

---

# 五、核心实现

逻辑应该类似：

```python
records = await query_runtime.list_records_async(...)
return analytics_service.snapshot(records)
```

但：

**必须根据真实接口实现。**

不要复制 Query Service。

不要复制 Aggregation Service。

不要复制 Analytics Service。

---

# 六、依赖边界

Facade 可以依赖：

```text
LLMUsageQueryRuntime
LLMUsageAnalyticsService
LLMUsageQueryFilter
LLMUsageAnalyticsSnapshot
```

Facade 禁止直接依赖：

```text
LLMUsageRepository
SQLAlchemy Session
SQLAlchemy Engine
SQLAlchemy Connection
PostgreSQL
SQL
```

架构必须保持：

```text
Facade
   ↓
Query Runtime
   ↓
Query Service
   ↓
Repository
   ↓
PostgreSQL
```

而不是：

```text
Facade
   ↓
Repository
```

---

# 七、不得重新实现 Snapshot

不要在 Facade 中写：

```python
tuple(records)
```

如果 Analytics Service 已经负责：

```text
Iterable → tuple snapshot
```

就直接：

```text
Facade → AnalyticsService.snapshot(records)
```

Snapshot 的唯一实现仍然是：

```text
LLMUsageAnalyticsService
```

---

# 八、不得改变 Pagination

Facade 必须原样传递当前 Query Contract。

例如如果真实接口是：

```python
list_records_async(filter)
```

那么直接传递：

```text
filter
```

不要创建第二套：

```text
FacadePagination
FacadeFilter
FacadePageSize
```

不要重新定义：

```text
DEFAULT_PAGE_SIZE
MAX_PAGE_SIZE
```

---

# 九、Pagination 语义

本阶段明确：

```text
Facade Analytics
=
当前 Query Filter 返回的记录集合的 Analytics
```

例如：

```text
page_size = 100
```

那么 Analytics 只代表：

```text
这 100 条记录
```

不是：

```text
整个数据库
```

不要增加：

```text
COUNT(*)
total_count
global_total
```

---

# 十、测试

新增：

```text
tests/test_llm_usage_analytics_facade.py
```

优先使用现有测试中的 Fake / Stub 模式。

**不要创建新的 Mock Framework。**

测试目标：

### 1. 正常 Snapshot

验证：

```text
Facade
 ↓
Runtime
 ↓
Analytics
 ↓
Snapshot
```

最终：

```text
snapshot.total
snapshot.by_provider
snapshot.by_model
snapshot.by_provider_model
```

正确。

---

### 2. Runtime 只调用一次

Fake Runtime：

```text
call_count = 0
```

调用：

```text
await facade.query_snapshot(...)
```

断言：

```text
call_count == 1
```

确保不存在：

```text
summary → query
provider → query
model → query
provider_model → query
```

---

### 3. Filter 原样传递

构造一个真实：

```text
LLMUsageQueryFilter
```

验证 Runtime 收到的对象与 Facade 接收到的 Filter 一致。

不要重新构造 Filter。

---

### 4. Pagination 原样传递

验证：

```text
limit
offset
```

或者项目实际分页字段，都没有被 Facade 修改。

---

### 5. Runtime Error

Fake Runtime 抛出：

```text
RuntimeError
```

验证：

```text
await facade.query_snapshot(...)
```

直接抛出相同异常。

不要吞异常。

不要：

```python
except Exception:
    return None
```

---

### 6. Analytics Error

Fake Analytics Service 抛出异常。

验证异常正常向上传递。

---

### 7. Empty Result

Runtime 返回：

```python
[]
```

最终：

```text
snapshot.total.total_requests == 0
```

并保持现有 Analytics Contract。

---

### 8. Immutability

确认：

```text
LLMUsageAnalyticsSnapshot
```

仍然是 frozen。

Facade 不允许把结果转换成：

```text
dict
list
```

---

### 9. Security

使用 AST / 静态检查确认 Facade 不导入：

```text
sqlalchemy
psycopg
database session
repository
```

不要用脆弱的字符串搜索替代 AST。

---

### 10. No DB

测试：

```text
DB Access = 0
```

不得：

```text
RUN_DB_TESTS
PostgreSQL
Session
Engine
Connection
```

---

### 11. No Network

不得调用：

```text
HTTP
LLM
OpenAI
DeepSeek
SiliconFlow
```

---

# 十一、测试数量

目标：

```text
15～25 tests
```

重点质量，不追求数量。

---

# 十二、第一轮测试

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

然后执行：

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

# 十三、如果出现问题

如果发现：

```text
Query Runtime 接口不适合
Analytics Service 接口不适合
Filter 无法直接传递
Pagination Contract 不清晰
```

不要立即修改旧模块。

立即停止并报告：

```text
发现问题：
涉及模块：
当前接口：
Facade 需要：
建议最小修改：
```

---

# 十四、禁止事项

本 Step 禁止：

```text
修改 Query Runtime
修改 Query Service
修改 Query Repository
修改 Aggregation
修改 Analytics
修改数据库
新增数据库表
新增数据库字段
HTTP API
FastAPI
Dashboard
Frontend
Billing
Cost
Pricing
Redis
Queue
Worker
Outbox
Chat API
```

---

# 十五、完成标准

必须满足：

```text
Facade = implemented

Facade Tests = PASS
Analytics Tests = PASS
Aggregation Tests = PASS

Query Runtime = unchanged
Query Service = unchanged
Query Repository = unchanged
Aggregation = unchanged
Analytics = unchanged

DB Access = 0
Network = 0
```

---

# 十六、完成后立即停止

最终只报告：

```text
Step 1 COMPLETE

Facade = implemented
Tests = XX passed, 0 failed

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
运行 pytest -q
运行 RUN_DB_TESTS=1
compileall
LSP
Git final check
```

这些全部留到后续 Step。

不要进入 Phase 3.10.22。
