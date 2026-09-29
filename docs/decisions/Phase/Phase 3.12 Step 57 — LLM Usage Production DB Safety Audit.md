# Phase 3.12 Step 57 — LLM Usage Production DB Safety Audit

你现在开始执行：

`D:\coding\ai\wms-ai-assistant`

---

# 一、阶段目标

Phase 3.12 Step 56 已经完成：

```text
Production Default LLMClient
        ↓
DatabaseLLMAccountingSink
        ↓
LLMUsagePersistenceService
        ↓
LLMUsageRepository
        ↓
ai_ops.llm_usage_record
```

本阶段只解决一个问题：

> **审计 Production LLM Usage Persistence 接入以后，对数据库连接、事务、并发、失败恢复以及 LLM 主链路的安全边界是否成立。**

本阶段：

**只做 Audit + 最小验证。**

不要优化。

不要做压力测试。

不要修改生产架构。

不要进入下一阶段。

---

# 二、严格范围

允许阅读：

```text
backend/app/llm/client.py

backend/app/services/llm_usage_persistence_service.py
backend/app/services/llm_usage_persistence_runtime.py

backend/app/db/llm_usage_repository.py
backend/app/db/session.py
backend/app/db/models/llm_usage_record.py

backend/app/api/orchestrator_chat.py

tests/test_llm_usage*
tests/test_assistant_trace*
tests/test_llm_usage_production_wiring*
```

允许新增：

```text
tests/test_llm_usage_production_db_safety.py

docs/evaluation/
    phase-3.12-step-57-llm-usage-production-db-safety.md
```

如果现有测试已经能够证明某项，不要重复造测试。

---

# 三、明确禁止

本阶段禁止修改：

```text
LLMClient
LLMProvider
DeepSeekProvider
TextToSQLService
RagService
ToolChatService
AIRouterService
AIOrchestratorService
LLMUsageRepository contract
AssistantTrace
RAG Observability
Tool Observability
Prompt
```

禁止新增：

```text
数据库表
数据库列
数据库索引
migration
Alembic
runtime config
environment variable
retry
queue
background worker
connection cache
global Session
```

禁止：

```text
压力测试
benchmark
load test
真实生产数据库写入
真实 WMS 数据
真实 DeepSeek API
```

不要修改：

```text
assistant_request_id index
```

不要修改：

```text
llm_usage_record schema
```

---

# 四、先阅读真实实现

重点阅读：

```text
backend/app/db/session.py
backend/app/db/llm_usage_repository.py

backend/app/services/llm_usage_persistence_service.py
backend/app/services/llm_usage_persistence_runtime.py

backend/app/llm/client.py
```

以及：

```text
docker-compose.yml
backend/app/core/config.py
backend/app/db/init_db.py
```

确认：

1. Engine 如何创建。
2. pool_size 当前是多少。
3. max_overflow 当前是多少。
4. pool_timeout 是否配置。
5. pool_pre_ping 是否配置。
6. pool_recycle 是否配置。
7. Session factory 如何创建。
8. Repository 是否每次创建独立 Session。
9. Transaction 如何开始/提交/rollback。
10. DatabaseLLMAccountingSink 是否共享。
11. Runtime Bridge 是否使用线程。
12. `asyncio.to_thread()` 是否可能导致并发 DB connection。
13. DB exception 如何向上收敛。

---

# 五、画出真实连接生命周期

必须根据真实代码写出：

```text
LLM request
    ↓
LLMClient._emit_accounting()
    ↓
DatabaseLLMAccountingSink.arecord()
    ↓
Persistence Service
    ↓
Runtime Bridge
    ↓
asyncio.to_thread()
    ↓
Repository.create()
    ↓
Session Factory
    ↓
Session
    ↓
Transaction
    ↓
INSERT
    ↓
COMMIT
    ↓
Session.close()
    ↓
Connection returned to Pool
```

不要假设。

必须以当前源码为准。

---

# 六、Session 生命周期

确认每一次：

```text
LLM usage record
```

是否满足：

```text
Session created
        ↓
Transaction begins
        ↓
INSERT
        ↓
COMMIT
        ↓
Session closes
```

异常：

```text
INSERT failure
        ↓
ROLLBACK
        ↓
Session closes
```

必须确认不存在：

```text
global Session
```

不存在：

```text
long-lived transaction
```

不存在：

```text
Session leak
```

---

# 七、Connection Pool

读取当前真实配置。

记录：

```text
pool_size
max_overflow
pool_timeout
pool_pre_ping
pool_recycle
```

如果某项没有配置：

记录：

```text
None / SQLAlchemy default
```

