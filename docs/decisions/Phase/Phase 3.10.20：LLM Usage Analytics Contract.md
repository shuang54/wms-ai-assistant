你现在开始实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.10.20：LLM Usage Analytics Contract

## 一、阶段目标

Phase 3.10.19 已完成：

```text
LLMUsageRecordView
        ↓
Usage Aggregation
        ↓
LLMUsageAggregate
        ├── Provider
        ├── Model
        └── Provider + Model
```

本阶段不做 HTTP API、不做 Dashboard、不做 Billing。

唯一目标：

**在现有 Query + Aggregation 基础上建立一个稳定的 LLM Usage Analytics Service Contract。**

最终形成：

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
Usage Analytics Service
    ├── Basic Summary
    ├── Provider Summary
    ├── Model Summary
    └── Provider + Model Summary
```

本阶段仍然属于：

**Domain / Service 层能力建设。**

---

# 二、开始编码前必须先阅读

先阅读真实代码：

```text
backend/app/services/llm_usage_query_service.py
backend/app/services/llm_usage_query_runtime.py
backend/app/services/llm_usage_aggregation_service.py
backend/app/db/llm_usage_repository.py
backend/app/db/models/llm_usage_record.py
backend/app/llm/accounting.py
backend/app/llm/observability.py
tests/test_llm_usage_query.py
tests/test_llm_usage_query_runtime.py
tests/test_llm_usage_aggregation.py
docs/architecture.md
```

重点确认：

1. `LLMUsageRecordView`
2. `LLMUsageQueryFilter`
3. `LLMUsageQueryService`
4. `LLMUsageQueryRuntimeBridge`
5. `LLMUsageAggregate`
6. `ProviderUsageAggregate`
7. `ModelUsageAggregate`
8. `ProviderModelUsageAggregate`

**不要重新定义这些 DTO。**

---

# 三、严格范围

## 允许

新增：

```text
backend/app/services/llm_usage_analytics_service.py
tests/test_llm_usage_analytics.py
docs/evaluation/Phase 3.10.20 — Usage Analytics Contract.md
```

修改：

```text
docs/architecture.md
```

如果确有必要，可以对 Aggregation Service 做最小兼容性调整。

---

## 禁止

本阶段禁止：

```text
HTTP API
FastAPI Router
Dashboard
Frontend
Billing
Cost Calculation
Pricing
数据库 GROUP BY
SQL Analytics
Materialized View
新的数据库表
新的数据库字段
Migration
Redis
Cache
Queue
Worker
Outbox
Kafka
Celery
```

禁止修改：

```text
LLM Client
Observation
Accounting
Persistence
Idempotency
Query Repository
Query SQL
Query Service Contract
Query Runtime Contract
RAG
Router
Tool
Text-to-SQL
SQLValidator
SQLExecutor
RelevantTableSelector
Business Semantic
ProjectContext
```

---

# 四、核心原则

本阶段 Analytics Service：

**不是新的数据库层。**

它负责：

```text
Query
  ↓
Aggregation
  ↓
Analytics Result
```

而不是：

```text
Analytics
  ↓
SQL
  ↓
Database
```

Analytics Service 不得：

```text
创建 Session
执行 SQL
访问 Repository
读取 ORM
访问 PostgreSQL
```

---

# 五、Analytics Contract

新增：

```text
backend/app/services/llm_usage_analytics_service.py
```

定义一个明确的 Analytics Service。

建议职责：

```text
summary(records)
by_provider(records)
by_model(records)
by_provider_model(records)
```

但先阅读 Phase 3.10.19 的实际 API。

**优先复用 Aggregation Service，而不是重复实现 sum/group-by。**

---

# 六、Summary

Analytics 最基础结果：

```text
LLMUsageAnalyticsSummary
```

至少表达：

```text
total_requests
prompt_tokens
completion_tokens
total_tokens
```

并继承 Phase 3.10.19 的：

```text
*_known
```

语义。

不要重新设计 NULL 行为。

例如：

```text
LLMUsageAggregate
        ↓
LLMUsageAnalyticsSummary
```

如果两者字段完全一致：

**不要为了名字而复制一个 DTO。**

可以直接复用 `LLMUsageAggregate`。

只有当 Analytics 层确实增加新的业务语义时，才新增 DTO。

---

# 七、Provider Analytics

提供：

```text
analytics.by_provider(records)
```

直接复用：

```text
aggregate_by_provider()
```

不得重新实现：

```text
grouping
sum
sorting
NULL handling
```

结果必须保持 Phase 3.10.19 Contract。

---

# 八、Model Analytics

提供：

```text
analytics.by_model(records)
```

直接复用：

```text
aggregate_by_model()
```

同样：

**不要复制 Aggregation 算法。**

---

# 九、Provider + Model Analytics

提供：

```text
analytics.by_provider_model(records)
```

复用：

```text
aggregate_by_provider_model()
```

保持：

```text
provider ASC
model ASC
None 最后
```

---

# 十、Analytics 输入 Contract

Analytics Service 接受：

```text
Iterable[LLMUsageRecordView]
```

支持：

```text
list
tuple
generator
```

但要注意：

generator 是一次性 iterable。

如果一个 Analytics 调用需要同时生成：

```text
summary
provider
model
provider_model
```

必须避免：

```text
第一次消费 generator
第二次已经为空
```

可以：

```python
records = tuple(records)
```

然后基于同一份不可变快照执行多个 aggregation。

不要建立复杂缓存。

---

# 十一、Analytics Snapshot

建议明确：

一次 Analytics 计算应该基于：

**同一个 Usage Snapshot。**

例如：

```text
records
   ↓
