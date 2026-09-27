你现在开始执行：

# Phase 3.11 Step 17 — Tool Execution Metrics Read Model

项目：

```text
D:\coding\ai\wms-ai-assistant
```

## 一、阶段目标

在 Phase 3.11 Step 14～16 已完成：

```text
ToolExecutionContext
        ↓
ToolExecutionService
        ↓
ToolExecutionRecord
        ↓
ToolExecutionObserver
        ↓
InMemoryToolExecutionCollector
```

基础上，建立一个：

**纯内存、只读、确定性的 Tool Execution Metrics Read Model。**

目标：

```text
ToolExecutionRecord
        ↓
InMemoryToolExecutionCollector
        ↓
Metrics Calculator
        ↓
ToolExecutionMetricsSnapshot
```

本阶段只回答：

```text
执行了多少次？
成功多少次？
失败多少次？
平均耗时多少？
最大耗时多少？
```

不要实现：

```text
Dashboard
Database
Prometheus
OpenTelemetry
实时监控
告警
```

---

# 二、严格范围

## 允许

新增：

```text
backend/app/services/tool_execution_metrics_service.py
tests/test_tool_execution_metrics_service.py
```

以及：

```text
C19 Architecture Contract
docs/evaluation/Phase 3.11 Step 17 — Tool Execution Metrics Read Model.md
```

可以新增一个 frozen DTO：

```text
ToolExecutionMetricsSnapshot
```

必要时可以新增纯函数：

```text
calculate_metrics(records)
```

## 禁止

绝对不要：

```text
数据库
Repository
Migration
Redis
Kafka
Prometheus
OpenTelemetry
Grafana
Dashboard
HTTP API
WebSocket
Background Worker
Scheduler
Alert
```

不要修改：

```text
ToolResult
ToolRegistry
Tool Handler
Router
AIOrchestrator
Tool Argument Extractor
ToolChatService 的业务语义
ToolExecutionContext
ToolExecutionRecord
ToolExecutionObserver
InMemoryToolExecutionCollector 的既有 Contract
```

不要新增：

```text
Agent
MCP
LangGraph
Memory
Planning
Retry
Fallback
Parallel Tool
```

---

# 三、Step 1：先阅读真实代码

先不要修改。

阅读：

```text
backend/app/services/tool_execution_record.py
backend/app/services/tool_execution_observer.py
backend/app/services/in_memory_tool_execution_collector.py
backend/app/services/tool_execution_service.py
```

以及：

```text
tests/test_tool_execution_record.py
tests/test_tool_execution_observer.py
tests/test_in_memory_tool_execution_collector.py
```

重点确认：

1. Record 当前全部字段。
2. Collector 当前查询 API。
3. Record 中 duration_ms 的真实类型。
4. success 的真实语义。
5. project_id / tool_name / request_id 的真实约束。
6. 当前项目是否已有类似 metrics / snapshot DTO。

**不要假设。**

如果已有等价 Metrics DTO：

优先复用。

---

# 四、Metrics Snapshot

建议新增：

```text
backend/app/services/tool_execution_metrics_service.py
```

其中定义：

```python
@dataclass(frozen=True)
class ToolExecutionMetricsSnapshot:
    total_count: int
    success_count: int
    failure_count: int

    success_rate: float | None
    failure_rate: float | None

    total_duration_ms: float
    average_duration_ms: float | None
    max_duration_ms: float | None
```

最终字段必须根据现有项目实际风格调整。

不要为了本阶段增加复杂指标。

---

# 五、Metric 定义

必须明确：

## total_count

```text
Record 数量
```

---

## success_count

```text
count(record.success is True)
```

---

## failure_count

```text
count(record.success is False)
```

必须保证：

```text
success_count + failure_count == total_count
```

---

# 六、Success Rate

如果：

```text
total_count > 0
```

则：

```text
success_rate = success_count / total_count
```

如果：

```text
total_count == 0
```

则：

```text
success_rate = None
failure_rate = None
```

**不要返回 0。**

因为：

```text
0 records
```

与：

```text
100 records / 0 success
```

不是同一个含义。

---

# 七、Failure Rate

同样：

```text
failure_rate = failure_count / total_count
```

如果：

```text
total_count == 0
```

必须：

```text
None
```

---

# 八、Duration Metrics

基于：

```text
record.duration_ms
```

计算：

## total_duration_ms

所有 Record：

```text
sum(duration_ms)
```

---

## average_duration_ms

```text
total_duration_ms / total_count
```

无 Record：

```text
None
```

---

## max_duration_ms

所有 Record：

```text
max(duration_ms)
```

无 Record：

```text
None
```

本阶段不要增加：

```text
p50
p90
p95
p99
min
stddev
```

这些留到后续真正需要 Metrics 时再做。

---

# 九、不要重新计算 Timing

非常重要。

Metrics Service：

**只能使用：**

