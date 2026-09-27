你现在开始执行：

# Phase 3.11 Step 16 — In-Memory Tool Execution Collector

项目：

```text
D:\coding\ai\wms-ai-assistant
```

## 一、阶段目标

在 Step 14 / Step 15 已完成：

```text
ToolExecutionContext
        ↓
ToolExecutionService
        ↓
ToolExecutionRecord
        ↓
ToolExecutionObserver
```

本阶段增加一个：

**生产可用但纯内存的 Tool Execution Collector。**

目标：

```text
ToolExecutionService
        ↓
ToolExecutionRecord
        ↓
ToolExecutionObserver
        ↓
InMemoryToolExecutionCollector
        ↓
read-only query
```

用于验证：

1. Record 可以被安全收集。
2. Collector 不影响 Tool 执行。
3. Collector 可以按 request / project / tool 查询。
4. Collector 不持久化。
5. Collector 不参与 Tool execution。
6. Collector 不接触 arguments / result.data / SQL / secrets。

---

# 二、严格范围

## 允许

新增：

```text
backend/app/services/in_memory_tool_execution_collector.py
tests/test_in_memory_tool_execution_collector.py
```

以及：

```text
C18 Architecture Contract
docs/evaluation/Phase 3.11 Step 16 — In-Memory Tool Execution Collector.md
```

必要时对：

```text
ToolExecutionObserver
ToolExecutionService
```

做最小兼容修改。

## 禁止

绝对不要：

```text
数据库
Repository
Migration
Audit Table
Redis
Kafka
消息队列
文件持久化
Metrics Backend
Dashboard
Tracing Backend
OpenTelemetry
```

不要修改：

```text
ToolResult
ToolRegistry
Tool Handler
Router
AIOrchestrator 业务语义
Tool Argument Extractor
API Response
LLM Message
```

不要增加：

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
backend/app/services/tool_execution_service.py
backend/app/services/tool_execution_context.py
```

以及：

```text
tests/test_tool_execution_record.py
tests/test_tool_execution_observer.py
tests/test_tool_execution_service.py
tests/test_tool_chat_execution_boundary.py
```

重点确认：

1. Record 当前全部字段。
2. Observer 当前 Protocol。
3. ToolExecutionService 当前 observer 生命周期。
4. 当前 project_id / request_id / tool_call_id / round 语义。
5. 当前线程 / async 模型。
6. 项目是否已有类似 in-memory collector / repository-like read-only object。

不要假设。

---

# 四、Collector Contract

新增：

```text
backend/app/services/in_memory_tool_execution_collector.py
```

建议：

```python
class InMemoryToolExecutionCollector:
    def on_execution(self, record: ToolExecutionRecord) -> None:
        ...

    def records(self) -> tuple[ToolExecutionRecord, ...]:
        ...

    def clear(self) -> None:
        ...
```

最终 API 根据实际项目风格调整。

核心要求：

```text
on_execution()
    ↓
append Record

records()
    ↓
return immutable snapshot
```

---

# 五、必须防止外部修改内部集合

不要直接：

```python
return self._records
```

因为这样外部代码可以：

```text
append
clear
pop
```

破坏 Collector 内部状态。

必须返回：

```text
tuple
```

或者其他不可变 snapshot。

例如：

```python
tuple(self._records)
```

---

# 六、Record 原样保存

Collector：

**不得复制、修改、重新构造 Record。**

要求：

```text
collector.records()[0] is record
```

或者至少保持完全相同的值。

不要修改：

```text
request_id
project_id
tool_call_id
round
tool_name
started_at
finished_at
duration_ms
success
error_code
error_type
```

---

# 七、Query Contract

本阶段只提供最小查询。

建议：

```python
records()
records_by_request_id(request_id)
records_by_project_id(project_id)
records_by_tool_name(tool_name)
```

如果实际项目不需要其中某个查询：

不要为了数量强行增加。

所有查询必须：

```text
read-only
deterministic
no IO
no DB
no LLM
```

---

# 八、查询返回值

所有查询：

```text
tuple[ToolExecutionRecord, ...]
```

不得返回内部 mutable list。

例如：

```text
records()
→ tuple

