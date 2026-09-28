# Phase 3.11 Step 21 — Tool Observability Read Query Boundary

> 目标：为未来 HTTP API / Dashboard / Persistence 提供一个**稳定、只读**的
> 应用层读边界，避免上层直接依赖 `InMemoryToolExecutionCollector` 的内部查询方法。
>
> 本阶段：不做 HTTP API、不做数据库、不做 Redis、不做 Dashboard。

```text
ToolExecutionRecord
        ↓
InMemoryToolExecutionCollector（Record 存储 + Retention + 查询原语）
        ↓
有限 Retention Window
        ↓
ToolObservabilityQueryService（**Read Facade**）
        ├── records()
        ├── records_by_request_id / records_by_project_id / records_by_tool_name
        └── metrics() → ToolExecutionMetricsSnapshot
```

---

## 1. Purpose

```text
Step 20 之后，Collector 既是"观测出口"又是"查询 API"：
    上层（未来 HTTP / Dashboard）若直接调用 collector.records() /
    records_by_*()，就会和 In-Memory 实现、retention 细节、锁语义耦合，
    未来换持久化 / 聚合存储时改动面不可控。

本阶段只做一个薄层：

    ToolObservabilityQueryService = 只读查询 Facade（无状态、无存储、无算法）

它不替代 Collector，也不替代 Metrics Service —— 只是把它们组合成
"未来上层唯一应该依赖的读入口"。
```

---

## 2. API（第一版，最小）

```text
backend/app/services/tool_observability_query_service.py

ToolObservabilityQueryService(
    collector,                                   # InMemoryToolExecutionCollector
    metrics_service=ToolExecutionMetricsService, # 可注入 Fake（测试）
)

records()                       → tuple[ToolExecutionRecord, ...]
records_by_request_id(id)       → tuple[...]
records_by_project_id(pid)      → tuple[...]（pid=None 只匹配 None）
records_by_tool_name(name)      → tuple[...]（严格相等）
metrics()                       → ToolExecutionMetricsSnapshot

只读属性：collector / metrics_service
**无** clear() / on_execution() / append() —— 只提供读能力（§十二）
```

```python
collector = InMemoryToolExecutionCollector()
query = ToolObservabilityQueryService(collector)
query.records()
query.metrics()
```

---

## 3. 不是什么（边界）

```text
不是第二份存储：内部只有 {_collector, _metrics_service} 两个引用；
    无 list / deque / dict / 缓存 / 索引（源码层锁定）
不是第二份 retention：不实现 FIFO / 淘汰 / max_records / 锁
不是第二份过滤：过滤、顺序、字段校验全部由 Collector 决定（原样委托）
不是第二份统计：metrics() 只调用 ToolExecutionMetricsService.snapshot()
不是 Repository / Port / Store Protocol（§十四：不过度抽象；
    未来真正需要 DB / Redis 时再抽象）
```

---

## 4. Retention 一致性（§七）

```text
Collector max_records = 3，写入 A B C D：

    collector.records()   == (B, C, D)      ← 权威
    query.records()       == (B, C, D)      ← 完全一致
    query.records_by_request_id(A) == ()    ← 已淘汰，不可恢复
    query.metrics()       → total=3 / success=2 / failure=1（只统计 B C D）
```

Query Service **不**持有快照、**不**缓存结果：每次调用都重新读取
Collector 当前窗口 → retention 语义天然一致（C23.7）。

---

## 5. Metrics 行为（§十）

```text
query.metrics()
    ↓
ToolExecutionMetricsService.snapshot(collector.records())
    ↓
ToolExecutionMetricsSnapshot（Step 17 DTO，零修改）

* 统计语义（空数据集 None 比率 / 时长均值 / 最大值）与直接调用
  Metrics Service 完全等价（测试用等式断言锁定）；
* 不复制任何 arithmetic（源码层无 sum / len / max / *_count / *_rate 标识符）；
* metrics_service 可注入 Fake → 测试可证明"委托"（返回 Fake 的哨兵快照）。
```

---

## 6. Read-only Contract（§八 / §九）

```text
返回值：tuple[ToolExecutionRecord, ...]（不可变快照；不是 list / deque /
       iterator / generator；调用方无法 append / clear / pop）
旧快照稳定：拿到快照后再 append / 淘汰，旧快照内容不变
不修改 Collector：records / 三个过滤查询 / metrics 全部执行后
    collector.records() 与 before 完全一致（条数、顺序、max_records 均不变）
无 clear：Query Service 不提供任何写能力（collector.clear() 仍是
    显式内部生命周期操作；Query 层只读，未来不会暴露 DELETE 端点）
```

---

## 7. Security

