你现在开始实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.10.18：LLM Usage Query Async Runtime Boundary

## 一、阶段目标

本阶段只解决：

**LLM Usage Query 在 async 业务调用环境中不能阻塞 event loop。**

当前：

```text
async business
      ↓
LLMUsageQueryService
      ↓
LLMUsageRepository
      ↓
同步 SQLAlchemy Session
      ↓
PostgreSQL
```

如果直接在 async event loop 中执行同步 DB Query：

```text
asyncio event loop
      ↓
sync SQLAlchemy
      ↓
PostgreSQL
      ↓
event loop 被阻塞
```

本阶段建立：

```text
async caller
      ↓
Usage Query Runtime Bridge
      ↓
asyncio.to_thread()
      ↓
LLMUsageQueryService
      ↓
LLMUsageRepository
      ↓
sync SQLAlchemy
      ↓
PostgreSQL
```

---

# 二、重要原则

**不要把当前 Repository 改造成 AsyncSession。**

不要：

```text
AsyncEngine
AsyncSession
async_sessionmaker
asyncpg
```

不要为了一个 Query Read Boundary 改造整个数据库基础设施。

当前：

```text
Repository = synchronous
```

保持不变。

新增：

```text
Runtime Bridge = asynchronous
```

这样与 Phase 3.10.15 的 Persistence Runtime 保持一致。

SQLAlchemy 官方支持 asyncio，但 `AsyncSession` 本身也有独立的并发使用规则；本阶段没有必要为了 Query 引入第二套数据库访问体系。

---

# 三、开始编码前必须先阅读

先阅读：

```text
backend/app/services/llm_usage_query_service.py
backend/app/db/llm_usage_repository.py
backend/app/services/llm_usage_persistence_runtime.py
backend/app/services/llm_usage_persistence_service.py
```

重点参考：

```text
backend/app/services/llm_usage_persistence_runtime.py
```

以及项目已有：

```text
asyncio.to_thread
```

使用位置。

搜索：

```text
asyncio.to_thread
```

确认是否已经存在其它成熟 Runtime Bridge。

**优先复用已有模式。**

---

# 四、严格范围

## 允许

新增：

```text
backend/app/services/llm_usage_query_runtime.py
```

新增：

```text
tests/test_llm_usage_query_runtime.py
```

新增：

```text
docs/evaluation/Phase 3.10.18 — Usage Query Async Runtime.md
```

修改：

```text
docs/architecture.md
```

必要时对：

```text
llm_usage_query_service.py
```

做极小的兼容性修改。

---

## 禁止

不要修改：

```text
AI Router
AI Orchestrator
RAG
Tool
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
Persistence
Persistence Runtime
Persistence Idempotency
Usage Repository SQL
Usage Query DTO Contract
```

不要增加：

```text
AsyncEngine
AsyncSession
asyncpg
Redis
Kafka
Queue
Worker
Outbox
Celery
```

不要增加数据库表。

不要增加数据库字段。

不要增加 API。

不要增加 Dashboard。

不要增加 Metrics。

不要增加 Billing。

---

# 五、Runtime Bridge

新增：

```text
LLMUsageQueryRuntimeBridge
```

职责非常单一：

```text
async query
      ↓
asyncio.to_thread()
      ↓
同步 LLMUsageQueryService
      ↓
返回结果
```

例如概念：

```python
async def list_records_async(...):
    return await asyncio.to_thread(
        self._service.list_records,
        ...
    )
```

具体参数形式必须根据当前：

```text
LLMUsageQueryService
```

真实接口实现。

---

# 六、不要复制 Query Logic

Runtime Bridge：

**不能自己实现 SQL。**

禁止：

```text
Runtime Bridge
 ↓
select(...)
```

也禁止：

```text
Runtime Bridge
 ↓
Session
```

Runtime Bridge 只能调用：

```text
LLMUsageQueryService
```

因此：

```text
Repository
 ↓
Query Service
 ↓
Runtime Bridge
```

职责保持：

```text
Repository = DB
Service = Query Contract
Bridge = Async Boundary
```

---

# 七、Runtime Bridge 必须保持同步 Service 不变

当前：

```text
LLMUsageQueryService
```

仍然提供：

