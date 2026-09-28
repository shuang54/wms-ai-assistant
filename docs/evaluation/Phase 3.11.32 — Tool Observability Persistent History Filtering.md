# Phase 3.11.32 — Tool Observability Persistent History Filtering

> Step 32 只为 **Persistent History API** 增加**精确过滤**能力
> （project_id / tool_name / success）+ 既有分页 + 稳定排序。
> Runtime API 与 Persistent API 仍并列（不合并、不 fallback）。

---

## 1. Scope

```text
新增：project_id / tool_name / success 精确过滤（API + Service + Repository）
保留：limit / offset 分页；ORDER BY started_at DESC, id DESC
不改：GET /api/observability/tools          （仍内存；0 参数）
      GET /api/observability/tools/metrics  （仍内存；0 参数）
不改：ToolExecutionService / Record / Observer / Composite / Adapter /
      PersistenceService / Collector / Runtime QueryService / ORM Model
```

## 2. Filter parameters

```text
GET /api/observability/tools/history?project_id=project-a
GET /api/observability/tools/history?tool_name=get_inventory
GET /api/observability/tools/history?success=true
GET /api/observability/tools/history?success=false
GET /api/observability/tools/history
    ?project_id=project-a&tool_name=get_inventory&success=true
    &limit=20&offset=0
```

```text
参数（OpenAPI 实测）：limit(1~1000, 默认 100) · offset(>=0, 默认 0) ·
                     project_id(str|None) · tool_name(str|None) ·
                     success(bool|None)
```

## 3. Exact-match semantics

```text
project_id = :project_id     （精确；无 LIKE / ILIKE / lower / trim / fuzzy）
tool_name  = :tool_name      （精确；无 contains / startswith / regex）
success    = true | false    （False **≠** 未提供；None = 不追加条件）

条件之间 AND；无过滤 → **不生成 WHERE**（无空 WHERE）
""（空字符串）是普通字符串值（``WHERE project_id = ''``），不自动转 None
```

## 4. Filter + pagination

```text
WHERE exact filters      （先过滤）
    ↓
ORDER BY started_at DESC, id DESC      （同一排序窗口）
    ↓
LIMIT :limit
    ↓
OFFSET :offset           （后分页）

SQL 实测：WHERE 位置 < ORDER BY 位置 < LIMIT 位置 < OFFSET 位置
即：filter → order → limit → offset（**不是** limit → filter）
Service / API 均不做 Python 过滤（AST：无 filter / 无带条件推导式）
```

## 5. Ordering

```text
ORDER BY started_at DESC, id DESC   （过滤**不改变**排序）
不按过滤列排序（无 ORDER BY project_id / tool_name / success）
不在 Python 中排序
```

## 6. SQL safety

```text
过滤值一律 bound parameters（SQLAlchemy ``where(Model.col == value)``）
无字符串拼接 / 无 text() 拼接用户输入
注入串实测：
    project_id="' OR 1=1 --"        → 作为普通字符串值；返回 []
    tool_name="get_inventory' OR 1=1 --" → 同上；返回 []
    SQL 文本中不出现 "OR 1=1"；无 multi-statement；表数据未被破坏
显式列（禁用 SELECT *）；无 COUNT( / 无 JOIN
```

## 7. Error semantics

```text
无匹配 / 空库 / offset 超界 → 200 {items: [], limit: …, offset: …}（**不是 404**）
limit / offset / success 非法（0 / -1 / 1001 / abc / success=maybe）→ 422
    （Service / Repository / DB **均未触达**）
DB 未配置 / 查询失败 → 502（RepositoryError 映射；**无** fallback 到内存视图）
其它未预期异常 → 500
```

## 8. Runtime isolation

```text
Runtime 端点（/tools · /metrics）契约未变：0 参数、仍只读 InMemoryCollector
实测：/history 侧过滤（project_id=…）不影响 Runtime；
      /tools?project_id=does-not-exist → 仍返回内存记录（多余参数被忽略）
      Persistent 侧 DB 失败 → 502，且**不**回退到内存数据
```

## 9. Tests