```text
输出只有两种类型：
    ToolExecutionRecord（Step 14 字段白名单 11 项）
    ToolExecutionMetricsSnapshot（Step 17 字段白名单 8 项）
不含：Tool arguments / LLM prompt / SQL / DB 连接 / API key /
      password / Authorization header（repr 级断言锁定）
依赖方向（§十三）：
    API / Dashboard（未来）→ Query Service → Collector
    **不允许**反向（Collector 不知道 API；Query Service 不 import API）
imports（源码锁定）：typing / collections 相关无 / 三个 Service 模块；
    无 sqlalchemy / psycopg / redis / kafka / celery / requests / httpx /
    os / subprocess / pathlib / backend.app.api / llm / db / tools
无新增 settings / 环境变量 / 表 / migration / 后台线程 / 定时任务 / TTL
```

---

## 8. C23

```text
C23.1  Query Service 只读（无写方法）
C23.2  不持有自己的 Record storage
C23.3  不实现 Retention
C23.4  不实现 FIFO
C23.5  不实现 Metrics arithmetic
C23.6  records 返回 immutable snapshot
C23.7  retention window 语义与 Collector 一致
C23.8  query 不修改 Collector
C23.9  不暴露 clear
C23.10 不依赖 DB
C23.11 不依赖 LLM
C23.12 不依赖 Tool Registry
C23.13 不依赖 API
C23.14 不暴露 secrets / SQL / arguments
C23.15 Metrics 复用现有 Metrics Service
```

---

## 9. 修改边界（本阶段）

```text
新增（生产，唯一）：backend/app/services/tool_observability_query_service.py
新增（测试）：      tests/test_tool_observability_query_service.py（37）
契约：             tests/test_tool_chat_architecture_contract.py +C23（11）
文档：             本文件 + docs/architecture.md §8.39

未修改：InMemoryToolExecutionCollector（retention 行为零变化）/
        ToolExecutionService / ToolExecutionRecord / ToolExecutionObserver /
        ToolExecutionMetricsService / AIOrchestrator / Router / ToolRegistry /
        ToolResult / ToolChatService / API / main.py / DB schema
未引入：HTTP API / Dashboard / WebSocket / Database / Redis / Kafka /
        RabbitMQ / Prometheus / OpenTelemetry / Audit / Event Bus /
        settings / 环境变量 / 后台线程 / 定时任务 / TTL /
        Repository / Port / Store Protocol
```

---

## 10. Test Summary

```text
tests/test_tool_observability_query_service.py                      37 passed
    Empty（3）/ Single（3）/ Multiple（5）/ Retention（4）/
    No mutation（3）/ Snapshot（3）/ Clear isolation（3）/
    Metrics reuse（5）/ Security & structure（5）/ 构造校验（3）

tests/test_tool_chat_architecture_contract.py::TestC23*              11 passed
    C23.1 ~ C23.15（C23.3+4 与 C23.10~13 各合并为一例）

定向（Query + Collector + Metrics）                               195 passed
全量 no DB：3270 + 37 + 11 = 3318 passed / 337 skipped（0 failed）
（Step 20 基线 3270 / 337 → +48 = 37 + 11）

DB-gated（RUN_DB_TESTS=1）：**排除** tests/test_knowledge_ingestion_service.py
    → 3601 passed / 41 skipped（0 failed）
    （3566 + 48 − 13（该文件 DB 用例）= 3601；与预期完全一致）
    未排除时：该文件的 DB 用例先因数据状态失败
    （test_txt_full_pipeline_success：assert 'already_exists' == 'ready' ——
      knowledge_document 中已有遗留 fixture 文档），随后下一个 DB 用例挂起，
    全量套件无法跑完。属既有（Step 19 已记录）的 DB 数据状态 / 跨用例耦合问题，
    与 Step 21（新增只读查询 Facade）无关；按纪律**未**修改任何代码绕过。
```

---

## 11. Limitations

```text
* 无生产接线：Query Service 由测试 / 未来装配显式构造；
  未加入 Composition Root（api/orchestrator_chat.py 本阶段**未**修改）、
  无 FastAPI dependency / lifespan / 单例
* 无 HTTP API（GET /api/...）—— 仅建立 Application Read Boundary
* 无聚合 / 维度分析（by_tool / by_project / 时间窗口 = 后续阶段）
* 无过滤 DSL / 分页 / 排序（第一版只暴露 Collector 既有查询原语）
* 无权限 / 租户隔离（未来 HTTP 层职责）
* 仅支持 In-Memory Collector（依赖具体类型；未来持久化时再抽象）
* Query 不缓存：每次调用重新读取当前窗口（O(n) 快照）
* lint 工具不可用（环境未安装 ruff / flake8，未新装工具）
```
