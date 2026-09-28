# Phase 3.11.29 — Tool Observability Persistent Query Boundary

> Step 29 建立 **Persistent Observability 的只读 Query Boundary**
> （与 Runtime Query **并列**，本阶段**不合并**）。
>
> `HTTP API = unchanged` · `History API = NOT IMPLEMENTED` ·
> `Metrics = NOT IMPLEMENTED` · `Pagination = NOT IMPLEMENTED` ·
> `Filtering = NOT IMPLEMENTED` · `Retention = NOT IMPLEMENTED` ·
> `Dashboard = NOT IMPLEMENTED`

---

## 1. Goal

```text
Persistence（Step 27/28）= WRITE READY
    ↓
Step 29：让**已写入**的 Tool 执行事实可以被只读查询：
    PostgreSQL → Repository → Persistent Query Service → Snapshot Read Model

且：不改 HTTP API / 不改执行链 / 不改 Observer / 不改 Collector /
    不做 Metrics / 不做分页 / 不做过滤 / 不做 retention
```

---

## 2. Current Runtime Query（未变）

```text
ToolObservabilityQueryService（Step 21；API 仍只用它）
        ↓
InMemoryToolExecutionCollector（process-local；max_records=1000）
        ↓
ToolExecutionSnapshot / ToolExecutionMetricsSnapshot
```

---

## 3. Persistent Query（新增）

```text
ToolExecutionPersistentQueryService
        ↓
ToolExecutionRepository.list_recent(limit=...)
        ↓
SELECT id, request_id, round, tool_name, started_at, finished_at,
       duration_ms, success, project_id, tool_call_id, error_code,
       error_type
  FROM ai_ops.tool_execution_record
 ORDER BY started_at DESC, id DESC
 LIMIT :limit
        ↓
PostgreSQL
```

```text
backend/app/services/tool_execution_persistent_query_service.py
    ToolExecutionPersistentQueryService
        __init__(repository: ToolExecutionRepository | None = None)
        list_recent(*, limit: int = DEFAULT_RECENT_LIMIT=100)
            -> list[ToolExecutionSnapshot]

backend/app/db/tool_execution_repository.py（Step 29 新增最小只读查询）
    MIN_RECENT_LIMIT = 1 / MAX_RECENT_LIMIT = 1000（模块级常量；无全局配置）
    build_recent_select(*, limit) -> Select        （唯一 recent SQL 构造点）
    list_recent(*, limit: int = 100) -> list[ToolExecutionRecordRow]
```

分层（§五）：

```text
Query Service = application read boundary（不 import SQLAlchemy / Session / ORM）
Repository    = persistence boundary（唯一 SQL 构造点）
```

---

## 4. Read Model（Row → Snapshot，显式映射）

```text
ToolExecutionRecordRow（Repository 内部 frozen record；含 id）
        ↓ _to_snapshot()（**显式逐字段映射**；不用 vars / asdict / __dict__ / model_dump）
ToolExecutionSnapshot（Step 22 对外 Read Model；**严格 11 字段**）

主键 id **不外泄**（仅供 Repository 排序 / tie-breaker）；
不新增 created_at / database_name 等字段。
```

---

## 5. Ordering

```text
ORDER BY started_at DESC, id DESC

理由：started_at 可能相同（同毫秒批量执行）→ 主键 id 提供
      **deterministic tie-breaker**；无稳定排序的 LIMIT 查询返回顺序不确定。
实测（DB）：同一 started_at 的两条记录 → 后写入（id 更大）在前；
            两次查询结果完全一致。
```

## 6. Limit

```text
MIN_RECENT_LIMIT = 1     MAX_RECENT_LIMIT = 1000     DEFAULT = 100
（模块级常量，**不新增全局配置**；非分页语义 —— 单次有界查询）

非法 → ValueError（参数错误；**不触达数据库**）
      Repository 与应用层**双重校验**（defense in depth）
说明：项目既有 MAX_QUERY_LIMIT = 100 属 LLM Usage 的**分页**契约；
      本方法不是分页 API（无 offset / cursor），故使用独立常量，
      但仍远小于「整表读取」，防止误用造成无界查询。
```

## 7. Security

```text
只读：SELECT only（无 INSERT / UPDATE / DELETE / TRUNCATE / DDL）
显式列（禁用 SELECT *）；列 = 主键 + Record 11 字段
返回：仅 ToolExecutionSnapshot 的 11 个字段
无 tool_arguments / ToolResult.data / SQL / prompt / LLM response /
  API key / password / DATABASE_URL / connection string /
  exception message / traceback
JSON 序列化仍属 Step 24 的 Serialization Boundary（本层不做 dumps / isoformat）
```

## 8. Error Semantics（§十四 / §十五）

```text
成功但无数据        → []（不是 None）
DB 未配置 / 查询失败 → ToolExecutionRepositoryError **原样透传**
                      （**不** return [] —— DB failure ≠ empty database）
limit 非法          → ValueError（不触达 DB）
沿用项目约定（与 LLMUsageQueryService 一致：RepositoryError 原样透传，
不新造异常体系；不改写应用异常）
```

