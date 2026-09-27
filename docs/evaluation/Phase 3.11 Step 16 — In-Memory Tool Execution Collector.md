# Phase 3.11 Step 16 — In-Memory Tool Execution Collector

> 目标：在 Step 14（Record）/ Step 15（Observer）之上，新增一个
> **生产可用但纯内存**的 Tool Execution Collector：安全收集
> ``ToolExecutionRecord`` 并提供**只读查询**。
>
> **不是 Audit / Metrics / Tracing**：无持久化、无统计、无后台任务、无单例。

---

## 1. Purpose

```text
Step 15 只定义了 Observer Protocol；本阶段提供它的第一个生产可用实现：

    ToolExecutionService（Step 15：可选 observer）
        ↓ ToolExecutionRecord（frozen；Step 14）
    InMemoryToolExecutionCollector.on_execution(record)
        ↓ 原样 append（O(1)：不复制 / 不修改 / 不排序 / 不去重 / 不聚合）
    records() / records_by_request_id() / records_by_project_id() /
    records_by_tool_name()
        ↓
    tuple[ToolExecutionRecord, ...]（不可变快照；无匹配 → ()）

职责：**保存 + 最小只读查询**。
不做：统计 / 持久化 / 执行 / 索引 / 清理调度 / 分布式共享。
```

---

## 2. Architecture

```text
ToolExecutionService
        ↓
ToolExecutionRecord
        ↓
ToolExecutionObserver（Step 15 Protocol；@runtime_checkable）
        ↓
InMemoryToolExecutionCollector
        ↓
Read-only Query（tuple 快照）
```

```text
backend/app/services/in_memory_tool_execution_collector.py

class InMemoryToolExecutionCollector:        # 实现 ToolExecutionObserver（结构化）
    def __init__(self) -> None:              # 空集合 + 单个 threading.Lock
    def on_execution(self, record) -> None   # 唯一写入点（原样 append；非 Record → TypeError）
    def records(self) -> tuple[...]           # 全部（写入顺序）
    def records_by_request_id(self, request_id) -> tuple[...]
    def records_by_project_id(self, project_id) -> tuple[...]
    def records_by_tool_name(self, tool_name) -> tuple[...]
    def clear(self) -> None                   # 唯一显式清空（无 TTL / 无后台清理）
```

集成方式（**无生产接线**；显式实例 + 既有构造函数注入）：

```python
collector = InMemoryToolExecutionCollector()
boundary = ToolExecutionService(registry=..., observer=collector)
service = ToolChatService(llm_client=..., execution_service=boundary)
...
collector.records_by_request_id("req-1")
```

---

## 3. Query

```text
records()                        → 全部记录（写入顺序；空 → ()）
records_by_request_id(id)        → 一次 chat 的全部 Tool 执行（保持写入顺序）
records_by_project_id(pid)       → pid 严格相等；pid=None **只**匹配
                                   record.project_id is None（≠"全部"）
records_by_tool_name(name)       → 严格相等（无大小写折叠 / 无前缀 / 无别名 / 无模糊）

全部查询：
    * 返回 tuple（不可变快照）——调用方无法 append / clear / pop；
    * read-only / deterministic / 无 IO / 无 DB / 无 LLM；
    * 无匹配 → ()（绝不返回 None）；
    * 不做 sort / deduplicate / aggregate。
```

输入校验：非 str → ``TypeError``；空 / 纯空白 → ``ValueError``
（与项目既有 DTO / Service 校验风格一致；``project_id=None`` 是合法查询）。

---

## 4. Security

```text
Collector 只保存 ToolExecutionRecord
    * on_execution 非 Record → TypeError（不落任何对象）
    * Record 本身（Step 14 契约）不含 arguments / ToolResult.data / SQL /
      prompt / response / API key / password / DATABASE_URL / connection string /
      Authorization / traceback
    * 模块 import 白名单：{__future__, threading, collections.abc, tool_execution_record}
      —— 无 sqlalchemy / db / api / llm / tools / httpx / kafka / redis / celery /
        opentelemetry / os / pathlib（AST 锁定）
    * 无 execute / registry / handler 标识符（无 Tool 执行能力）
```

---

## 5. Lifecycle

```text
create（显式实例；无模块级单例）
    ↓
collect（on_execution：append O(1)；多次 / 重复对象均原样保留）
    ↓
query（records / by request / by project / by tool；均为快照）
    ↓
clear（显式；之后 records() == ()；可继续收集）
```

