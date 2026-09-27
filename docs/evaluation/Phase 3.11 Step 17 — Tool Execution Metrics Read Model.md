# Phase 3.11 Step 17 — Tool Execution Metrics Read Model

> 目标：在 Step 14（Record）/ Step 15（Observer）/ Step 16（Collector）之上，
> 新增**纯内存、只读、确定性**的 Tool 执行统计（Read Model）：
> ``ToolExecutionRecord`` → ``ToolExecutionMetricsSnapshot``。
>
> **不是 Dashboard / Monitoring**：无 DB、无 HTTP API、无 Prometheus、
> 无 OpenTelemetry、无告警、无后台任务、无持久化、无单例。

---

## 1. Purpose

```text
Step 16 结束时，Records 已经可收集、可只读查询，但**没有统计**：
Collector 明确不做 count / success_rate / average_duration（§C18）。

本阶段只补一层：**对已完成的执行事实做只读分析**，回答 5 个问题：

    执行了多少次？成功多少次？失败多少次？平均耗时多少？最大耗时多少？

为什么单独一层（而不是塞进 Collector / API / 执行边界）：

    * Collector 的 Contract 是"原样保存 + 最小查询"（不聚合）→ 不破坏；
    * 执行边界（ToolExecutionService）不得因统计失败而影响 ToolResult
      → Metrics 必须是**旁路 Read Model**（不反向依赖）；
    * 数据来源未来可能不是内存（DB / 文件 / 导出文件）→ Metrics 只接受
      ``Iterable[ToolExecutionRecord]``，与存储方式解耦（Storage-agnostic）。
```

---

## 2. Architecture

```text
ToolExecutionContext（Step 13）
        ↓
ToolExecutionService（Step 15：可选 observer；执行链本体未变）
        ↓
ToolRegistry → Tool → ToolResult
        +
Timing（Step 14）
        ↓
ToolExecutionRecord（frozen）
        ↓
ToolExecutionObserver（Step 15 Protocol）
        ↓
InMemoryToolExecutionCollector（Step 16）
        ↓
records()（不可变 tuple 快照）
        ↓
ToolExecutionMetricsService.snapshot(records)      ← 本阶段新增（纯计算）
        ↓
ToolExecutionMetricsSnapshot（frozen；全局单快照）
```

```text
backend/app/services/tool_execution_metrics_service.py

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

    def assert_field_whitelist(self) -> None: ...

class ToolExecutionMetricsService:
    @staticmethod
    def snapshot(records: Iterable[ToolExecutionRecord]) -> ToolExecutionMetricsSnapshot: ...
```

使用方式（显式实例；无单例）：

```python
collector = InMemoryToolExecutionCollector()
boundary = ToolExecutionService(registry=..., observer=collector)
service = ToolChatService(llm_client=..., execution_service=boundary)
...
snapshot = ToolExecutionMetricsService.snapshot(collector.records())
```

```text
边界（§十 ~ §十三 / §二十二）：
    * Pure：无 IO / 日志 / DB / LLM / Tool 执行 / 网络；
    * Deterministic：同一输入 → 同一 Snapshot（不依赖时间 / 随机 / set 顺序）；
    * 无状态：snapshot 是 @staticmethod（无 __init__ / 无缓存 / 无单例）；
    * 只读：不修改输入（Record 本身 frozen）；
    * 不接受 Collector：绝不访问 collector._records（只吃 records() 的 tuple）。
```

---

## 3. Metrics

```text
total_count         记录数
success_count       count(record.success is True)
failure_count       count(record.success is False)
                    → success_count + failure_count == total_count（恒成立）

success_rate        success_count / total_count
failure_rate        failure_count / total_count
                    → 0 <= rate <= 1；success_rate + failure_rate == 1（浮点误差内）
                    → **不 round**（保持原始精度）

total_duration_ms   sum(record.duration_ms)              （空 → 0.0）
average_duration_ms total_duration_ms / total_count      （空 → None）
max_duration_ms     max(record.duration_ms)              （空 → None）

时长来源：只读 record.duration_ms（Step 14 已完成的执行事实）；
          不 perf_counter / 不 datetime.now（Metrics 不重新测量时间）。

失败记录同样计入时长（Metrics 不按 success 过滤耗时）。
Record 允许 int duration_ms → Metrics 统一输出 float。
```

Snapshot 构造期校验（DTO 不变式，§二十）：

```text
* counts 必须是非负 int（bool 拒绝）；
* success_count + failure_count == total_count（否则 ValueError）；
* 空数据集（total_count=0）→ total_duration_ms 必须为 0
  且 rates / average_duration_ms / max_duration_ms 必须为 None；
* 非空数据集 → rates / average / max 不得为 None，且数值有限、非负；
* rates ∈ [0, 1] 且 success_rate + failure_rate ≈ 1（math.isclose）。
```