不要自行添加配置。

---

# 八、并发模型

Step 56 使用：

```text
asyncio.to_thread()
```

因此必须理解：

```text
N concurrent LLM calls
        ↓
N accounting persistence tasks
        ↓
N repository calls
        ↓
N possible DB sessions
        ↓
pool limits
```

本阶段不要进行压力测试。

只做：

> 静态上限分析。

计算：

```text
最大并发 DB connection pressure
```

必须基于：

```text
现有 pool_size
+
现有 max_overflow
```

而不是自行假设。

---

# 九、关键问题

回答：

> 如果同时有 20 个 `/api/ai/chat` 请求，会发生什么？

不要真的发送 20 个请求。

根据：

```text
HTTP async concurrency
LLM concurrency
asyncio.to_thread
SQLAlchemy Pool
```

进行静态分析。

需要明确：

```text
是否排队
是否等待 pool
是否超时
是否阻塞 event loop
是否可能 connection exhausted
```

如果无法从当前配置确定：

写：

```text
需要运行环境实际配置确认
```

不要猜。

---

# 十、最重要：DB 慢不能影响 LLM 正常语义

当前设计：

```text
LLM response
    ↓
accounting persistence
```

要求：

```text
DB accounting failure
        ↓
warning
        ↓
business result preserved
```

审计：

```text
DB connection timeout
DB pool timeout
DB insert failure
DB transaction failure
DB connection broken
```

是否都会：

```text
NOT become LLM business failure
```

不要修改代码。

如果发现某个异常路径没有被隔离：

**停止并报告。**

不要为了通过测试直接修改生产代码。

---

# 十一、Accounting failure 是否触发 Retry

明确确认：

```text
DB failure
    ↓
NO LLM retry
NO sleep
NO backoff
NO queue
NO fallback
```

必须保持 Step 3.10.2 和 Step 56 的行为。

---

# 十二、Duplicate Protection

确认当前：

```text
LLMUsageRepository.create()
```

仍然使用：

```sql
ON CONFLICT DO NOTHING
```

以及：

```text
request_id
```

对应的既有唯一约束/索引。

分析：

```text
same provider request_id
        ↓
two persistence attempts
        ↓
one DB row
```

确认：

```text
no exception escapes
```

并且不会导致：

```text
LLM business failure
```

---

# 十三、assistant_request_id 不参与幂等

非常重要。

确认：

```text
request_id
```

仍然是：

```text
Provider request ID
```

而：

```text
assistant_request_id
```

只是：

```text
Assistant Trace correlation ID
```

不要让：

```text
assistant_request_id
```

成为唯一键。

不要新增 unique constraint。

---

# 十四、Production DB Unavailable

静态验证：

```text
DATABASE_URL configured
        ↓
DatabaseLLMAccountingSink
        ↓
DB unavailable
```

预期：

```text
LLM business path continues
```

测试可以使用 fake repository / fake engine。

禁止连接真实生产数据库。

---

# 十五、Database URL 未配置

确认 Step 56 行为仍然成立：

```text
DATABASE_URL absent
        ↓
get_engine() is None
        ↓
NoopAccountingSink
```

必须保持：

```text
no warning noise
no DB connection
no usage persistence
LLM still works
```

不要新增配置项。

---

# 十六、Default Client Singleton

再次确认：

```text
get_default_llm_client()
```

进程级 singleton。

以及：

```text
get_default_accounting_sink()
```

进程级 singleton。

分析：

```text
client
  ↓
sink
  ↓
runtime bridge
  ↓
repository/session
```

其中只有：

```text
client
sink
```

长生命周期。

而：

```text
Session
Transaction
Connection
```

必须是短生命周期。

---

# 十七、Reset 行为

确认：

```text
reset_default_llm_client()
```

仍然同时 reset：

```text
default client
default accounting sink
```

并且测试中不会产生：

```text
old sink retained
old engine retained
old session retained
```

---

# 十八、RAG / Tool / Text-to-SQL

不要修改这些模块。

只验证：

### RAG

```text
one actual LLM call
→ one usage persistence attempt
```

### Text-to-SQL

```text
semantic retry
→ each actual LLM call
→ independent usage persistence
```

### Tool

如果 `/api/ai/chat` Tool 路径没有 LLM：

```text
0 LLM
0 usage
```

保持现状。

---

# 十九、Refusal

保持：

```text
refusal
→ 1 actual LLM call
→ 1 usage record
→ no Validator
→ no Executor
```

不允许 accounting 逻辑改变 refusal。

---

# 二十、Security Audit

再次确认：

```text
ai_ops.llm_usage_record
```

