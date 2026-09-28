你现在开始执行：

# Phase 3.11 Step 23 — Tool Observability Architecture Hardening & Contract Audit

## 一、阶段目标

Phase 3.11 Step 1～22 已经建立：

```text
Tool
  ↓
ToolExecutionService
  ↓
ToolExecutionRecord
  ↓
Observer
  ↓
InMemoryCollector
  ↓
Finite Retention
  ↓
Query Service
  ↓
Snapshot Read Model
  ↓
Metrics
```

本阶段不新增业务能力。

唯一目标：

> 对当前 Tool Execution / Observability 架构进行一次完整的 Contract Audit，确认前 22 个 Step 形成的边界没有发生职责泄漏、循环依赖、隐式写能力或 API 漂移。

这是一次：

```text
Architecture Hardening
+
Contract Audit
+
Regression
```

不是功能开发。

---

# 二、严格规则

本阶段默认：

```text
生产代码修改 = 0
```

只有发现明确的真实架构缺陷，并且可以通过最小修改修复时，才允许修改。

如果只是：

```text
命名不够漂亮
代码可以重构
可以更抽象
未来可能需要
```

**不要修改。**

---

# 三、先阅读

完整阅读当前相关模块：

```text
backend/app/services/tool_execution_context.py
backend/app/services/tool_execution_record.py
backend/app/services/tool_execution_observer.py
backend/app/services/tool_execution_service.py
backend/app/services/in_memory_tool_execution_collector.py
backend/app/services/tool_execution_metrics_service.py
backend/app/services/tool_observability_query_service.py
backend/app/services/tool_observability_snapshot.py
backend/app/services/tool_argument_extractor.py
backend/app/services/project_orchestrator_factory.py
backend/app/services/ai_orchestrator_service.py
backend/app/api/orchestrator_chat.py
backend/app/api/tool_chat.py
```

阅读：

```text
tests/test_tool_architecture_contract.py
tests/test_tool_chat_architecture_contract.py
tests/test_tool_execution_context.py
tests/test_tool_execution_record.py
tests/test_tool_execution_observer.py
tests/test_in_memory_tool_execution_collector.py
tests/test_tool_execution_metrics_service.py
tests/test_tool_observability_query_service.py
tests/test_tool_observability_snapshot.py
tests/test_ai_orchestrator_tool_observability.py
tests/test_tool_observability_composition.py
```

同时检查：

```text
docs/architecture.md
docs/evaluation/
```

---

# 四、建立完整依赖图

首先不要改代码。

输出一个静态依赖关系：

```text
AIOrchestrator
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
ToolExecutionObserver
    ↓
InMemoryToolExecutionCollector
    ↓
ToolObservabilityQueryService
    ├── ToolExecutionSnapshot
    └── ToolExecutionMetricsService
```

同时检查是否出现：

```text
Collector → Orchestrator
Collector → Tool
Metrics → Collector mutation
Snapshot → Collector
Snapshot → Record storage
Record → API
Tool → Orchestrator
Tool → Query Service
```

这些都应该：

```text
NO
```

---

# 五、检查循环依赖

检查 Python import graph。

重点：

```text
ToolExecutionRecord
ToolExecutionObserver
ToolExecutionContext
ToolExecutionService
Collector
Metrics
Query
Snapshot
AIOrchestrator
```

要求：

```text
没有循环 import
```

特别关注当前历史上出现过的：

```text
delayed import
```

不要因为看到 delayed import 就自动修改。

只有确认存在：

```text
实际循环依赖
```

才处理。

---

# 六、Execution 与 Observability 必须保持单向

验证：

```text
Execution
    ↓
Observability
```

允许：

```text
ToolExecutionService
    ↓
Record
    ↓
Observer
```

允许：

```text
Query
    ↓
Collector
    ↓
Metrics
```

禁止：

```text
Metrics
    ↓
Execution
```

禁止：

```text
Query
    ↓
ToolExecutionService
```

禁止：

```text
Observability
    ↓
Tool
```

Observability：

**只能观察，不能改变执行。**

---

# 七、Record Contract Audit

检查：

```text
ToolExecutionRecord
```

确认：

1. frozen
2. 字段 whitelist 固定
3. 没有 Tool arguments
4. 没有 SQL
5. 没有 Prompt
6. 没有 LLM response
7. 没有 DB connection
8. 没有 DB session
9. 没有 secret
10. 没有 traceback
11. 没有可变 collection
12. 没有 persistence

如果全部满足：

```text
PASS
```

不要修改 Record。

---

# 八、Collector Contract Audit

检查 Step 20 的：

```text
max_records
FIFO
thread safety
clear
```

确认：

```text
Retention ≠ Persistence
```

要求：

```text
finite
FIFO
thread-safe
explicit clear
no TTL
no background thread
no persistence
```

检查：

