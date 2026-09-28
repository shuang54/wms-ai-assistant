你现在开始执行：

# Phase 3.11 Step 22 — Tool Observability Snapshot Read Model

## 一、阶段目标

在 Step 21 已完成：

```text
ToolExecutionRecord
        ↓
InMemoryToolExecutionCollector
        ↓
ToolObservabilityQueryService
        ↓
records / filtered records / Metrics Snapshot
```

本阶段只解决一个问题：

> **建立稳定的 Observability Snapshot Read Model，使未来 HTTP API / Dashboard 不需要直接暴露内部 ToolExecutionRecord。**

目标：

```text
Collector
    ↓
Query Service
    ↓
Observability Snapshot DTO
    ↓
未来 API
```

本阶段：

**不做 HTTP API。**

**不做数据库。**

**不做持久化。**

**不做 Dashboard。**

---

# 二、开始前必须阅读

先阅读：

```text
backend/app/services/tool_execution_record.py
backend/app/services/tool_execution_metrics_service.py
backend/app/services/tool_observability_query_service.py
backend/app/services/in_memory_tool_execution_collector.py
backend/app/services/tool_execution_context.py
```

以及：

```text
tests/test_tool_execution_record.py
tests/test_tool_execution_metrics_service.py
tests/test_tool_observability_query_service.py
tests/test_in_memory_tool_execution_collector.py
```

同时检查：

```text
docs/architecture.md
docs/evaluation/
```

重点确认：

1. 当前 `ToolExecutionRecord` 的字段。
2. 哪些字段适合成为未来 API 的公开 Read Model。
3. 哪些字段属于内部执行模型。
4. 当前 `ToolExecutionMetricsSnapshot` 是否已经可以直接作为 Read Model。
5. 是否已有类似 Snapshot / View DTO，避免重复设计。

---

# 三、严格范围

## 允许

新增：

```text
backend/app/services/tool_observability_snapshot.py
```

或者根据项目实际结构选择更自然的文件名。

新增：

```text
tests/test_tool_observability_snapshot.py
```

修改：

```text
backend/app/services/tool_observability_query_service.py
```

仅用于增加 Snapshot 转换能力。

增加：

```text
docs/evaluation/Phase 3.11 Step 22 — Tool Observability Snapshot Read Model.md
```

以及必要的：

```text
docs/architecture.md
tests/test_tool_architecture_contract.py
```

---

# 四、禁止修改的核心生产逻辑

本阶段禁止修改：

```text
AIOrchestrator
AI Router
ToolExecutionService
ToolExecutionObserver
ToolExecutionContext
ToolExecutionRecord
ToolExecutionMetricsService
InMemoryToolExecutionCollector 的 Retention
ToolRegistry
ToolResult
ToolChatService
Composition Root
```

特别禁止：

```text
修改 ToolExecutionRecord 字段
修改 Record validation
修改 Collector retention
修改 Metrics arithmetic
修改 Tool execution semantics
```

---

# 五、Snapshot DTO

新增一个面向 Read Model 的 frozen DTO。

建议：

```python
ToolExecutionSnapshot
```

字段只选择未来 API 真正可能需要的非敏感信息。

建议最小字段：

```text
request_id
round
tool_name
started_at
finished_at
duration_ms
success
project_id
tool_call_id
error_code
error_type
```

注意：

这里暂时可以与 `ToolExecutionRecord` 字段高度接近。

**但它必须是独立 DTO。**

原因：

```text
ToolExecutionRecord
=
内部 Execution Event

ToolExecutionSnapshot
=
对外 Read Model
```

以后即使内部 Record 增加字段，也不应该自动改变 API Read Model。

---

# 六、不要把 Metrics 混进单条 Record Snapshot

不要设计成：

```text
ToolExecutionSnapshot
    + total_count
    + success_rate
    + average_duration
```

这是两个不同层级：

```text
Record Snapshot
```

和：

```text
Metrics Snapshot
```

应该保持：

```text
ToolObservabilitySnapshot
├── records
└── metrics
```

或者由 Query Service 分别提供：

```text
records
metrics
```

具体选择遵循现有项目风格。

**不要为了一个 DTO 强行把两个概念合并。**

---

# 七、DTO 必须 immutable

使用项目现有 frozen DTO 风格：

```python
@dataclass(frozen=True)
class ToolExecutionSnapshot:
    ...
```

不要：

```text
dict
普通 class
可变 dataclass
Pydantic Model
```

除非现有项目已有明确统一规范要求使用其他 DTO。

