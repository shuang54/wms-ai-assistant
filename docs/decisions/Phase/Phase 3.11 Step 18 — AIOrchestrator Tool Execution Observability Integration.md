你现在开始执行：

# Phase 3.11 Step 18 — AIOrchestrator Tool Execution Observability Integration

项目：

`D:\coding\ai\wms-ai-assistant`

---

# 一、阶段目标

Step 17 已经完成：

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
        ↓
ToolExecutionMetricsService
```

但目前：

```text
AIOrchestrator
```

仍然：

```text
context=None
```

因此 AIOrchestrator 主链路上的 Tool 执行不会产生：

```text
ToolExecutionRecord
```

本阶段唯一目标：

> **让 AIOrchestrator 的 TOOL 路径接入现有 ToolExecutionContext + Observer/Collector 链路。**

最终形成：

```text
Question
   ↓
AIOrchestrator
   ↓
Router
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
   ↓
ToolExecutionRecord
   ↓
Observer
   ↓
Collector
   ↓
Metrics
```

---

# 二、严格禁止

本阶段禁止：

```text
❌ 修改 Router 核心行为
❌ 修改 ToolRegistry
❌ 修改 Tool Handler
❌ 修改 ToolResult
❌ 修改 ToolExecutionService 核心执行逻辑
❌ 修改 ToolExecutionRecord
❌ 修改 ToolExecutionObserver
❌ 修改 InMemoryToolExecutionCollector
❌ 修改 ToolExecutionMetricsService
❌ 修改 ToolChatService
❌ 修改 API
❌ 修改数据库
❌ 新增数据库表
❌ 新增 HTTP Metrics API
❌ Dashboard
❌ Prometheus
❌ OpenTelemetry
❌ Audit
❌ Event Bus
❌ Agent
❌ MCP
❌ LangGraph
❌ Memory
❌ Planning
❌ Retry
❌ Multi-step Tool
```

尤其注意：

**不要为了接入 Metrics 而修改 ToolExecutionService。**

Step 15～17 已经建立好了执行与观测边界，本阶段只负责正确装配。

---

# 三、开始前先阅读

先阅读真实代码：

```text
backend/app/services/ai_orchestrator_service.py

backend/app/services/tool_execution_service.py
backend/app/services/tool_execution_context.py
backend/app/services/tool_execution_record.py
backend/app/services/tool_execution_observer.py
backend/app/services/in_memory_tool_execution_collector.py
backend/app/services/tool_execution_metrics_service.py

tests/test_ai_orchestrator_service.py
tests/test_tool_execution_context.py
tests/test_tool_execution_observer.py
tests/test_in_memory_tool_execution_collector.py
tests/test_tool_execution_metrics_service.py

docs/architecture.md
```

重点确认：

1. AIOrchestrator 当前如何创建 ToolExecutionService。
2. `_run_tool()` 当前如何调用 ToolExecutionService。
3. `ToolExecutionContext` 当前 API。
4. `ToolExecutionObserver` 当前 API。
5. `InMemoryToolExecutionCollector` 当前 API。
6. `ToolExecutionRecord` 当前字段。
7. Step 17 Metrics 当前 API。
8. 现有 AIOrchestrator 测试如何构造 fake Tool / fake Registry。
9. 是否已经存在可复用的 request_id / context 测试工具。

**不要重新创建任何已经存在的基础设施。**

---

# 四、设计目标

AIOrchestrator 需要增加：

## 1. 可选 Context / Observer 装配

原则：

```text
默认行为保持兼容
```

不能因为没有 Collector：

```text
AIOrchestrator
```

就改变 Tool 执行结果。

---

## 2. 一次 execute() 一个 request_id

例如：

```python
result = orchestrator.execute(
    "查询物料 MAT-001 当前库存"
)
```

整个 execute：

```text
request_id = UUID-A
```

ToolExecutionContext：

```text
request_id = UUID-A
round = 1
project_id = current project_id
tool_call_id = None
```

因为 AIOrchestrator 当前 Tool 路径不是 ToolChatService 的 OpenAI Function Calling round。

因此：

```text
tool_call_id = None
```

是正确语义。

---

# 五、Context 生命周期

严格要求：

```text
AIOrchestrator.execute()
        ↓
create request_id
        ↓
Router
        ↓
TOOL
        ↓
ToolExecutionContext(round=1)
        ↓
