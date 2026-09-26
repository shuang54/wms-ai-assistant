# Phase 3.10.14 — LLM Usage / Cost Persistence Contract & Minimal Storage

你现在开始实现项目：

`D:\coding\ai\wms-ai-assistant`

---

# 一、阶段目标

基于：

```text
Phase 3.10.12
LLM Usage Visibility

Phase 3.10.13
Usage / Cost Consumption Boundary
```

本阶段只解决一个问题：

> **如何将 request-level LLM Usage 安全、稳定、可追溯地持久化。**

当前已经存在：

```text
LLMResponse
    ↓
LLMObservation
    ↓
LLMUsage
    ↓
Accounting Consumer
```

本阶段建立：

```text
LLMObservation
      ↓
Persistence Boundary
      ↓
LLM Usage Storage
```

目标：

**建立最小 request-level Usage 持久化能力。**

---

# 二、严格范围

## 允许

允许新增：

```text
backend/app/llm/
backend/app/models/
backend/app/repositories/
backend/app/services/
tests/
docs/architecture.md
docs/evaluation/
```

具体目录必须先阅读当前项目结构，再选择最自然的位置。

允许：

* 新增数据库模型
* 新增 migration
* 新增 repository
* 新增 persistence DTO
* 新增 persistence service
* 新增数据库测试
* 新增 architecture 文档
* 新增少量 integration tests

---

# 三、严格禁止

本阶段禁止：

```text
Billing
Dashboard
Web UI
Admin UI
Prometheus
Grafana
Langfuse
OpenTelemetry
Sentry
Kafka
Redis
消息队列
异步任务系统
用户账单
月度账单
价格 API
自动获取价格
模型价格硬编码
Session Aggregation
日统计
月统计
项目统计 API
用户统计 API
成本报表
```

禁止修改：

```text
RAG
Tool Framework
Router
Orchestrator
Text-to-SQL Generator
SQL Validator
SQL Executor
RelevantTableSelector
Business Semantic
Project Context
Prompt
```

除非发现本阶段无法完成的明确 bug。

如果发现核心业务 bug：

**先停止并报告，不要顺手修改。**

---

# 四、开始编码前必须先阅读

先阅读真实代码：

```text
backend/app/llm/accounting.py
backend/app/llm/accounting_consumer.py
backend/app/llm/observability.py
backend/app/llm/client.py

backend/app/models/
backend/app/repositories/
backend/app/services/

tests/test_llm_accounting.py
tests/test_llm_accounting_integration.py
tests/test_llm_accounting_lifecycle.py
tests/test_llm_accounting_consumption.py
tests/test_llm_usage_visibility.py

docs/architecture.md
```

同时搜索：

```text
SQLAlchemy
Base
DeclarativeBase
sessionmaker
AsyncSession
create_engine
migration
alembic
metadata.create_all
RUN_DB_TESTS
```

重点确认：

1. 当前 SQLAlchemy 使用方式
2. 当前数据库 Session 生命周期
3. 当前 model 命名方式
4. 当前 migration 机制
5. 当前测试数据库 fixture
6. 当前 DB transaction / rollback 方式
7. 是否已有 repository pattern
8. 是否已有 audit / execution / request 记录模型

**必须复用现有数据库基础设施。**

不要重新创建：

```text
Engine
Session Factory
Base
Migration Framework
```

---

# 五、第一原则：Observation 与 Persistence 解耦

必须保持：

```text
LLMObservation
        ↓
Persistence Adapter
        ↓
Database
```

而不是：

```text
LLMObservation
        ↓
SQLAlchemy
```

Observation 不得：

```text
import sqlalchemy
```

不要把数据库逻辑放进：

```text
backend/app/llm/observability.py
```

---

# 六、Accounting Consumer 与 Persistence 的关系

当前：

```text
LLMObservation
    ↓
consume_usage()
    ↓
LLMUsage
```

本阶段增加：

```text
LLMObservation
    ↓
Usage Persistence
    ↓
Database
```

Persistence 层可以使用：

```text
observation.usage
```

但不要重新计算：

```text
total_tokens
```

不要创建新的 token DTO。

继续复用：

```text
LLMUsage
```

---

# 七、是否需要新的 Persistence DTO

先判断当前结构。

