你现在开始实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.10.15：LLM Usage Persistence Runtime Hardening

## 一、阶段目标

Phase 3.10.14 已完成：

```text
LLMObservation
    ↓
DatabaseLLMAccountingSink
    ↓
LLMUsagePersistenceService
    ↓
LLMUsageRepository
    ↓
ai_ops.llm_usage_record
```

已经证明：

* Usage 可以正确持久化
* Full / Partial / None 语义正确
* Tool Calling 正确
* T2S Retry 正确
* RAG 正确
* Refusal 正确
* Persistence Failure 不影响业务结果
* Concurrency 基础场景正确

但当前存在一个明确的运行时限制：

```text
async LLM request
      ↓
同步 SQLAlchemy DB write
      ↓
可能短暂阻塞 event loop
```

因此本阶段唯一目标：

> **验证并最小化 LLM Usage Persistence 对异步业务请求链路的运行时影响。**

本阶段不是重新设计 Persistence。

不是重新设计 Repository。

不是引入消息队列。

不是实现异步数据库体系。

---

# 二、严格范围

## 允许

允许修改：

```text
backend/app/llm/
backend/app/services/llm_usage_persistence_service.py
backend/app/db/llm_usage_repository.py
tests/
docs/architecture.md
docs/evaluation/
```

必要时允许增加：

```text
backend/app/services/
```

中的极少量 runtime adapter。

如果当前架构已经有适合的 async bridge，则优先复用。

---

## 禁止

绝对禁止：

```text
Kafka
Redis
RabbitMQ
Celery
消息队列
Outbox
Event Bus
Background Worker
独立 Persistence Service
OpenTelemetry
Langfuse
Prometheus
Grafana
Sentry
Billing
Dashboard
Pricing Registry
Cost Persistence
Daily Aggregation
Monthly Aggregation
User Aggregation
Project Aggregation
```

禁止修改：

```text
RAG
Tool Framework
Router
Orchestrator
Text-to-SQL
Validator
Executor
Business Semantic
Project Context
Prompt
Schema Explorer
```

除非测试证明存在明确 bug。

---

# 三、开始编码前必须阅读真实代码

先完整阅读：

```text
backend/app/llm/client.py
backend/app/llm/observability.py
backend/app/llm/accounting.py
backend/app/llm/accounting_consumer.py

backend/app/services/llm_usage_persistence_service.py

backend/app/db/llm_usage_repository.py
backend/app/db/models/llm_usage_record.py
backend/app/db/init_db.py

tests/test_llm_usage_persistence.py
tests/test_llm_accounting_lifecycle.py
tests/test_llm_observability.py
tests/test_llm_usage_visibility.py

docs/architecture.md
docs/evaluation/Phase 3.10.14 — Usage Persistence.md
```

同时搜索：

```text
DatabaseLLMAccountingSink
LLMAccountingSink
LLMUsagePersistenceService
LLMUsageRepository
session_factory
create_llm_client
chat(
generate(
```

必须确认当前真实调用关系。

不要假设接口。

---

# 四、先回答一个核心问题

开始修改代码前，先判断：

> 当前 `DatabaseLLMAccountingSink.record()` 是否在 async LLM request 的 event loop 中直接执行同步 SQLAlchemy 操作？

如果：

```text
是
```

再设计最小 async bridge。

如果：

```text
否
```

不要为了本阶段强行重构。

如果当前代码已经通过其他机制避免 event-loop blocking：

**保持现有实现。**

---

# 五、核心架构目标

理想情况下形成：

```text
Async LLM Request
        │
        ▼
   LLMObservation
        │
        ▼
 DatabaseLLMAccountingSink
        │
        ▼
 Runtime Persistence Boundary
        │
        ▼
 synchronous Repository
        │
        ▼
 PostgreSQL
```

关键原则：

```text
LLM Business Result
        │
        ├──────────────► caller
        │
        └──────────────► optional persistence
```

Persistence 永远不能成为 LLM Business Result 的前置依赖。

---

# 六、最重要原则：业务结果优先

必须继续保持 Phase 3.10.14：

```text
Persistence success
    → business success

Persistence failure
    → business success
```

也就是说：

```text
LLM call success
+
DB persistence failure
=
LLM call still succeeds
```

禁止：

```text
DB failure
    ↓
LLM retry
```

禁止：

```text
DB failure
    ↓
HTTP 500
```

禁止：

```text
DB failure
    ↓
LLM request retry
```

---