Python `dataclass(frozen=True)` 会阻止对字段进行重新赋值，适合当前 Read Model 的不可变语义。

---

# 八、不要让 Snapshot 持有原始 Record

禁止：

```python
ToolExecutionSnapshot(
    record=record
)
```

也不要：

```python
self._record = record
```

Snapshot 必须复制需要的值：

```text
ToolExecutionRecord
        ↓
字段转换
        ↓
ToolExecutionSnapshot
```

这样未来：

```text
Record
```

即使发生内部结构变化，也不会直接污染 Read Model。

---

# 九、Snapshot Conversion

建议提供一个纯转换函数：

```python
ToolExecutionSnapshot.from_record(record)
```

要求：

```text
输入：
ToolExecutionRecord

输出：
ToolExecutionSnapshot
```

不能：

```text
DB
LLM
Collector
ToolRegistry
HTTP
```

转换过程必须：

```text
pure
deterministic
no IO
```

---

# 十、字段映射必须显式

不要使用：

```python
vars(record)
```

不要使用：

```python
asdict(record)
```

不要使用：

```python
**record.__dict__
```

原因：

未来 Record 增加字段时，不应该自动把新字段泄露到 Read Model。

必须明确写：

```text
record.request_id
record.round
record.tool_name
...
```

逐字段映射。

---

# 十一、敏感信息边界

Snapshot 不允许新增：

```text
LLM prompt
LLM response
Tool arguments
SQL
Database connection
Database session
API key
password
Authorization header
Exception traceback
```

尤其：

```text
ToolExecutionRecord
```

当前已经没有 Tool arguments / SQL。

不要为了“调试方便”把这些信息加入 Snapshot。

---

# 十二、Query Service 增加 Snapshot API

在：

```text
ToolObservabilityQueryService
```

增加：

```python
snapshots()
```

以及过滤版本：

```python
snapshots_by_request_id(request_id)
snapshots_by_project_id(project_id)
snapshots_by_tool_name(tool_name)
```

语义：

```text
collector
    ↓
query.records()
    ↓
ToolExecutionSnapshot.from_record()
    ↓
tuple[ToolExecutionSnapshot, ...]
```

注意：

**不要改变现有 API：**

```text
records()
records_by_request_id()
records_by_project_id()
records_by_tool_name()
metrics()
```

这些 API 继续保留。

本阶段只是新增 Snapshot Read Model。

---

# 十三、Retention 必须保持一致

例如：

```text
max_records = 3

A
B
C
D
```

Collector：

```text
B
C
D
```

那么：

```text
query.snapshots()
```

必须：

```text
B
C
D
```

不能：

```text
A
B
C
D
```

不能通过 Snapshot 层恢复被淘汰记录。

---

# 十四、Snapshot 必须是独立快照

测试：

```python
snapshots = query.snapshots()

collector.on_execution(record_d)
```

然后：

```text
snapshots
```

必须保持原来的内容。

再次：

```python
query.snapshots()
```

才看到新的 Record。

---

# 十五、Snapshot 与 Record 解耦测试

增加测试：

```text
Record
    ↓
Snapshot
```

验证：

1. Snapshot 类型不是 ToolExecutionRecord。
2. 修改 Record 不可能影响 Snapshot。
3. Record 新增未来字段不会自动出现在 Snapshot。
4. Snapshot 字段集合固定。
5. Snapshot 不持有 Collector。
6. Snapshot 不持有 Metrics Service。
7. Snapshot 不持有 DB / LLM / Tool 对象。

如果 Record 当前已经 frozen：

重点测试：

```text
snapshot is independent object
```

即可。

---

# 十六、Metrics 不受影响

确认：

```text
query.metrics()
```

仍然完全走：

```text
ToolExecutionMetricsService
```

不要修改 Metrics Service。

不要把：

```text
ToolExecutionSnapshot
```

拿去重新计算 Metrics。

正确关系：

```text
Collector
 ├── records → Snapshot
 └── records → Metrics Service
```

而不是：

```text
Collector
    ↓
Snapshot
    ↓
Metrics
```

---

# 十七、Query Service 最终结构

目标：

```text
ToolObservabilityQueryService

records()
records_by_request_id()
records_by_project_id()
records_by_tool_name()

snapshots()
snapshots_by_request_id()
snapshots_by_project_id()
snapshots_by_tool_name()

metrics()
```

其中：

```text
records*
```

是内部 Execution Record 查询。

```text
snapshots*
```

是稳定 Read Model 查询。

```text
metrics()
```

是聚合 Read Model。

---

# 十八、不要增加分页 / 排序 / DSL

