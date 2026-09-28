你现在开始执行：

# Phase 3.11 Step 21 — Tool Observability Read Query Boundary

## 一、阶段目标

在 Step 20 已完成：

```text
ToolExecutionRecord
        ↓
InMemoryToolExecutionCollector
        ↓
有限 Retention Window
        ↓
ToolExecutionMetricsService
```

本阶段只解决一个问题：

> **为未来 HTTP API / Dashboard / Persistence 提供一个稳定的只读查询边界，避免上层直接依赖 InMemoryToolExecutionCollector 的内部查询方法。**

目标：

```text
Collector
   ↓
ToolObservabilityQueryService
   ↓
Records / Metrics Snapshot
```

本阶段：

**不做 HTTP API。**

**不做数据库。**

**不做 Redis。**

**不做 Dashboard。**

---

# 二、开始前必须阅读

先阅读真实实现：

```text
backend/app/services/in_memory_tool_execution_collector.py
backend/app/services/tool_execution_record.py
backend/app/services/tool_execution_metrics_service.py
backend/app/services/tool_execution_observer.py
backend/app/services/tool_execution_context.py
backend/app/services/tool_execution_service.py
backend/app/api/orchestrator_chat.py
```

以及：

```text
tests/test_in_memory_tool_execution_collector.py
tests/test_tool_execution_metrics_service.py
tests/test_tool_observability_composition.py
tests/test_tool_architecture_contract.py
```

检查：

* Collector 当前公开 API
* Metrics 当前 API
* Step 20 retention 语义
* Composition Root 当前装配方式
* 现有测试契约
* 是否已经存在类似 Query / Read Service

**如果已经存在等价 Query Service，不要重复创建。**

---

# 三、严格范围

## 允许

新增：

```text
backend/app/services/tool_observability_query_service.py
```

或者项目已有更合适的位置。

新增：

```text
tests/test_tool_observability_query_service.py
```

必要时：

```text
docs/evaluation/Phase 3.11 Step 21 — Tool Observability Read Query Boundary.md
```

可以增加：

```text
docs/architecture.md
```

中的架构说明。

---

## 禁止

本阶段禁止修改：

```text
ToolExecutionService
ToolExecutionRecord
ToolExecutionObserver
ToolExecutionMetricsService
AIOrchestrator
AI Router
ToolRegistry
ToolResult
ToolChatService
InMemoryToolExecutionCollector 的 Retention 行为
```

禁止：

```text
Database
Redis
Kafka
RabbitMQ
Prometheus
OpenTelemetry
HTTP API
Dashboard
WebSocket
Audit persistence
Event Bus
```

禁止新增：

```text
settings
环境变量
数据库表
migration
后台线程
定时任务
TTL
```

---

# 四、Query Service 设计

新增一个纯应用层：

```text
ToolObservabilityQueryService
```

职责：

> 从 Collector 获取当前 retention window 内的只读数据，并提供 Metrics Snapshot。

建议构造：

```python
ToolObservabilityQueryService(
    collector,
    metrics_service=ToolExecutionMetricsService,
)
```

如果当前项目更适合 static / classmethod 设计，可以遵循现有代码风格。

---

# 五、第一版只提供最小 API

建议提供：

```python
records()
records_by_request_id(request_id)
records_by_project_id(project_id)
records_by_tool_name(tool_name)
metrics()
```

语义：

```text
records()
    ↓
collector.records()

records_by_request_id(...)
    ↓
collector.records_by_request_id(...)

records_by_project_id(...)
    ↓
collector.records_by_project_id(...)

records_by_tool_name(...)
    ↓
collector.records_by_tool_name(...)

metrics()
    ↓
ToolExecutionMetricsService.snapshot(
    collector.records()
)
```

---

# 六、非常重要：Query Service 不得复制 Collector 逻辑

不要在 Query Service 中重新实现：

```text
FIFO
Retention
Lock
Filtering
Record storage
Eviction
```

例如禁止：

```python
self._records = ...
```

禁止：

```python
deque(...)
```

禁止：

```python
threading.Lock()
```

禁止自己维护缓存。

Collector 仍然是：

```text
Record Storage + Retention + Query Primitive
```

Query Service 只是：

```text
Read Facade
```

---

# 七、Retention 语义必须保持一致

例如：

```text
Collector max_records = 3

A
B
C
D
```