# 七、不要修改 LLM Retry

继续保持：

```text
LLM transport retry
    = existing behavior

Text-to-SQL semantic retry
    = existing behavior
```

Persistence 不得进入：

```text
retry counter
attempts
TextToSQL attempts
LLM retry
```

例如：

```text
T2S:
LLM Request #1
Validator reject
LLM Request #2
```

仍然应该：

```text
2 LLM observations
2 usage records
```

Persistence 本身：

```text
0 retry
```

---

# 八、Async Bridge 设计

如果确认当前同步数据库写入发生在 async request event loop：

优先考虑最小：

```text
asyncio.to_thread(...)
```

或者项目已有等价线程边界。

但是：

**必须先检查项目当前 async/sync 架构。**

不要直接假设 `asyncio.to_thread()` 一定适合。

---

# 九、线程安全

如果使用：

```text
asyncio.to_thread()
```

必须确认：

> SQLAlchemy Session 不跨线程复用。

绝对禁止：

```text
request thread
    ↓
existing Session
    ↓
worker thread
```

正确方向应该是：

```text
worker thread
    ↓
session_factory()
    ↓
new Session
    ↓
transaction
    ↓
commit / rollback
```

继续复用现有：

```text
session_factory
```

不要共享：

```text
Session
Connection
Transaction
```

---

# 十、Repository Contract

不要为了 async bridge 重写 Repository。

当前：

```text
LLMUsageRepository.create()
```

继续保持同步 repository。

如果需要增加 async adapter：

推荐：

```text
AsyncRuntimeAdapter
    ↓
existing synchronous Repository
```

而不是：

```text
重新创建 AsyncLLMUsageRepository
```

除非现有项目已经有成熟 async SQLAlchemy repository pattern。

---

# 十一、不要复制 Persistence DTO

继续复用：

```text
LLMObservation
LLMUsage
LLMUsageRecord
```

禁止新增：

```text
AsyncUsage
UsageEvent
UsageMessage
PersistenceUsage
```

等重复 DTO。

---

# 十二、Persistence Sink Contract

继续保持：

```text
LLMAccountingSink.record(observation)
```

如果需要异步边界：

不要破坏现有同步 Sink Contract，除非当前架构明确要求。

可以考虑：

```text
DatabaseLLMAccountingSink
```

内部使用 runtime bridge。

但：

**必须保证现有同步测试仍然工作。**

---

# 十三、同步调用兼容性

必须保持：

```python
sink.record(observation)
```

仍然可以被当前测试和同步调用方使用。

不要突然变成：

```python
await sink.record(...)
```

除非确认整个现有 Sink Contract 都可以安全迁移。

本阶段优先：

> 最小 runtime bridge，而不是 API breaking change。

---

# 十四、Async Event Loop Non-Blocking Test

这是本阶段最重要的新测试。

需要证明：

> 同步 DB persistence 不会长时间阻塞 async event loop。

不要依赖真实 PostgreSQL 做这个测试。

使用 Fake Persistence / Fake Repository。

构造：

```text
Persistence takes 100~300ms
```

同时运行：

```text
heartbeat task
```

例如：

```text
heartbeat
heartbeat
heartbeat
heartbeat
```

如果 Persistence 仍直接运行在 event loop：

```text
heartbeat 会明显停止
```

如果进入 thread boundary：

```text
heartbeat 可以继续运行
```

测试不要求极端精确的 latency benchmark。

只需要证明：

```text
event loop remains schedulable
```

---

# 十五、Concurrency

增加：

```text
5 concurrent LLM requests
```

每个：

```text
different request_id
different token count
```

最终验证：

```text
5 observations
5 persistence operations
5 correct request_id
5 correct usage
```

不能：

```text
cross contamination
```

---

# 十六、Thread / Session Isolation

如果使用 thread bridge：

必须增加测试确认：

```text
request A
    ↓
Session A

request B
    ↓
Session B
```

不得：

```text
shared Session
```

尤其不能：

```text
global Session
global Connection
global transaction
```

---

# 十七、Persistence Failure

继续验证：

```text
Repository raises Exception
```

结果：

```text
LLM business result = unchanged
```

并且：

```text
LLM retry = 0
Persistence retry = 0
```

如果使用 thread bridge：

异常必须能够被 persistence boundary 捕获。

不能因为：

```text
asyncio.to_thread()
```

导致异常泄漏到业务请求。

---

# 十八、Cancellation

增加一个最小测试：