ToolExecutionService.execute(...)
```

当前 AIOrchestrator 是：

```text
one question
→ one route
→ one Tool
```

所以：

```text
round = 1
```

不要实现：

```text
round loop
```

不要引入：

```text
retry
```

不要引入：

```text
multi-tool
```

---

# 六、ProjectContext

如果 AIOrchestrator 已经具有：

```text
project_id
```

则：

```text
ToolExecutionContext.project_id
```

必须使用当前 server-side project_id。

例如：

```text
project_id = "project-a"
```

绝不能从：

```text
question
```

或：

```text
Tool arguments
```

推导 project_id。

如果当前 AIOrchestrator 没有 project_id：

**先阅读现有 ProjectContext 设计，不要自行增加新的 ProjectContext 系统。**

---

# 七、Observer 装配

AIOrchestrator 不应该知道：

```text
database
redis
prometheus
```

只允许知道：

```text
ToolExecutionObserver
```

推荐结构：

```text
AIOrchestrator(
    ...
    execution_service=None,
    execution_observer=None,
)
```

如果现有架构有更自然的注入方式：

优先遵循现有项目风格。

---

# 八、Collector 装配

不要让 AIOrchestrator 内部强制创建：

```python
InMemoryToolExecutionCollector()
```

禁止：

```text
AIOrchestrator.__init__()
    ↓
new Collector()
```

否则会形成隐式 singleton / 生命周期不清晰。

应该：

```text
Application / Test
       ↓
Collector
       ↓
AIOrchestrator(observer=collector)
```

即：

**Collector 属于调用方 / composition root。**

AIOrchestrator 只依赖：

```text
ToolExecutionObserver
```

---

# 九、默认兼容行为

如果：

```text
observer=None
```

则：

```text
ToolExecutionService
```

不产生 Record。

这必须保持。

如果：

```text
execution_service
```

没有注入：

使用现有默认 ToolExecutionService 创建逻辑。

不要破坏已有调用方。

---

# 十、Tool 路径行为

例如：

```text
查询物料 MAT-001 当前库存
```

流程：

```text
AIOrchestrator.execute()
        ↓
Router
        ↓
RouteType.TOOL
        ↓
RouteDecision.tool_name
        ↓
create ToolExecutionContext
        ↓
ToolExecutionService.execute(
    "get_inventory",
    arguments={...},
    context=context
)
        ↓
ToolRegistry
        ↓
get_inventory
        ↓
ToolResult
```

如果 Collector 存在：

必须得到：

```text
1 ToolExecutionRecord
```

Record：

```text
request_id = execute() request id
round = 1
tool_name = "get_inventory"
project_id = current project_id / None
tool_call_id = None
success = True
```

---

# 十一、RAG 路径必须 0 Tool Event

例如：

```text
采购入库怎么操作？
```

如果 Router：

```text
RAG
```

则：

```text
ToolExecutionRecord = 0
```

不能因为 Orchestrator 创建了 Context：

就产生空 Tool Record。

---

# 十二、Text-to-SQL 路径必须 0 Tool Event

例如：

```text
统计当前知识库文档数量
```

如果 Router：

```text
TEXT_TO_SQL
```

则：

```text
ToolExecutionRecord = 0
```

不要把 SQL Executor 当 Tool。

本阶段只观察：

```text
TOOL route
```

---

# 十三、Capability Denied

例如：

```text
project-b
```

没有：

```text
get_inventory
```

调用：

```text
查询 MAT-001 当前库存
```

应保持：

```text
AIOrchestratorCapabilityError
```

并且：

```text
HTTP / Service 上层错误语义不变
```

同时：

```text
ToolExecutionRecord = 0
```

因为 Capability Denied 在真正 Tool Execution 之前发生。

不要将：

```text
Capability Denied
```

转换为：

```text
ToolResult(False)
```

也不要生成成功/失败 Record。

---

# 十四、Tool Failure

如果：

```text
ToolExecutionService
```

正常进入 Tool Execution，但 Tool 返回：

```text
ToolResult(success=False)
```

则：

```text
ToolExecutionRecord.success = False
```

Collector：

```text
1 record
```

Metrics：

```text
failure_count = 1
```

不要 retry。

不要 fallback。

不要修改 ToolResult。

---

# 十五、Observer Failure

如果：

```text
Collector.on_execution()
```

自身抛异常：

AIOrchestrator：

```text
仍然返回原 ToolResult / AIOrchestrationResult
```

不能：

```text
Tool execution failed
```

不能：

```text
retry
```

不能：

```text
fallback
```

现有 Step 15 的 failure isolation 必须保持。

因此本阶段重点测试：

```text
Observer failure ≠ Tool failure
```

---

# 十六、Metrics 验证

至少做：

```text
Collector
    ↓
records()
    ↓