Collector 当前窗口：

```text
B
C
D
```

那么：

```python
query.records()
```

必须只能看到：

```text
B
C
D
```

不能恢复：

```text
A
```

Metrics：

```text
query.metrics()
```

也只能统计：

```text
B
C
D
```

---

# 八、返回值必须保持只读

要求：

```python
records()
```

返回：

```python
tuple[ToolExecutionRecord, ...]
```

不要返回：

```text
list
deque
内部容器
iterator
generator
```

Collector 已经提供 snapshot semantics。

Query Service 不得破坏。

---

# 九、Query Service 不得修改 Collector

测试：

```text
before = collector.records()

query.records()

after = collector.records()
```

必须：

```text
before == after
```

所有 Query：

```text
records()
records_by_request_id()
records_by_project_id()
records_by_tool_name()
metrics()
```

都不能：

```text
append
clear
evict
sort
deduplicate
```

---

# 十、Metrics 语义

Query Service 不重新实现 Metrics 计算。

必须复用：

```text
ToolExecutionMetricsService
```

例如：

```text
collector
   ↓
collector.records()
   ↓
ToolExecutionMetricsService.snapshot()
   ↓
ToolExecutionMetricsSnapshot
```

因此：

```text
Collector Retention
        ↓
Query
        ↓
Metrics
```

保持完全一致。

---

# 十一、Query Service 不暴露敏感信息

Query Service 可以返回：

```text
ToolExecutionRecord
ToolExecutionMetricsSnapshot
```

但不能新增：

```text
Tool arguments
LLM prompt
LLM response
SQL
DB connection
DB session
API key
password
authorization header
```

Record 本身已经经过 Step 14 的字段约束。

不要扩展 Record。

---

# 十二、是否允许 clear？

**第一版 Query Service 不提供 clear。**

也就是说：

```text
ToolObservabilityQueryService
```

是：

```text
READ ONLY
```

不能：

```python
query.clear()
```

Collector 的：

```python
collector.clear()
```

仍然是显式内部生命周期操作。

这样可以避免未来 HTTP API 意外暴露：

```text
DELETE /observability/records
```

之类的破坏性能力。

---

# 十三、Collector 类型依赖

Query Service 可以依赖：

```text
InMemoryToolExecutionCollector
```

因为当前阶段的实际 Read Model 就是 InMemory Collector。

但是：

**不要让 Query Service 依赖 API。**

架构方向：

```text
API
 ↓
Query Service
 ↓
Collector
```

而不是：

```text
Collector
 ↓
API
```

---

# 十四、是否需要 Protocol？

本阶段：

**不要过度抽象。**

如果当前项目没有明确的 Read Repository / Protocol 模式：

不要为了“未来可能换 Redis”而新增：

```text
ToolObservabilityQueryRepository
ToolObservabilityReadPort
ToolObservabilityStoreProtocol
```

先实现一个简单：

```text
ToolObservabilityQueryService
```

即可。

未来真正需要 DB / Redis 时再抽象。

---

# 十五、测试要求

新增：

```text
tests/test_tool_observability_query_service.py
```

至少覆盖：

### 1. Empty

```text
collector = empty
query.records() == ()
query.metrics().total_count == 0
```

---

### 2. Single Record

写入一个 Record：

```text
collector.on_execution(record)
```

验证：

```text
query.records()
query.metrics()
```

都正确。

---

### 3. Multiple Records

验证：

```text
records()
records_by_request_id()
records_by_project_id()
records_by_tool_name()
```

---

### 4. Retention

```text
max_records=3

A
B
C
D
```

验证 Query Service 只能看到：

```text
B
C
D
```

---

### 5. Metrics + Retention

验证：

```text
Collector retention
        ↓
Query Service
        ↓
Metrics
```

统计只包含 retained records。

---

### 6. Query Does Not Mutate

执行所有 query：

```text
records
records_by_request_id
records_by_project_id
records_by_tool_name
metrics
```

验证 Collector 状态完全不变。

---

### 7. Snapshot Immutability

验证：

```python
result = query.records()
```

返回 tuple。

并且 Collector 后续新增 Record：

```text
old result
```

保持不变。

例如：

```text
snapshot = query.records()

collector.append(B)

snapshot
```

不能突然出现 B。

---

### 8. Clear Isolation

直接：

