你现在开始执行：

# Phase 3.11 — Step 26

# Tool Observability Persistence Boundary Survey & Design

项目：

`D:\coding\ai\wms-ai-assistant`

---

# 一、阶段目标

Step 25 已经完成 Tool Observability 的 HTTP Read-only API：

```text
Tool Execution
    ↓
ToolExecutionRecord
    ↓
Observer
    ↓
InMemoryCollector
    ↓
QueryService
    ↓
Snapshot / Metrics
    ↓
Serialization
    ↓
FastAPI
    ↓
JSON
```

当前问题：

```text
InMemoryCollector
```

是进程内存。

应用重启后：

```text
records = lost
```

但现在：

**不要立即实现 PostgreSQL Persistence。**

Step 26 唯一目标：

> 调研当前项目已有持久化架构，并设计 Tool Observability 的 Persistence Boundary。

本阶段只做：

```text
Survey
    ↓
Architecture Analysis
    ↓
Persistence Boundary Design
    ↓
Decision Document
    ↓
Tests / Architecture Contract
    ↓
STOP
```

---

# 二、严格禁止

本阶段禁止：

```text
❌ 新增数据库表
❌ 新增 migration
❌ 修改数据库 schema
❌ 新增 PostgreSQL Repository 实现
❌ Collector 直接写 PostgreSQL
❌ Collector 直接依赖 SQLAlchemy
❌ Collector 直接依赖 Session
❌ 修改 ToolExecutionService 行为
❌ 修改 ToolExecutionRecord
❌ 修改 ToolExecutionObserver
❌ 修改 QueryService 行为
❌ 修改 Snapshot DTO
❌ 修改 Metrics DTO
❌ 修改 HTTP API
❌ 新增 Redis
❌ 新增 Kafka
❌ 新增 Celery
❌ 新增后台线程
❌ 新增消息队列
❌ 新增异步持久化
❌ 新增 Dashboard
❌ 新增 Prometheus
❌ 新增 OpenTelemetry
```

尤其禁止为了“验证设计”而真的连接数据库。

---

# 三、第一步：阅读当前项目 Persistence 架构

先不要修改代码。

重点检查：

```text
backend/app/db/
backend/app/db/models/
backend/app/repositories/
backend/app/services/
backend/app/projects/
```

以及：

```text
backend/app/main.py
backend/app/api/
tests/
```

搜索项目中现有：

```text
Repository
Session
sessionmaker
AsyncSession
SQLAlchemy
Base
UnitOfWork
transaction
get_db
engine
```

同时检查：

```text
LLMUsageRecord
LLMUsageRepository
LLM usage persistence
```

尤其分析 Phase 3.10 已经实现的：

```text
LLM Usage Persistence
```

因为 Tool Execution Observability 未来可能需要复用相同的 Persistence 架构思想。

---

# 四、重点回答以下问题

不要凭经验回答。

必须根据真实代码回答。

## 1. 当前项目数据库访问入口是什么？

例如：

```text
engine
Session
Repository
Service
```

具体文件：

```text
xxx.py
```

---

## 2. 当前 ORM Model 放在哪里？

例如：

```text
backend/app/db/models/
```

记录真实路径。

---

## 3. 当前 Repository 放在哪里？

记录：

```text
文件
类名
职责
```

---

## 4. Repository 是否直接暴露 SQLAlchemy Session？

例如：

```python
Repository(session)
```

还是：

```python
Repository(engine)
```

或者其他模式。

必须根据实际代码判断。

---

## 5. 当前 Persistence 是否使用：

```text
Unit of Work
Transaction
Repository
Service
```

分别说明：

```text
有 / 无
```

不要假设。

---

# 五、重点研究 LLM Usage Persistence

找到：

```text
LLMUsageRecord
LLMUsageRepository
```

分析：

```text
LLM Execution
 ↓
Usage Record
 ↓
Repository
 ↓
Database
```

回答：

### A

LLM Usage Record 与 ToolExecutionRecord 是否属于同一种：

```text
Operational Event
```

如果是：

说明可以复用哪些设计思想。

如果不是：

说明为什么。

---

### B

当前 LLM Usage Persistence 是否：

```text
Execution Layer
        ↓
Repository
```

还是：

```text
Execution Layer
        ↓
Service
        ↓
Repository
```

必须根据代码确认。

---

### C

当前 Repository 是否包含：

```text
business logic
aggregation
validation
serialization
```

逐项检查。

---

# 六、Tool Observability 当前边界

分析现有：

```text
ToolExecutionRecord
ToolExecutionObserver
InMemoryToolExecutionCollector
ToolObservabilityQueryService
ToolExecutionSnapshot
ToolExecutionMetricsSnapshot
```

回答：

