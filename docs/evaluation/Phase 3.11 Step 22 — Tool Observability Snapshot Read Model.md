# Phase 3.11 Step 22 — Tool Observability Snapshot Read Model

> 目标：建立**稳定的对外 Observability Snapshot Read Model**，使未来
> HTTP API / Dashboard 不需要直接暴露内部 `ToolExecutionRecord`。
>
> 本阶段：不做 HTTP API、不做数据库、不做持久化、不做 Dashboard。

```text
ToolExecutionRecord（内部 Execution Event，可演进）
        ↓ from_record()（显式逐字段映射）
ToolExecutionSnapshot（对外 Read Model，字段固定）
        ↓
ToolObservabilityQueryService.snapshots*()
        ↓
未来 HTTP API / Dashboard
```

---

## 1. Purpose

```text
Step 21 之后：Query Service 直接返回 ToolExecutionRecord（内部执行事件）。

问题：Record 是内部模型，未来可能因 tracing / retry / 维度需求新增字段；
      若上层直接消费 Record，"内部演进"会立刻变成"对外契约变更"。

本阶段只加一层稳定契约：

    ToolExecutionSnapshot = 对外 Read Model（独立 frozen DTO）
    Query Service 新增 snapshots*()，records*() 全部保留
```

---

## 2. Snapshot DTO

```text
backend/app/services/tool_observability_snapshot.py

@dataclass(frozen=True)
class ToolExecutionSnapshot:
    request_id    : str
    round         : int
    tool_name     : str
    started_at    : datetime
    finished_at   : datetime
    duration_ms   : float
    success       : bool
    project_id    : str | None = None
    tool_call_id  : str | None = None
    error_code    : str | None = None
    error_type    : str | None = None

    @classmethod
    def from_record(cls, record: ToolExecutionRecord) -> ToolExecutionSnapshot
    def assert_field_whitelist(self) -> None      # 字段固定 11 项自检
```

```text
* 字段与 Step 14 Record 一致，但**不是** Record（独立类型、独立文件）；
* 不持有 Record / Collector / Metrics Service（内部属性 = 11 个字段）；
* 不含任何 Metrics 字段（§六：Record Snapshot 与 Metrics Snapshot 是两个层级，
  不合并；metrics 仍由 ToolExecutionMetricsService 单独计算）；
* 使用项目既有 frozen DTO 风格（不是 dict / 可变 dataclass / Pydantic Model）。
```

---

## 3. Record → Snapshot

```text
ToolExecutionSnapshot.from_record(record)
    * 逐字段**显式**映射（record.request_id / record.round / …）；
    * 禁止 vars(record) / asdict(record) / **record.__dict__
      → Record 未来新增字段**不会**自动进入对外 Read Model（AST 层锁定）；
    * 纯函数：无 IO / 无 DB / 无 LLM / 无 Collector / 无 Registry / 无 HTTP；
    * deterministic（同一 Record 两次转换结果相等）；
    * 非 Record 输入 → TypeError。
```

---

## 4. Query Snapshot API

```text
ToolObservabilityQueryService（新增 4 个只读方法，原有 API 全部保留）

records()                     ← 不变（内部 Execution Record）
records_by_request_id()       ← 不变
records_by_project_id()       ← 不变
records_by_tool_name()        ← 不变
metrics()                     ← 不变（仍走 ToolExecutionMetricsService）

snapshots()                   → tuple[ToolExecutionSnapshot, ...]
snapshots_by_request_id(id)   → tuple[...]
snapshots_by_project_id(pid)  → tuple[...]（pid=None 只匹配 None）
snapshots_by_tool_name(name)  → tuple[...]（严格相等）
```

```text
转换链：
    collector.records()/records_by_*()
        ↓
    ToolExecutionSnapshot.from_record()
        ↓
    tuple[ToolExecutionSnapshot, ...]（新对象；不引用 Record）
```

---

## 5. Retention 一致性

```text
Collector max_records=3，写入 A B C D：

    collector.records()                  == (B, C, D)
    query.snapshots()                    == (B, C, D)     ← 一致
    query.snapshots_by_request_id("A")   == ()            ← 已淘汰，不可恢复
    query.metrics()                      → total=3（只统计 B C D）

Snapshot 层不缓存、不持有数据 → 每次调用读取当前窗口 → 天然一致（C24.11）。
```

---

## 6. 快照独立性

```text
snapshots = query.snapshots()
collector.on_execution(recordD)

    snapshots                 （旧快照）→ 仍为 A…（不含 D）
    query.snapshots()         （新调用）→ 才包含 D

且：
    * Snapshot 与 Record **无引用关系**（Record frozen，无法修改；Snapshot 复制值）
    * 旧快照在淘汰发生后同样保持稳定
```

---

## 7. Metrics Isolation

```text
Collector
 ├── records → Snapshot Read Model（对外）
 └── records → ToolExecutionMetricsService.snapshot()（聚合）

**不是**：Collector → Snapshot → Metrics（禁止）

验证：
    * Fake Metrics Service 收到的入参全部是 ToolExecutionRecord，
      **没有** ToolExecutionSnapshot（C24 / 测试用例锁定）；
    * query.metrics() == ToolExecutionMetricsService.snapshot(query.records())；
    * ToolExecutionMetricsService 零修改（方法集合仍为 {snapshot}）。
```