```python
collector.clear()
```

之后：

```text
query.records() == ()
```

但 Query Service：

```text
没有 clear()
```

---

### 9. Metrics Service Reuse

使用一个可记录调用次数的 Fake Metrics Service：

确认：

```text
query.metrics()
```

确实通过 Metrics Service 计算。

不要复制 metrics arithmetic。

---

### 10. Security

检查 Query Service 源码 import：

允许：

```text
typing
collections.abc
InMemoryToolExecutionCollector
ToolExecutionRecord
ToolExecutionMetricsService
ToolExecutionMetricsSnapshot
```

具体按真实实现调整。

禁止：

```text
sqlalchemy
psycopg
redis
kafka
celery
requests
httpx
os
subprocess
pathlib
```

不要为了测试安全性而增加复杂扫描框架。

---

# 十六、Architecture Contract

新增：

```text
C23 — Tool Observability Read Query Boundary
```

至少包含：

```text
C23.1 Query Service 只读
C23.2 不持有自己的 Record storage
C23.3 不实现 Retention
C23.4 不实现 FIFO
C23.5 不实现 Metrics arithmetic
C23.6 records 返回 immutable snapshot
C23.7 retention window 语义保持一致
C23.8 query 不修改 Collector
C23.9 不暴露 clear
C23.10 不依赖 DB
C23.11 不依赖 LLM
C23.12 不依赖 Tool Registry
C23.13 不依赖 API
C23.14 不暴露 secrets / SQL / arguments
C23.15 Metrics 复用现有 Metrics Service
```

测试必须是行为/结构契约，不要测试实现细节到无法维护的程度。

---

# 十七、不要修改 Composition Root

本阶段：

```text
backend/app/api/orchestrator_chat.py
```

**原则上不要修改。**

Query Service 可以由测试直接构造：

```python
collector = InMemoryToolExecutionCollector()

query = ToolObservabilityQueryService(
    collector
)
```

暂时不需要：

```text
API singleton
Application global query service
FastAPI dependency
lifespan
```

---

# 十八、不要增加 HTTP API

特别禁止新增：

```text
GET /api/usage/...
GET /api/tool-observability/...
```

本阶段只建立：

```text
Application Read Boundary
```

以后真正做 HTTP Metrics API 时再单独设计。

---

# 十九、测试命令

先：

```powershell
python -m pytest -q tests/test_tool_observability_query_service.py
```

然后：

```powershell
python -m pytest -q tests/test_in_memory_tool_execution_collector.py tests/test_tool_execution_metrics_service.py
```

然后：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

compile：

```powershell
python -m compileall -q backend tests
```

LSP：

```text
0 error
0 warning
```

lint：

如果环境没有 ruff / flake8：

```text
记录 unavailable
```

不要安装新 lint 工具。

---

# 二十、完成标准

必须满足：

```text
Query Service = PASS
Collector = PASS
Metrics = PASS
Retention = PASS
Read-only = PASS
Security = PASS
```

并且：

```text
生产数据库写入 = 0
API = 无变化
ToolResult = 无变化
AIOrchestrator = 无变化
ToolExecutionService = 无变化
ToolExecutionRecord = 无变化
Metrics Service = 无变化
Collector Retention = 无变化
```

---

# 二十一、最终报告格式

完成后只汇报：

```text
【Phase 3.11 Step 21 COMPLETE】

1. 新增文件
2. 修改文件
3. Query Service API
4. Retention 行为
5. Metrics 行为
6. Read-only Contract
7. C23
8. 测试结果
9. compile / LSP / lint
10. DB writes
11. API 是否变化
12. 生产逻辑是否变化
13. 当前限制
```

最后给出：

```text
Architecture:

ToolExecutionRecord
        ↓
InMemoryToolExecutionCollector
        ↓
Finite Retention Window
        ↓
ToolObservabilityQueryService
        ├── records
        ├── filtered records
        └── Metrics Snapshot
```

然后：

**立即停止。**

不要进入 Step 22。

不要开发 HTTP API。

不要开发 Database Persistence。

不要开发 Dashboard。

不要开发 Prometheus。

不要开发 OpenTelemetry。

不要开发 Audit。

不要开发 Event Bus。

不要开发 Redis / Kafka。

不要开发 Agent / MCP / LangGraph / Memory / Planning。

等待下一步指令。