## 1. 哪一个对象最适合成为未来 Persistence Adapter 的输入？

候选：

```text
ToolExecutionRecord
ToolExecutionSnapshot
dict
```

必须根据架构分析给出结论。

---

## 2. Persistence 应该放在哪一层？

比较：

### Option A

```text
ToolExecutionService
    ↓
Repository
```

### Option B

```text
ToolExecutionObserver
    ↓
Persistence Adapter
```

### Option C

```text
Collector
    ↓
Repository
```

### Option D

```text
QueryService
    ↓
Repository
```

逐项说明：

```text
优点
缺点
违反的当前架构约束
```

最终给出一个：

```text
Recommended Boundary
```

但是：

**不要修改生产代码实现这个 Boundary。**

---

# 七、重点原则

必须维护当前架构：

```text
Execution
    ↓
Observability
```

而不能变成：

```text
Execution
    ↓
Database
```

尤其不能出现：

```text
ToolExecutionService
    ↓
SQLAlchemy
```

或者：

```text
Tool Handler
    ↓
Repository
```

Tool 本身不能获得 Persistence 能力。

---

# 八、设计目标

未来理想结构：

```text
                ┌──────────────────────┐
                │ ToolExecutionService │
                └──────────┬───────────┘
                           │
                           ↓
                ToolExecutionRecord
                           │
                           ↓
                ToolExecutionObserver
                           │
             ┌─────────────┴─────────────┐
             │                           │
             ↓                           ↓
      InMemoryCollector          PersistenceAdapter
             │                           │
             ↓                           ↓
       QueryService                 Repository
             │                           │
             ↓                           ↓
        HTTP API                    PostgreSQL
```

注意：

这只是：

**目标架构设计。**

本阶段不要实现：

```text
PersistenceAdapter
Repository
PostgreSQL
```

---

# 九、InMemory Collector 的定位

明确当前：

```text
InMemoryToolExecutionCollector
```

未来应该是什么角色。

候选：

### A

唯一 Observability Store

### B

短期 Runtime Store

### C

Test Store

### D

Cache

分析这几个选项。

最终明确：

```text
当前 Collector 的职责
未来 Persistence 存储的职责
两者是否共存
```

---

# 十、Retention 分析

Step 20 已经有：

```text
DEFAULT_MAX_RECORDS = 1000
```

以及：

```text
deque(maxlen=N)
```

分析：

如果未来加入 PostgreSQL：

```text
InMemory max_records = 1000
```

是否应该继续保留？

讨论：

```text
Runtime retention
```

和：

```text
Persistent retention
```

是否应该分离。

例如：

```text
Memory:
1000 records

Database:
long-term records
```

但是：

**本阶段只做设计，不实现 retention policy。**

---

# 十一、Process / Multi-worker 问题

当前 Collector：

```text
process-local
```

Step 25 已经明确：

```text
multi-process independent
```

分析：

如果 FastAPI 使用：

```text
workers > 1
```

当前 API：

```text
GET /api/observability/tools
```

是否能够看到所有 worker 的数据？

必须给出：

```text
当前行为
```

以及：

```text
Persistence 后的目标行为
```

---

# 十二、失败处理设计

未来 Persistence 如果失败：

例如：

```text
PostgreSQL unavailable
connection timeout
insert failure
```

必须讨论：

是否应该影响：

```text
Tool Execution
```

答案必须基于当前：

```text
ToolExecutionObserver
```

的：

```text
failure-isolated
```

原则进行设计。

目标原则：

```text
Observability failure
        ≠
Tool execution failure
```

即：

```text
DB write failed
        ↓
log / metric
        ↓
Tool execution continues
```

但本阶段：

**不要实现。**

---

# 十三、Security Analysis

未来 Persistence 不能存储：

```text
API key
password
authorization header
database URL
connection string
SQLAlchemy Session
DB connection
LLM prompt
LLM response
tool arguments
```

当前 Record 已经限制为：

```text
11 fields
```

检查：

```text
ToolExecutionRecord
```

是否已经足够安全。

如果发现需要修改 Record：

**不要修改。**

只记录：

```text
发现问题
影响
建议
```

然后停止。

---

# 十四、Repository Boundary Design

设计未来接口。

例如：

```python
class ToolExecutionRecordRepository(Protocol):
    def save(self, record: ToolExecutionRecord) -> None:
        ...
```

注意：

本阶段：

**只允许写设计文档。**

不要真的新增 Python Protocol。

不要新增 Repository 文件。

不要新增数据库 Model。

---

# 十五、Query Boundary Design

未来查询是否应该：

```text
QueryService
    ↓
Collector
```

和：

```text
QueryService
    ↓
Repository
```

统一？

比较：

```text
Runtime Store
Persistent Store
```