```text
query()
list_records()
get_by_request_id()
```

这些方法保持：

```text
def
```

不要修改成：

```text
async def
```

因为同步 Query Service 仍然可以：

```text
CLI
Test
Background Job
Sync Code
```

直接使用。

---

# 八、async API

Runtime Bridge 提供对应 async 方法。

至少：

```text
get_by_request_id_async()
list_records_async()
query_async()
```

具体是否需要三个方法：

先检查当前 Service 使用场景。

如果可以避免重复 wrapper，可以只提供：

```text
query_async()
```

但不要为了抽象而抽象。

优先：

**最少 API。**

---

# 九、Session Thread Boundary

这是本阶段最重要的 Contract。

必须保证：

```text
event loop thread
        X
       不执行
       ↓
SQLAlchemy Session
```

而是：

```text
event loop
   ↓
asyncio.to_thread()
   ↓
worker thread
   ↓
Session factory()
   ↓
Repository
   ↓
PostgreSQL
```

Repository 当前的：

```text
with factory() as session:
```

继续保持。

不要把 Session 创建在 event loop thread。

---

# 十、Session Isolation

继续遵循：

```text
1 query
↓
1 Session
```

例如：

```text
5 concurrent async queries
↓
5 worker executions
↓
5 independent Sessions
```

不能：

```text
5 queries
↓
1 shared Session
```

SQLAlchemy 官方明确说明 Session 是 mutable/stateful transaction object，不应该被并发线程或 asyncio task 共享。

---

# 十一、Event Loop Non-blocking Test

新增 heartbeat 测试。

模拟：

```text
Repository Query = 200ms
```

启动：

```text
query_async()
```

同时：

```text
heartbeat every 20ms
```

如果 Runtime Bridge 正确：

```text
event loop
↓
heartbeat continues
```

要求：

```text
最大 heartbeat gap < 0.1s
```

具体阈值可以参考 Phase 3.10.15 已有测试，不要重新发明 benchmark。

本测试：

**只判断 blocking / non-blocking。**

不要建立性能 Benchmark 平台。

---

# 十二、Direct Sync Baseline

为了证明测试有意义，可以复用 Phase 3.10.15 的思路：

```text
直接同步调用
```

应该出现：

```text
event loop blocking
```

而：

```text
query_async()
```

应该：

```text
event loop remains schedulable
```

如果已有通用 helper：

**直接复用。**

不要复制一套 heartbeat framework。

---

# 十三、Concurrency

测试：

```text
5 concurrent query_async()
```

例如：

```text
asyncio.gather(
    query_1,
    query_2,
    query_3,
    query_4,
    query_5,
)
```

验证：

```text
5 results
```

且：

```text
request_id
provider
model
```

分别正确。

不能发生：

```text
result 串线
```

---

# 十四、真实 PostgreSQL Async Query

如果项目 DB 测试设施允许：

增加：

```text
RUN_DB_TESTS=1
```

真实 PostgreSQL 验证：

```text
5 async query
↓
Runtime Bridge
↓
5 sync worker executions
↓
PostgreSQL
```

验证：

```text
结果正确
Session 正确关闭
DB 数据没有变化
```

注意：

**这里的“async”是调用边界 async。**

不是：

```text
asyncpg
```

也不是：

```text
AsyncSession
```

---

# 十五、Query Contract 不变

3.10.18 不得改变：

```text
LLMUsageQueryFilter
LLMUsageRecordView
```

字段。

仍然：

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

仍然：

```text
frozen
```

仍然：

```text
timezone-aware created_at
```

---

# 十六、Query Filter 不变

仍然支持：

```text
request_id
provider
model
created_at_from
created_at_to
limit
offset
```

仍然：

```text
limit 1~100
offset >= 0
```

不增加：

```text
project_id
user_id
tenant_id
cost
status
```

---

# 十七、Repository 完全不变

本阶段重点：

```text
Repository SQL = unchanged
```

不要修改：

```text
build_record_select()
list_records()
get_by_request_id()
```

不要新增 index。

不要修改：

```text
ORDER BY created_at DESC, id DESC
```

不要修改：

```text
explicit 8 columns
```

不要修改：

```text
request_id unique contract
```

如果测试发现 Repository 本身有 bug：

