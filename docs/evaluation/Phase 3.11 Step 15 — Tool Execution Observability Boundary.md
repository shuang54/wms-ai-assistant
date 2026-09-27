# Phase 3.11 Step 15 — Tool Execution Observability Boundary

> 目标：在不改变 ``ToolResult`` / ``execute()`` 契约的前提下，让
> ToolExecutionService 把一次执行产生的 ``ToolExecutionRecord``
> 交给一个**可选的、进程内的** observer。
>
> **不是 Audit System**：无持久化、无 metrics 聚合、无 dashboard、无 tracing。

---

## 1. Architecture

```text
ToolChatService / AIOrchestrator
        ↓
ToolExecutionService
        ├── started_at = now_utc() / timer = perf_counter()
        ├── ToolRegistry.execute(...) → ToolResult（原样返回）
        ├── finished_at = now_utc() / duration_ms = elapsed_ms(timer)
        ↓ from_execution(context, tool_name, result, timing)
ToolExecutionRecord（frozen）
        ↓ observer.on_execution(record)          ← 可选（observer=None → 不产生）
        ↓
return ToolResult（**契约不变**）
```

```text
执行 → ToolResult
     + Timing
     ↓
   ToolExecutionRecord
     ↓
   Optional Observer
```

### 事件规则

```text
成功执行            → 1 个事件（record.success=True）
ToolResult(False)   → 1 个事件（record.success=False）—— 不跳过 observer
capability 拒绝      → 0 个事件（未发生执行；异常语义不变）
Registry 抛异常      → 0 个事件（无 ToolResult → 不构造 Record；异常原样传播）
context=None        → 0 个事件（边界不生成 request_id；链路 A 维持 Deferred）
observer=None       → 不计时 / 不建 Record（零开销，旧行为逐字不变）
```

---

## 2. Observer Contract

```text
backend/app/services/tool_execution_observer.py

class ToolExecutionObserver(Protocol):
    def on_execution(self, record: ToolExecutionRecord) -> None: ...
```

```text
* 只接收 ToolExecutionRecord —— 不接收 arguments / ToolResult.data / SQL /
  exception object / LLM message / DB session / Engine / Registry；
* 唯一回调点：ToolExecutionService._emit_record → observer.on_execution(record)
  （AST 锁定：全模块 1 处调用、仅 1 个位置参数、无关键字参数）；
* 单向 consumer：不参与 Tool 执行决策，不持有执行能力；
* 恰好一次：一次执行最多 1 个事件（不 0 / 不 2 / 不 N）；
* 不得改写 Record：frozen DTO → FrozenInstanceError；
* 零持久化：无 EventBus / MessageBus / Kafka / Redis Stream / Celery /
  OpenTelemetry / Plugin System / DI Framework / 文件日志。
```

方法名说明：项目 LLM 侧 sink 使用 ``record(observation)``；此处 payload 本身
就叫 ``ToolExecutionRecord``（``record(record)`` 语义含混），故按执行观测语义
命名为 ``on_execution(record)``。

---

## 3. Failure Isolation

```text
Observer failure
    ≠
Tool failure
```

```text
Tool execution SUCCESS + observer FAILED → ToolResult(success=True)（不变）
Tool execution FAILED  + observer FAILED → ToolResult(success=False)（原样）
Tool 恰好执行 1 次；observer 恰好回调 1 次（不重试 / 不 fallback / 不重跑）
Record 构建失败与回调失败**整体**被 try/except 隔离（`_emit_record`）
隔离动作：logger.warning("tool execution observer failed",
                        extra={tool_name, error_type})
          —— 不写 exception message / traceback（防 secret 外溢）
不捕获 BaseException（KeyboardInterrupt / SystemExit 照旧传播）
```

---

## 4. Security

```text
No arguments        —— arguments 不进入 Record（用含 secret 的 arguments 验证 0 泄漏）
No result.data      —— ToolResult.data 不进入 Record
No SQL              —— 无 SQL / statement / query 字段
No secrets          —— API Key / password / DATABASE_URL / connection string /
                       Authorization / Bearer / sk- / traceback 均不出现
No DB / No LLM      —— observer 模块 import 仅 {__future__, typing, record}；
                       边界 observer 路径无 sqlalchemy / engine / session / LLM
error_type 仅类名    —— 复用既有 ToolError allow-list（无法确定 → None）
```

---

## 5. Compatibility

