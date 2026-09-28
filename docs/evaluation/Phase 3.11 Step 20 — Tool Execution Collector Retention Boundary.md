# Phase 3.11 Step 20 — Tool Execution Collector Retention Boundary

> 目标：给 `InMemoryToolExecutionCollector` 建立**最小、明确、可测试**的
> retention / capacity contract（有限内存窗口），消除 Application lifetime
> 下 `records` 无限增长的内存风险。
>
> 只加一个概念：`max_records`（条数上限，FIFO 淘汰）。
> 不加 TTL / 时间窗口 / 定时清理 / 后台线程 / LRU / 分桶 / 持久化。

```text
Application Composition Root
        ↓
InMemoryToolExecutionCollector（Finite Retention Window）
        ↓
ToolExecutionObserver
        ↓
AIOrchestrator → ToolExecutionService → ToolRegistry → Tool
        ↓
ToolExecutionRecord
        ↓
Collector（最新 N 条）
        ↓
ToolExecutionMetricsService（只读当前窗口）
```

---

## 1. Purpose

```text
Step 19 起 Collector 是 Application lifetime 的对象：
    应用不重启 → Collector 不释放 → records 持续 append → 无界增长。

本阶段只回答一个问题：

    Collector 最多保留多少条 Record，超出后如何处理？

答案（第一版，最小契约）：

    max_records（int >= 1；默认 1000）
    超出 → FIFO 淘汰最旧 → 始终保留最新 N 条

不做：持久化 / TTL / 时间窗口 / 定时线程 / LRU / 按 project·tool 分桶 /
      Dashboard / HTTP Metrics API / Prometheus / OpenTelemetry / Audit。
```

---

## 2. max_records

```text
backend/app/services/in_memory_tool_execution_collector.py

DEFAULT_MAX_RECORDS = 1000

InMemoryToolExecutionCollector(max_records=DEFAULT_MAX_RECORDS)   # keyword-only
collector.max_records          # 只读属性（构造后不可变）

校验：
    bool（True / False）      → TypeError（isinstance(True, int) 为 True，
                               必须显式排除）
    非 int（1.5 / 1.0 / "100" / None）→ TypeError
    < 1（0 / -1）             → ValueError
```

配置来源（§十七）：**不做新的全局配置层**（无
`settings.tool.metrics.max_records` 之类）；retention 由构造参数显式决定；
当前 Composition Root（`backend/app/api/orchestrator_chat.py`）使用默认值，
且**未**被本阶段修改（`InMemoryToolExecutionCollector()` 无关键字参数，
C22.16 锁定）。

---

## 3. Default

```text
default max_records = 1000
```

```text
兼容性依据（先检查既有使用方式，再决定默认值）：
    * 既有测试与调用方全部使用 InMemoryToolExecutionCollector() 默认构造；
    * 单次测试 / 单次 chat 的 append 数量最大为 O(10)（多步 Tool Calling /
      并发 smoke 为 200 条）；
    * 1000 远大于既有全部用例的 append 量 → 既有语义**零变化**
      （无既有断言依赖"无界"）；
    * 同时把"无界增长"变成"有限窗口"，无需任何调用方改动。
```

---

## 4. FIFO semantics

```text
append（on_execution）：
    deque(maxlen=max_records).append(record)      # O(1)
    → 超限时自动从左侧淘汰最旧（FIFO）            # O(1)

max_records = 3 的示例：
    A B C            （写入 A/B/C）
    B C D            （写入 D → 淘汰 A）
    C D E            （写入 E → 淘汰 B）

保持不变式：records() 永远 == 最新 min(写入数, max_records) 条。
淘汰只由**条数**触发（与时间无关）；
读取（records / records_by_*）**不**刷新顺序 → FIFO，不是 LRU。
```

被淘汰的 Record：

```text
Evicted Record cannot be recovered.
    * 无法通过 records() / records_by_request_id() /
      records_by_project_id() / records_by_tool_name() 取回；
    * Metrics 也看不到（只读当前窗口）；
    * 不做落盘 / 不做归档 → 淘汰 = 内存释放，不可恢复。
```

---

## 5. Query semantics

```text
records()                     → 当前窗口全部（写入顺序；tuple）
records_by_request_id(id)     → 仅当前窗口内匹配
records_by_project_id(pid)    → 仅当前窗口内匹配（pid=None 只匹配 None）
records_by_tool_name(name)    → 仅当前窗口内匹配（严格相等）

全部查询：
    * 返回 tuple（不可变快照；调用方无法 append / clear / pop）；
    * 快照在淘汰后仍然稳定（旧快照 == 淘汰前的元组）；
    * O(n)（n <= max_records）。
```