禁止无意义地新增：

```text
UsageRecord
TokenUsageRecord
AccountingRecord
```

如果数据库 ORM Model 与 `LLMUsage` 在职责上确实不同：

允许建立：

```text
LLMUsagePersistenceModel
```

或项目当前命名风格对应的数据库 Model。

原因必须明确：

```text
LLMUsage
=
domain/request-level DTO

DB Model
=
persistence representation
```

两者可以不同。

但不要再创建第三套 Usage DTO。

---

# 八、数据库表设计

建议建立最小表：

```text
llm_usage_record
```

最终字段必须根据当前项目命名规范决定。

至少需要考虑：

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

其中：

```text
request_id
```

必须来自：

```text
LLMObservation.request_id
```

不能重新生成假的 Provider request ID。

---

# 九、request_id 的特殊情况

如果：

```text
observation.request_id is None
```

不要：

```text
uuid.uuid4()
```

冒充 Provider request ID。

需要根据当前项目设计决定：

### 方案 A

允许数据库：

```text
request_id = NULL
```

### 方案 B

另外增加：

```text
internal_request_id
```

但如果选择 B：

必须明确区分：

```text
provider_request_id
internal_request_id
```

绝对不能把内部 UUID 称为 Provider request ID。

如果当前架构没有明确需求：

**优先方案 A，保持最小。**

---

# 十、provider

直接使用：

```text
LLMObservation.provider
```

如果：

```text
None
```

数据库可以：

```text
NULL
```

禁止：

```text
unknown
deepseek
default
```

等隐式填充。

---

# 十一、model

直接使用：

```text
LLMObservation.model
```

禁止使用：

```text
settings.model
```

作为 fallback。

原因：

Observation 记录的是：

> 实际 Provider 返回的模型。

---

# 十二、Usage 字段

直接保存：

```text
prompt_tokens
completion_tokens
total_tokens
```

保持：

```text
None ≠ 0
```

例如：

```text
prompt_tokens=None
completion_tokens=200
total_tokens=None
```

数据库必须能够保留：

```text
NULL
200
NULL
```

不能转换为：

```text
0
200
0
```

---

# 十三、Cost 本阶段如何处理

**默认不持久化 Cost。**

原因：

```text
Usage = Provider fact
Cost = Pricing-dependent calculation
```

当前项目没有正式：

```text
Pricing Registry
```

因此：

本阶段数据库只保存：

```text
Usage
```

不保存：

```text
input_price
output_price
currency
input_cost
output_cost
total_cost
```

不要为了 Cost 建表。

---

# 十四、LLMUsage 不允许保存 Pricing

数据库不要保存：

```text
model_price
input_price
output_price
```

也不要建立：

```text
llm_pricing
```

表。

本阶段：

```text
Pricing = future external input
```

---

# 十五、成功 / 失败请求

本阶段重点是 Usage。

先判断：

```text
LLMObservation.success
```

对于：

```text
success=True
usage != None
```

正常写入。

对于：

```text
success=True
usage=None
```

可以写入一条 request record，但 token 字段全部 NULL。

对于：

```text
success=False
```

如果：

```text
usage=None
```

则根据最终 schema 决定：

### 推荐

**不进入 Usage Record。**

因为本表是：

```text
LLM Usage Storage
```

不是：

```text
LLM Request Audit Log
```

不要为了记录失败请求而扩大本阶段。

如果当前架构认为失败调用也必须记录：

先停止并报告理由，不要自动扩展成 Audit Log。

---

# 十六、不要把 Observation 全量落库

绝对禁止保存：

```text
prompt
messages
system prompt
tool definitions
tool arguments
tool results
SQL
RAG chunks
exception message
stack trace
raw response
headers
API key
authorization
database URL
```

数据库只能保存明确批准的 Usage 字段。

---

# 十七、created_at

可以增加：

```text
created_at
```

表示：

> Usage Record 持久化记录产生的时间。

但不要误称为：

```text
llm_started_at
```

也不要在本阶段增加复杂 tracing timestamp：

```text
started_at
completed_at
first_token_at
```

除非当前 Observation 已经存在这些字段且有明确需求。

保持最小。

---

# 十八、ID 设计

如果现有项目数据库统一使用 UUID：

复用现有方式。