```text
async task
    ↓
LLM result ready
    ↓
persistence running
    ↓
caller cancellation
```

需要明确当前架构语义。

目标不是实现复杂 cancellation framework。

只需要确保：

> Persistence 不会破坏已经完成的 LLM Business Result。

如果当前同步 persistence 已经无法支持可靠 cancellation：

记录为：

```text
Known Limitation
```

不要扩大设计。

---

# 十九、No Retry

明确测试：

```text
repository fails
```

确认：

```text
repository calls == 1
```

不能：

```text
2
3
N
```

不要加入：

```text
backoff
sleep
queue
```

---

# 二十、Database Tests

继续使用：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

数据库测试需要确认：

```text
5 concurrent writes
```

最终：

```text
5 rows
```

并且：

```text
request_id
token counts
```

全部对应。

测试完成：

```text
TRUNCATE ai_ops.llm_usage_record
```

确保：

```text
DB residue = 0
```

---

# 二十一、事务

继续复用：

```text
with session_factory() as session:
    with session.begin():
        ...
```

不要重新设计：

```text
transaction manager
unit of work
outbox
```

Persistence failure：

```text
rollback
```

必须保持。

---

# 二十二、Production Default

继续保持：

```text
create_llm_client()
```

默认：

```text
NoopAccountingSink
```

所以：

```text
普通开发运行
    ↓
不会自动写 ai_ops.llm_usage_record
```

必须显式：

```text
DatabaseLLMAccountingSink
```

才启用 Persistence。

不要改变默认行为。

---

# 二十三、Mock LLM

继续保持：

```text
MockLLMClient
```

默认行为不变。

不要为了 Persistence Runtime Hardening 给 Mock 增加：

```text
fake usage
fake request id
fake cost
```

除非已有测试明确需要。

---

# 二十四、Observability 不得反向依赖 DB

继续确认：

```text
backend/app/llm/observability.py
```

不能 import：

```text
sqlalchemy
psycopg
database
repository
```

保持：

```text
Observation
    ↓
Sink
```

而不是：

```text
Observation
    ↓
Database
```

---

# 二十五、Security

Persistence 仍然只能保存：

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

继续禁止：

```text
prompt
messages
system_prompt
SQL
RAG content
tool args
tool result
raw response
headers
API key
password
authorization
database URL
exception message
stack trace
cost
price
currency
```

如果本阶段新增 runtime adapter：

也不得扩大字段。

---

# 二十六、性能记录

不要建立 benchmark platform。

只做非常轻量的验证：

```text
direct sync persistence
```

和：

```text
runtime bridged persistence
```

确认：

```text
event loop blocking
```

是否得到改善。

不要记录：

```text
P50
P95
P99
throughput dashboard
```

除非现有测试基础设施已经天然提供。

---

# 二十七、Documentation

修改：

```text
docs/architecture.md
```

新增：

```text
§8.15 LLM Usage Persistence Runtime Boundary
```

明确：

```text
LLM Business Result
        ↓
Observation
        ↓
Persistence Runtime Boundary
        ↓
Repository
```

说明：

1. Persistence 不属于 LLM Business Result
2. Persistence failure 不影响业务
3. Repository Session 不跨线程共享
4. 如果使用 thread bridge，worker thread 创建独立 Session
5. 不做 retry
6. 不做 queue
7. 不做 aggregation
8. 不做 billing
9. 默认仍为 NoopAccountingSink

---