---

## 4. Empty Semantics

```text
snapshot(()) →
    total_count         = 0
    success_count       = 0
    failure_count       = 0
    success_rate        = None        ← 不是 0
    failure_rate        = None        ← 不是 0
    total_duration_ms   = 0.0
    average_duration_ms = None
    max_duration_ms     = None

理由：0 条记录 ≠ 100 条记录 0 成功（后者 success_rate = 0.0）。
无 ZeroDivisionError / NaN / Infinity。

对照（同样 0 成功，但有记录）：
    1 条失败记录 → success_rate = 0.0（不是 None）
    全 0 耗时记录 → average_duration_ms = 0.0（不是 None）
```

---

## 5. Security

```text
Snapshot 字段白名单（8 个）：只有 count / rate / duration。

不包含（且 DTO 拒绝扩展）：
    request_id / project_id / tool_call_id / round / tool_name /
    arguments / SQL / ToolResult.data / error message / error_type /
    exception / traceback / API key / password / DATABASE_URL /
    connection string / Authorization header。

输入校验（§十四）：
    * 非 ToolExecutionRecord 元素 → TypeError（**不静默跳过 / 不转换**）；
    * None / str / bytes / 不可迭代 → TypeError；
    * 错误信息只含类型名，不回显元素内容（不泄露 Tool 数据）。

无维度（§十七 / §十八）：只有全局单快照 —— 无 by_tool / by_project /
    by_request / top_slowest_tools / failure_by_tool / success_rate_by_project。

执行链隔离（§二十五）：ToolExecutionService / ToolChatService / API /
    Orchestrator 源码均不引用 Metrics → Metrics 计算失败**不可能**导致
    ToolResult(success=False) / retry / fallback（Read Model 是旁路）。
```

---

## 6. Limitations

```text
* 无持久化：Snapshot 只在调用方内存中（重启即消失）；
* 无 HTTP API / Dashboard / CLI；
* 无 Prometheus / OpenTelemetry / Grafana / 告警；
* 无维度聚合（by_tool / by_project / by_request）—— 属后续 Analytics；
* 无分位数（p50 / p90 / p95 / p99）/ min / stddev / 时间窗口（hour / day）；
* 无历史聚合 / 趋势 / 对比（不同 Snapshot 之间不合并）；
* 无并发度量语义：Metrics 无锁（只读一份 Records 快照；线程安全由
  Collector 保证 —— 正确用法是 collector.records() → snapshot）；
* 无生产接线：Collector / Metrics 由调用方显式装配（AIOrchestrator 仍
  context=None → 0 事件）；
* 无 Metrics 缓存：每次调用重新计算（O(n)，n = Records 数）。
```

---

## 7. Test Summary

```text
tests/test_tool_execution_metrics_service.py            60 passed
    Empty / Success / Failure / Mixed / Arithmetic / Rates / Duration /
    Zero（无 NaN / Infinity）/ Immutability / Invalid Input / Determinism /
    Collector 集成 / Security / Static Boundaries（无 IO · DB · 计时重测 ·
    执行能力 · 单例 · 持久化）/ DTO 不变式

tests/test_tool_chat_architecture_contract.py::TestC19*  13 passed
    C19.1 ~ C19.12 + 生产链未接线

定向（§二十七 7 文件：Record / Observer / Collector / Metrics /
      ExecutionService / FailureContract / Context）     338 passed / 10 skipped

全量 no DB：3113 passed / 338 skipped（0 failed）
    （Step 16 基线 3040 / 338 → +73 passed = 60（Metrics）+ 13（C19））

DB-gated：Environment DB unavailable
    psycopg.errors.ConnectionTimeout: connection timeout expired
    （host localhost:5432；TCP 探测 ConnectionRefusedError [WinError 10061]）
    → 未执行 DB 套件；未修改任何代码绕过（§二十七）
    → 本阶段 DB writes = 0（全程未发生数据库访问）

0 DB（本阶段自身）/ 0 Network / 0 LLM / 0 Tool 执行（全部纯内存）
```

## 8. 未修改 / 未引入

```text
未修改：ToolResult / ToolRegistry / Tool Handler / Router / AIOrchestrator /
        ToolArgumentExtractor / ToolChatService 业务语义 / ToolExecutionContext /
        ToolExecutionRecord / ToolExecutionObserver /
        InMemoryToolExecutionCollector 既有 Contract / DB schema /
        backend/app/api/**（API 不变）
未引入：Database / Repository / Migration / Redis / Kafka / Prometheus /
        OpenTelemetry / Grafana / Dashboard / HTTP API / WebSocket /
        Background Worker / Scheduler / Alert / Audit / 分布式 Event Bus /
        Agent / MCP / LangGraph / Memory / Planning / Retry / Fallback /
        Parallel Tool
```