如果现有表使用：

```text
BIGINT
```

继续遵循项目规范。

不要为了本表引入：

```text
Snowflake
ULID
NanoID
```

等新机制。

---

# 十九、索引

不要过度设计。

本阶段只考虑：

```text
request_id
created_at
```

是否需要索引。

如果：

```text
request_id
```

允许 NULL，则不要为了“看起来完整”建立无意义的复杂索引。

如果当前项目没有明确查询需求：

**可以暂时不新增额外业务索引。**

---

# 二十、Repository

如果项目已有 repository pattern：

复用。

例如：

```text
LLMUsageRepository
```

最小 API：

```text
create(...)
```

不要提前实现：

```text
list_by_user()
list_by_project()
daily_usage()
monthly_usage()
aggregate()
sum_tokens()
sum_cost()
```

本阶段只有：

> 保存一条 request-level Usage Record。

---

# 二十一、Persistence Service

如果需要 service：

例如：

```text
LLMUsagePersistenceService
```

职责：

```text
LLMObservation
    ↓
validate persistence fields
    ↓
create DB model
    ↓
repository.create()
```

不得：

```text
calculate cost
```

不得：

```text
aggregate
```

不得：

```text
retry LLM
```

不得：

```text
modify observation
```

---

# 二十二、数据库失败不能污染 LLM 业务结果

这是本阶段最重要的设计之一。

如果：

```text
LLM call = SUCCESS
```

然后：

```text
Usage persistence = FAILURE
```

不能把：

```text
AI answer
```

改成：

```text
HTTP 500
```

原则：

```text
LLM business result
        ≠
Usage persistence result
```

推荐：

```text
LLM success
     ↓
try persist usage
     ↓
persist failure
     ↓
log warning
     ↓
business result still succeeds
```

但：

**不要在本阶段实现复杂重试队列。**

---

# 二十三、Persistence Failure Handling

禁止：

```text
LLM retry
```

禁止：

```text
sleep
```

禁止：

```text
backoff
```

禁止：

```text
Kafka
```

禁止：

```text
Redis
```

如果数据库写失败：

```text
warning
+
business result unchanged
```

如果当前项目已有统一 logging：

复用。

不要新增 logging framework。

---

# 二十四、是否自动接入 Client

本阶段先判断：

是否应该修改：

```text
backend/app/llm/client.py
```

原则：

**不要直接让 Client 依赖 SQLAlchemy。**

如果要自动接入：

```text
Client
 ↓
AccountingSink
 ↓
Persistence Adapter
```

则必须通过：

```text
LLMAccountingSink
```

而不是：

```text
Client → Repository
```

推荐架构：

```text
LLM Client
   ↓
LLMObservation
   ↓
LLMAccountingSink
   ↓
Usage Persistence Adapter
   ↓
Repository
   ↓
PostgreSQL
```

---

# 二十五、默认行为

这是关键：

默认：

```text
create_llm_client()
```

仍然不能因为本阶段而自动连接数据库。

也就是说：

如果没有显式配置：

```text
Persistence Accounting Sink
```

则：

```text
LLM call
→ Observation
→ Noop Accounting
```

保持现有行为。

不要改变：

```text
MockLLMClient
```

的默认行为。

---

# 二十六、Persistence Sink

如果当前：

```text
LLMAccountingSink
```

已经适合作为边界：

可以新增：

```text
DatabaseLLMAccountingSink
```

或者项目风格对应名称。

职责：

```text
record(observation)
    ↓
if usage is None:
    return

persist usage
```

不能：

```text
calculate cost
```

不能：

```text
aggregate
```

不能：

```text
modify observation
```

---

# 二十七、事务

Repository 使用当前项目标准 Session/transaction。

不要：

```text
commit
```

到处散落。

如果现有项目采用：

```text
service → session.begin()
```

复用。

如果 repository 负责 commit：

遵循现有项目规范。

**不要在本阶段重新设计 transaction architecture。**

---

# 二十八、幂等性

本阶段至少讨论：

```text
同一个 observation.record()
```

被调用两次怎么办？

不要自动产生重复记录。

推荐：

如果：

```text
provider_request_id
```

可靠且非空：

可以考虑唯一约束。

但如果：

```text
request_id = NULL
```

