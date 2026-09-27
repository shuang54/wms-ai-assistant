你现在开始实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.10.19：LLM Usage Aggregation Contract

## 一、阶段目标

Phase 3.10.18 已完成：

```text
PostgreSQL
 ↓
Usage Repository
 ↓
Usage Query Service
 ↓
Usage Query Runtime Bridge
 ↓
Async Caller
```

本阶段开始进入 Usage 数据的**统计/聚合层**。

唯一目标：

**在不修改数据库、不新增 API、不做 Dashboard、不引入 Billing 的前提下，为 LLM Usage 建立一个纯内存、可测试、稳定的 Aggregation Contract。**

最终形成：

```text
Usage Query
      ↓
LLMUsageRecordView
      ↓
Usage Aggregation
      ↓
Aggregation Result
```

---

# 二、开始编码前必须先阅读

先阅读真实代码：

```text
backend/app/services/llm_usage_query_service.py
backend/app/services/llm_usage_query_runtime.py
backend/app/db/llm_usage_repository.py
backend/app/db/models/llm_usage_record.py
backend/app/llm/accounting.py
backend/app/llm/accounting_consumer.py
backend/app/llm/observability.py
tests/test_llm_usage_query.py
tests/test_llm_usage_query_runtime.py
docs/architecture.md
```

确认：

1. `LLMUsageRecordView` 当前字段。
2. `LLMUsageQueryFilter` 当前能力。
3. `LLMUsageQueryService` 当前返回结构。
4. `LLMCost` / `LLMPricing` 当前 Contract。
5. `LLMUsage` 当前字段。
6. 当前项目是否已经存在 aggregation / statistics helper。

**如果已有可复用的 aggregation helper，优先复用。**

不要重复定义 Usage / Cost DTO。

---

# 三、严格范围

## 允许

新增：

```text
Usage Aggregation DTO
Usage Aggregation Service
Aggregation tests
Evaluation document
Architecture documentation
```

可以基于：

```text
LLMUsageRecordView
```

进行纯 Python 聚合。

允许：

```text
sum
count
group by provider
group by model
group by provider + model
```

允许增加：

```text
total_requests
total_prompt_tokens
total_completion_tokens
total_tokens
```

---

## 禁止

本阶段禁止：

```text
HTTP API
FastAPI endpoint
Dashboard
Frontend
Billing
真实价格计算
Pricing persistence
数据库 aggregation SQL
GROUP BY SQL
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
Repository SQL
Query Service
Query Runtime
Persistence
Idempotency
LLM Client
Observation
Accounting
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

# 四、核心设计原则

本阶段的 Aggregation 必须是：

```text
Pure
Deterministic
Read-only
In-memory
No DB knowledge
No SQL knowledge
```

也就是说：

```text
Database
   ↓
Query Service
   ↓
List[LLMUsageRecordView]
   ↓
Aggregation Service
   ↓
Aggregation DTO
```

Aggregation Service：

**不能创建 Session。**

不能调用 Repository。

不能执行 SQL。

不能知道 PostgreSQL。

---

# 五、Aggregation DTO

建议新增：

```text
backend/app/services/llm_usage_aggregation_service.py
```

定义 frozen DTO。

例如：

```python
@dataclass(frozen=True)
class LLMUsageAggregate:
    total_requests: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
```

但：

**不要机械照抄。**

先根据当前 `LLMUsageRecordView` 和项目 DTO 风格确定最终字段。

必须保持：

```text
frozen / immutable
```

---

# 六、基础聚合

实现：

```text
aggregate(records)
```

输入：

```text
Iterable[LLMUsageRecordView]
```

输出：

```text
LLMUsageAggregate
```

例如：

```text
3 records

request A
prompt = 100
completion = 50
total = 150

request B
prompt = 200
completion = 100
total = 300

request C
prompt = 300
completion = 200
total = 500
```

得到：

```text
total_requests = 3
prompt_tokens = 600
completion_tokens = 350
total_tokens = 950
```

---

# 七、NULL Token 语义

这是本阶段必须明确的 Contract。

当前 Usage Persistence 允许：

```text
prompt_tokens = NULL
completion_tokens = NULL
total_tokens = NULL
```

因此不能简单：

```python
sum(record.prompt_tokens)
```

必须明确 NULL 语义。

建议：

```text
None = unknown / unavailable
0 = explicitly zero
```

不能把：

```text
None
```

静默解释成：

```text
0
```

例如：

```text
A:
prompt_tokens = 100

