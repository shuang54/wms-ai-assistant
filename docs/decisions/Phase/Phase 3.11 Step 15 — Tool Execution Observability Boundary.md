你现在开始执行：

# Phase 3.11 Step 15 — Tool Execution Observability Boundary

项目：

```text
D:\coding\ai\wms-ai-assistant
```

## 一、阶段目标

在 Phase 3.11 Step 13 / Step 14 已完成：

```text
ToolExecutionContext
ToolExecutionRecord
```

的基础上，建立一个**最小的 Tool Execution Observability Boundary**。

本阶段唯一目标：

> 让 ToolExecutionService 在不改变现有 ToolResult / execute() 契约的情况下，可以把一次执行产生的 `ToolExecutionRecord` 交给一个可选的、进程内的 observer。

目标结构：

```text
ToolChatService / AIOrchestrator
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
Optional Observer
```

注意：

本阶段只是建立：

```text
Execution
→ Record
→ Observer
```

**不是 Audit System。**

---

# 二、严格范围

## 允许

* 新增最小 Observer Protocol
* ToolExecutionService 增加可选 observer
* ToolExecutionService 在一次执行结束后生成 ToolExecutionRecord
* Observer 接收 Record
* 新增内存 Fake/Recording Observer
* 新增单元测试
* 新增 Architecture Contract
* 更新 Evaluation / Architecture 文档

## 禁止

绝对不要实现：

```text
数据库持久化
Repository
Migration
Audit Table
Metrics Aggregation
Dashboard
Tracing Backend
OpenTelemetry
Kafka
Redis
消息队列
文件日志
```

不要修改：

```text
ToolResult
ToolRegistry
Tool Handler
Router
Tool Argument Extractor
AI Router
AIOrchestrator 的业务语义
ToolChatService 的多轮语义
API Response
LLM Message
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

# 三、Step 1：先阅读真实实现

先不要修改代码。

阅读：

```text
backend/app/services/tool_execution_service.py
backend/app/services/tool_execution_context.py
backend/app/services/tool_execution_record.py
backend/app/services/tool_chat_service.py
backend/app/tools/registry.py
backend/app/tools/base.py
```

以及：

```text
tests/test_tool_execution_service.py
tests/test_tool_execution_context.py
tests/test_tool_execution_record.py
tests/test_tool_runtime_failure_contract.py
tests/test_tool_chat_execution_boundary.py
tests/test_tool_chat_capability.py
tests/test_tool_architecture_contract.py
```

重点确认：

1. `ToolExecutionService.execute()` 当前完整流程。
2. `ToolExecutionRecord.from_execution()` 当前字段。
3. `ToolResult` 当前真实结构。
4. ToolExecutionService 当前 capability failure 与 ToolResult failure 的区别。
5. ToolChatService 当前如何注入 ToolExecutionService。
6. 是否已有类似 callback / sink / observer contract。

**不要假设。**

---

# 四、Observer Contract

新增：

```text
backend/app/services/tool_execution_observer.py
```

建议使用最小 Protocol：

```python
class ToolExecutionObserver(Protocol):
    def on_execution(self, record: ToolExecutionRecord) -> None:
        ...
```

要求：

* 只接收 `ToolExecutionRecord`
* 不接收 arguments
* 不接收 ToolResult.data
* 不接收 SQL
* 不接收 exception object
* 不接收 LLM message
* 不接收 DB session
* 不接收 Engine

Observer 是：

```text
Record consumer
```

不是：

```text
Tool executor
```

---

# 五、Observer 生命周期

ToolExecutionService：

```text
execute()
  ↓
开始计时
  ↓
ToolRegistry.execute()
  ↓
得到 ToolResult
  ↓
生成 ToolExecutionRecord
  ↓
observer.on_execution(record)
  ↓
return ToolResult
```

重要：

**ToolExecutionRecord 的创建不能改变 ToolResult。**

必须保持：

```text
execute(...) -> ToolResult
```

不变。

---

# 六、Timing

使用 Step 14 已经建立的 timing 工具。

优先复用：

```text
now_utc()
elapsed_ms()
```

不要重新实现第二套 timing。

执行时：

```text
started_at = now_utc()
timer_start = perf_counter()
```

然后：

```text
ToolRegistry.execute(...)
```

结束：

```text
finished_at = now_utc()
duration_ms = elapsed_ms(timer_start)
```

---

# 七、成功执行

例如：

```text
get_inventory
```

执行成功：

```text
ToolResult.success = True
```

最终：

```text
Record.success = True
```

然后：

```text
observer.on_execution(record)
```

最后：

```text
return ToolResult
```

顺序必须保持。

---

# 八、ToolResult Failure

如果 Registry / Handler 返回：

```text
ToolResult(success=False)
```

也必须生成 Record：

```text
success=False
```

然后 observer 收到：

```text
ToolExecutionRecord
```

最后原样返回：

```text
ToolResult(success=False)
```

不要因为 ToolResult failure 就跳过 observer。

---

# 九、Exception Boundary

重点处理：

```text
ToolRegistry.execute()
```

如果抛出异常：

不要改变当前 ToolExecutionService 的既有异常语义。

也不要为了 Record 而吞异常。

必须：

```text
exception
   ↓