```text
max_records = 1
```

边界。

以及：

```text
max_records = very large
```

行为。

不要做 benchmark。

---

# 九、Observer Contract Audit

确认：

```text
Observer
```

只接收：

```text
ToolExecutionRecord
```

不能接收：

```text
arguments
SQL
prompt
response
ToolResult.data
```

Observer 失败：

```text
不能影响 Tool Execution
```

确认：

```text
observer.on_execution(...)
```

不能触发：

```text
retry
fallback
re-execution
```

---

# 十、Metrics Contract Audit

确认：

```text
ToolExecutionMetricsService
```

仍然是：

```text
pure read-only calculation
```

不能：

```text
modify Record
modify Collector
write DB
call Tool
call LLM
```

确认：

```text
empty
single
multiple
retained window
```

全部稳定。

尤其：

```text
N/A / None
```

语义不能被误认为：

```text
0
```

---

# 十一、Query Boundary Audit

确认：

```text
ToolObservabilityQueryService
```

仍然：

```text
READ ONLY
```

不能：

```text
clear
append
on_execution
evict
```

并且：

```text
Query Service
```

没有：

```text
list
deque
dict cache
```

作为自己的 Record Storage。

---

# 十二、Snapshot Boundary Audit

确认：

```text
ToolExecutionSnapshot
```

是：

```text
独立 DTO
frozen
explicit mapping
```

禁止：

```text
vars()
asdict()
__dict__
```

自动传播字段。

确认：

```text
Record
    ≠
Snapshot
```

两者必须是两个独立类型。

---

# 十三、API Boundary Audit

检查：

```text
backend/app/api/
```

确认当前没有因为 Step 1～22 而产生：

```text
/tool-observability
/usage
/metrics
```

等新的 Tool Observability HTTP API。

本阶段：

**不要新增 API。**

---

# 十四、Composition Root Audit

检查：

```text
backend/app/api/orchestrator_chat.py
```

确认：

```text
Application Composition Root
```

仍然负责：

```text
Collector
Observer
Orchestrator
```

的装配。

同时确认：

```text
AIOrchestrator
```

没有：

```text
创建 Collector
```

也没有：

```text
创建 Observer
```

更不能：

```text
创建 Query Service
```

---

# 十五、Project Boundary Audit

检查：

```text
project_id
```

在 Tool Execution Context 中的语义：

```text
Authorization Scope
```

不是：

```text
LLM argument
Tool argument
Database credential
```

确认：

```text
project-a
project-b
```

不会因为 Observability Query 而绕过 Tool capability。

Query Service：

**不得重新做权限判断，也不得扩大权限。**

---

# 十六、AIOrchestrator Boundary Audit

确认 AIOrchestrator：

```text
只负责 orchestration
```

不得包含：

```text
Collector logic
Retention logic
Metrics arithmetic
Snapshot conversion
Query logic
```

确认：

```text
RAG
Tool
Text-to-SQL
```

仍然保持路线隔离。

特别验证：

```text
RAG → 0 Tool Record
TEXT_TO_SQL → 0 Tool Record
TOOL → 1 Tool Record
```

---

# 十七、ToolChatService Boundary Audit

检查：

```text
ToolChatService
```

确认：

```text
ToolChatService
    ↓
ToolExecutionService
    ↓
ToolRegistry
```

而不是：

```text
ToolChatService
    ↓
ToolRegistry.execute
```

确认：

```text
Capability
ProjectContext
ExecutionContext
Observer
```

仍然位于正确边界。

不要改变 ToolChatService 的 multi-step 行为。

---

# 十八、Security Import Audit

对以下模块执行 import 检查：

```text
ToolExecutionRecord
ToolExecutionObserver
ToolExecutionContext
ToolExecutionService
InMemoryToolExecutionCollector
ToolExecutionMetricsService
ToolObservabilityQueryService
ToolExecutionSnapshot
```

Observability Read Model 层不得依赖：

```text
sqlalchemy
psycopg
redis
kafka
celery
requests
httpx
subprocess
```

同时：

```text
Collector
```

不得依赖：

```text
LLM
ToolRegistry
Tool Handler
API
Database
```

---

# 十九、Contract Matrix

新增一个测试：

```text
tests/test_tool_observability_architecture_audit.py
```

建议建立一张 Contract Matrix：

```text
Layer | Can Write | Can Read | Can Execute Tool | Can Access DB | Can Access LLM
```

目标：

```text
ToolExecutionService
    Write: execution only
    Read: ToolResult
    Tool: YES
    DB: indirectly through Tool
    LLM: NO

Record
    Write: NO
    Read: data
    Tool: NO
    DB: NO
    LLM: NO

Observer
    Write: NO
    Read: Record
    Tool: NO
    DB: NO
    LLM: NO

Collector
    Write: Record collection only
    Read: Record
    Tool: NO
    DB: NO
    LLM: NO

Metrics
    Write: NO
    Read: Record
    Tool: NO
    DB: NO
    LLM: NO

Query
    Write: NO
    Read: Collector
    Tool: NO
    DB: NO
    LLM: NO

Snapshot
    Write: NO
    Read: Record
    Tool: NO
    DB: NO
    LLM: NO
```