B:
prompt_tokens = NULL

C:
prompt_tokens = 200
```

聚合结果必须能够表达：

```text
prompt_tokens = 300
```

同时：

```text
prompt_tokens_known = False
```

或者采用项目中更合适的明确表达方式。

**关键是：不能丢失 NULL 的信息。**

先检查当前项目 DTO 风格，再决定最终 Contract。

---

# 八、Provider Aggregation

增加：

```text
aggregate_by_provider(records)
```

例如：

```text
deepseek:
    requests = 10
    prompt_tokens = ...
    completion_tokens = ...
    total_tokens = ...

siliconflow:
    requests = 5
    prompt_tokens = ...
    completion_tokens = ...
    total_tokens = ...
```

结果必须稳定。

建议使用：

```text
dict[str, LLMUsageAggregate]
```

但：

**不要让 dict 直接成为对外 DTO。**

内部可以使用 dict。

最终返回结构必须稳定、明确。

---

# 九、Model Aggregation

增加：

```text
aggregate_by_model(records)
```

例如：

```text
deepseek-chat
BAAI/bge-m3
...
```

注意：

当前 Usage Record 只记录 LLM Usage。

不要把：

```text
embedding
```

强行混入 LLM Cost / Usage 语义。

先根据当前 provider/model 数据确认实际情况。

---

# 十、Provider + Model Aggregation

增加：

```text
aggregate_by_provider_model(records)
```

结果类似：

```text
deepseek / deepseek-chat
deepseek / another-model
...
```

可以使用：

```text
tuple[str, str]
```

作为内部 group key。

例如：

```python
(provider, model)
```

不要创建复杂的 Grouping Framework。

---

# 十一、确定性排序

Aggregation 输出必须稳定。

例如：

```text
provider ASC
model ASC
```

或者根据当前项目 DTO / API 设计选择明确排序。

禁止依赖：

```text
database return order
dict insertion order
thread completion order
```

测试必须证明：

```text
same input
→ same output
```

即使输入顺序不同，也应该得到相同聚合结果。

---

# 十二、空输入

明确：

```text
aggregate([])
```

必须返回合法结果。

不能：

```text
None
```

不能抛异常。

例如：

```text
total_requests = 0
```

Token 字段则遵循前面定义的 NULL/unknown Contract。

---

# 十三、数据一致性

重点验证：

```text
total_tokens
```

不能擅自重新计算成：

```text
prompt_tokens + completion_tokens
```

当前 Persistence 保存的是：

```text
prompt_tokens
completion_tokens
total_tokens
```

三者必须被视为独立观测值。

例如：

```text
prompt = 100
completion = 50
total = 999
```

Aggregation 应该：

```text
sum total_tokens = 999
```

而不是：

```text
100 + 50 = 150
```

不要在 Aggregation 层修改 Usage Contract。

---

# 十四、Cost 暂时不要混入

虽然当前项目已经有：

```text
LLMPricing
LLMCost
calculate_llm_cost()
```

但本阶段：

**不要自动计算 Cost。**

不要：

```text
Usage × Price
```

不要默认 DeepSeek 价格。

不要把：

```text
currency
input_price
output_price
cost
```

加入 Usage Aggregate。

本阶段只负责：

```text
Usage
```

---

# 十五、测试

新增：

```text
tests/test_llm_usage_aggregation.py
```

建议覆盖：

## 1. Empty

```text
[] → zero aggregate
```

## 2. Single record

验证：

```text
requests = 1
tokens = original values
```

## 3. Multiple records

验证：

```text
sum
```

## 4. NULL token

验证：

```text
None != 0
```

不会静默丢失 unknown 信息。

## 5. Provider grouping

验证：

```text
provider A
provider B
```

正确分组。

## 6. Model grouping

验证：

```text
model A
model B
```

正确分组。

## 7. Provider + Model grouping

验证组合 key。

## 8. Input order independence

输入：

```text
A B C
```

以及：

```text
C A B
```

得到相同结果。

## 9. Duplicate records

两个完全相同的 `LLMUsageRecordView`：

必须被视为两个 Usage Record。

不要在 Aggregation 层偷偷去重。

## 10. Immutability

确认 Aggregate DTO：

```text
frozen
```

---

# 十六、不要连接数据库

本阶段默认：

```text
RUN_DB_TESTS
```

不需要。

Aggregation 本身：

```text
0 DB
0 Network
0 SQL
```

这是一个纯 Python Contract。

如果测试需要构造：

```text
LLMUsageRecordView
```

直接构造 Fake / fixture。

不要为了 Aggregation 再访问 PostgreSQL。

---

# 十七、与 Query Service 的关系

本阶段不要直接把：

```text
Query Service
```

改造成：

```text
Query + Aggregation
```

保持职责分离：

```text
Query Service
    ↓