```text
tests/test_tool_execution_repository.py                55 passed（+10 过滤用例）
    无 filter → 无 WHERE / project_id·tool_name 为 bound parameter（无 LIKE/ILIKE）/
    success=true·false 分别生成 = true·= false（None 不生成条件）/
    三条件 AND（单 WHERE）+ LIMIT + OFFSET 同句 /
    WHERE → ORDER BY → LIMIT → OFFSET 顺序 / 注入串作为参数（无 "OR 1=1"、无 ";"）/
    非法类型（project_id=123 / tool_name=123 / success=1）→ ValueError 且不触达 DB /
    "" 是字面值

tests/test_tool_execution_persistent_query_service.py  31 passed（+6 过滤用例）
    过滤默认 None / 透传不变 / success=False 不被当作缺失 / "" 是字面值 /
    非法类型 → ValueError 且不触达 repository / Service 无 Python 过滤（AST）

tests/test_tool_observability_api.py                   93 passed（+6 过滤用例）
    过滤透传到 Service（含 limit/offset 组合）/ success=false 透传为 False /
    无过滤 → 全 None / "" 透传 / success=maybe → 422 且未进端点 /
    filter + pagination 响应 / Runtime 端点不受过滤参数影响（仍内存）

tests/test_tool_observability_history_filtering_db.py  14 passed（DB-gated）
    A~E synthetic 矩阵：无过滤（全量顺序）/ project_id / tool_name /
    success=true / success=false / 组合过滤 /
    filter + pagination（filter → order → limit → offset）/
    未知值 → 200 + items=[] / "" 无匹配 / 排序不因过滤改变 /
    SQL 注入输入 → [] 且表未被破坏 / 字段白名单（11 字段，无 id）/
    invalid 参数 → 422 / llm_usage 未触碰

tests/test_tool_observability_persistence_architecture.py  73 passed（含 C38 = 10）
全量 no DB                                    3689 passed / 393 skipped（0 failed）
  （Step 31 基线 3657 / 379 → +32；skipped +14 = 新增 DB 文件）
```

## 10. DB residue

```text
0（teardown TRUNCATE **本表** RESTART IDENTITY，并断言 residue = 0）
未 TRUNCATE ai_ops.llm_usage_record（只读断言未被本测试修改）
未触碰 public.* / 未删除 ai_ops schema
测试数据 synthetic（step32-A..E / get_inventory / get_work_order /
                  project-a / project-b）
未读取真实库存 / 工单 / WMS 数据
```

## 11. Limitations

```text
* 仅**精确**过滤（3 个字段）；无模糊 / 前缀 / regex / 全文检索
* 无时间范围过滤（started_at from / to）
* 无 total_count / COUNT(*)（属后续 Metrics / Query metadata）
* 无 Persistent Metrics（SUM / AVG / success_rate）
* 无 retention / TTL / cleanup / 归档
* 仅 LIMIT + OFFSET 分页（无 cursor / keyset）
* Runtime 与 Persistent 视图不合并；DB 不可用 → 502（不回退内存）
* Dashboard / Prometheus / OpenTelemetry = NOT IMPLEMENTED
* lint unavailable（环境未安装 ruff / flake8）
```

---

## 12. 修改边界

```text
新增：tests/test_tool_observability_history_filtering_db.py（14，DB-gated）
      docs/evaluation/Phase 3.11.32 — …md
修改：backend/app/db/tool_execution_repository.py（+ project_id / tool_name /
          success 精确过滤 + 值校验；build_recent_select 支持 WHERE）
      backend/app/services/tool_execution_persistent_query_service.py
          （+ 过滤参数透传 + 校验）
      backend/app/api/tool_observability.py（+ 3 个 Query 参数 + 透传）
      tests/{test_tool_execution_repository, test_tool_execution_persistent_query_service,
             test_tool_observability_api}.py（+ 过滤用例；fake 同步过滤参数）
      tests/test_tool_chat_architecture_contract.py（C27.20：history 5 参数）
      tests/test_tool_observability_persistence_architecture.py
          （+C38（10）；C37.1 / C37.2 同步为"精确过滤已允许"；
           C35.7 / C36.7-8 / C37.8 的 fake 同步过滤参数）
      docs/api.md（§2.7 过滤）/ docs/architecture.md（§8.50）/
      docs/decisions/ADR-3.11.26-…md（Implementation Status）
未修改：ToolExecutionService / ToolExecutionRecord / Observer / Composite /
        PersistenceAdapter / PersistenceService / InMemoryCollector /
        ToolObservabilityQueryService / ORM Model / main.py / Runtime 端点行为
```
