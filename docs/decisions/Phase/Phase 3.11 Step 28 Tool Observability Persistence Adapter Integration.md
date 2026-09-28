你现在开始执行：

# Phase 3.11 — Step 28

# Tool Observability Persistence Adapter Integration

项目：

`D:\coding\ai\wms-ai-assistant`

---

# 一、阶段目标

Step 26 已确定 Persistence Boundary：

```text
ToolExecution
    ↓
ToolExecutionRecord
    ↓
ToolExecutionObserver
    ├── InMemoryCollector
    └── PersistenceAdapter
             ↓
       PersistenceService
             ↓
       ToolExecutionRepository
             ↓
       ai_ops.tool_execution_record
```

Step 27 已完成：

```text
ORM Model
Repository
DB Table
Repository Tests
DB Integration Tests
```

但目前：

```text
Observer → Persistence = NOT CONNECTED
```

本阶段只解决：

> **让 ToolExecutionObserver 可以把 ToolExecutionRecord 持久化到 PostgreSQL，同时确保 Persistence 失败绝对不能影响 Tool Execution。**

---

# 二、严格范围

## 允许

新增：

```text
backend/app/services/tool_execution_persistence_service.py
backend/app/services/tool_execution_persistence_adapter.py
```

或者根据当前项目命名习惯选择等价位置。

允许：

* PersistenceService
* PersistenceAdapter
* Observer composition wiring
* Persistence 单元测试
* DB-gated Integration Tests
* Architecture Contract
* Evaluation 文档
* ADR 状态更新

---

## 禁止

本阶段禁止：

```text
❌ 修改 ToolExecutionRecord
❌ 修改 ToolExecutionService 核心执行逻辑
❌ 修改 ToolRegistry
❌ 修改 Tool Handler
❌ 修改 Router
❌ 修改 RAG
❌ 修改 Text-to-SQL
❌ 修改 QueryService
❌ 修改 Snapshot
❌ 修改 Metrics
❌ 修改 Serialization
❌ 修改 HTTP API
❌ 新增 HTTP endpoint
❌ 新增数据库表
❌ 新增 ORM Model
❌ 新增 Repository
❌ 修改 Repository API
❌ Redis
❌ Kafka
❌ Celery
❌ Outbox
❌ Background Worker
❌ Retry
❌ Backoff
❌ Queue
❌ 批量写入
❌ Async Persistence
❌ Retention / TTL
❌ Dashboard
```

特别注意：

**不要为了接入 Persistence 而修改 ToolExecutionService。**

---

# 三、当前真实架构

先确认 Step 27 当前真实代码。

必须阅读：

```text
backend/app/services/tool_execution_observer.py
backend/app/services/tool_execution_record.py
backend/app/services/tool_execution_service.py
backend/app/services/in_memory_tool_execution_collector.py

backend/app/db/tool_execution_repository.py
backend/app/db/models/tool_execution_record.py

backend/app/api/orchestrator_chat.py
```

同时阅读：

```text
backend/app/services/llm_usage_persistence_service.py
```

如果存在对应：

```text
DatabaseLLMAccountingSink
```

也一起阅读。

不要假设接口。

---

# 四、核心设计

本阶段必须实现：

```text
ToolExecutionObserver
        │
        ├──────────────→ InMemoryCollector
        │
        └──────────────→ PersistenceAdapter
                              ↓
                      PersistenceService
                              ↓
                      ToolExecutionRepository
```

其中：

## Observer

负责：

```text
fan-out
```

即：

```text
一个 Record
    ↓
Memory
    ↓
Persistence
```

两个观察者互相独立。

---

# 五、PersistenceAdapter

新增：

```text
ToolExecutionPersistenceAdapter
```

它必须实现当前：

```text
ToolExecutionObserver
```

Protocol。

接口保持：

```python
on_execution(record: ToolExecutionRecord) -> None
```

不要新增：

```text
save()
persist()
execute()
write()
```

等第二套 Observer 接口。

---

# 六、Adapter 职责

Adapter 只做：

```text
Observer Record
      ↓
PersistenceService
```

不要让 Adapter：

```text
❌ 创建 Session
❌ import SQLAlchemy
❌ import ORM Model
❌ 调 Repository
❌ 做 SQL
❌ 做 JSON
❌ 做业务校验
❌ 做 aggregation
❌ 做 metrics
```

因此：

```text
Adapter
    ↓
PersistenceService
    ↓
Repository
```

必须保持：

```text
Adapter = infrastructure boundary
Service = application persistence operation
Repository = database access
```

---

# 七、PersistenceService

新增：

```text
ToolExecutionPersistenceService
```