## 9. Tests

```text
tests/test_tool_execution_repository.py                     40 passed（+12 = list_recent Case A~F）
    Case A 行序透传 / Case B SQL ORDER BY started_at DESC, id DESC /
    Case C limit=1 / Case D limit=MAX / Case E invalid limit（0 / -1 / MAX+1 /
    999999999 / "10" / 1.5 / True → ValueError 且不触达 DB）/ Case F 空 → []
    + 默认 limit=100 / 查询失败 → RepositoryError（非 []）/
      读不开写事务 / 显式列 / 不返回 ORM

tests/test_tool_execution_persistent_query_service.py       20 passed
    1 记录 → Snapshot / 2 显式映射 / 3 11 字段 + 主键不泄漏 / 4 None 保留 /
    5 空 → [] / 5b 失败记录映射 / 6 默认 limit / 7 边界 min·max /
    8 invalid limit（不触达 repository）/ 9 RepositoryError 原样透传 /
    9b 意外异常透传 / 9c 单次查询无 retry / 10 不返回 ORM /
    12 deterministic / 12b 顺序由 Repository 决定 /
    静态 无 JSON / 无 SQLAlchemy·ORM / 无 Collector·执行链 /
        只读 API（无 create·insert·update·delete·count·sum）

tests/test_tool_execution_persistent_query_service_db.py     9 passed（DB-gated）
    Case 1 recent 排序（新→旧）/ Case 2 limit=2 / Case 3 同 started_at →
    id DESC 且两次一致 / Case 4 NULL → None / Case 5 空表 → [] /
    Case 6 查询失败 → RepositoryError / + 11 字段 + 无 id /
    + timezone 保留 / + 未触碰 llm_usage_record

tests/test_tool_observability_persistence_architecture.py    45 passed（含 C35 = 10）
全量 no DB                                        3610 passed / 366 skipped（0 failed）
  （Step 28 基线 3569 / 357 → +41 = 12 + 20 + 10；skipped +9 = 新增 DB 文件）
```

## 10. DB Residue

```text
0（teardown TRUNCATE **本表** RESTART IDENTITY）
未触碰 ai_ops.llm_usage_record / public.* / 未删除 ai_ops schema
测试数据全部 synthetic（step29-test-* / get_inventory / test-project）
未查询真实库存 / 工单 / WMS 数据
```

## 11. HTTP API

```text
HTTP API = unchanged
    GET /api/observability/tools           （仍读 InMemoryCollector）
    GET /api/observability/tools/metrics   （仍读内存 Metrics）

未新增 /api/observability/tools/history、/persistent 或任何端点；
HTTP API 当前**不依赖** Persistent Query Service（C35.8）。
```

### 为什么本阶段不改 API

```text
Runtime View      = recent process-local observations（本进程近期执行）
Persistent View   = long-term database history（跨进程 / 跨重启）

两者目前**并列**（parallel），不是 fallback，也不是 merged。
若强行合并会立即引入：
    * 去重（同一 Record 两边都有）
    * 排序语义冲突（插入序 vs started_at 序）
    * 窗口语义冲突（内存 FIFO 窗口 vs 数据库全量）
    * 分页语义冲突（内存无分页 vs 数据库有 LIMIT）
→ 因此 Step 29 刻意只建立边界，不合并、不接 API（交由后续独立阶段设计）。
```

## 12. Deferred

```text
HTTP History API
Pagination（offset / cursor / page）
Filtering（project_id / tool_name / success / 时间范围 / 关键词）
Persistent Metrics（COUNT / SUM / AVG / success_rate）
Retention（TTL / cleanup / 归档）
Dashboard / Prometheus / OpenTelemetry
```

---

## 13. 修改边界

```text
新增：backend/app/services/tool_execution_persistent_query_service.py
      tests/test_tool_execution_persistent_query_service.py（20）
      tests/test_tool_execution_persistent_query_service_db.py（9，DB-gated）
      docs/evaluation/Phase 3.11.29 — …md
修改：backend/app/db/tool_execution_repository.py（+ list_recent /
      build_recent_select / limit 常量 —— 最小只读新增）
      tests/test_tool_execution_repository.py（+12；公共 API 断言同步）
      tests/test_tool_observability_persistence_architecture.py
          （+C35（10）；C33.6 allowlist + C33.8 API 断言同步）
      docs/decisions/ADR-3.11.26-…md（+ Implementation Status）
      docs/architecture.md（§8.47）
未修改：ToolExecutionService / ToolExecutionRecord / Observer /
        PersistenceAdapter / PersistenceService / InMemoryCollector /
        ToolObservabilityQueryService / Snapshot DTO / Metrics DTO /
        Serialization / HTTP API / ORM Model / 既有 Repository 写路径
```
