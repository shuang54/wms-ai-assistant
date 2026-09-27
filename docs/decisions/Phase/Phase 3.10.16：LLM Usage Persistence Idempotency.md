你现在开始实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.10.16：LLM Usage Persistence Idempotency

## 一、阶段目标

本阶段只解决一个问题：

**LLM Usage Persistence 在存在 `request_id` 时具备数据库级幂等能力。**

Phase 3.10.15 已经证明：

```text
Async LLM
  ↓
Observation
  ↓
Accounting Sink
  ↓
Runtime Bridge
  ↓
asyncio.to_thread()
  ↓
Persistence Service
  ↓
Repository
  ↓
PostgreSQL
```

Runtime 阻塞已经解决。

当前明确遗留：

```text
无幂等保证
```

本阶段解决：

```text
同一个 request_id
重复持久化 N 次
        ↓
PostgreSQL 最终只存在 1 条记录
```

---

# 二、开始编码前必须先阅读

先阅读真实代码，不要假设接口：

```text
backend/app/db/models/llm_usage_record.py
backend/app/db/llm_usage_repository.py
backend/app/services/llm_usage_persistence_service.py
backend/app/services/llm_usage_persistence_runtime.py
backend/app/llm/client.py
backend/app/llm/observability.py
backend/app/db/init_db.py
```

同时阅读：

```text
tests/test_llm_usage_persistence.py
tests/test_llm_usage_persistence_runtime.py
tests/test_llm_accounting_lifecycle.py
```

以及：

```text
docs/architecture.md
docs/evaluation/Phase 3.10.14 — Usage Persistence.md
docs/evaluation/Phase 3.10.15 — Persistence Runtime Hardening.md
```

确认当前：

1. `request_id` 类型
2. `request_id` 是否 nullable
3. Repository 当前 create 行为
4. Persistence Service 当前同步/异步边界
5. Runtime Bridge 当前调用方式
6. PostgreSQL 初始化方式
7. 当前是否存在 migration framework
8. 当前测试数据库如何清理
9. 当前 `ai_ops.llm_usage_record` 是否已经存在
10. 当前是否已经存在任何 index

**不要重新设计已有 Persistence Contract。**

---

# 三、严格范围

## 允许

允许：

* 给 `request_id` 增加数据库级幂等约束
* 修改 LLM Usage Repository
* 修改 Persistence Service
* 必要时修改 DB Model
* 必要时修改 `init_db`
* 新增 persistence idempotency tests
* 新增 evaluation 文档
* 使用真实 PostgreSQL 验证并发幂等
* 使用 Fake Repository / Fake Session 验证调用契约

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
LLM Observation Contract
LLM Accounting Contract
```

不要增加：

```text
Redis
Kafka
Celery
RQ
Queue
Worker
Outbox
Billing
Dashboard
Metrics Platform
Tracing Platform
```

不要增加新的业务表。

不要修改 `public` schema。

不要把 `llm_usage_record` 移回 `public`。

---

# 四、幂等 Contract

本阶段必须明确以下行为。

## 4.1 request_id 存在

例如：

```text
request_id = "req-123"
```

第一次：

```text
INSERT
↓
1 row
```

第二次：

```text
same request_id
↓
DO NOTHING
↓
仍然 1 row
```

第 N 次：

```text
same request_id
↓
DO NOTHING
↓
仍然 1 row
```

最终：

```text
1 request_id = 1 persisted record
```

---

# 五、request_id 为 NULL

如果：

```text
request_id = NULL
```

则：

**不要人为生成 request_id。**

也不要使用：

```text
UUID
hash
timestamp
provider + model
```

作为伪幂等 key。

当前 Contract：

```text
request_id = NULL
↓
没有幂等保证
↓
每次 persistence 都允许产生新记录
```

例如：

```text
NULL
NULL
NULL
```

允许最终：

```text
3 rows
```

这是有意设计，不是 bug。

---

# 六、幂等必须由数据库保证

不要使用：

```python
SELECT request_id
IF NOT EXISTS:
    INSERT