注意：

这里的：

```text
Write
```

指的是**职责层面的写能力**，不是 Python 对象内部变量赋值。

不要因为 Collector 有 `append` 就错误地把它归类成数据库写层。

---

# 二十、Contract Audit Tests

至少覆盖：

### Dependency

```text
no circular import
```

### Execution direction

```text
Execution → Observability
Observability ↛ Execution
```

### Collector

```text
finite retention
FIFO
thread safety
no persistence
```

### Record

```text
immutable
no secrets
```

### Observer

```text
failure isolated
```

### Metrics

```text
read-only
```

### Query

```text
read-only
```

### Snapshot

```text
immutable
independent
```

### API

```text
no API changes
```

### Composition

```text
Collector only created by composition root
```

### Project

```text
project boundary cannot be bypassed
```

---

# 二十一、不要增加大量新测试

本阶段主要是：

```text
Contract Audit
```

建议：

```text
15～30 个新增测试
```

如果已有测试能够证明某个契约：

**直接复用现有测试，不要重复创建。**

---

# 二十二、历史 Contract 清理

检查：

```text
docs/architecture.md
docs/evaluation/
```

搜索历史残留：

```text
_resolve_tool_name
_tokenize
direct registry.execute
no max_records
Collector unbounded
ToolChatService → Registry
```

但注意：

**不要修改历史 Evaluation 报告中的历史事实。**

只允许：

```text
新增补充说明
```

例如：

```text
Historical note:
This behavior existed before Step X and was changed in Step Y.
```

不能改写过去的测试结果。

---

# 二十三、生产代码修改规则

如果 Audit 发现：

```text
Architecture bug
Security boundary violation
Actual circular dependency
```

才允许最小修复。

修复原则：

```text
最小修改
不改变 API
不改变 ToolResult
不改变 Router
不改变 Tool Execution semantics
不改变 Retention semantics
不改变 Metrics semantics
```

如果没有真实 bug：

```text
生产代码 = 0 修改
```

---

# 二十四、完整回归

先：

```powershell
python -m pytest -q tests/test_tool_observability_architecture_audit.py
```

然后：

```powershell
python -m pytest -q tests/test_tool_architecture_contract.py tests/test_tool_chat_architecture_contract.py
```

然后：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

如果 knowledge ingestion 的既有数据状态问题再次出现：

**不要修改它。**

单独记录：

```text
Pre-existing DB fixture/data-state coupling
```

不要为了 Step 23 绕过。

---

# 二十五、静态检查

执行：

```powershell
python -m compileall -q backend tests
```

LSP：

```text
0 error
0 warning
```

lint：

如果没有：

```text
ruff
flake8
```

保持：

```text
unavailable
```

不要安装新工具。

---

# 二十六、DB / Network

本阶段原则：

```text
新增生产 DB writes = 0
新增 Network = 0
新增 LLM calls = 0
```

Contract Audit 默认不应该访问：

```text
PostgreSQL
DeepSeek
SiliconFlow
Redis
Kafka
```

---

# 二十七、最终报告格式

完成后只汇报：

```text
【Phase 3.11 Step 23 COMPLETE】

1. Audit 范围
2. Dependency Graph
3. Circular Dependency
4. Execution / Observability Boundary
5. Record Contract
6. Collector Contract
7. Observer Contract
8. Metrics Contract
9. Query Boundary
10. Snapshot Boundary
11. API Boundary
12. Composition Root
13. Project Boundary
14. Security Import Audit
15. C25 Contract Tests
16. 测试结果
17. compile / LSP / lint
18. DB writes
19. Network / LLM
20. 生产代码修改
21. 发现的问题
22. 当前限制
```

最后输出：

```text
Architecture Audit:

Execution
    ↓
ToolExecutionRecord
    ↓
Observer
    ↓
Collector
    ↓
Retention
    ↓
Query Service
    ├── Snapshot Read Model
    └── Metrics Read Model

Forbidden Direction:

Observability
      X
      ↓
Execution
```

如果生产代码没有真实 bug：

```text
Production changes = 0
```

然后：

**立即停止。**

不要进入 Step 24。

不要开发 HTTP API。

不要开发 Database Persistence。

不要开发 Dashboard。

不要开发 Prometheus。

不要开发 OpenTelemetry。

不要开发 Audit Persistence。

不要开发 Event Bus。

不要开发 Redis / Kafka。

不要开发 Agent / MCP / LangGraph / Memory / Planning。

等待下一步指令。