ToolExecutionMetricsService.snapshot()
```

验证：

### 一次成功 Tool

```text
total_count = 1
success_count = 1
failure_count = 0
```

### 一次失败 Tool

```text
total_count = 1
success_count = 0
failure_count = 1
```

### RAG

```text
total_count = 0
```

### Text-to-SQL

```text
total_count = 0
```

不要新增 Metrics API。

---

# 十七、请求 ID 语义

测试：

```text
execute()
execute()
```

两个独立调用：

```text
request_id_A != request_id_B
```

同一次 execute 中：

```text
record.request_id == current_execute_request_id
```

当前 AIOrchestrator 一次只允许一个 Tool：

```text
record count <= 1
```

---

# 十八、Tool Call ID

AIOrchestrator 当前不是 OpenAI Function Calling ToolChatService。

因此：

```text
tool_call_id = None
```

不要伪造：

```text
call_xxx
```

不要生成随机 tool_call_id。

ToolChatService 的真实：

```text
tool_call_id
```

仍然由 Function Calling 链路提供。

---

# 十九、测试文件

新增：

```text
tests/test_ai_orchestrator_tool_observability.py
```

建议覆盖：

### Context

1. Tool path creates context
2. request_id exists
3. round == 1
4. tool_call_id is None
5. project_id propagated correctly

### Collector

6. successful Tool produces one record
7. failed Tool produces one record
8. RAG produces zero records
9. Text-to-SQL produces zero records
10. capability denied produces zero records

### Request isolation

11. two execute calls have different request_id
12. one execute produces max one Tool record

### Metrics

13. successful Tool metrics
14. failed Tool metrics
15. empty metrics for RAG
16. empty metrics for Text-to-SQL

### Failure isolation

17. observer failure does not alter ToolResult
18. observer failure does not alter AIOrchestrationResult
19. observer failure does not trigger retry
20. observer failure does not trigger fallback

### Security

21. Collector only receives ToolExecutionRecord
22. no question/arguments in Record
23. no ToolResult.data in Record
24. no SQL
25. no password/API key/database URL

---

# 二十、Architecture Contract C20

新增：

```text
C20 — AIOrchestrator Tool Observability Integration
```

至少：

```text
C20.1
AIOrchestrator TOOL path passes ToolExecutionContext.

C20.2
One execute() creates one request_id.

C20.3
TOOL context round == 1.

C20.4
AIOrchestrator tool_call_id == None.

C20.5
project_id comes only from server-side ProjectContext.

C20.6
RAG creates zero ToolExecutionRecord.

C20.7
TEXT_TO_SQL creates zero ToolExecutionRecord.

C20.8
Capability denied creates zero ToolExecutionRecord.

C20.9
Successful Tool creates exactly one Record.

C20.10
ToolResult failure creates exactly one failure Record.

C20.11
Observer failure cannot change Tool execution result.

C20.12
No retry/fallback introduced.

C20.13
AIOrchestrator does not create Collector internally.

C20.14
No API / DB / persistence introduced.

C20.15
Metrics remain read-only and downstream.
```

---

# 二十一、生产代码修改边界

允许修改：

```text
backend/app/services/ai_orchestrator_service.py
```

以及必要的：

```text
tests/
docs/architecture.md
docs/evaluation/
```

禁止修改：

```text
ToolExecutionService
ToolExecutionRecord
ToolExecutionObserver
InMemoryToolExecutionCollector
ToolExecutionMetricsService
ToolRegistry
Tool
ToolResult
Router
API
DB
```

如果必须修改上述禁止文件：

**立即停止并报告原因，不要自行修改。**

---

# 二十二、不要接 API

本阶段：

```text
POST /api/...
```

全部保持不变。

不要把：

```text
Collector
Metrics
```

放进 FastAPI global singleton。

不要新增：

```text
GET /api/tools/metrics
```

---

# 二十三、不要做持久化

禁止：

```text
PostgreSQL
Redis
Kafka
Celery
文件
JSON
```

所有数据仍然：

```text
InMemory
```

---

# 二十四、测试命令

首先：

```powershell
python -m pytest -q tests/test_ai_orchestrator_tool_observability.py
```

然后：

```powershell
python -m pytest -q tests/test_ai_orchestrator_service.py
```

然后：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

如果出现：

```text
psycopg.errors.ConnectionTimeout
ConnectionRefusedError
localhost:5432
```

不要修改代码绕过。

记录：

```text
DB environment unavailable
```

---

# 二十五、编译

执行：

```powershell
python -m compileall -q backend tests
```

检查：

```text
LSP diagnostics
```

不要为了 lint 新安装工具。

---

# 二十六、最终报告

完成后严格按照：

```text
【Phase 3.11 Step 18 COMPLETE】

1. 新增文件
2. 修改文件
3. AIOrchestrator Tool Context
4. request_id
5. round
6. tool_call_id
7. project_id
8. Collector
9. RAG / Text-to-SQL isolation
10. Capability Denied
11. Tool Failure
12. Observer Failure
13. Metrics
14. C20
15. 测试结果
16. compile / LSP
17. DB writes
18. API 是否变化
19. ToolResult 是否变化
20. 当前限制
```

最后输出：

```text
Architecture:

Question
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
Observer
   ↓
InMemoryCollector
   ↓
Metrics Read Model
```

并明确：

```text
RAG → 0 Tool Record
Text-to-SQL → 0 Tool Record
Capability Denied → 0 Tool Record
Tool Success → 1 Record
Tool Failure → 1 Record
```

---

# 二十七、完成后立即停止

不要进入 Step 19。

不要做：

```text
数据库持久化
HTTP Metrics API
Dashboard
Prometheus
OpenTelemetry
Audit
Event Bus
Agent
MCP
LangGraph
Memory
Planning
```

**Phase 3.11 Step 18 完成后立即停止，等待下一步指令。**