# 二十八、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.10.15 — Persistence Runtime Hardening.md
```

记录：

## 1. Runtime Problem

```text
sync DB write inside async path
```

## 2. Solution

如果有：

```text
asyncio.to_thread
```

记录实际方案。

如果没有必要修改：

明确记录原因。

## 3. Event Loop Test

记录：

```text
blocking = PASS / FAIL
```

## 4. Concurrency

记录：

```text
5 requests
5 persistence records
```

## 5. Session Isolation

记录：

```text
shared session = false
```

## 6. Failure Isolation

记录：

```text
DB failure
→ business result unchanged
```

## 7. Retry

记录：

```text
LLM retry = 0
Persistence retry = 0
```

## 8. DB Residue

记录：

```text
0 rows
```

---

# 二十九、测试文件

新增：

```text
tests/test_llm_usage_persistence_runtime.py
```

至少覆盖：

1. sync persistence compatibility
2. async runtime boundary
3. event loop remains schedulable
4. 5 concurrent requests
5. session isolation
6. persistence failure isolation
7. persistence failure no retry
8. cancellation behavior
9. default NoopAccountingSink unchanged
10. DatabaseLLMAccountingSink contract unchanged
11. security fields unchanged
12. no global Session
13. no global Connection
14. no global transaction
15. deterministic behavior

---

# 三十、AST / Static Checks

如果新增 async bridge：

检查：

```text
backend/app/llm/
backend/app/services/
```

不存在：

```text
global SQLAlchemy Session
global Connection
global transaction
```

也不要出现：

```text
asyncio.create_task(...)
```

用于偷偷后台持久化。

禁止：

```text
fire-and-forget
```

因为这样会造成：

```text
task lifecycle
shutdown
exception
data loss
```

等问题。

---

# 三十一、测试命令

先运行：

```powershell
python -m pytest tests/test_llm_usage_persistence_runtime.py -q
```

然后：

```powershell
python -m pytest tests/test_llm_usage_persistence.py tests/test_llm_accounting_lifecycle.py tests/test_llm_accounting_consumption.py -q
```

然后：

```powershell
python -m pytest -q
```

然后：

```powershell
python -m compileall backend tests scripts
```

最后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

---

# 三十二、Baseline

Phase 3.10.14：

```text
pytest:
2153 passed
284 skipped
0 failed
```

DB：

```text
2396 passed
41 skipped
0 failed
```

本阶段新增测试允许增加 passed。

但是：

```text
旧测试不得因为本阶段而删除
```

不要修改旧测试来隐藏问题。

---

# 三十三、如果发现真正架构问题

如果发现：

```text
同步 DB persistence
```

无法安全放进当前 async architecture：

**不要立即引入复杂基础设施。**

先报告：

```text
问题：
影响：
当前架构限制：
最小可行方案：
是否建议进入下一阶段：
```

本阶段允许：

```text
最小 runtime adapter
```

不允许：

```text
Queue
Worker
Outbox
Kafka
Redis
Celery
```

---

# 三十四、Git Diff

完成后执行：

```powershell
git status --short
git diff --stat
git diff
```

重点确认：

```text
□ RAG 未修改
□ Router 未修改
□ Tool Framework 未修改
□ T2S 未修改
□ Validator 未修改
□ Executor 未修改
□ Project Context 未修改
□ Prompt 未修改
□ Business Semantic 未修改
□ 默认 NoopAccountingSink 未改变
□ 无新数据库表
□ 无 migration
□ 无第三方依赖
□ 无 Queue
□ 无 Redis
□ 无 Kafka
□ 无 Billing
□ 无 Dashboard
```

---

# 三十五、最终验收标准

必须满足：

```text
□ 当前 async persistence runtime 问题已明确
□ 如果需要 bridge，使用最小方案
□ Repository API 没有无必要重构
□ Session 不跨线程共享
□ Connection 不跨线程共享
□ Transaction 不跨线程共享
□ Event loop 不被同步 persistence 长时间阻塞
□ 5 并发 persistence 正常
□ Persistence failure 不影响业务结果
□ Persistence failure 不触发 LLM retry
□ Persistence failure 不触发 persistence retry
□ Cancellation 行为明确
□ Security whitelist 保持
□ Default NoopAccountingSink 保持
□ MockLLMClient 行为保持
□ DB residue = 0
□ 全量测试通过
□ DB regression 通过
□ compileall 通过
```

---

# 三十六、最终报告格式

完成后严格输出：

```text
【Phase 3.10.15 COMPLETE】

1. Runtime 问题
2. 解决方案
3. 修改文件
4. Async Event Loop
5. Session / Connection Isolation
6. Concurrency
7. Persistence Failure
8. Retry
9. Cancellation
10. Security
11. Default Production Path
12. Tests
13. DB Regression
14. Network
15. DB writes / residue
16. Git Diff
17. 当前限制
18. 是否需要下一阶段 Runtime Infrastructure

Phase 3.10.15 READY
Phase 3.10.15 STOP
```

---

# 三十七、硬停止

完成本阶段后立即 STOP。

不要进入：

```text
Billing
Dashboard
Pricing Registry
Cost Persistence
Aggregation
OpenTelemetry
Langfuse
Prometheus
Grafana
Kafka
Redis
Queue
Outbox
```

也不要开发：

```text
Agent
MCP
Multi-Agent
Planning
Memory
```

当前唯一目标：

> **让已经完成的 LLM Usage Persistence 在异步业务运行环境下安全、隔离、可控。**

完成后停止，等待下一阶段指令。