职责：

```text
ToolExecutionRecord
       ↓
Repository.create()
```

不要增加复杂逻辑。

第一版：

```python
persist(record) -> ToolExecutionRecordRow
```

即可。

---

# 八、PersistenceService 不修改 Record

必须直接使用：

```text
ToolExecutionRecord
```

不要：

```text
Record
 ↓
dict
 ↓
JSON
 ↓
ORM
```

也不要：

```text
Record
 ↓
Snapshot
```

原因：

Step 26 已确定：

```text
ToolExecutionRecord = internal execution fact
ToolExecutionSnapshot = read model
```

Persistence 输入必须保持：

```text
ToolExecutionRecord
```

---

# 九、Failure Isolation

这是本阶段最重要的要求。

当前 Observer contract 已经规定：

```text
Observability failure ≠ Tool execution failure
```

因此：

```text
Tool Execution
    ↓
Record
    ↓
Observer
    ├── Memory Collector
    │      ↓
    │    success
    │
    └── Persistence Adapter
           ↓
        DB failure
```

最终：

```text
ToolResult = unchanged
```

Persistence DB failure：

**不得传播到 Tool Execution。**

---

# 十、Adapter Failure Handling

PersistenceAdapter：

```text
try:
    persistence_service.persist(record)
except Exception:
    logger.warning(...)
```

但注意：

不能：

```text
❌ retry
❌ sleep
❌ backoff
❌ fallback
❌ queue
❌ re-run Tool
```

也不要把：

```text
exception message
traceback
```

写入：

```text
ToolExecutionRecord
```

---

# 十一、Exception Security

日志可以包含有限上下文：

允许：

```text
tool_name
request_id
round
project_id
error_type
```

禁止：

```text
API key
password
database URL
connection string
tool arguments
tool result
SQL
prompt
LLM response
traceback
```

尤其不要：

```python
logger.exception(...)
```

如果项目当前 logging policy 会把 traceback 输出到日志。

优先：

```text
logger.warning(
    "Tool execution persistence failed: request_id=%s tool_name=%s ...",
    ...
)
```

具体 logging 风格遵循项目现有规范。

---

# 十二、Memory Collector 与 Persistence 的关系

Observer 必须实现：

```text
Record
 ↓
Memory Collector
 ↓
Persistence Adapter
```

但：

**不能让 Persistence Adapter 依赖 Collector。**

禁止：

```text
PersistenceAdapter
    ↓
Collector
```

也禁止：

```text
Collector
    ↓
PersistenceAdapter
```

两者都是：

```text
Observer Port
```

的并列实现。

---

# 十三、Composition Root

必须在：

```text
backend/app/api/orchestrator_chat.py
```

Composition Root 中完成 wiring。

当前已有：

```text
InMemoryToolExecutionCollector
```

以及：

```text
ToolExecutionObserver
```

不要改变现有 Collector 生命周期。

新增：

```text
ToolExecutionPersistenceService
ToolExecutionPersistenceAdapter
```

然后组成：

```text
Observer Fan-out
    ├── InMemoryCollector
    └── PersistenceAdapter
```

---

# 十四、重要：不要创建第二个 Collector

禁止：

```text
new InMemoryCollector()
```

第二份。

必须继续使用 Step 20/19 当前：

```text
module-level application lifetime collector
```

否则：

```text
GET /api/observability/tools
```

会出现数据断裂。

---

# 十五、Observer Fan-out 设计

如果当前项目的 Observer 只是单一 Protocol：

不要为了 Step 28 大改 Observer Framework。

可以新增最小：

```text
CompositeToolExecutionObserver
```

如果当前架构已经有等价 Fan-out 能力，则直接复用。

目标：

```text
CompositeObserver
      │
      ├── InMemoryCollector
      └── PersistenceAdapter
```

接口：

```python
on_execution(record)
```

即可。

---

# 十六、Fan-out Failure Isolation

必须特别测试：

### Case A

```text
Memory success
Persistence success
```

结果：

```text
2 observers called
```

### Case B

```text
Memory success
Persistence failure
```

结果：

```text
Memory record exists
Tool execution unaffected
```

### Case C

```text
Memory failure
Persistence success
```

结果：

```text
Persistence still receives record
```

### Case D

```text
Memory failure
Persistence failure
```

结果：

```text
Tool execution unaffected
```

因此 Composite Observer 必须：

```text
observer A failure
    ↓
continue
    ↓
observer B
```

不能：

```text
observer A failure
    ↓
stop
    ↓
observer B never called
```

---

# 十七、Observer Failure 与 Persistence Failure 必须区分

不要把：