则不能简单依赖唯一约束实现幂等。

如果当前项目没有足够信息设计可靠幂等：

**不要伪造幂等机制。**

在文档中明确：

```text
Persistence is at-least-once / caller-controlled
```

或者：

```text
Idempotency is not guaranteed in this phase
```

选择符合实际实现的描述。

---

# 二十九、测试数据库

必须使用：

```text
RUN_DB_TESTS=1
```

现有测试数据库。

不要：

```text
生产数据库
开发数据库
真实 WMS 数据
```

---

# 三十、Migration

如果项目使用 Alembic：

新增 migration。

必须：

```text
upgrade
```

和：

```text
downgrade
```

都能够工作。

如果项目当前没有 migration：

不要突然引入 Alembic。

复用当前机制。

---

# 三十一、DB Schema 测试

至少测试：

### 1. Table exists

```text
llm_usage_record
```

存在。

### 2. Full usage

```text
100
20
120
```

正确保存。

### 3. Partial usage

例如：

```text
None
200
None
```

数据库：

```text
NULL
200
NULL
```

### 4. None usage

确认 sink 不产生错误。

### 5. Provider

```text
deepseek
```

正确保存。

### 6. Model

使用：

```text
LLMResponse.model
```

保存。

### 7. Request ID

正确保存。

### 8. Secrets

确认数据库记录不包含：

```text
api_key
password
authorization
```

---

# 三十二、Persistence Failure Test

模拟：

```text
repository.create()
```

抛异常。

确认：

```text
LLM business result = unchanged
```

并且：

```text
LLM call 不 retry
```

---

# 三十三、Concurrency

至少：

```text
5 concurrent LLM observations
```

写入数据库。

确认：

```text
5 records
```

每条：

```text
request_id
usage
```

正确对应。

禁止：

```text
usage cross-talk
```

---

# 三十四、Tool Calling

两轮：

```text
Observation 1
Observation 2
```

分别保存。

数据库必须是：

```text
2 records
```

不能：

```text
1 aggregate record
```

---

# 三十五、T2S Retry

验证：

```text
LLM Request 1
    ↓
Usage 1

LLM Request 2
    ↓
Usage 2
```

数据库：

```text
2 request-level records
```

不要保存：

```text
retry_total_tokens
```

不要聚合。

---

# 三十六、RAG

RAG：

```text
LLM call
↓
Observation
↓
Usage persistence
```

业务答案不改变。

数据库新增：

```text
1 usage record
```

---

# 三十七、Refusal

这是重点。

Phase 3.9.25 / 3.9.26 已经固定：

```text
refusal
→ 1 LLM call
→ attempts=1
→ validator=0
→ executor=0
```

本阶段确认：

```text
refusal
→ Observation
→ Usage persistence
```

如果 refusal LLM response 有 usage：

保存 usage。

不要：

```text
retry
```

不要：

```text
special billing
```

---

# 三十八、Cost

本阶段数据库：

```text
NO COST
```

但是测试可以确认：

```text
LLMUsage
```

未来仍然能够进入：

```text
consume_cost()
```

不会因为 persistence 改变 Usage DTO。

---

# 三十九、Security

数据库中只能出现明确字段：

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

如果实际 schema 需要其他字段：

必须在文档中解释原因。

禁止：

```text
prompt
messages
sql
rag_content
tool_args
tool_result
raw_response
headers
secret
```

---

# 四十、Documentation

修改：

```text
docs/architecture.md
```

新增：

```text
§8.14 LLM Usage Persistence Contract
```

明确：

```text
LLMObservation
      ↓
LLMAccountingSink
      ↓
Database Persistence
```

并明确：

```text
Usage persisted = YES
Cost persisted = NO
Billing = NO
Aggregation = NO
Pricing Registry = NO
Dashboard = NO
```

说明：

```text
Persistence failure must not change LLM business result.
```

---

# 四十一、Evaluation Document

新增：

```text
docs/evaluation/Phase 3.10.14 — Usage Persistence.md
```

记录：

## Environment

```text
Python
PostgreSQL
pytest
```

## Schema

```text
llm_usage_record
```

## Cases

```text
Full Usage
Partial Usage
None Usage
Tool Calling
T2S Retry
RAG
Refusal
Concurrency
Persistence Failure
```