```text
record.duration_ms
```

不能：

```text
datetime.now()
perf_counter()
```

Metrics 是对已经完成的 Record 做分析。

不要重新测量执行时间。

---

# 十、Metrics Service Contract

建议：

```python
class ToolExecutionMetricsService:
    @staticmethod
    def snapshot(
        records: Iterable[ToolExecutionRecord],
    ) -> ToolExecutionMetricsSnapshot:
        ...
```

或者按照当前项目 Service 风格实现。

核心要求：

```text
Pure
Deterministic
No IO
No DB
No LLM
No Tool execution
No mutation
```

---

# 十一、不要强依赖 Collector

Metrics Service 最好接收：

```text
Iterable[ToolExecutionRecord]
```

而不是：

```text
InMemoryToolExecutionCollector
```

原因：

未来可以直接：

```text
Collector
    ↓
records()
    ↓
Metrics Service
```

也可以：

```text
Database
    ↓
records
    ↓
Metrics Service
```

这样 Metrics 层不绑定存储方式。

---

# 十二、Collector + Metrics 使用方式

最终允许：

```python
records = collector.records()

snapshot = metrics_service.snapshot(records)
```

形成：

```text
InMemory Collector
        ↓
Immutable Records
        ↓
Metrics Service
        ↓
Immutable Snapshot
```

不要让 Metrics Service 自己访问 Collector 内部 `_records`。

禁止：

```text
metrics_service._collector._records
```

---

# 十三、Immutability

Metrics Snapshot 必须：

```text
@dataclass(frozen=True)
```

验证：

```text
snapshot.total_count = 10
```

必须失败。

Metrics Service 不允许修改：

```text
records
collector
record
```

---

# 十四、输入安全

Metrics Service 接收：

```text
Iterable[ToolExecutionRecord]
```

对于非 Record：

不要静默转换。

建议：

```text
TypeError
```

例如：

```python
snapshot([record, "bad"])
```

必须明确失败。

不要：

```text
skip bad item
```

否则会隐藏数据问题。

---

# 十五、空数据

必须测试：

```text
snapshot(())
```

结果：

```text
total_count = 0
success_count = 0
failure_count = 0

success_rate = None
failure_rate = None

total_duration_ms = 0
average_duration_ms = None
max_duration_ms = None
```

不要出现：

```text
ZeroDivisionError
NaN
Infinity
```

---

# 十六、浮点数

`duration_ms` 当前是 float。

Metrics：

保持：

```text
float
```

不要为了显示而：

```text
round()
```

除非项目现有 DTO 已明确规定精度。

本阶段：

**保持计算结果原始精度。**

---

# 十七、Metrics 不包含敏感信息

Snapshot 只能包含：

```text
count
success
failure
rate
duration
```

不要加入：

```text
request_id
project_id
tool_name
arguments
SQL
result.data
error message
exception
```

注意：

本阶段是**全局 metrics snapshot**。

不要同时做：

```text
by_tool
by_project
by_request
```

这些属于下一层维度分析。

---

# 十八、不要增加维度

本阶段不要实现：

```text
metrics_by_tool()
metrics_by_project()
metrics_by_request()
```

也不要实现：

```text
top_slowest_tools()
failure_by_tool()
success_rate_by_project()
```

原因：

这些属于：

```text
Analytics / Aggregation
```

不是当前最小 Metrics Read Model。

---

# 十九、Determinism

同样的：

```text
records
```

连续执行：

```text
snapshot(records)
snapshot(records)
```

必须：

```text
== 
```

不要依赖：

```text
current time
random
set ordering
network
DB
```

---

# 二十、Snapshot Arithmetic Contract

必须验证：

```text
success_count + failure_count == total_count
```

如果：

```text
total_count > 0
```

必须：

```text
0 <= success_rate <= 1
0 <= failure_rate <= 1
success_rate + failure_rate == 1
```

允许浮点误差。

使用：

```text
math.isclose()
```

而不是字符串比较。

---

# 二十一、Collector Integration Test

不要修改 Collector。

增加少量集成测试：

```text
Collector
    ↓
records()
    ↓
Metrics Service
```

例如：

```text
record1 success duration=10
record2 success duration=20
record3 failure duration=30
```

结果：

```text
total_count = 3
success_count = 2
failure_count = 1

success_rate = 2/3
failure_rate = 1/3

total_duration_ms = 60
average_duration_ms = 20
max_duration_ms = 30
```

不要 hardcode rounding。

---

# 二十二、Concurrency

Metrics Service 本身：

**不需要 Lock。**

因为它：

```text
只读取一个 records snapshot
```

Collector 已经负责：

```text
thread-safe collection
```

正确方式：

```text
collector.records()
        ↓
tuple snapshot
        ↓
metrics
```

不要：

```text
metrics service
    ↓
collector._records
```

---

# 二十三、Architecture Contract C19

新增：