**先停止并报告。**

不要为了 async runtime 顺手修 Repository。

---

# 十八、Error Propagation

保持现有：

```text
Repository error
 ↓
Query Service
 ↓
Runtime Bridge
 ↓
caller
```

不要：

```text
catch Exception
 ↓
return []
```

不要：

```text
warning
 ↓
silent success
```

Query 与 Persistence 不同：

Persistence 是：

```text
best-effort accounting
```

Query 是：

```text
explicit read operation
```

因此 Query failure 必须可观察。

---

# 十九、Cancellation

本阶段需要明确 Cancellation Contract。

测试：

```text
query_async()
```

运行期间：

```text
caller cancellation
```

要求先观察当前项目 async cancellation 约定。

不要擅自复制 Persistence 的：

```text
CancelledError → warning → absorb
```

因为：

**Query 是主动读取请求。**

默认建议：

```text
Query cancellation
↓
允许向 caller 传播
```

但必须结合项目已有 async service 约定。

不要为了测试通过强行吞掉 cancellation。

---

# 二十、No Fire-and-forget

禁止：

```text
asyncio.create_task()
```

禁止：

```text
ensure_future()
```

禁止：

```text
background task
```

禁止：

```text
fire-and-forget
```

必须：

```text
await query_async()
```

调用者必须等待结果。

---

# 二十一、No Global Query State

禁止：

```text
global Session
global Connection
global query result
global cache
global dict
global set
```

也不要：

```text
LRU cache
```

本阶段不做缓存。

---

# 二十二、Read-only Contract

Runtime Bridge 不允许：

```text
INSERT
UPDATE
DELETE
```

数据库操作仍然：

```text
SELECT only
```

测试：

```text
row_count_before
↓
query_async()
↓
row_count_after
```

要求：

```text
before == after
```

---

# 二十三、Security

Runtime Bridge 不增加任何数据字段。

最终返回仍然：

```text
LLMUsageRecordView
```

不得出现：

```text
Session
Connection
Engine
SQLAlchemy Row
ORM Model
password
api_key
authorization
prompt
messages
SQL
RAG
tool
database_url
cost
price
currency
```

---

# 二十四、Fake Runtime Test

新增：

```text
tests/test_llm_usage_query_runtime.py
```

建议至少覆盖：

```text
1. query_async success
2. get_by_request_id_async success
3. list_records_async success
4. service exception propagation
5. repository exception propagation
6. 5 concurrent queries
7. session isolation
8. event loop heartbeat
9. no create_task
10. no global session
11. no DB mutation
12. DTO identity / content correctness
```

如果项目已有对应 helper：

优先复用。

---

# 二十五、真实 PostgreSQL Tests

至少覆盖：

```text
1. async get_by_request_id
2. async list
3. async provider/model filter
4. async pagination
5. async stable ordering
5. concurrent async query
```

全部通过：

```text
Runtime Bridge
```

不能直接：

```text
Repository
```

作为最终入口。

---

# 二十六、不要重新测试全部 Query Contract

3.10.17 已经有：

```text
67 tests
```

3.10.18 不需要复制：

```text
所有 filter validation
所有 DTO security
所有 SQL compilation
```

只需要验证：

```text
同步 Query Service
        ↓
Runtime Bridge
        ↓
async caller
```

不会破坏原 Contract。

---

# 二十七、Performance

只记录：

```text
event loop blocking = PASS
```

可以记录：

```text
concurrent query = PASS
```

不要记录：

```text
QPS
TPS
P95
P99
throughput
```

不要建立 Benchmark。

---

# 二十八、Architecture 文档

修改：

```text
docs/architecture.md
```

新增：

```text
§8.18 LLM Usage Query Async Runtime Boundary
```

说明：

```text
Synchronous DB Query Layer
        ↓
LLMUsageQueryService
        ↓
asyncio.to_thread()
        ↓
Async Runtime Boundary
```

明确：

```text
Repository remains synchronous
Database driver remains unchanged
No AsyncSession introduced
No asyncpg introduced
```

---

# 二十九、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.10.18 — Usage Query Async Runtime.md
```

记录：

## 1. Goal

```text
Prevent synchronous Usage Query DB I/O from blocking async event loop
```

## 2. Architecture

```text
Async Caller
    ↓