---

## 8. Security

```text
Snapshot 字段集合固定 11 项：
    无 arguments / SQL / LLM prompt / LLM response / DB 连接 / DB session /
    API key / password / Authorization header / traceback
模块 imports（AST 锁定）：__future__ / dataclasses / datetime / typing /
    tool_execution_record —— 无 sqlalchemy / psycopg / redis / kafka /
    celery / requests / httpx / os / subprocess / pathlib / api / llm / db / tools
repr(Snapshot)：无敏感串（postgresql:// / password / Bearer / api_key / Traceback / SELECT）
Query Service imports 仅新增 tool_observability_snapshot（其余不变）
```

---

## 9. C24

```text
C24.1  Snapshot 是独立 DTO
C24.2  Snapshot immutable（frozen）
C24.3  Snapshot 不持有 Record
C24.4  Snapshot 不持有 Collector
C24.5  Snapshot 不持有 Metrics Service
C24.6  字段显式映射
C24.7  不使用 vars / __dict__ / asdict 自动泄露字段
C24.8  不包含 Tool arguments
C24.9  不包含 SQL
C24.10 不包含 secrets
C24.11 retention window 与 Collector 一致
C24.12 旧 snapshot 不随 Collector 变化
C24.13 Metrics Service 不变化
C24.14 Query Service 原有 API 不变化
C24.15 Snapshot Query 是 read-only
```

---

## 10. 修改边界（本阶段）

```text
新增（生产）：backend/app/services/tool_observability_snapshot.py
修改（生产，仅增加 Snapshot 能力）：
    backend/app/services/tool_observability_query_service.py
        + snapshots() / snapshots_by_request_id / snapshots_by_project_id /
          snapshots_by_tool_name
        （records* / metrics / collector / metrics_service 全部不变）
新增（测试）：tests/test_tool_observability_snapshot.py（33）
契约：       tests/test_tool_chat_architecture_contract.py +C24（11）

未修改：ToolExecutionRecord / ToolExecutionMetricsService /
        InMemoryToolExecutionCollector（retention）/ ToolExecutionService /
        ToolExecutionObserver / ToolExecutionContext / AIOrchestrator /
        Router / ToolRegistry / ToolResult / ToolChatService /
        API（backend/app/api/** 0 修改）/ Composition Root / main.py / DB schema
未引入：HTTP API / Dashboard / WebSocket / Database / Redis / Kafka /
        Prometheus / OpenTelemetry / Audit / Event Bus / settings /
        环境变量 / 后台线程 / 定时任务 / 分页 / 排序 / filter DSL / 时间范围
```

---

## 11. Test Summary

```text
tests/test_tool_observability_snapshot.py                            33 passed
    DTO（5）/ Conversion（5）/ Independence（4）/ Query（8）/
    Retention（3）/ Metrics isolation（3）/ Security（5）

tests/test_tool_chat_architecture_contract.py::TestC24*               11 passed
    C24.1 ~ C24.15（部分合并为一例）

定向（Snapshot + Query + Collector + Metrics + 链路 B 契约）      377 passed
全量 no DB：3318 + 33 + 11 = 3362 passed / 337 skipped（0 failed）
（Step 21 基线 3318 / 337 → +44 = 33 + 11）

DB-gated（RUN_DB_TESTS=1，PostgreSQL 可达）：
    * 排除 4 个 knowledge 相关文件（test_knowledge_ingestion_service /
      test_knowledge_lifecycle_real / test_knowledge_lifecycle_smoke /
      test_knowledge_models）→ **3619 passed / 40 skipped（0 failed）**
    * 未排除时：knowledge 域 DB 用例先因**遗留数据状态**失败
      （如 test_knowledge_lifecycle_real::test_create_then_duplicate_returns_
      already_exists；Step 19/21 同现象 test_txt_full_pipeline_success），
      随后下一个 DB 用例挂起 → 全量无法跑完
    * 已单独验证真实 DB 的 Tool 链路：tests/test_get_inventory_tool.py +
      tests/test_get_work_order_tool.py → 125 passed（DB 环境本身可用）
    * 与本阶段无关（Step 22 为纯内存 Snapshot DTO + 只读查询）；
      按纪律**未**修改任何代码绕过，仅以排除方式取得回归信号
    * 本阶段生产代码零 DB 访问；DB writes = 0
```

---

## 12. Limitations

```text
* 无生产接线：Snapshot 查询未接入 Composition Root / 无单例 /
  无 FastAPI dependency / 无 HTTP 端点
* Snapshot 字段当前与 Record 高度一致（有意：先建立稳定契约，
  未来按 API 需要**显式**裁剪，而不是随 Record 自动变化）
* 无分页 / 排序 / 过滤 DSL / 时间范围 / 聚合维度（未来 HTTP Read Layer）
* 无序列化层（无 to_dict / JSON schema / Pydantic 导出）
* 无权限 / 租户过滤（未来 HTTP 层职责）
* 仅支持 In-Memory Collector（未来持久化时再抽象）
* lint unavailable（环境未安装 ruff / flake8，未新装工具）
```