```text
C19 Tool Execution Metrics Read Model
```

至少：

```text
C19.1 Metrics Service accepts Records, not Collector internals
C19.2 Metrics Snapshot is frozen
C19.3 No timing re-measurement
C19.4 total = success + failure
C19.5 empty dataset uses None rates
C19.6 duration uses Record.duration_ms
C19.7 no request/project/tool dimensions
C19.8 no sensitive fields
C19.9 no DB/LLM/Tool execution
C19.10 deterministic
C19.11 no persistence
C19.12 no global singleton
```

---

# 二十四、测试

新增：

```text
tests/test_tool_execution_metrics_service.py
```

建议：

**25～35 个高质量测试。**

至少覆盖：

## Empty

```text
empty records
```

## Success

```text
all success
```

## Failure

```text
all failure
```

## Mixed

```text
success + failure
```

## Arithmetic

```text
success + failure = total
```

## Rates

```text
success_rate
failure_rate
```

## Duration

```text
total
average
max
```

## Zero

确认：

```text
no ZeroDivisionError
no NaN
no Infinity
```

## Immutability

Snapshot frozen。

## Invalid Input

非 Record：

```text
TypeError
```

## Determinism

同一输入：

```text
same snapshot
```

## Collector Integration

```text
Collector
→ records()
→ Metrics
```

## Security

Snapshot 不包含：

```text
arguments
SQL
secret
result.data
request_id
project_id
tool_name
```

---

# 二十五、不要修改生产执行链

本阶段原则：

```text
ToolExecutionService
        ↓
ToolExecutionRecord
        ↓
Observer
        ↓
Collector
```

保持不变。

Metrics 是：

```text
Read Model
```

不要反向影响：

```text
Tool Execution
```

所以：

```text
Metrics calculation failure
```

不能导致：

```text
Tool failure
```

---

# 二十六、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.11 Step 17 — Tool Execution Metrics Read Model.md
```

记录：

## 1. Purpose

为什么建立 Metrics Read Model。

## 2. Architecture

```text
ToolExecutionRecord
        ↓
InMemoryToolExecutionCollector
        ↓
records()
        ↓
ToolExecutionMetricsService
        ↓
ToolExecutionMetricsSnapshot
```

## 3. Metrics

```text
total_count
success_count
failure_count
success_rate
failure_rate
total_duration_ms
average_duration_ms
max_duration_ms
```

## 4. Empty Semantics

```text
total = 0
rates = None
average = None
max = None
```

## 5. Security

```text
No arguments
No result data
No SQL
No secrets
No request/project/tool dimension
```

## 6. Limitations

```text
No persistence
No API
No dashboard
No Prometheus
No OpenTelemetry
No dimensions
No historical aggregation
```

---

# 二十七、测试命令

先：

```powershell
python -m pytest -q tests/test_tool_execution_metrics_service.py
```

然后：

```powershell
python -m pytest -q `
  tests/test_tool_execution_record.py `
  tests/test_tool_execution_observer.py `
  tests/test_in_memory_tool_execution_collector.py `
  tests/test_tool_execution_metrics_service.py `
  tests/test_tool_execution_service.py `
  tests/test_tool_runtime_failure_contract.py
```

再：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

如果 PostgreSQL 继续出现：

```text
psycopg.errors.ConnectionTimeout
```

不要修改代码绕过。

记录：

```text
Environment DB unavailable
```

---

# 二十八、compile / LSP

运行：

```powershell
python -m compileall backend tests
```

要求：

```text
0 errors
0 warnings
```

如果没有 ruff / flake8：

不要安装新的 lint 工具。

---

# 二十九、最终报告

完成后严格：

```text
【Phase 3.11 Step 17 COMPLETE】

1. 新增文件
2. 修改文件
3. Metrics Snapshot Contract
4. Metrics 定义
5. Empty Dataset
6. Duration Metrics
7. Determinism
8. Security
9. Architecture Contract C19
10. 测试结果
11. compile / LSP
12. DB writes
13. API 是否变化
14. ToolResult 是否变化
15. 当前限制
```

最后输出：

```text
Tool Execution Observability:

ToolExecutionContext
        ↓
ToolExecutionService
        ↓
ToolRegistry
        ↓
Tool
        ↓
ToolResult
        +
Timing
        ↓
ToolExecutionRecord
        ↓
ToolExecutionObserver
        ↓
InMemoryToolExecutionCollector
        ↓
Read-only Records
        ↓
ToolExecutionMetricsService
        ↓
ToolExecutionMetricsSnapshot
```

然后：

**立即停止。**

不要进入 Step 18。

不要做数据库持久化。

不要做 HTTP Metrics API。

不要做 Dashboard。

不要做 Prometheus。

不要做 OpenTelemetry。

不要做 Audit。

不要做分布式 Event Bus。

不要开发 Agent / MCP / LangGraph / Memory / Planning。