```text
PersistenceRepositoryError
```

转换成：

```text
ToolExecutionError
```

也不要返回：

```text
ToolResult(False)
```

因为：

```text
ToolResult
```

属于 Tool execution contract。

Observability 不得改变它。

---

# 十八、DB Integration

新增：

```text
tests/test_tool_execution_persistence_integration.py
```

只有：

```text
RUN_DB_TESTS=1
```

运行。

测试真实链路：

```text
ToolExecutionRecord
    ↓
PersistenceAdapter
    ↓
PersistenceService
    ↓
ToolExecutionRepository
    ↓
PostgreSQL
```

验证：

```text
row exists
```

---

# 十九、DB Integration Case

至少：

### Case 1

成功 Tool Execution Record：

```text
success=True
```

成功写入。

### Case 2

失败 Tool Execution Record：

```text
success=False
error_code != None
```

成功写入。

### Case 3

nullable：

```text
project_id=None
tool_call_id=None
error_code=None
error_type=None
```

成功写入。

### Case 4

同一个 request：

```text
round=1
round=2
```

两条都存在。

### Case 5

Persistence DB failure：

Repository 抛异常。

验证：

```text
Adapter does not propagate
```

---

# 二十、真实 Tool E2E

必须增加至少一个 DB-gated E2E：

```text
AIOrchestrator
    ↓
Tool
    ↓
ToolExecutionObserver
    ↓
CompositeObserver
    ├── Memory
    └── Persistence
          ↓
        PostgreSQL
```

使用现有：

```text
get_inventory
```

不要新增 Tool。

使用：

```text
project-a
```

现有测试能力。

不要读取真实生产数据。

如果已有安全的 synthetic DB fixture，优先复用。

---

# 二十一、E2E 验证

调用：

```text
AIOrchestrator.execute()
```

不要直接：

```text
ToolExecutionService.execute()
```

作为最终 E2E 入口。

验证：

```text
route == TOOL
```

并且：

```text
ToolResult = expected
```

同时：

```text
InMemoryCollector
    ↓
record exists
```

以及：

```text
ai_ops.tool_execution_record
    ↓
row exists
```

---

# 二十二、零越权验证

必须确认：

```text
RAG
```

不会写：

```text
tool_execution_record
```

因为没有 Tool Execution。

同样：

```text
TEXT_TO_SQL
```

不会写：

```text
tool_execution_record
```

除非实际发生 Tool Execution。

至少增加 architecture / behavior test：

```text
RAG → 0 persistence records
TEXT_TO_SQL → 0 persistence records
TOOL → 1 persistence record
```

---

# 二十三、API 保持不变

Step 25 API：

```text
GET /api/observability/tools
GET /api/observability/tools/metrics
```

本阶段：

**不修改 API contract。**

但注意：

API 当前 QueryService 仍然只读取：

```text
InMemoryCollector
```

所以：

```text
API = runtime memory view
Database = persistent history
```

这是预期行为。

不要为了“马上看到 DB 数据”修改 QueryService。

---

# 二十四、Collector 与 DB 双写

不要把代码写成：

```python
collector.on_execution(record)
repository.create(record)
```

因为这会把 Composition Root 的职责泄漏到执行链。

必须保持：

```text
Observer
    ├── Collector
    └── PersistenceAdapter
```

即：

```text
Fan-out
```

而不是：

```text
Collector + Repository
```

---

# 二十五、Architecture Contracts

新增：

```text
C34
```

至少：

### C34.1

PersistenceAdapter implements Observer Protocol。

### C34.2

PersistenceAdapter does not import:

```text
SQLAlchemy
ORM Model
Repository
Session
```

### C34.3

PersistenceService may depend on Repository。

### C34.4

ToolExecutionService does not import PersistenceAdapter / PersistenceService。

### C34.5

Collector does not import PersistenceAdapter。

### C34.6

PersistenceAdapter failure cannot propagate from Observer.

### C34.7

CompositeObserver continues after child observer failure。

### C34.8

Composition Root is the only place creating the Persistence Adapter.

### C34.9

No API module directly imports Repository.

### C34.10

Tool Handler does not import Persistence modules.

---

# 二十六、日志测试

增加至少：

```text
Persistence failure
    ↓
warning log
```

验证日志：

允许：

```text
request_id
tool_name
round
project_id
```

禁止出现：

```text
tool arguments
tool result
database URL
password
API key
SQL
traceback
```

不要把完整 exception message 直接塞进日志。

---

# 二十七、性能

本阶段不要做性能优化。

Persistence 是：

```text
synchronous
single-row
```

只记录：

