# Phase 3.11.33 — Tool Observability Persistent Metrics

> Step 33 增加 **Persistent（数据库侧）Tool Execution Metrics Read Model**：
> 只读 SQL 聚合 + 与 History 一致的精确过滤。
> Runtime Metrics 与 Persistent Metrics **并列**（不合并、不 fallback）。

---

## 1. Persistent Metrics

```text
GET /api/observability/tools/metrics/persistent
    ?project_id=…&tool_name=…&success=…

        ↓
ToolExecutionPersistentQueryService.metrics(project_id, tool_name, success)
        ↓
ToolExecutionRepository.get_metrics(...)        （SQL 聚合，单行）
        ↓
PostgreSQL：ai_ops.tool_execution_record
```

## 2. Metrics DTO

```text
复用既有 frozen DTO：ToolExecutionMetricsSnapshot（8 字段，**未新增 DTO**）

    total_count / success_count / failure_count /
    success_rate / failure_rate /
    total_duration_ms / average_duration_ms / max_duration_ms

空数据集语义（与 Runtime Metrics 完全一致）：
    total_count = 0；success_count = 0；failure_count = 0
    success_rate = None / failure_rate = None        （不是 0）
    average_duration_ms = None / max_duration_ms = None
    total_duration_ms = 0.0
（SQL 中 SUM/AVG/MAX 对空集为 NULL → AVG/MAX 保持 None；
  空 SUM 归一为 0.0 —— "无耗时记录"，与 Runtime Metrics 语义一致）
```

## 3. Repository

```text
backend/app/db/tool_execution_repository.py
    build_metrics_select(*, project_id=None, tool_name=None, success=None) -> Select
    get_metrics(*, project_id=None, tool_name=None, success=None)
        -> ToolExecutionMetricsRow（frozen；**原始聚合行**，非 ORM / 非 API DTO）
    _apply_exact_filters(...)      （recent 与 metrics 共用的唯一过滤构造点）

SQL（实测）：
    SELECT count(*)                                    AS total_count,
           count(*) FILTER (WHERE success IS TRUE)      AS success_count,
           count(*) FILTER (WHERE success IS FALSE)     AS failure_count,
           sum(duration_ms)                             AS total_duration_ms,
           avg(duration_ms)                             AS average_duration_ms,
           max(duration_ms)                             AS max_duration_ms
    FROM ai_ops.tool_execution_record
    [WHERE project_id = :p] [AND tool_name = :t] [AND success = :s]

* 无过滤 → **不生成 WHERE**（无 WHERE 1=1）；条件 AND；
* 过滤值全部 bound parameters（无字符串拼接 / 无 text() 拼接用户输入）；
* 无 ORDER BY / LIMIT / OFFSET（聚合单行）；无 GROUP BY；无 SELECT *；
* **不做** Python 全量扫描后再统计；
* 空数据集 → 计数 0、SUM/AVG/MAX 为 NULL（→ None；不伪装 0）；
* DB 失败 → ToolExecutionRepositoryError（**不**返回 0 指标）。
```

## 4. PersistentQueryService

```text
list_recent(...)   （Step 29/31/32；未变）
metrics(*, project_id=None, tool_name=None, success=None)
    -> ToolExecutionMetricsSnapshot

职责：validate filters → Repository.get_metrics() → 比率计算 → DTO
* 比率在**应用层**计算（Repository 只给原始聚合；不知道 API response）；
* 无 Python 过滤 / 排序 / 聚合 / 全量扫描；
* 不访问 ORM / Session / SQLAlchemy；不创建数据库连接；
* 参数校验在触达数据库之前（非法 → ValueError）。
```

## 5. API

```text
GET /api/observability/tools/metrics/persistent
    Query: project_id(str|None) · tool_name(str|None) · success(bool|None)
    （**无** limit / offset / cursor / page）

Response 200（有数据）：
{
  "total_count": 5, "success_count": 3, "failure_count": 2,
  "success_rate": 0.6, "failure_rate": 0.4,
  "total_duration_ms": 1500.0, "average_duration_ms": 300.0,
  "max_duration_ms": 500.0
}

Response 200（空数据集）：计数 0；rates / 均值 / 最大值 null；
                            total_duration_ms 0.0
422：非法过滤值（如 success=maybe）
502：数据库未配置 / 查询失败（`"Tool 持久观测指标不可用"`）
500：其它未预期异常（`"Tool 观测数据不可用"`）

响应**不含** request_id / project_id / tool_name / tool_call_id /
arguments / result / SQL / prompt / 凭据 / traceback / rows。
```