本阶段不要实现：

```text
page
page_size
offset
cursor
sort
order_by
filter DSL
time range
aggregation dimensions
```

这些属于未来 HTTP/API Read Layer。

当前只建立：

```text
Stable Snapshot Contract
```

---

# 十九、Architecture Contract

新增：

```text
C24 — Tool Observability Snapshot Read Model
```

至少覆盖：

```text
C24.1 Snapshot 是独立 DTO
C24.2 Snapshot immutable
C24.3 Snapshot 不持有 Record
C24.4 Snapshot 不持有 Collector
C24.5 Snapshot 不持有 Metrics Service
C24.6 字段显式映射
C24.7 不使用 vars / __dict__ / asdict 自动泄露字段
C24.8 不包含 Tool arguments
C24.9 不包含 SQL
C24.10 不包含 secrets
C24.11 retention window 与 Collector 一致
C24.12 old snapshot 不随 Collector 变化
C24.13 Metrics Service 不变化
C24.14 Query Service 原有 API 不变化
C24.15 Snapshot Query 是 read-only
```

不要过度测试实现细节。

---

# 二十、测试要求

新增：

```text
tests/test_tool_observability_snapshot.py
```

至少覆盖：

### DTO

```text
constructor
field equality
frozen / immutable
exact fields
```

### Conversion

```text
Record → Snapshot
```

### Snapshot independence

```text
old snapshot != future collector state
```

### Retention

```text
max_records=3
A B C D
→ B C D
```

### Query

```text
snapshots()
snapshots_by_request_id()
snapshots_by_project_id()
snapshots_by_tool_name()
```

### Empty

```text
empty collector
→ ()
```

### Metrics isolation

确认：

```text
query.metrics()
```

没有通过 Snapshot 计算。

### Security

确认 Snapshot 模块不依赖：

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

按照实际 import 再确定最终白名单。

---

# 二十一、测试数量

目标：

```text
25～40 个测试
```

不要为了数量重复。

如果实际只需要 20 个高质量测试，也可以。

---

# 二十二、不要修改 API

本阶段：

```text
backend/app/api/**
```

原则上：

**0 修改。**

不要增加：

```text
GET /api/tool-observability
GET /api/tool-observability/records
GET /api/tool-observability/metrics
```

这些留到后续独立阶段。

---

# 二十三、不要修改 Composition Root

不要把 Snapshot Query Service 接入：

```text
backend/app/api/orchestrator_chat.py
```

也不要增加：

```text
singleton
lifespan
FastAPI dependency
```

本阶段通过测试显式构造：

```python
collector = InMemoryToolExecutionCollector()

query = ToolObservabilityQueryService(
    collector
)
```

即可。

---

# 二十四、验证命令

先：

```powershell
python -m pytest -q tests/test_tool_observability_snapshot.py
```

然后：

```powershell
python -m pytest -q tests/test_tool_observability_query_service.py tests/test_in_memory_tool_execution_collector.py tests/test_tool_execution_metrics_service.py
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

如果环境没有：

```text
ruff
flake8
```

记录：

```text
lint unavailable
```

不要安装新工具。

---

# 二十五、完成标准

必须满足：

```text
Snapshot DTO = PASS
Conversion = PASS
Query Snapshot = PASS
Retention = PASS
Metrics Isolation = PASS
Read-only = PASS
Security = PASS
```

并且：

```text
ToolExecutionRecord       = 无修改
Collector Retention       = 无修改
Metrics Service           = 无修改
ToolExecutionService      = 无修改
AIOrchestrator            = 无修改
Router                    = 无修改
ToolRegistry              = 无修改
ToolResult                = 无修改
API                       = 无修改
Composition Root          = 无修改
DB writes                 = 0
```

---

# 二十六、最终报告格式

完成后只汇报：

```text
【Phase 3.11 Step 22 COMPLETE】

1. 新增文件
2. 修改文件
3. Snapshot DTO
4. Record → Snapshot
5. Query Snapshot API
6. Retention 行为
7. Metrics Isolation
8. C24
9. 测试结果
10. compile / LSP / lint
11. DB writes
12. API 是否变化
13. 生产逻辑是否变化
14. 当前限制
```

最后：

```text
Architecture:

ToolExecutionRecord
        ↓
InMemoryToolExecutionCollector
        ↓
Finite Retention Window
        ↓
ToolObservabilityQueryService
        ├── Execution Records
        ├── Snapshot Read Model
        └── Metrics Snapshot
```

然后：

**立即停止。**

不要进入 Step 23。

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