preserve existing behavior
```

如果当前 architecture 会把异常转换为：

```text
ToolResult(False)
```

则保持现有行为。

如果当前 boundary 会直接抛：

则继续抛。

Record 是否记录 exception：

**只根据当前已有 ToolResult / Error Contract 能否安全构造决定。**

不要创建新的异常体系。

---

# 十、Observer Failure

这是本阶段最重要的安全规则之一。

如果：

```text
ToolExecutionRecord
        ↓
observer.on_execution(record)
```

observer 自己抛异常：

**不能影响 Tool 执行结果。**

例如：

```text
Tool execution SUCCESS
observer FAILED
```

最终仍然：

```text
execute() -> ToolResult(success=True)
```

不能把：

```text
observer exception
```

转换成：

```text
ToolResult(success=False)
```

也不能重新执行 Tool。

也不能 retry observer。

也不能 retry Tool。

建议：

```text
try:
    observer.on_execution(record)
except Exception:
    observer failure is isolated
```

但不要随意新增 logging framework。

如果当前项目已有安全 logger，可以复用。

如果没有：

**可以静默隔离，并在测试中证明 ToolResult 不受影响。**

---

# 十一、Observer Optional

保持向后兼容。

当前：

```python
ToolExecutionService(registry)
```

仍然合法。

没有 observer：

```text
execution
→ ToolResult
```

即可。

有 observer：

```text
execution
→ ToolResult
+
ToolExecutionRecord
→ observer
```

---

# 十二、Observer 不允许修改 Record

因为：

```text
ToolExecutionRecord
```

是：

```text
@dataclass(frozen=True)
```

所以：

```text
observer
```

不能修改：

```text
request_id
tool_name
success
duration
```

测试必须验证：

```text
FrozenInstanceError
```

或者等价 immutable 行为。

---

# 十三、Observer 不得获得敏感数据

Observer 只能收到：

```text
ToolExecutionRecord
```

Record 中不能出现：

```text
arguments
result.data
SQL
password
API key
database URL
connection string
authorization header
prompt
LLM response
traceback
```

新增 architecture contract：

```text
C17 Observer Data Boundary
```

至少验证：

```text
Observer parameter = ToolExecutionRecord
```

而不是：

```text
ToolResult
dict
arguments
Exception
```

---

# 十四、RecordingObserver

为了测试：

新增一个仅用于测试的：

```text
RecordingToolExecutionObserver
```

位置：

```text
tests/
```

不要放进生产代码。

例如：

```python
class RecordingToolExecutionObserver:
    records: list[ToolExecutionRecord]

    def on_execution(self, record):
        self.records.append(record)
```

用于验证：

```text
execution
→ record
→ observer
```

---

# 十五、测试

新增：

```text
tests/test_tool_execution_observer.py
```

至少覆盖：

## 1. No Observer

```text
execute()
→ ToolResult
```

行为不变。

---

## 2. Success

```text
ToolResult.success=True
→ observer receives one record
```

---

## 3. Tool Failure

```text
ToolResult.success=False
→ observer receives one record
```

---

## 4. Context Mapping

验证：

```text
request_id
project_id
tool_call_id
round
```

全部来自 Context。

---

## 5. Timing

验证：

```text
started_at
finished_at
duration_ms
```

有效。

---

## 6. Tool Name

验证：

```text
record.tool_name
```

等于实际：

```text
execute(tool_name)
```

---

## 7. Observer One Event

一次 Tool execution：

```text
exactly 1 observer event
```

不能：

```text
0
2
N
```

---

## 8. Observer Failure

Fake Observer：

```python
raise RuntimeError("observer failure")
```

验证：

```text
ToolResult unchanged
Tool executed exactly once
No retry
No fallback
```

---

## 9. Record Immutable

Observer 尝试：

```text
record.success = False
```

必须失败。

---

## 10. Sensitive Data Boundary

构造：

```text
arguments contains secret
ToolResult.data contains secret
ToolResult.error contains secret
```

确认 observer 收到的 Record：

```text
不包含 secret
```

---

# 十六、AIOrchestrator

本阶段：

**不要强制接入 AIOrchestrator。**

因为 Step 13 已经明确：

```text
AIOrchestrator context = deferred
```

保持：

```text
AIOrchestrator
→ ToolExecutionService(context=None)
```

当前语义不变。

本阶段只验证：

```text
ToolExecutionService
```

这个基础边界。

---

# 十七、ToolChatService

ToolChatService 当前已经拥有：

```text
ToolExecutionContext
```

所以如果现有注入结构允许：

可以让它继续自然使用 observer。

但不要修改：

```text
round
request_id
tool_call_id
```

的语义。

验证：

```text
Round 1
→ Record round=1