```text
ToolResult                     = unchanged（tool_name / success / data / error）
ToolExecutionService.execute() = 返回 ToolResult（不是 Record / tuple）
ToolExecutionService 构造       = 旧形态合法（observer 为可选关键字参数，默认 None）
ToolRegistry / Tool Handler     = unchanged（仍只接收 arguments）
ToolChatService                = unchanged（多轮语义 / request_id / round / tool_call_id 未动）
AIOrchestrator                  = unchanged（context=None → 0 事件；Step 13 Deferred 保持）
API / LLM messages              = unchanged（api/tool_chat.py SHA256 未变）
```

---

## 6. Current Limitations

```text
No persistence         —— Record 不被写往任何地方（observer 默认 None）
No audit               —— 无审计表 / 无留存
No metrics aggregation —— 无计数 / 直方图 / 聚合
No dashboard           —— 无 UI
No tracing backend     —— 不接 OpenTelemetry / Jaeger 等
无内置 observer 实现     —— 只提供 Protocol（记录型实现位于 tests/，不进生产）
链路 A 无事件            —— AIOrchestrator 不创建 Context（context=None → 0 事件）
                          与 Step 13 的 Deferred 边界一致
```

环境观察（沿用 Step 12–14）：本会话后段本地 PostgreSQL 出现
``psycopg ConnectionTimeout``（localhost:5432）→ 含 DB 的套件可能无法执行；
Step 15 的 DB-gated 仅 1 个只读回归。

---

## 7. Tests

```text
tests/test_tool_execution_observer.py                              32 tests（31 非 DB + 1 DB-gated）
tests/test_tool_chat_architecture_contract.py::TestC17…            9 tests（C17.1 – C17.10）

1  No Observer        = PASS（行为不变；结果与无 observer 时逐字段相等）
2  Success            = PASS（1 个 record；success=True）
3  Tool Failure       = PASS（ToolResult(False) / schema 拒绝 / unknown tool 均产出 record）
4  Context Mapping    = PASS（4 字段原值）
5  Timing             = PASS（tz-aware / started<=finished / duration>=0 / 实测 ≥15ms）
6  Tool Name          = PASS（== execute 实参）
7  One Event          = PASS（每次执行恰好 1；capability / 异常 / 无 context → 0）
8  Observer Failure   = PASS（ToolResult 不变 / Tool 1 次 / observer 1 次 / 无 retry）
9  Record Immutable   = PASS（FrozenInstanceError）
10 Sensitive Data     = PASS（arguments / data / error 中的 secret 0 泄漏）
附加 Multi-Step       = PASS（round 1,2,3；同 chat 同 request_id；不同 chat 不同）
附加 静态边界          = PASS（唯一回调点 / try 隔离 / 无 loop / 条件发射）
附加 日志安全          = PASS（warning 只含 tool_name + error_type）
附加 DB 回归          = PASS / 环境限制（见 §8）
```

### 测试结果

```text
定向（§二十一 9 文件）：                    388 passed / 10 skipped
定向（Observer 文件）：                     31 passed / 1 skipped
全量 no DB：                                2968 passed / 338 skipped
                                           （Step 14 基线 2928/337 → +40 passed / +1 skipped =
                                             31 非 DB + 9 C17 ✓ 逐项对齐）
定向 DB（Observer 文件，RUN_DB_TESTS=1）：   31 passed + 1 error
                                           —— ERROR at setup：
                                              psycopg.errors.ConnectionTimeout
                                              （localhost:5432）
                                              → **Environment DB unavailable**
                                                （环境问题；未用代码绕过）
                                           同一真实链路已在 Step 12（36 passed）/
                                              Step 13（52 passed）DB 套件中验证
compileall backend tests：                  clean（exit 0）
LSP：                                       0 error / 0 warning
lint：                                      unavailable（未安装新工具）
DB writes：                                 0
Real LLM：                                  NO
```

---

## 8. 测试结果（最终）

```text
定向（9 文件）：                            388 passed / 10 skipped
定向 DB（Observer 文件，RUN_DB_TESTS=1）：   31 passed / 1 error（Environment DB unavailable：
                                           psycopg ConnectionTimeout @ localhost:5432）
全量 no DB：                                2968 passed / 338 skipped（0 failed）
全量 DB：                                   Environment DB unavailable（未跑完；不用代码绕过）
compileall backend tests：                  clean
LSP：                                       0 error / 0 warning
lint：                                      unavailable
DB writes：                                 0
Network：                                   0（无真实 LLM）
```

---

## 9. Final State

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

ToolExecutionObserver = IMPLEMENTED（Protocol；可选；单向 Record 出口）
Persistence / Audit / Metrics / Dashboard / Tracing = NOT IMPLEMENTED
Real Tool = get_inventory   get_work_order = DEFERRED   Third Tool = NOT ADDED
Agent / MCP / LangGraph / Memory / Planning = NOT IMPLEMENTED
```