是否需要：

```text
CompositeStore
```

例如：

```text
QueryService
    ↓
ObservabilityStore
    ├── Memory
    └── PostgreSQL
```

但是：

**不要实现 CompositeStore。**

只分析。

---

# 十六、Architecture Decision

新增：

```text
docs/decisions/ADR-3.11.26-tool-observability-persistence-boundary.md
```

内容至少：

```text
# ADR

Status:
Accepted / Proposed

Context

Current Architecture

Problem

Options

Option A
Option B
Option C
Option D

Decision

Consequences

Security

Failure Isolation

Multi-process

Retention

Future Migration Path
```

重点写：

```text
本阶段不实现 Persistence。
```

---

# 十七、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.11.26 — Tool Observability Persistence Boundary.md
```

记录：

## 1. Survey

真实代码：

```text
Persistence
Repository
ORM
Session
```

## 2. Current Architecture

```text
Tool Execution
↓
Observability
↓
InMemory
↓
HTTP
```

## 3. Persistence Options

比较：

```text
Option A
Option B
Option C
Option D
```

## 4. Recommended Boundary

给出最终设计。

## 5. Security

## 6. Multi-process

## 7. Failure Isolation

## 8. Retention

## 9. Next Step

只写：

```text
Future step may implement the selected Persistence Adapter.
```

不要指定自动进入下一阶段。

---

# 十八、Architecture Tests

增加少量 architecture contract tests。

例如：

```text
tests/test_tool_observability_persistence_architecture.py
```

只验证当前架构没有被破坏：

### C28

Tool Execution Service：

```text
不能 import SQLAlchemy
不能 import repository
不能 import database session
```

### C29

Tool Handler：

```text
不能 import Tool Observability Persistence
```

### C30

Collector：

```text
不能 import SQLAlchemy
不能 import Repository
不能 import Database Session
```

### C31

QueryService：

```text
不能执行数据库写操作
```

### C32

Observability API：

```text
不能直接 import Repository
不能直接 import SQLAlchemy
```

注意：

这些测试只保护：

**当前边界。**

不要为了测试而修改生产代码。

---

# 十九、不要修改现有 API

Step 25 已经定义：

```text
GET /api/observability/tools
GET /api/observability/tools/metrics
```

Step 26：

```text
API contract = unchanged
```

不要新增：

```text
/history
/search
/filter
/project
/tool
```

等 endpoint。

---

# 二十、测试要求

本阶段主要是：

```text
Architecture / Static Contract
```

不需要：

```text
DB integration
```

不要启动 PostgreSQL。

不要执行：

```text
INSERT
UPDATE
DELETE
CREATE TABLE
ALTER TABLE
```

建议：

```text
pytest -q tests/test_tool_observability_persistence_architecture.py
```

然后：

```text
pytest -q
```

以及：

```text
python -m compileall backend tests
```

如果项目有 LSP：

```text
0 diagnostics
```

---

# 二十一、成功标准

Step 26 完成必须满足：

```text
✓ 已阅读现有 Persistence 架构
✓ 已分析 LLM Usage Persistence
✓ 已分析 Tool Observability 当前架构
✓ 已比较 Persistence Boundary Options
✓ 已确定推荐 Boundary
✓ 已明确 Collector 未来定位
✓ 已明确 Multi-worker 行为
✓ 已明确 Failure Isolation
✓ 已明确 Security Boundary
✓ 已新增 ADR
✓ 已新增 Evaluation 文档
✓ 已新增 Architecture Contract Tests
✓ 没有新增 DB Model
✓ 没有新增 Migration
✓ 没有 DB Write
✓ 没有修改 Tool Execution 行为
✓ 没有修改 HTTP API
✓ 没有 Redis/Kafka
```

---

# 二十二、最终报告

完成后严格按照：

```text
【Phase 3.11 Step 26 COMPLETE】

1. Survey
2. Current Persistence Architecture
3. LLM Usage Persistence Analysis
4. Tool Observability Persistence Boundary
5. Options Comparison
6. Final Decision
7. Collector 定位
8. Multi-process
9. Failure Isolation
10. Security
11. 新增文件
12. 修改文件
13. Tests
14. DB writes
15. API 是否变化
16. 当前限制

Architecture:

ToolExecutionService
        ↓
ToolExecutionRecord
        ↓
ToolExecutionObserver
        ↓
┌───────────────┬──────────────────┐
│               │                  │
Memory Store    Persistence Adapter
│               │
QueryService    Repository
│               │
HTTP API        PostgreSQL
```

最后：

```text
Phase 3.11 Step 26 到此停止。

不要实现 PostgreSQL Persistence。
不要新增数据库表。
不要新增 Migration。
不要进入 Step 27。
等待下一步指令。
```