Round 2
→ Record round=2

Round 3
→ Record round=3
```

同一次 Chat：

```text
request_id 相同
```

不同 Chat：

```text
request_id 不同
```

如果接入 observer 会需要修改 ToolChatService constructor：

优先保持当前 constructor 兼容。

不要为了 Step 15 大范围重构。

---

# 十八、Architecture Contract C17

新增：

```text
C17 Tool Execution Observer Boundary
```

至少：

```text
C17.1 observer is optional
C17.2 observer receives ToolExecutionRecord only
C17.3 one execution produces at most one record event
C17.4 ToolResult contract unchanged
C17.5 observer failure cannot alter ToolResult
C17.6 observer cannot trigger retry
C17.7 observer cannot execute Tool
C17.8 observer cannot access arguments/result.data
C17.9 observer cannot access DB/LLM/Engine/Session
C17.10 no persistence
```

---

# 十九、Security

确认源码不存在：

```text
observer → Registry
observer → Handler
observer → DB
observer → LLM
observer → API
```

Observer 是：

```text
one-way consumer
```

只能：

```text
receive Record
```

---

# 二十、不要引入复杂 Observer Framework

禁止：

```text
EventBus
MessageBus
Kafka
Redis Stream
Celery
Pub/Sub
OpenTelemetry
Plugin System
Dependency Injection Framework
```

当前只需要：

```text
Protocol
+
Optional Observer
+
Record
```

---

# 二十一、测试命令

先：

```powershell
python -m pytest -q tests/test_tool_execution_observer.py
```

然后：

```powershell
python -m pytest -q `
  tests/test_tool_execution_record.py `
  tests/test_tool_execution_context.py `
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

如果本地 PostgreSQL 继续出现：

```text
psycopg ConnectionTimeout
```

不要修改代码绕过。

明确记录：

```text
Environment DB unavailable
```

---

# 二十二、compile / LSP

运行：

```powershell
python -m compileall backend tests
```

检查：

```text
0 errors
0 warnings
```

如果项目没有 ruff / flake8：

不要安装新的 lint 工具。

---

# 二十三、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.11 Step 15 — Tool Execution Observability Boundary.md
```

记录：

## 1. Architecture

```text
Tool Execution
      ↓
ToolResult
      +
Timing
      ↓
ToolExecutionRecord
      ↓
Optional Observer
```

## 2. Observer Contract

记录：

```text
Observer receives Record only
```

## 3. Failure Isolation

记录：

```text
Observer failure
≠
Tool failure
```

## 4. Security

```text
No arguments
No result.data
No SQL
No secrets
No DB
No LLM
```

## 5. Compatibility

```text
ToolResult unchanged
ToolExecutionService.execute() -> ToolResult unchanged
ToolRegistry unchanged
Tool Handler unchanged
API unchanged
```

## 6. Current Limitations

```text
No persistence
No audit
No metrics aggregation
No dashboard
No tracing backend
```

---

# 二十四、最终报告

完成后严格：

```text
【Phase 3.11 Step 15 COMPLETE】

1. 新增文件
2. 修改文件
3. Observer Contract
4. ToolExecutionRecord 接入
5. Success / Failure
6. Observer Failure Isolation
7. Context / Round / Request ID
8. Security Boundary
9. Architecture Contract C17
10. 测试结果
11. compile / LSP
12. DB writes
13. API 是否变化
14. ToolResult 是否变化
15. 当前限制
```

最后输出：

```text
Tool Execution:

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
Optional Observer
```

然后：

**立即停止。**

不要进入 Step 16。

不要做数据库持久化。

不要做 Audit。

不要做 Metrics。

不要做 Dashboard。

不要做 Tracing。

不要开发 Agent / MCP / LangGraph / Memory / Planning。