records_by_request_id(...)
→ tuple
```

不存在匹配：

```text
→ ()
```

不要返回：

```text
None
```

---

# 九、request_id 查询

例如：

```text
request-A
request-B
request-A
```

查询：

```text
records_by_request_id("request-A")
```

只返回：

```text
request-A
```

并保持原始 execution 顺序。

---

# 十、project_id 查询

支持：

```text
project-a
project-b
None
```

如果：

```text
project_id=None
```

只匹配：

```text
record.project_id is None
```

不要把：

```text
None
```

解释成：

```text
全部 project
```

---

# 十一、tool_name 查询

例如：

```text
get_inventory
get_work_order
```

必须严格：

```text
record.tool_name == tool_name
```

不要：

```text
case-insensitive
partial match
alias match
fuzzy match
```

Collector 不是 Tool Selector。

---

# 十二、顺序保证

Collector 必须保持：

```text
on_execution(record1)
on_execution(record2)
on_execution(record3)
```

那么：

```text
records()
```

必须：

```text
(record1, record2, record3)
```

不要：

```text
sort
deduplicate
aggregate
```

---

# 十三、不要做统计

本阶段不要实现：

```text
count()
success_rate()
average_duration()
p95()
p99()
failure_rate()
```

这些属于：

```text
Metrics / Analytics
```

后续再做。

当前 Collector：

> 只负责保存 Execution Record，并提供最小只读查询。

---

# 十四、线程安全

先阅读当前项目执行模型。

如果项目当前没有明确要求跨线程共享：

**不要为了 Step 16 引入复杂锁体系。**

优先保持：

```text
simple in-memory collector
```

如果实际代码确认 ToolExecutionService 会在多线程环境共享同一个 Collector：

才增加最小：

```text
threading.Lock
```

不要引入：

```text
asyncio.Queue
thread pool
event loop
```

---

# 十五、生命周期

Collector：

```text
constructor
    ↓
empty
```

之后：

```text
on_execution()
```

不断增加。

调用：

```text
clear()
```

之后：

```text
records() == ()
```

不要：

```text
自动 TTL
自动清理
后台线程
定时任务
```

---

# 十六、Security Boundary

Collector 内部只能保存：

```text
ToolExecutionRecord
```

不得保存：

```text
arguments
ToolResult
ToolResult.data
SQL
LLM prompt
LLM response
API key
password
DATABASE_URL
connection string
authorization header
Exception object
traceback
```

Architecture Contract 必须 AST 检查：

```text
C18.1
Collector only stores ToolExecutionRecord
```

---

# 十七、Observer Failure

Collector 自身异常：

原则保持 Step 15：

```text
Observer failure
≠
Tool failure
```

如果：

```text
collector.on_execution(record)
```

由于 Collector 内部异常：

不得：

```text
retry Tool
change ToolResult
raise to caller
```

仍然由 Step 15 的 Observer isolation boundary 负责隔离。

不要在 Collector 内部实现第二套异常隔离机制，除非当前接口需要。

---

# 十八、不要把 Collector 直接做成 Singleton

禁止：

```python
collector = InMemoryToolExecutionCollector()
```

作为全局隐藏状态。

不要：

```text
global collector
module singleton
```

原因：

会导致：

```text
test contamination
request contamination
project contamination
memory leak
```

Collector 必须：

```text
explicit instance
```

由上层显式创建和注入。

---

# 十九、与 ToolExecutionService 集成

如果当前 Step 15 的：

```text
ToolExecutionService(..., observer=...)
```

已经支持 Observer：

那么 Collector 只需要：

```text
collector
    implements ToolExecutionObserver
```

即可。

不要修改：

```text
execute() -> ToolResult
```

不要新增：

```text
execute_and_collect()
```

不要新增第二条 Tool execution path。

---

# 二十、ToolChatService 集成

不要修改 ToolChatService 的核心语义。

如果测试需要：

```text
ToolChatService
    ↓