## Security

```text
No Prompt
No SQL
No RAG content
No Tool args/results
No Secrets
```

## Final

```text
Usage Persistence = PASS
Cost Persistence = NOT IMPLEMENTED
Billing = NOT IMPLEMENTED
Aggregation = NOT IMPLEMENTED
```

---

# 四十二、测试命令

Windows PowerShell：

```powershell
python -m pytest tests/test_llm_usage_persistence.py -q
```

然后：

```powershell
python -m pytest tests/test_llm_accounting.py tests/test_llm_accounting_integration.py tests/test_llm_accounting_lifecycle.py tests/test_llm_accounting_consumption.py tests/test_llm_usage_visibility.py -q
```

然后：

```powershell
python -m pytest -q
```

最后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

编译：

```powershell
python -m compileall backend tests scripts
```

如果项目已有 lint：

继续运行当前项目 lint。

---

# 四十三、DB Residue

测试结束后必须确认：

```text
测试开始 Usage Record 数量
        =
测试结束 Usage Record 数量
```

对于需要写入的 integration test：

必须使用：

```text
rollback
```

或者：

```text
cleanup fixture
```

不能留下测试垃圾。

注意：

本阶段测试数据库可以临时产生：

```text
llm_usage_record
```

但是测试完成后：

```text
test rows = 0
```

---

# 四十四、Regression Baseline

Phase 3.10.13：

```text
2153 passed
267 skipped
0 failed

DB:
2379 passed
41 skipped
0 failed
```

本阶段新增测试后：

旧测试不得减少。

生产行为不得发生无关变化。

---

# 四十五、Git Diff

完成：

```powershell
git status --short
git diff --stat
git diff
```

重点确认：

```text
□ 无 RAG 修改
□ 无 Router 修改
□ 无 Tool Framework 修改
□ 无 T2S 修改
□ 无 Validator 修改
□ 无 Executor 修改
□ 无 Prompt 修改
□ 无 Project Context 修改
□ 无真实 Pricing
□ 无 Billing
□ 无 Aggregation
□ 无 Dashboard
□ 无 Telemetry Platform
□ 无无关依赖
```

如果新增 migration：

只允许：

```text
llm_usage_record
```

相关 migration。

---

# 四十六、最终验收

必须满足：

```text
□ Usage Persistence Contract 建立
□ Observation 与 DB 解耦
□ 不重复定义 Usage DTO
□ LLMUsage 事实不被修改
□ Full Usage 正确保存
□ Partial Usage 正确保存
□ None ≠ 0
□ Provider 正确
□ Model 来自实际 response
□ Request ID 不伪造
□ Cost 不落库
□ Pricing 不落库
□ Prompt 不落库
□ SQL 不落库
□ RAG 内容不落库
□ Tool 参数/结果不落库
□ Secrets 不落库
□ Persistence failure 不影响业务结果
□ 无 LLM retry
□ Tool Calling request-level
□ T2S Retry request-level
□ RAG request-level
□ Refusal request-level
□ 5 并发无串线
□ DB migration 正常
□ DB cleanup 正常
□ pytest 通过
□ DB regression 通过
□ compileall 通过
```

---

# 四十七、最终报告

完成后严格按照：

```text
【Phase 3.10.14 COMPLETE】

1. 新增文件
2. 修改文件
3. Database Schema
4. Usage Persistence Contract
5. Persistence Sink
6. Repository
7. Transaction
8. Full / Partial / None
9. Tool Calling
10. T2S Retry
11. RAG
12. Refusal
13. Concurrency
14. Persistence Failure
15. Security
16. Cost
17. Tests
18. DB Regression
19. DB Residue
20. Network
21. Production Path
22. Git Diff
23. 当前限制

Phase 3.10.14 READY

Phase 3.10.14 STOP
```

---

# 四十八、硬停止

完成后立即停止。

不要进入：

```text
Usage Dashboard
Cost Dashboard
Billing
Pricing Registry
Daily Aggregation
Monthly Aggregation
User Billing
Project Billing
OpenTelemetry
Langfuse
Prometheus
Grafana
Kafka
Redis
```

这些全部留到后续明确 Phase。

**不要自动进入 Phase 3.10.15。**