```text
无 TTL / 无自动清理 / 无后台线程 / 无定时任务 / 无 LRU / 无索引 / 无 cache
线程模型：单个 threading.Lock（与 InMemoryProjectRegistry /
          InMemoryToolCapabilityRegistry 一致）；不引入 asyncio.Queue /
          线程池 / event loop
```

---

## 6. Limitations

```text
No persistence          —— 进程内、内存中；重启即丢失
No audit                —— 无审计留存 / 无合规存储
No metrics              —— 无 count / success_rate / average_duration / p95 / p99
No dashboard            —— 无 UI
No tracing              —— 不接 OpenTelemetry / Jaeger
No TTL                  —— 不自动清理（clear() 显式）
No distributed sharing  —— 仅进程内；无 Redis / Kafka / 消息队列
无生产接线               —— 由上层显式创建并注入（禁止全局单例，避免
                          test / request / project 污染与内存泄漏）
链路 A 无事件            —— AIOrchestrator（context=None）不产生 Record（Step 13/15 Deferred）
查询复杂度               —— O(n) 线性扫描（small in-memory；不做索引 / 缓存优化）
```

---

## 7. Tests

```text
tests/test_in_memory_tool_execution_collector.py                     60 tests（全部非 DB）
tests/test_tool_chat_architecture_contract.py::TestC18…              12 tests（C18.1 – C18.12）

初始化 / 单条 / 多条          = PASS（空 () / (record,) / 顺序）
Snapshot / Immutability      = PASS（tuple；append → AttributeError；每次调用新快照）
Record Identity              = PASS（records()[0] is record；字段值不变）
Request Query                = PASS（match / no match () / multiple / 顺序保持）
Project Query                = PASS（project-a / project-b / None 只匹配 None / 空 ()）
Tool Query                   = PASS（get_inventory / get_work_order / unknown () /
                                      严格相等：GET_INVENTORY / get / inventory → ()）
Clear                        = PASS（清空后 ()；可继续收集；查询同步为空）
Isolation                    = PASS（两个 Collector 互不影响；无模块级单例）
Security                     = PASS（仅 Record；非 Record → TypeError；无 data / SQL / secret）
Determinism                  = PASS（同插入顺序 → 同查询结果）
统计禁用                      = PASS（public API 仅 6 个方法；无 count / success_rate / p95）
线程安全 smoke                = PASS（4 线程 × 50 次 append → 200 条，无丢失）
集成 ToolExecutionService     = PASS（成功 / 失败均收集；无 Context → 0 条）
集成 ToolChatService          = PASS（round 1,2,3；同 chat 同 request_id；不同 chat 不同）
Collector 异常                = PASS（由 Step 15 隔离：ToolResult 不变、Tool 1 次）
```

### 测试结果

```text
定向（§二十六 8 文件）：              356 passed / 9 skipped
定向（Collector 文件）：              60 passed
全量 no DB：                          3040 passed / 338 skipped
                                      （Step 15 基线 2968/338 → +72 passed =
                                        60 Collector + 12 C18 ✓ 逐项对齐）
定向 DB（Observer 文件，RUN_DB_TESTS=1）：31 passed + 1 error
                                      —— ERROR at setup：
                                         psycopg.errors.ConnectionTimeout（localhost:5432）
                                         → **Environment DB unavailable**（环境问题；
                                           未修改代码绕过；Step 16 无新增 DB 依赖）
compileall backend tests：            clean（exit 0）
LSP：                                 0 error / 0 warning
lint：                                unavailable（未安装新工具）
DB writes：                           0
Network：                             0
Real LLM：                            NO
```

---

## 8. 测试结果（最终）

```text
定向（8 文件）：                       356 passed / 9 skipped
全量 no DB：                           3040 passed / 338 skipped（0 failed）
全量 DB：                              Environment DB unavailable（psycopg
                                      ConnectionTimeout @ localhost:5432；
                                      未跑完，不用代码绕过）
compileall backend tests：             clean（exit 0）
LSP：                                  0 error / 0 warning
lint：                                 unavailable
DB writes：                            0
Network：                              0
```

---

## 9. Final State

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

InMemoryToolExecutionCollector = IMPLEMENTED（进程内；无单例；tuple 快照查询）
Persistence / Audit / Metrics / Dashboard / Tracing / TTL / 分布式共享 = NOT IMPLEMENTED
Real Tool = get_inventory   get_work_order = DEFERRED   Third Tool = NOT ADDED
Agent / MCP / LangGraph / Memory / Planning = NOT IMPLEMENTED
```