```

作为唯一实现。

原因：

```text
Request A ── SELECT ── not exists
Request B ── SELECT ── not exists
Request A ── INSERT
Request B ── INSERT
```

仍然可能产生重复。

必须依赖：

```text
PostgreSQL UNIQUE INDEX / UNIQUE constraint
```

实现真正的并发安全。

---

# 七、推荐数据库设计

检查当前：

```text
ai_ops.llm_usage_record
```

建议：

```text
request_id nullable
```

保持不变。

新增：

```text
UNIQUE PARTIAL INDEX
```

只约束：

```text
request_id IS NOT NULL
```

概念：

```sql
CREATE UNIQUE INDEX ...
ON ai_ops.llm_usage_record(request_id)
WHERE request_id IS NOT NULL;
```

这样：

```text
request_id = req-001
request_id = req-001
```

不允许重复。

而：

```text
NULL
NULL
NULL
```

仍然允许。

SQLAlchemy 当前 PostgreSQL dialect 支持 partial index，并支持通过 `ON CONFLICT ... DO NOTHING` 使用唯一索引处理冲突。

---

# 八、不要使用普通全局 UNIQUE

不要简单改成：

```text
UNIQUE(request_id)
```

虽然 PostgreSQL 对普通 UNIQUE 下多个 NULL 的行为能够允许多个 NULL，但本阶段仍然明确要求：

**用 partial unique index 表达业务语义。**

原因是：

```text
request_id NULL
```

本身就是：

```text
"没有幂等身份"
```

partial index 可以直接把这个 Contract 写进数据库结构。

---

# 九、Repository 幂等实现

检查：

```text
backend/app/db/llm_usage_repository.py
```

当前 Repository 应该保持最小职责。

允许增加：

```text
create / create_if_absent
```

之类的最小能力。

推荐优先使用 PostgreSQL：

```text
INSERT
ON CONFLICT
DO NOTHING
```

而不是：

```text
INSERT
→ IntegrityError
→ rollback
→ 判断是不是 duplicate
```

这样可以避免把正常幂等冲突当成异常控制流。

SQLAlchemy PostgreSQL dialect 原生支持：

```text
insert()
.on_conflict_do_nothing(...)
```

并可以针对唯一索引指定 conflict target。

---

# 十、不要 Upsert

本阶段：

**禁止 DO UPDATE。**

即：

```text
request_id 已存在
```

不能：

```text
UPDATE provider
UPDATE model
UPDATE token
UPDATE usage
```

只允许：

```text
DO NOTHING
```

因此采用：

```text
first write wins
```

语义：

```text
第一次 persistence
    ↓
保存

后续相同 request_id
    ↓
忽略
```

不要引入：

```text
last write wins
```

---

# 十一、重复数据内容不同怎么办

测试：

第一次：

```text
request_id = req-001
provider = deepseek
model = deepseek-chat
total_tokens = 100
```

第二次：

```text
request_id = req-001
provider = another-provider
model = another-model
total_tokens = 999
```

结果：

```text
仍然只有 1 row
```

不得 UPDATE。

不得覆盖第一次数据。

不得产生第二条记录。

本阶段不实现：

```text
conflict resolution
reconciliation
```

只保证：

```text
one request_id → max one persisted row
```

---

# 十二、Runtime Bridge 不应该重新实现幂等

保持：

```text
DatabaseLLMAccountingSink.arecord()
        ↓
PersistenceRuntimeBridge
        ↓
asyncio.to_thread()
        ↓
PersistenceService.persist()
        ↓