tuple(records)
   ↓
┌──────────────┬──────────────┬──────────────┐
│              │              │              │
Summary      Provider       Model       Provider+Model
```

这样可以保证：

```text
total_requests
```

与：

```text
provider requests
```

之间不会因为输入 generator 被多次消费而产生不一致。

---

# 十二、Consistency Contract

必须验证：

```text
sum(provider.total_requests)
    ==
summary.total_requests
```

对于已知 token：

```text
sum(provider.prompt_tokens)
    ==
summary.prompt_tokens
```

同理：

```text
completion
total
```

但必须注意：

**不要把 `known=False` 当成普通 0 去解释业务含义。**

例如：

```text
summary.prompt_tokens_known = False
```

那么：

```text
provider aggregate
```

也应该保持对应的 unknown 语义。

---

# 十三、Provider / Model 一致性

验证：

```text
Provider Aggregation
        ↓
total requests

Model Aggregation
        ↓
total requests

Provider + Model
        ↓
total requests
```

三者都应该能回溯到：

```text
summary.total_requests
```

即：

```text
Σ provider requests
=
Σ model requests
=
Σ provider-model requests
=
total requests
```

前提是：

**所有记录都被纳入相应维度。**

`None` provider/model 也必须作为独立 group 参与统计。

---

# 十四、不要添加时间聚合

当前 `LLMUsageRecordView` 有：

```text
created_at
```

但本阶段：

**不要实现：**

```text
hour
day
week
month
```

不要实现：

```text
daily_usage
monthly_usage
time_series
```

原因：

当前数据库没有专门的时间聚合 Contract。

如果现在加入：

```text
date bucket
timezone
day boundary
```

会提前引入新的业务语义。

留到后续专门阶段。

---

# 十五、不要添加 Cost

虽然当前已有：

```text
LLMPricing
LLMCost
calculate_llm_cost()
```

但本阶段仍然：

```text
Cost = NOT INCLUDED
```

不要出现：

```text
price
currency
cost
billing
estimated_cost
```

Analytics 当前只回答：

**用了多少。**

不回答：

**花了多少钱。**

---

# 十六、错误 Contract

定义必要的：

```text
LLMUsageAnalyticsError
LLMUsageAnalyticsInputError
```

但不要过度封装。

输入非法时：

```text
None
错误类型
错误 iterable
```

应该有明确行为。

Aggregation Service 已经定义的异常：

**不要无意义地重新包装。**

例如：

```text
AggregationError
    ↓
Analytics
    ↓
same error
```

优先保持原始异常类型。

---

# 十七、Immutability

所有 Analytics Result：

```text
frozen=True
```

如果复用现有：

```text
LLMUsageAggregate
ProviderUsageAggregate
ModelUsageAggregate
ProviderModelUsageAggregate
```

则无需创建新的可变 DTO。

禁止：

```text
global mutable result
cache
singleton state
```

---

# 十八、Determinism

相同输入：

```text
A B C
```

和：

```text
C A B
```

必须得到：

```text
完全相同的 Analytics Result
```

不能依赖：

```text
input order
dict insertion order
thread order
database order
```

直接复用 Phase 3.10.19 已验证的稳定排序 Contract。

---

# 十九、纯内存验证

Analytics 测试：

```text
DB Access = 0
Network = 0
```

直接构造：

```text
LLMUsageRecordView
```

不要：

```text
RUN_DB_TESTS
```

不要查询真实 PostgreSQL。

---

# 二十、测试文件

新增：

```text
tests/test_llm_usage_analytics.py
```

至少覆盖：

## 1. Empty

```text
[] → valid zero analytics
```

## 2. Single record

验证所有维度：

```text
summary
provider
model
provider-model
```

## 3. Multiple records

验证聚合一致性。

## 4. Generator

```text
generator → analytics
```

验证不会出现 generator 被提前消费导致的空结果。

## 5. Input order independence

```text
A B C
C A B
```

结果相同。

## 6. Provider None

```text
provider=None
```

作为独立 group。

## 7. Model None

```text
model=None
```

作为独立 group。

## 8. NULL tokens

验证：

```text
None != 0
```

并保持：

```text
*_known
```

## 9. Duplicate records

重复记录不能被去重。

## 10. Consistency

验证：

```text
provider requests == summary requests
model requests == summary requests
provider-model requests == summary requests
```

## 11. Immutability

结果不能被修改。

## 12. No database dependency

静态检查：

```text
sqlalchemy
psycopg
redis
celery
kafka
backend.app.db
```

不得出现在 Analytics Service 的代码 import / executable AST 中。

注意：

**不要使用全文字符串扫描。**

继续采用 Phase 3.10.19 已修正的：

```text
AST Name / Attribute
```

检查方式。

---

# 二十一、不要复制 Aggregation 测试

Phase 3.10.19 已经验证：

```text
Basic Aggregation
Provider Grouping
Model Grouping
Provider+Model Grouping
NULL
Ordering
```

本阶段重点验证：

```text
Analytics → Aggregation
```

也就是：

**Analytics Service 是否正确组合已有 Contract。**

不要把 Phase 3.10.19 的 23 个测试再复制一遍。

---

# 二十二、Architecture

修改：

```text
docs/architecture.md
```

新增：

```text
§8.20 LLM Usage Analytics Contract
```

架构：

```text
LLM Request
    ↓
