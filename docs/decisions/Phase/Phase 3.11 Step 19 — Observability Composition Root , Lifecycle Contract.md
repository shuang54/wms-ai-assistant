你现在开始执行：

# Phase 3.11 Step 19 — Observability Composition Root / Lifecycle Contract

项目：

`D:\coding\ai\wms-ai-assistant`

---

# 一、阶段目标

Phase 3.11 Step 18 已完成：

```text
AIOrchestrator
    ↓
ToolExecutionContext
    ↓
ToolExecutionService
    ↓
ToolExecutionRecord
    ↓
ToolExecutionObserver
    ↓
InMemoryToolExecutionCollector
    ↓
ToolExecutionMetricsService
```

但目前生产接线仍然是：

```text
AIOrchestrator
    ↓
observer=None
```

因此：

```text
ToolExecutionRecord = 0
```

本阶段只解决一个问题：

> **明确 Observability 组件由谁创建、谁持有、如何注入，以及它的生命周期。**

最终形成：

```text
Application Composition Root
        │
        ├── create Collector
        │
        ├── create Observer
        │
        └── create AIOrchestrator(observer=...)
                         ↓
                    Tool Execution
                         ↓
                    Collector
```

---

# 二、严格范围

本阶段允许：

```text
AIOrchestrator 的 Composition Root 接线
Application Factory / Service Factory 的最小修改
新增轻量 Observability Composition 对象（如果现有架构确实需要）
新增测试
新增 architecture / evaluation 文档
```

本阶段禁止：

```text
❌ PostgreSQL 持久化
❌ Redis
❌ Kafka
❌ Event Bus
❌ HTTP Metrics API
❌ Dashboard
❌ Prometheus
❌ OpenTelemetry
❌ Audit
❌ Background Worker
❌ Celery
❌ Agent
❌ MCP
❌ LangGraph
❌ Memory
❌ Planning
❌ Retry
❌ Multi-Agent
❌ 修改 Router
❌ 修改 ToolRegistry
❌ 修改 Tool Handler
❌ 修改 ToolResult
❌ 修改 ToolExecutionService
❌ 修改 ToolExecutionRecord
❌ 修改 ToolExecutionObserver
❌ 修改 Collector
❌ 修改 Metrics Service
```

尤其：

**不要修改 Step 15～18 已经建立的 Observability 核心组件。**

---

# 三、Step 1：先阅读真实 Composition Root

开始编码前必须先阅读：

```text
backend/app/main.py
backend/app/api/
backend/app/services/ai_orchestrator_service.py
backend/app/services/in_memory_tool_execution_collector.py
backend/app/services/tool_execution_observer.py
backend/app/services/tool_execution_metrics_service.py
```

以及所有创建：

```text
AIOrchestratorService(...)
```

的地方。

使用搜索确认：

```text
AIOrchestratorService(
AIOrchestrator(
create_app(
create_orchestrator
orchestrator factory
```

重点回答：

1. 当前 AIOrchestrator 是在哪里创建的？
2. 是 module singleton、request scoped 还是临时创建？
3. API 是否每次 request 创建 Orchestrator？
4. ProjectContext 在哪里注入？
5. ToolExecutionService 在哪里创建？
6. 有没有现成的 application factory？
7. 有没有现成的 service composition / dependency assembly 层？

**不要猜。**

---

# 四、核心生命周期设计

本阶段推荐：

```text
Application Process
        ↓
One InMemoryToolExecutionCollector
        ↓
One Observer
        ↓
AIOrchestrator instances
```

但：

**只有在当前项目 Composition Root 结构适合时才采用。**

不要强行创建 global singleton。

---

# 五、Collector 生命周期

如果采用进程级 Application Composition：

```text
Application startup
        ↓
create Collector
        ↓
Application lifetime
        ↓
Application shutdown
```

Collector：

```text
InMemoryToolExecutionCollector
```

只负责：

```text
append Record
read Record
clear
```

不要给 Collector 增加：

```text
start()
stop()
flush()
persist()
close()
```

本阶段不需要生命周期方法。

---

# 六、禁止 AIOrchestrator 自己创建 Collector

必须继续保证：

```python
AIOrchestratorService(...)
```

内部：

**不能出现：**

```python
InMemoryToolExecutionCollector()
```

也不能：

```python
Collector()
```

不能：

```python
get_default_collector()
```

Collector 的创建权只能属于：

```text
Composition Root / Application Factory / Test
```

---

# 七、Observer 生命周期

如果当前：

```text
ToolExecutionObserver
```

只是 Protocol：

不要新增复杂 Observer Manager。

直接：

```text
Collector
   ↑
Observer
   ↑
AIOrchestrator
```