```text
每次 Tool Execution 增加一次 DB write
```

不要增加：

```text
batch
queue
worker
asyncio.to_thread
```

---

# 二十八、测试命令

先运行：

```powershell
python -m pytest -q tests/test_tool_execution_persistence_service.py
```

然后：

```powershell
python -m pytest -q tests/test_tool_execution_persistence_adapter.py
```

然后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_tool_execution_persistence_integration.py
```

然后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_ai_e2e_evaluation.py
```

如果该测试文件已经存在且包含历史 fixture coupling：

不要为了 Step 28 重写历史测试。

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
python -m compileall backend tests
```

---

# 二十九、DB Residue

必须：

```text
DB writes during test = synthetic only
Production WMS writes = 0
```

测试结束：

```text
ai_ops.tool_execution_record = 0
```

不要清空：

```text
ai_ops.llm_usage_record
```

不要清空：

```text
public.*
```

不要删除整个：

```text
ai_ops
```

---

# 三十、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.11.28 — Tool Observability Persistence Integration.md
```

记录：

## 1. Architecture

```text
ToolExecutionRecord
        ↓
CompositeObserver
        ├── InMemoryCollector
        └── PersistenceAdapter
                 ↓
          PersistenceService
                 ↓
             Repository
                 ↓
          PostgreSQL
```

## 2. Failure Isolation

记录：

```text
Persistence success
Persistence failure
Memory success + Persistence failure
Memory failure + Persistence success
```

## 3. DB Integration

记录：

```text
rows written
rollback
DB residue
```

## 4. E2E

记录：

```text
Question
Route
Tool
Memory Record
Persistent Record
Tool Result
```

## 5. Security

确认：

```text
11 fields only
No arguments
No result
No secrets
```

## 6. API

明确：

```text
HTTP API unchanged
API remains memory-only
Database is persistent history only
```

---

# 三十一、ADR 更新

更新：

```text
docs/decisions/ADR-3.11.26-tool-observability-persistence-boundary.md
```

Implementation Status：

```text
Step 27:
ORM + Repository = implemented

Step 28:
PersistenceService = implemented
PersistenceAdapter = implemented
Observer Integration = implemented

QueryService DB integration = Deferred
HTTP history API = Deferred
Retention = Deferred
```

不要修改 Step 26 的 Boundary Decision。

---

# 三十二、成功标准

必须：

```text
✓ PersistenceService
✓ PersistenceAdapter
✓ CompositeObserver（如果需要）
✓ Observer → Persistence connected
✓ Memory Collector 仍然工作
✓ Persistence failure isolated
✓ Tool Result unchanged
✓ Real Repository used
✓ Real PostgreSQL DB-gated test
✓ Real AIOrchestrator Tool E2E
✓ RAG 不产生 Tool Persistence Record
✓ Text-to-SQL 不产生 Tool Persistence Record
✓ C34 全部 PASS
✓ Security log contract PASS
✓ DB residue = 0
✓ API contract unchanged
✓ ToolExecutionService 未增加 DB dependency
✓ Tool Handler 未增加 Persistence dependency
✓ 无 retry / queue / async / batch
```

---

# 三十三、最终报告

严格使用：

```text
【Phase 3.11 Step 28 COMPLETE】

1. PersistenceService
2. PersistenceAdapter
3. CompositeObserver
4. Observer Integration
5. Failure Isolation
6. Security
7. Unit Tests
8. DB Integration Tests
9. Real Tool E2E
10. RAG / Text-to-SQL Isolation
11. Architecture Contract C34
12. DB residue
13. API 是否变化
14. Runtime 是否变化
15. 新增文件
16. 修改文件
17. 当前限制
```

最后输出：

```text
Architecture:

AIOrchestrator
      ↓
ToolExecutionService
      ↓
ToolRegistry
      ↓
ToolResult
      ↓
ToolExecutionRecord
      ↓
CompositeObserver
      ├───────────────┐
      ↓               ↓
InMemoryCollector   PersistenceAdapter
      ↓               ↓
QueryService      PersistenceService
      ↓               ↓
HTTP API          Repository
                      ↓
                  PostgreSQL
```

明确：

```text
QueryService → PostgreSQL = NOT IMPLEMENTED
HTTP History API = NOT IMPLEMENTED
Retention = NOT IMPLEMENTED
Dashboard = NOT IMPLEMENTED
```

---

# 三十四、立即停止

完成 Step 28 后：

**立即停止。**

不要进入 Step 29。

不要实现：

```text
Database Query Service
History API
Pagination
Filtering
Retention
Dashboard
Prometheus
OpenTelemetry
```

等待下一步指令。