Observation
    ↓
Accounting
    ↓
Persistence
    ↓
Idempotency
    ↓
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
```

明确：

```text
Query       = 获取记录
Aggregation = 数学聚合
Analytics   = 组合 Usage 统计能力
Billing     = 未实现
Dashboard   = 未实现
API         = 未实现
```

---

# 二十三、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.10.20 — Usage Analytics Contract.md
```

记录：

## 1. Scope

```text
In-memory Usage Analytics
```

## 2. Input

```text
LLMUsageRecordView
```

## 3. Aggregation reuse

说明：

```text
Analytics 不重复实现聚合算法
```

## 4. Snapshot

说明：

```text
Iterable
→ immutable snapshot
→ multiple aggregation views
```

## 5. Consistency

记录：

```text
provider totals
model totals
provider-model totals
summary totals
```

一致性结果。

## 6. NULL semantics

记录：

```text
NULL ≠ 0
```

## 7. Cost

```text
NOT INCLUDED
```

## 8. Database

```text
DB Access = 0
DB Writes = 0
Network = 0
```

---

# 二十四、测试命令

先执行：

```powershell
python -m pytest -q tests/test_llm_usage_analytics.py
```

然后：

```powershell
python -m pytest -q `
  tests/test_llm_usage_aggregation.py `
  tests/test_llm_usage_analytics.py
```

然后：

```powershell
python -m pytest -q `
  tests/test_llm_usage_query.py `
  tests/test_llm_usage_query_runtime.py `
  tests/test_llm_usage_aggregation.py `
  tests/test_llm_usage_analytics.py
```

最后：

```powershell
python -m pytest -q
```

再：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

以及：

```powershell
python -m compileall backend tests scripts
```

检查：

```text
LSP
lint
```

要求：

```text
0 failed
0 diagnostics
```

---

# 二十五、成功标准

必须全部满足：

```text
Usage Analytics Contract     = PASS

Summary                      = PASS
Provider Analytics           = PASS
Model Analytics              = PASS
Provider + Model Analytics   = PASS
Snapshot                     = PASS
Consistency                  = PASS
NULL Semantics               = PASS
Stable Ordering              = PASS
Generator                    = PASS
Immutability                 = PASS
```

并且：

```text
DB Access                    = 0
DB Writes                    = 0
Network                      = 0
New Tables                   = 0
New Fields                   = 0
New Dependencies             = 0
HTTP API                     = 0
Dashboard                    = 0
Billing                      = 0
```

核心模块：

```text
Repository                   = unchanged
Query Service                = unchanged
Query Runtime                = unchanged
Aggregation Contract         = unchanged
Persistence                  = unchanged
Idempotency                  = unchanged
Accounting                   = unchanged
Observation                  = unchanged
RAG                          = unchanged
Router                       = unchanged
Tool                         = unchanged
Text-to-SQL                  = unchanged
```

---

# 二十六、最终报告格式

完成后严格按照：

```text
【Phase 3.10.20 COMPLETE】

1. 新增文件
2. 修改文件
3. Analytics Contract
4. Summary
5. Provider Analytics
6. Model Analytics
7. Provider + Model Analytics
8. Snapshot
9. Consistency
10. NULL Semantics
11. Stable Ordering
12. Immutability
13. Cost 是否涉及
14. Repository 是否修改
15. Aggregation 是否修改
16. 测试结果
17. DB Access / DB writes
18. 发现并修复的问题
19. 当前限制
```

最后给出：

```text
LLM Usage Architecture:

LLM Request
    ↓
Observation
    ↓
Accounting
    ↓
Persistence
    ↓
Idempotency
    ↓
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
```

状态：

```text
Usage Persistence       = PASS
Usage Idempotency       = PASS
Usage Query             = PASS
Async Query Runtime     = PASS
Usage Aggregation       = PASS
Usage Analytics         = PASS

Billing                 = NOT IMPLEMENTED
Dashboard               = NOT IMPLEMENTED
HTTP API                = NOT IMPLEMENTED
Queue / Worker          = NOT IMPLEMENTED
Outbox                  = NOT IMPLEMENTED
```

**立即停止。**

不要进入 Billing。

不要进入 Dashboard。

不要开发 HTTP API。

不要引入 AsyncSession。

不要引入 Queue / Worker / Outbox。

不要修改 RAG / Router / Tool / Text-to-SQL。

等待下一阶段指令。