如果当前 Collector 本身已经实现：

```text
on_execution(record)
```

且符合 Observer Protocol：

优先直接复用。

不要为了“概念完整”再创建：

```text
CollectorObserver
```

之类的多余 Adapter。

---

# 八、AIOrchestrator 注入

最终应该满足：

```text
Composition Root
        ↓
collector
        ↓
AIOrchestratorService(
    ...,
    tool_execution_observer=collector
)
```

AIOrchestrator：

```text
只依赖 ToolExecutionObserver
```

不要依赖：

```text
InMemoryToolExecutionCollector
```

因此类型层面仍然保持：

```python
ToolExecutionObserver
```

而不是：

```python
InMemoryToolExecutionCollector
```

---

# 九、必须保持测试可替换

测试必须可以：

```python
collector = InMemoryToolExecutionCollector()

orchestrator = AIOrchestratorService(
    ...,
    tool_execution_observer=collector,
)
```

然后：

```python
records = collector.records()
```

测试不应该依赖：

```text
FastAPI global state
```

也不应该依赖：

```text
module singleton
```

---

# 十、请求之间的数据隔离

如果 Collector 是 Application lifetime：

不同 request 的 Record 可以同时存在：

```text
request-A
request-B
request-C
```

例如：

```text
collector.records()
```

得到：

```text
A
B
C
```

这是允许的。

但：

```text
request_id
```

必须继续保证独立：

```text
A.request_id != B.request_id
```

不要因为 Collector 共享而共享 request_id。

---

# 十一、不要在本阶段自动清理

禁止：

```text
每个 request 完成 → collector.clear()
```

否则 Application-level Collector 没有任何观察价值。

也不要：

```text
每 N 秒自动 clear
```

不要：

```text
TTL
```

不要：

```text
max_records
```

本阶段只定义：

> **Collector 生命周期 = Application lifetime**

实际容量治理留到未来阶段。

---

# 十二、API 行为

如果现有 API 使用：

```text
AIOrchestrator
```

则 API response：

**不得增加：**

```text
request_id
tool_execution_records
metrics
duration
```

也不得修改：

```text
AIOrchestrationResult
```

因此：

```text
API response contract = unchanged
```

Observability 是旁路能力。

---

# 十三、生产默认行为的变化

本阶段允许一个重要变化：

以前：

```text
observer=None
```

现在 Application Composition Root 可以：

```text
observer=collector
```

因此真实应用运行时：

```text
TOOL
 ↓
Record
 ↓
Collector
```

开始产生。

但：

```text
RAG
 ↓
0 Tool Record
```

以及：

```text
TEXT_TO_SQL
 ↓
0 Tool Record
```

仍然不变。

---

# 十四、失败隔离

Collector 如果发生异常：

```text
AIOrchestrator
```

不能失败。

已有 Step 15 / Step 18 契约必须继续成立：

```text
Observer failure
    ≠
Tool failure
```

不要在 Composition Root 增加：

```text
try/except
```

来重新定义这个语义。

直接复用现有 ToolExecutionService / Observer failure isolation。

---

# 十五、建议的最小实现

如果当前项目确实没有 Composition Root：

可以新增一个非常轻量的：

```text
backend/app/services/observability_composition.py
```

例如只负责：

```text
create_tool_execution_observer()
```

或者：

```text
ToolExecutionObservability
```

但：

**先确认当前项目是否已有更合适的位置。**

不要为了一个 Collector 创建复杂 DI Framework。

不要引入：

```text
dependency-injector
injector
punq
```

等第三方 DI 框架。

---

# 十六、推荐接口

如果确实需要新增对象，可以保持极简：

```python
@dataclass(frozen=True)
class ToolExecutionObservability:
    collector: InMemoryToolExecutionCollector

    @property
    def observer(self) -> ToolExecutionObserver:
        return self.collector
```

但是：

**只有当它能够明显改善当前 Composition Root 时才创建。**

如果：

```text
collector
```

直接作为：

```text
tool_execution_observer
```

已经足够：

**不要创建这个 DTO。**

优先最小实现。

---

# 十七、不要让 Metrics Service 进入 Composition Root

Composition Root：

可以创建：

```text
Collector
Observer
Orchestrator
```

但不要创建：

```text
ToolExecutionMetricsSnapshot
```

Metrics 是：

```text
Read Model
```

调用时计算：

```text
collector.records()
    ↓
ToolExecutionMetricsService.snapshot(...)
```

不要缓存：

```text
metrics_snapshot
```

不要维护：

```text
global metrics state
```

---

# 十八、测试

新增：

```text
tests/test_tool_observability_composition.py
```