ToolExecutionService(observer=collector)
```

可以通过现有 constructor injection 完成。

验证：

```text
round 1 → record
round 2 → record
round 3 → record
```

同一个 chat：

```text
request_id 相同
```

不同 chat：

```text
request_id 不同
```

---

# 二十一、AIOrchestrator

继续保持当前 Deferred：

```text
AIOrchestrator
→ context=None
→ no Record
→ no Collector event
```

本阶段不要接入 AIOrchestrator。

---

# 二十二、测试

新增：

```text
tests/test_in_memory_tool_execution_collector.py
```

建议 25～35 个高质量测试。

至少覆盖：

## 初始化

```text
empty collector
```

## 单条记录

```text
on_execution(record)
→ records() == (record,)
```

## 多条记录

验证顺序。

## Snapshot

验证：

```text
records()
```

返回 tuple。

尝试修改：

```text
records().append(...)
```

必须失败。

## Request Query

覆盖：

```text
match
no match
multiple records
order preserved
```

## Project Query

覆盖：

```text
project-a
project-b
None
```

## Tool Query

覆盖：

```text
get_inventory
get_work_order
unknown
```

## Clear

```text
records
→ clear
→ ()
```

## Isolation

两个 Collector：

```text
collector_a
collector_b
```

互不影响。

## Record Identity

确保不复制/修改 Record。

## Security

Record 中不存在：

```text
arguments
SQL
secrets
result.data
```

## Determinism

同样插入顺序：

```text
same records
same query result
```

---

# 二十三、Architecture Contract C18

新增：

```text
C18 In-Memory Tool Execution Collector
```

至少：

```text
C18.1 Collector stores ToolExecutionRecord only
C18.2 records() returns immutable snapshot
C18.3 query results are immutable snapshots
C18.4 insertion order preserved
C18.5 no deduplication
C18.6 no aggregation
C18.7 no persistence
C18.8 no DB / LLM / Registry / Handler dependency
C18.9 no Tool execution capability
C18.10 no global singleton
C18.11 clear() is explicit
C18.12 AIOrchestrator remains unchanged
```

---

# 二十四、性能边界

本阶段不要做性能优化。

只要求：

```text
O(1) append
```

查询可以：

```text
O(n)
```

因为当前只是：

```text
small in-memory collector
```

不要提前加入：

```text
index
LRU
cache
database
```

---

# 二十五、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.11 Step 16 — In-Memory Tool Execution Collector.md
```

记录：

## 1. Purpose

Collector 的职责。

## 2. Architecture

```text
ToolExecutionService
        ↓
ToolExecutionRecord
        ↓
ToolExecutionObserver
        ↓
InMemoryToolExecutionCollector
        ↓
Read-only Query
```

## 3. Query

```text
records
by request
by project
by tool
```

## 4. Security

```text
Record only
No arguments
No result data
No SQL
No secrets
```

## 5. Lifecycle

```text
create
→ collect
→ query
→ clear
```

## 6. Limitations

明确：

```text
No persistence
No audit
No metrics
No dashboard
No tracing
No TTL
No distributed sharing
```

---

# 二十六、测试命令

先：

```powershell
python -m pytest -q tests/test_in_memory_tool_execution_collector.py
```

然后：

```powershell
python -m pytest -q `
  tests/test_tool_execution_record.py `
  tests/test_tool_execution_observer.py `
  tests/test_tool_execution_service.py `
  tests/test_tool_runtime_failure_contract.py `
  tests/test_tool_chat_execution_boundary.py `
  tests/test_tool_chat_capability.py
```

再：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

如果 PostgreSQL 继续：

```text
psycopg.errors.ConnectionTimeout
```

不要修改代码绕过。

记录：

```text
Environment DB unavailable
```

---

# 二十七、compile / LSP

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

# 二十八、最终报告

完成后严格：

```text
【Phase 3.11 Step 16 COMPLETE】

1. 新增文件
2. 修改文件
3. Collector Contract
4. Query Contract
5. Snapshot / Immutability
6. Ordering
7. Security
8. Observer Failure Isolation
9. Architecture Contract C18
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
Read-only Query
```

然后：

**立即停止。**

不要进入 Step 17。

不要做数据库持久化。

不要做 Audit。

不要做 Metrics。

不要做 Dashboard。

不要做 Tracing。

不要做分布式 Event Bus。

不要开发 Agent / MCP / LangGraph / Memory / Planning。