Repository
```

幂等逻辑必须位于：

```text
Repository / DB
```

而不是：

```text
Runtime Bridge
```

不要在 async runtime 层：

```python
if request_id_seen:
```

也不要增加：

```text
in-memory set
dict cache
lock
```

因为进程重启后会失效，而且无法解决多进程并发。

---

# 十三、必须验证真实 PostgreSQL 并发

增加真实 DB 测试。

场景：

```text
5 concurrent persistence calls
```

全部使用：

```text
request_id = "same-request-id"
```

例如：

```text
Request 1 ─┐
Request 2 ─┤
Request 3 ─┤
Request 4 ─┤ → PostgreSQL
Request 5 ─┘
```

最终：

```text
rows = 1
```

必须验证：

```text
request_id count = 1
```

而不是只验证：

```text
repository called 5 times
```

---

# 十四、Sequential Idempotency

测试：

```text
persist(observation)
persist(observation)
persist(observation)
```

全部：

```text
request_id = req-001
```

最终：

```text
count = 1
```

---

# 十五、Different request_id

测试：

```text
req-001
req-002
req-003
```

最终：

```text
count = 3
```

确认不会错误地把所有 persistence 都视为重复。

---

# 十六、NULL request_id

测试：

```text
None
None
None
```

执行：

```text
3 persistence calls
```

最终：

```text
count = 3
```

确认：

```text
NULL 不参与幂等
```

---

# 十七、重复 persistence 不应该修改第一条记录

测试：

第一次：

```text
request_id = req-001
prompt_tokens = 10
completion_tokens = 20
total_tokens = 30
```

第二次：

```text
request_id = req-001
prompt_tokens = 100
completion_tokens = 200
total_tokens = 300
```

最终数据库：

```text
prompt_tokens = 10
completion_tokens = 20
total_tokens = 30
```

证明：

```text
DO NOTHING
```

而不是：

```text
DO UPDATE
```

---

# 十八、Persistence Failure Contract

保持 Phase 3.10.15 已经建立的语义：

```text
Persistence failure
        ↓
不能破坏 LLM Business Result
```

新增幂等逻辑后继续验证：

```text
business answer = unchanged
```

不要因为 duplicate：

```text
raise exception
```

不要让：

```text
duplicate persistence
```

传播到：

```text
AIOrchestrator
RAG
Router
Tool
T2S
```

---

# 十九、Repository Fake Tests

除了真实 DB 测试，再增加 Fake Repository / Fake Session 测试。

至少验证：

```text
request_id exists
request_id None
duplicate branch
```

以及：

```text
service contract unchanged
```

不要所有测试都依赖 PostgreSQL。

---

# 二十、数据库 Index 验证

增加一个 DB integration test：

查询 PostgreSQL catalog，确认：

```text
ai_ops.llm_usage_record
```

存在指定唯一 partial index。

验证：

```text
unique = true
```

并且 predicate 类似：

```text
request_id IS NOT NULL
```

不要只测试业务结果。

必须同时验证：

```text
Database Structure = PASS
```

---

# 二十一、已有数据库处理

当前项目没有要求引入 Alembic。

因此：

**不要为了这个阶段引入 migration framework。**

检查当前：

```text
init_db.py
```

确认已有数据库初始化方式。

如果项目当前通过：

```text
Base.metadata.create_all()
```

管理开发测试数据库，则优先复用当前机制。

但是：

**不要删除已有数据。**

如果已有数据库存在重复：

```text
request_id = req-001
request_id = req-001
```

在创建 unique index 前必须：

```text
检测
↓
明确报告
↓
停止
```

不要自动：

```text
DELETE
MERGE
UPDATE
```

---

# 二十二、测试数据清理

测试结束后：

```text
ai_ops.llm_usage_record
```

必须：

```text
count = 0
```

并且：

```text
public schema
```

不得产生任何新表。

最终确认：

```text
public knowledge_document
public knowledge_chunk
```

结构和数据均没有被本阶段改变。

---

# 二十三、安全边界

本阶段继续保持：

Repository 最终只能写入：

```text
request_id
provider
model
prompt_tokens
completion_tokens
total_tokens
```

不得新增：

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
cost
price
currency
```

不要因为实现幂等而把完整 Observation 写入数据库。

---

# 二十四、不要增加缓存

禁止：

```text
Redis
LRU
dict cache
process cache
request_id set
```

幂等必须是：

```text
PostgreSQL authoritative
```

而不是：

```text
application memory authoritative
```

---

# 二十五、不要增加 Metrics

本阶段不实现：

```text
duplicate_count
persistence_latency
persistence_success_rate
```

不要新增 metrics framework。

如果当前已有日志能力，可以仅保留现有日志语义。

---

# 二十六、测试文件

优先新增：

```text
tests/test_llm_usage_persistence_idempotency.py
```

如果现有项目测试结构更适合，也可以合并到：

```text
tests/test_llm_usage_persistence.py
```