## 6. Filtering

```text
与 History（Step 32）**完全一致**的精确过滤语义：
    project_id = :p（精确；无 LIKE / ILIKE / lower / trim / fuzzy）
    tool_name  = :t（精确；无 contains / startswith / regex）
    success    = true | false（False **≠** 省略；None = 不追加条件）
条件之间 AND；""（空字符串）是普通字符串值
无匹配 → 计数 0 + rates/avg/max = None（**不是** 404）
```

## 7. Runtime / Persistent Isolation

```text
Runtime                                   Persistent
──────────────────────                    ──────────────────────
GET /api/observability/tools/metrics      GET /api/observability/tools/metrics/persistent
      ↓                                         ↓
InMemoryCollector（本进程内存）            PostgreSQL（跨进程 / 跨重启）
      ↓                                         ↓
ToolExecutionMetricsService.snapshot()    Repository SQL 聚合 → Service 比率

parallel —— **not** merged —— **no** fallback
实测：持久侧 DB 失败 → 502，且响应不含任何内存指标；
      同时 /api/observability/tools/metrics 仍 200（读内存，未受污染）
```

## 8. Security

```text
只读：SQL 仅 COUNT / COUNT FILTER / SUM / AVG / MAX（无 INSERT/UPDATE/DELETE/DDL）
响应字段严格 = 8 个指标字段（无 identifier / 无 Snapshot 字段 / 无 args / 无 SQL）
过滤值 bound parameters；注入串实测（"' OR 1=1 --"）→ 计数 0 且表未被破坏
Service / API 不 import SQLAlchemy / Session / Collector（AST 断言）
无 LIKE / ILIKE / regex / fuzzy / 关键词 / 时间范围 / 分页参数
```

## 9. Architecture C39

```text
tests/test_tool_observability_persistence_architecture.py  85 passed（含 C39 = 12）
    C39.1  Persistent Metrics API 存在（OpenAPI + 200/502）
    C39.2  Runtime Metrics API 不变（/metrics 0 参数；新端点 3 参数）
    C39.3  API 只经 PersistentQueryService（无 collector / on_execution）
    C39.4  Service 不访问 ORM / Session / SQLAlchemy
    C39.5  Repository 执行 SQL 聚合（count/sum/avg/max + FILTER；无 ORDER BY/LIMIT）
    C39.6  过滤 bound parameters + 精确（无 LIKE / ILIKE / OR 1=1）
    C39.7  无 Python 全量聚合（无 sum/min/max/len/推导式；只读 API 白名单）
    C39.8  无 SELECT * / 无无关列（request_id / started_at / …）
    C39.9  无 LIKE / ILIKE / regex / contains / startswith / fuzzy
    C39.10 无 fallback / merge（DB 失败 → 502；Runtime 端点不受影响）
    C39.11 响应不含 Snapshot / 敏感字段
    C39.12 无 limit / offset / cursor / page
```

## 10. Unit Tests

```text
tests/test_tool_execution_repository.py                    65 passed（+11 metrics）
    SQL 形态（count(*) / FILTER / sum·avg·max；无 GROUP BY·ORDER BY·LIMIT）/ 
    无过滤 → 无 WHERE / 三过滤 AND + bound params / success true·false·None /
    注入串为字面值（无 OR 1=1、无 ;）/ get_metrics 映射（非 ORM）/
    空行保持 None / 非法类型 → ValueError（不触达 DB）/
    DB 失败 → RepositoryError（非 0 指标）/ 读路径不开写事务 /
    聚合只出现在 build_metrics_select（其它方法无 count·sum·avg·max）

tests/test_tool_execution_persistent_query_service.py      41 passed（+11 metrics）
    空语义（rates/avg/max None；total 0.0）/ 全成功（1.0 / 0.0）/
    混合（0.6 / 0.4）/ 过滤透传 / success=False ≠ 缺失 / "" 字面值 /
    非法过滤 → ValueError（不触达 repository）/ RepositoryError 透传 /
    单次调用无 retry / metrics 内无 Python 聚合（AST）

tests/test_tool_observability_api.py                       68 passed（+12 metrics）
    空响应 / 有数据响应 / 过滤透传（3 参数）/ success=false → False /
    注入串字面透传 / success=maybe → 422（未触达 Service）/
    DB 失败 → 502 且无内存回退 / 未预期异常 → 500 /
    字段白名单（8 字段，无 identifier·args·sql·rows）/ 无 limit·offset 参数 /
    Runtime Metrics 在持久侧故障时仍 200（调用次数不变）/ 只读（Collector 不变）
```