List[LLMUsageRecordView]

Aggregation Service
    ↓
LLMUsageAggregate
```

未来上层如果需要：

```text
Usage Analytics
```

再组合：

```text
Query
 ↓
Aggregation
```

---

# 十八、未来组合方向

本阶段完成后，架构应该可以形成：

```text
PostgreSQL
    ↓
Repository
    ↓
Query Service
    ↓
Query Runtime
    ↓
LLMUsageRecordView
    ↓
Aggregation Service
    ↓
Usage Aggregate
```

Async：

```text
Async Caller
    ↓
Query Runtime
    ↓
asyncio.to_thread
    ↓
Query Service
    ↓
Repository
```

Aggregation 本身：

```text
pure Python
```

所以未来可以：

```text
Async Query
     ↓
Aggregate
```

而无需让 Aggregation 自己接触数据库。

---

# 十九、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.10.19 — Usage Aggregation Contract.md
```

记录：

## 1. Scope

```text
Pure in-memory aggregation
```

## 2. DTO

记录：

```text
LLMUsageAggregate
```

## 3. Basic aggregation

```text
requests
prompt tokens
completion tokens
total tokens
```

## 4. Grouping

```text
provider
model
provider + model
```

## 5. NULL semantics

明确：

```text
NULL ≠ 0
```

## 6. Ordering

记录稳定排序规则。

## 7. Cost

明确：

```text
NOT INCLUDED
```

## 8. Database

```text
DB access = 0
DB writes = 0
```

---

# 二十、Architecture

修改：

```text
docs/architecture.md
```

新增：

```text
§8.19 LLM Usage Aggregation Contract
```

明确：

```text
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
Aggregation
    ↓
LLMUsageAggregate
```

并明确：

```text
Aggregation ≠ Billing
Aggregation ≠ Cost Calculation
Aggregation ≠ Dashboard
Aggregation ≠ API
```

---

# 二十一、不要过度设计

本阶段不要实现：

```text
GenericAggregationEngine
GenericGroupByFramework
SQLAggregationBuilder
AnalyticsFramework
MetricsFramework
ReportFramework
```

只实现：

```text
LLM Usage Aggregation
```

保持小而明确。

---

# 二十二、测试命令

先执行：

```powershell
python -m pytest -q tests/test_llm_usage_aggregation.py
```

然后：

```powershell
python -m pytest -q tests/test_llm_usage_query.py tests/test_llm_usage_query_runtime.py tests/test_llm_usage_aggregation.py
```

再：

```powershell
python -m pytest -q
```

最后：

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

# 二十三、成功标准

必须全部满足：

```text
Aggregation Contract = PASS

Basic Aggregation      = PASS
Provider Grouping      = PASS
Model Grouping         = PASS
Provider+Model Group   = PASS
NULL Semantics         = PASS
Stable Ordering        = PASS
Input Order Independence = PASS
Immutability           = PASS

DB Access              = 0
DB Writes              = 0
Network                = 0
New Dependencies       = 0
New Tables             = 0
New Fields             = 0
API Changes            = 0
```

并且：

```text
Repository = unchanged
Query Service = unchanged
Query Runtime = unchanged
Persistence = unchanged
Idempotency = unchanged
Accounting = unchanged
Observation = unchanged
RAG = unchanged
Router = unchanged
Tool = unchanged
Text-to-SQL = unchanged
```

---

# 二十四、最终报告格式

完成后严格按照：

```text
【Phase 3.10.19 COMPLETE】

1. 新增文件
2. 修改文件
3. Aggregation DTO
4. Basic Aggregation
5. Provider Grouping
6. Model Grouping
7. Provider + Model Grouping
8. NULL Semantics
9. Stable Ordering
10. Immutability
11. Cost 是否涉及
12. Repository 是否修改
13. Query Service 是否修改
14. 测试结果
15. DB Access / DB writes
16. 发现并修复的问题
17. 当前限制
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
LLMUsageAggregate
```

状态：

```text
Usage Persistence       = PASS
Usage Idempotency       = PASS
Usage Query             = PASS
Async Query Runtime     = PASS
Usage Aggregation       = PASS

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