只包含允许字段：

```text
request_id
provider
model
prompt_tokens
completion_tokens
total_tokens
assistant_request_id
created_at
id
```

不得加入：

```text
prompt
messages
system_prompt
tool_calls
SQL
RAG chunks
ToolResult
raw response
API key
Authorization
password
DATABASE_URL
exception message
stack trace
```

---

# 二十一、测试策略

不要新增大量测试。

优先：

```text
reuse existing Step 56 tests
```

只补缺失的：

```text
pool configuration inspection
session lifecycle contract
DB failure isolation
duplicate request_id idempotency
No DATABASE_URL fallback
singleton reset
```

如果已有测试已经完整覆盖：

**不要重复添加。**

---

# 二十二、DB-gated Test

如果需要 DB：

只使用测试数据库。

测试：

```text
1 LLM request
→ 1 usage row
→ query
→ cleanup
```

再测试：

```text
same request_id twice
→ 1 row
```

再测试：

```text
accounting failure
→ business result preserved
```

测试结束：

```text
ai_ops.llm_usage_record residue = 0
```

不要使用：

```text
TRUNCATE
```

不要删除真实数据。

---

# 二十三、不要做压力测试

禁止：

```text
20 requests benchmark
100 requests benchmark
1000 requests benchmark
Locust
JMeter
k6
```

本阶段只做：

```text
静态并发分析
+
最小 DB contract test
```

---

# 二十四、Documentation

新增：

```text
docs/evaluation/phase-3.12-step-57-llm-usage-production-db-safety.md
```

记录：

## 1. Connection lifecycle

## 2. Session lifecycle

## 3. Transaction lifecycle

## 4. Pool configuration

## 5. Concurrency analysis

## 6. Failure isolation

## 7. Idempotency

## 8. No DATABASE_URL behavior

## 9. Singleton behavior

## 10. Security

## 11. Limitations

---

# 二十五、完成标准

必须回答：

```text
[ ] Session 是否短生命周期
[ ] Transaction 是否短生命周期
[ ] Connection 是否正确归还 Pool
[ ] pool_size / max_overflow 已确认
[ ] pool_timeout 已确认
[ ] asyncio.to_thread 行为已确认
[ ] 并发连接压力边界已计算
[ ] DB failure 不影响 LLM
[ ] DB failure 不触发 LLM retry
[ ] duplicate request_id 幂等
[ ] assistant_request_id 不作为幂等键
[ ] DATABASE_URL 缺失时 Noop
[ ] singleton 正确
[ ] reset 正确
[ ] RAG 正常
[ ] Text-to-SQL retry 正常
[ ] Refusal 正常
[ ] Security 正常
[ ] DB residue = 0
```

---

# 二十六、测试命令

先：

```powershell
python -m pytest -q tests/test_llm_usage_production_wiring.py
```

然后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_llm_usage_production_wiring_e2e_db.py
```

如果新增专项测试：

```powershell
python -m pytest -q tests/test_llm_usage_production_db_safety.py
```

最后：

```powershell
python -m pytest -q
```

以及：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

最后：

```powershell
python -m compileall -q backend tests scripts
```

lint 如果仍不存在：

```text
lint unavailable
```

不要安装新的 lint 工具。

---

# 二十七、Git Diff

完成：

```powershell
git status --short
git diff --stat
git diff
```

确认：

```text
Production code changes = 0
```

除非发现明确的 Step 56 安全 bug。

如果发现 bug：

**先停止并报告，不要自行修复。**

---

# 二十八、最终报告

严格：

```text
Phase 3.12 Step 57 COMPLETE

1. Audit Scope
2. Connection Lifecycle
3. Session Lifecycle
4. Transaction Lifecycle
5. Pool Configuration
6. Concurrency Analysis
7. DB Failure Isolation
8. Retry Boundary
9. Idempotency
10. assistant_request_id Boundary
11. DATABASE_URL Missing
12. Singleton / Reset
13. RAG
14. Text-to-SQL
15. Refusal
16. Security
17. Tests
18. DB Tests
19. DB Residue
20. compileall
21. Git Diff
22. Current Limitations
```

最后：

```text
LLM Usage Production DB Safety = READY
Production Code Changes = 0
DB Schema Changes = 0

Phase 3.12 Step 57 STOP
```

**完成后立即停止。**

不要进入 Step 58。

不要做 Pagination。

不要做 Unified Timeline。

不要做 Conversation。

不要做 Memory。

不要做 Agent。

不要做 MCP。

不要做 OpenTelemetry。

不要做 Dashboard。

不要做 Cost Tracking。

不要做连接池优化。