至少测试：

### Composition

1. Composition Root 可以创建 Collector
2. Collector 实现 Observer Protocol
3. Orchestrator 接收到同一个 observer
4. 不创建第二个 Collector
5. 不创建第二个 request_id 系统

### Lifecycle

6. 同一个 Composition 生命周期内多个 Orchestrator 可以共享 Collector
7. 两个 execute request_id 不同
8. 两个 request 的 Record 都存在
9. clear 只由调用方显式执行

### Route isolation

10. TOOL → Record
11. RAG → 0 Record
12. TEXT_TO_SQL → 0 Record

### API

13. API response unchanged
14. request_id 不出现在 response
15. Record 不出现在 response
16. Metrics 不出现在 response

### Security

17. Composition Root 不持有 API Key
18. Collector 不持有 DB Connection
19. Collector 不持有 Tool Handler
20. Collector 不持有 LLM Client

### Failure

21. Observer / Collector failure 不改变 ToolResult
22. 不 retry
23. 不 fallback

---

# 十九、Architecture Contract C21

新增：

```text
C21 — Observability Composition / Lifecycle
```

至少：

```text
C21.1
Collector 由 Composition Root / 调用方创建。

C21.2
AIOrchestrator 不创建 Collector。

C21.3
AIOrchestrator 只依赖 ToolExecutionObserver。

C21.4
同一 Application lifetime 可以共享 Collector。

C21.5
不同 execute() 保持独立 request_id。

C21.6
Collector 不自动 clear。

C21.7
Collector 不持久化。

C21.8
Metrics Service 不进入 Collector lifecycle。

C21.9
API response contract 不变化。

C21.10
RAG / TEXT_TO_SQL 不产生 Tool Record。

C21.11
Tool execution failure semantics 不变化。

C21.12
Observer failure isolation 不变化。

C21.13
不引入第三方 DI framework。

C21.14
不引入 global singleton helper。

C21.15
Composition Root 不持有 Tool Handler / DB Session / LLM Client。
```

---

# 二十、必须检查是否存在隐式 Singleton

搜索：

```text
get_default_
_default_collector
_global_collector
collector =
ToolExecutionObservability(
```

以及：

```text
InMemoryToolExecutionCollector()
```

确认没有：

```text
module-level collector singleton
```

如果现有项目已经存在某个 application singleton：

先分析它。

不要为了本阶段强行删除。

---

# 二十一、数据库

本阶段：

```text
NO DB
NO MIGRATION
NO SQL
NO DB WRITE
```

即使 PostgreSQL 恢复：

也不需要 DB。

---

# 二十二、测试命令

先：

```powershell
python -m pytest -q tests/test_tool_observability_composition.py
```

然后：

```powershell
python -m pytest -q tests/test_ai_orchestrator_tool_observability.py
```

然后：

```powershell
python -m pytest -q
```

DB 不需要作为本阶段必测项。

如果为了完整回归执行：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

如果：

```text
localhost:5432
ConnectionRefusedError
ConnectionTimeout
```

不要修改代码。

---

# 二十三、compile / LSP

执行：

```powershell
python -m compileall -q backend tests
```

检查：

```text
LSP diagnostics
```

不要安装新的 lint 工具。

---

# 二十四、最终报告格式

完成后严格：

```text
【Phase 3.11 Step 19 COMPLETE】

1. 新增文件
2. 修改文件
3. Composition Root
4. Collector Lifecycle
5. Observer Lifecycle
6. AIOrchestrator Injection
7. Request Isolation
8. Route Isolation
9. API Contract
10. Security
11. Failure Isolation
12. C21
13. 测试结果
14. compile / LSP
15. DB writes
16. 是否引入 DI Framework
17. 是否创建 Global Singleton
18. 当前限制
```

最后输出：

```text
Architecture:

Application Composition Root
        ↓
InMemoryToolExecutionCollector
        ↓
ToolExecutionObserver
        ↓
AIOrchestrator
        ↓
AI Router
        ↓
TOOL
        ↓
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
ToolExecutionRecord
        ↓
Collector
        ↓
ToolExecutionMetricsService
```

并明确：

```text
Collector = Application lifetime
request_id = Request lifetime
ToolExecutionRecord = Request execution event
Metrics = Read-only derived view
```

---

# 二十五、完成后立即停止

不要进入 Step 20。

不要做：

```text
数据库持久化
HTTP Metrics API
Dashboard
Prometheus
OpenTelemetry
Audit
Event Bus
Redis
Kafka
Agent
MCP
LangGraph
Memory
Planning
```

**Phase 3.11 Step 19 完成后立即停止，等待下一步指令。**