Query Runtime Bridge
    ↓
asyncio.to_thread()
    ↓
Query Service
    ↓
Repository
    ↓
PostgreSQL
```

## 3. Event Loop

```text
Direct Sync Query = BLOCKING
Runtime Bridge = PASS
```

## 4. Concurrency

```text
5 async queries = PASS
```

## 5. Session Isolation

```text
5 queries
→ 5 Sessions
→ 5 closed Sessions
```

## 6. Error

```text
Query Error Propagation = PASS
```

## 7. Cancellation

记录实际验证出的 Contract。

## 8. Security

```text
DTO unchanged
```

## 9. DB Writes

```text
INSERT = 0
UPDATE = 0
DELETE = 0
```

---

# 三十、测试命令

先：

```powershell
python -m pytest -q tests/test_llm_usage_query_runtime.py
```

然后：

```powershell
python -m pytest -q tests/test_llm_usage_query.py tests/test_llm_usage_query_runtime.py
```

再：

```powershell
python -m pytest -q tests/test_llm_usage_persistence.py tests/test_llm_usage_persistence_runtime.py tests/test_llm_usage_persistence_idempotency.py tests/test_llm_usage_query.py tests/test_llm_usage_query_runtime.py
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

# 三十一、成功标准

全部满足：

```text
[PASS] Async Query Runtime Bridge
[PASS] asyncio.to_thread boundary
[PASS] Event loop remains schedulable
[PASS] 5 concurrent queries
[PASS] Session isolation
[PASS] Session closed
[PASS] Error propagation
[PASS] Cancellation contract
[PASS] No fire-and-forget
[PASS] No global Session
[PASS] Read-only
[PASS] DTO unchanged
[PASS] Query Filter unchanged
[PASS] Repository SQL unchanged
[PASS] No new dependency
[PASS] No new table
[PASS] No schema modification
```

---

# 三十二、如果发现问题

如果发现：

```text
同步 Query 阻塞 event loop
Session 泄漏
线程串线
Cancellation 异常
Repository regression
```

只允许修改：

```text
Usage Query Runtime Bridge
```

必要时最小修改：

```text
Usage Query Service
```

禁止修改：

```text
Persistence Runtime
Persistence Idempotency
LLM Client
Observation
Accounting
RAG
Router
Tool
Text-to-SQL
Validator
Executor
```

如果发现当前 Repository 本身需要改：

**停止并报告。**

---

# 三十三、最终报告格式

完成后严格按照：

```text
【Phase 3.10.18 COMPLETE】

1. 新增文件
2. 修改文件
3. Runtime Bridge
4. Event Loop Blocking
5. Concurrent Query
6. Session Isolation
7. Error Propagation
8. Cancellation
9. Read-only
10. Security
11. Repository 是否修改
12. 测试结果
13. DB writes
14. 发现并修复的问题
15. 当前限制
```

最后：

```text
LLM Usage Architecture:

                    ┌── Observation
LLM Request ────────┤
                    └── Accounting
                           ↓
                     Persistence
                           ↓
                      Idempotency
                           ↓
                      PostgreSQL
                           ↑
                           │
                    Query Repository
                           ↑
                           │
                    Query Service
                           ↑
                           │
                 ┌─────────┴─────────┐
                 │                   │
            Sync Caller        Async Caller
                                     ↓
                              Query Runtime
                                Bridge
                                     ↓
                              asyncio.to_thread
```

最终明确：

```text
Usage Persistence       = PASS
Usage Idempotency       = PASS
Usage Query             = PASS
Async Query Runtime     = PASS

Analytics               = NOT IMPLEMENTED
Billing                 = NOT IMPLEMENTED
Dashboard               = NOT IMPLEMENTED
HTTP API                = NOT IMPLEMENTED
Queue / Worker          = NOT IMPLEMENTED
Outbox                  = NOT IMPLEMENTED
```

最后：

**立即停止。**

不要进入 Analytics。

不要进入 Billing。

不要进入 Dashboard。

不要开发 Chat API。

不要开发 AsyncSession 数据库体系。

不要开发 Queue / Worker / Outbox。

不要修改 RAG / Router / Tool / Text-to-SQL。