## 11. DB Tests

```text
tests/test_tool_observability_persistent_metrics_db.py   13 passed（RUN_DB_TESTS=1）
                                                         13 skipped（默认）
    A~E synthetic 矩阵（project-a/b × get_inventory/get_work_order × success/fail；
    duration 100/200/300/400/500）：
        全量（5 / 3 / 2；0.6 / 0.4；sum 1500；avg 300；max 500）/
        project-a、project-b / get_inventory、get_work_order /
        success=true（1.0 / 0.0）、success=false（0.0 / 1.0）/
        组合（project+tool / project+success / 三元组）/
        未知值 → 计数 0 + None 比率 / "" 无匹配 /
        注入串 → 计数 0 且表未被破坏 / success=maybe → 422 /
        limit·offset 被忽略（聚合单行）/
        字段白名单 / Runtime 端点不受影响 / llm_usage 未触碰
清理：**不用 TRUNCATE**；按 request_id 前缀 step33- 定向 DELETE 并断言归零
```

## 12. 全量测试

```text
全量 no DB                                3741 passed / 406 skipped（0 failed）
  （Step 32 基线 3689 / 393 → +52 = 11 + 11 + 12 + 12（C39）+ 6 契约同步；
    skipped +13 = 新增 DB 文件）
DB-gated 定向（6 文件）                    630 passed
```

## 13. compile / LSP / lint

```text
python -m compileall -q backend tests → OK（0 errors / 0 warnings）
LSP diagnostics（7 个改动文件）→ 0 error / 0 warning
lint → unavailable（环境未安装 ruff / flake8；未新增工具）
```

## 14. DB residue

```text
0 —— 测试行（step33-*）按前缀定向 DELETE 后计数 0；
     表总行数 0（本环境无其它数据）
未 TRUNCATE；未删除真实数据；未触碰 ai_ops.llm_usage_record / public.*
```

## 15. 修改文件

```text
backend/app/db/tool_execution_repository.py（+ ToolExecutionMetricsRow /
    _apply_exact_filters / build_metrics_select / get_metrics / _to_metrics）
backend/app/services/tool_execution_persistent_query_service.py（+ metrics()）
backend/app/api/tool_observability.py（+ /metrics/persistent 端点）
tests/test_tool_execution_repository.py（+11；公共 API 白名单同步）
tests/test_tool_execution_persistent_query_service.py（+11；fake + metrics）
tests/test_tool_observability_api.py（+12；fake + metrics）
tests/test_tool_observability_persistence_architecture.py（+C39（12）；
    C33.8 / C37.9 同步；C25.13 端点白名单 +/metrics/persistent）
tests/test_tool_observability_architecture_audit.py（C25.13 同）
docs/api.md（§2.8）/ docs/architecture.md（§8.51）/
docs/decisions/ADR-3.11.26-…md（Implementation Status）
未修改：ToolExecutionService / ToolExecutionRecord / Context / Observer /
        Composite / PersistenceAdapter / PersistenceService / InMemoryCollector /
        ToolObservabilityQueryService / Snapshot / Metrics Service / ORM Model /
        AIOrchestrator / ToolRegistry / Runtime 端点行为 / main.py
```

## 16. 新增文件

```text
tests/test_tool_observability_persistent_metrics_db.py（13，DB-gated）
docs/evaluation/Phase 3.11.33 — Tool Observability Persistent Metrics.md
```

## 17. 当前限制

```text
* 无维度聚合（by_tool / by_project / by_time 均未实现；只有 3 个精确过滤）
* 无时间范围过滤（started_at from / to）；无关键词 / 模糊检索
* 无 Retention / TTL / cleanup / 归档
* 无缓存（每次请求直达数据库）；无物化视图 / 预聚合表
* 无 Dashboard / Prometheus / OpenTelemetry / Grafana / Redis / Kafka
* 无后台聚合任务（同步单查询）
* Runtime 与 Persistent 指标不合并（刻意；避免语义冲突）
* DB 不可用 → 502（不回退内存指标）
* 全套 DB-gated 仍受既有 knowledge 数据状态耦合影响（Pre-existing）
* lint unavailable（环境限制）
```