---

## 6. Clear semantics

```text
collector.clear()
    → 清空当前 retention window（records() == ()）
    → 之后可继续 append，且仍受 max_records 约束
    → 只有调用方显式调用；**无自动 clear**（执行路径 0 次调用 clear()）

clear 后的 Metrics（未修改的 Metrics Service）：
    total_count = 0 / success_rate = None /
    average_duration_ms = None / max_duration_ms = None
```

---

## 7. Thread safety

```text
仍是**单个** threading.Lock（与项目既有 In-Memory 组件一致）：
    * append + FIFO 淘汰在同一临界区内完成（deque 语义）；
    * 查询 = 锁内取 tuple 快照，锁外过滤；
    * 任何时刻 records() 都不会超过 max_records
      （并发写 + 并发读实测 max(len) <= max_records）；
    * 快照内无重复对象（id 唯一）、每条 Record 字段完整（白名单自检通过）。
```

---

## 8. Metrics semantics

```text
ToolExecutionMetricsService（**零修改**）：
    snapshot(collector.records()) → 只统计**当前 retention window**

示例（max_records=3，写入 A success / B success / C failure / D success）：
    窗口 = B C D
    total_count = 3 / success_count = 2 / failure_count = 1
    —— 已淘汰的 A **不参与**任何统计（不"知道"它曾经存在）
```

Retention ≠ Persistence（本阶段核心结论）：

```text
Retention = 内存窗口（有界、FIFO、可恢复性 = 无）
Persistence = NO（无 DB / 文件 / Redis / Kafka / 归档）
```

---

## 9. Security

```text
Collector 仅持有：{"_max_records", "_records", "_lock"}
    —— 无 Engine / Session / connect / execute / commit（无 DB）
    —— 无 Registry / Handler（无 Tool 执行能力）
    —— 无 LLM client / prompt（无 LLM）
    —— 无 json / pathlib / os / pickle（无持久化）
淘汰**不修改** Record（frozen DTO；字段逐字节不变）；
Retention 不引入新依赖（import 白名单：__future__ / threading /
    collections / collections.abc / tool_execution_record）；
无 TTL / 无 threading.Timer / 无 asyncio.create_task / 无后台线程。
```

---

## 10. C22

```text
C22.1  有限 max_records（默认 1000；可显式指定）
C22.2  max_records 为正 int；bool 拒绝（TypeError）；0 / -1 → ValueError
C22.3  淘汰策略 = FIFO（读取不刷新顺序，非 LRU）
C22.4  始终保留最新 N 条
C22.5  被淘汰 Record 对所有查询 API 不可见
C22.6  records() 仍为不可变 tuple 快照
C22.7  retention 线程安全（并发 append + 淘汰原子）
C22.8  clear() 显式清空当前窗口
C22.9  无自动 clear（执行路径 0 次；Collector 内仅 self._records.clear）
C22.10 无 TTL（无 time / monotonic / expires / deadline）
C22.11 无后台线程（仅 threading.Lock）
C22.12 Metrics 只看到保留窗口
C22.13 ToolExecutionRecord 不可变、从不被修改
C22.14 无持久化
C22.15 无 DB / LLM / Tool 依赖
C22.16 无 API 变化（ChatRequest / ChatResponse 不变；组合根不注入 retention）
C22.17 ToolResult 不变
C22.18 AIOrchestrator 执行语义不变（无 retention 感知）
```

---

## 11. Limitations

```text
* 无 TTL / 时间窗口：retention 只看条数（时间维度 = 后续阶段，若确有需要）；
* 无按 project / tool 分桶容量；无 LRU（有意保持 FIFO 简单语义）；
* 无持久化 / 无归档：淘汰即不可恢复（Evicted Record cannot be recovered）；
* 无容量治理配置层（无 settings.*.max_records；显式构造参数优先）；
* 无淘汰指标（evicted_count / drop 日志）= 后续阶段（需先有明确使用方）；
* 生产装配使用默认 1000（Composition Root 未改动）；
  多进程 / 多实例各自独立窗口（无跨进程聚合）；
* `backend/app/api/orchestrator_chat.py` 的 Step 19 注释仍写着
  "无 max_records"（该文件属禁止修改范围，本阶段未改动）→ 以本文件与
  architecture §8.38 为准；
* lint 工具不可用（环境未安装 ruff / flake8，未新装工具）。
```