但不要为了新阶段重构旧测试。

---

# 二十七、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.10.16 — Persistence Idempotency.md
```

记录：

## 1. Goal

```text
request_id-based persistence idempotency
```

## 2. Contract

```text
request_id != NULL
→ max 1 row

request_id == NULL
→ no idempotency guarantee
```

## 3. Database

记录：

```text
Index Name
Schema
Table
Column
Predicate
Unique
```

## 4. Sequential Test

```text
3 same request_id
→ 1 row
```

## 5. Concurrent Test

```text
5 concurrent same request_id
→ 1 row
```

## 6. Different IDs

```text
3 request_ids
→ 3 rows
```

## 7. NULL IDs

```text
3 NULL request_ids
→ 3 rows
```

## 8. First-write-wins

记录：

```text
duplicate does not UPDATE existing row
```

## 9. Security

记录：

```text
persisted fields unchanged
```

## 10. Final Result

```text
Sequential Idempotency = PASS
Concurrent Idempotency = PASS
NULL request_id = PASS
Different request_id = PASS
First-write-wins = PASS
DB Index = PASS
Security = PASS
DB writes after cleanup = 0
```

---

# 二十八、测试命令

先执行：

```powershell
python -m pytest -q tests/test_llm_usage_persistence_idempotency.py
```

然后：

```powershell
python -m pytest -q tests/test_llm_usage_persistence.py tests/test_llm_usage_persistence_runtime.py
```

然后完整：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

最后：

```powershell
python -m compileall backend tests scripts
```

如果项目已有：

```text
lint
LSP
```

继续执行。

要求：

```text
0 failed
0 diagnostics
```

---

# 二十九、成功标准

本阶段只有以下全部满足才算 COMPLETE：

```text
[PASS] request_id unique partial index
[PASS] sequential duplicate
[PASS] concurrent duplicate
[PASS] NULL request_id
[PASS] different request_id
[PASS] first-write-wins
[PASS] no UPDATE
[PASS] persistence failure isolation
[PASS] security field whitelist
[PASS] runtime bridge unchanged
[PASS] no application-memory idempotency
[PASS] no new dependency
[PASS] no new business table
[PASS] public schema unchanged
[PASS] DB cleanup = 0
```

---

# 三十、如果发现问题

如果发现：

```text
PostgreSQL index creation failure
Repository behavior mismatch
Runtime behavior regression
Session transaction issue
```

不要为了通过测试而修改：

```text
AI Client
Orchestrator
Router
RAG
Tool
T2S
Validator
Executor
```

先定位：

```text
Model
↓
DB initialization
↓
Repository
↓
Persistence Service
↓
Runtime Bridge
```

如果发现数据库中已有重复 `request_id`：

**停止，不要自动清理。**

最终报告明确：

```text
发现问题：
影响：
根因：
是否修改：
```

---

# 三十一、最终报告格式

完成后严格按照以下格式报告：

```text
【Phase 3.10.16 COMPLETE】

1. 新增文件
2. 修改文件
3. Idempotency Contract
4. Database Index
5. Sequential Idempotency
6. Concurrent Idempotency
7. NULL request_id
8. Different request_id
9. First-write-wins
10. Persistence Failure
11. Security
12. Runtime 是否修改
13. 测试结果
14. DB writes
15. 发现并修复的问题
16. 当前限制
```

最后写：

```text
Idempotency Architecture:

LLM Request
    ↓
LLMResponse
    ↓
Observation
    ↓
Accounting Sink
    ↓
Runtime Bridge
    ↓
Persistence Service
    ↓
Repository
    ↓
PostgreSQL
    │
    ├── request_id != NULL
    │       ↓
    │   UNIQUE PARTIAL INDEX
    │       ↓
    │   ON CONFLICT DO NOTHING
    │
    └── request_id == NULL
            ↓
        normal INSERT
```

最后：

**立即停止。**

不要进入 Queue / Worker / Outbox。

不要开发 Billing。

不要开发 Dashboard。

不要开发 Metrics Platform。

不要开发 Chat API。

不要修改 RAG / Router / Tool / Text-to-SQL。

等待下一阶段指令。
