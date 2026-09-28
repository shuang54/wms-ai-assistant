# Phase 3.11.31 — Tool Observability Persistent History Pagination

> Step 31 只为 **Persistent History API** 增加分页能力：
> **仅 LIMIT + OFFSET**（无 cursor / keyset / total_count / filter）。
> Runtime API 与 Persistent API 仍然并列（不合并、不 fallback）。

---

## 1. Scope

```text
新增：offset 分页参数（API + Service + Repository）
新增：Response 分页元信息 limit / offset（**不含** total_count）
不改：GET /api/observability/tools          （仍内存；0 参数）
      GET /api/observability/tools/metrics  （仍内存；0 参数）
不改：ToolExecutionService / Record / Observer / Composite / Adapter /
      PersistenceService / Collector / Runtime QueryService / ORM
```

## 2. API

```text
GET /api/observability/tools/history?limit=100&offset=0     （第 1 页）
GET /api/observability/tools/history?limit=100&offset=100   （第 2 页）
GET /api/observability/tools/history?limit=100&offset=200   （第 3 页）

query parameters：仅 limit + offset（FastAPI Query 校验）
Method: GET only（无写方法）   Side Effect: None   Persistence: None
```

## 3. limit

```text
默认 100 · 最小 1 · 最大 1000（Step 30 契约不变）
非法（0 / -1 / 1001 / abc）→ 422，且 Service / Repository / DB **未被调用**
```

## 4. offset

```text
默认 0 · 最小 0 · **无 MAX_OFFSET**（不引入新全局配置）
非法（-1 / -100 / abc）→ 422，且 Service / Repository / DB **未被调用**
超大 offset（如 999999999）合法 → 空页（200 + items=[]），不是 404
```

## 5. Ordering（跨页一致性的前提）

```text
ORDER BY started_at DESC, id DESC        （**未改变**）
    started_at 可能相同 → 主键 id 提供 deterministic tie-breaker
    所有页共用同一套排序窗口 → 无重复 / 无遗漏 / 顺序稳定
    Service / API 均**不**重排（AST 断言：无 sort / sorted / order_by）
```

## 6. Empty page

```text
offset 超出记录数        → 200 {"items": [], "limit": …, "offset": …}
数据库为空              → 200 {"items": [], "limit": …, "offset": …}
（不是 404 / 不是错误；也不做 COUNT 判断）
```

## 7. Error semantics

```text
limit / offset 非法     → 422（FastAPI Query 校验；不进入端点）
DB 未配置 / 查询失败    → 502 {"detail": "Tool 观测历史数据不可用"}
                          （RepositoryError 映射；无 fallback 到内存视图）
其它未预期异常          → 500 {"detail": "Tool 观测数据不可用"}
```

## 8. Security

```text
Response：items 项严格 = ToolExecutionSnapshot 11 字段
          （无 id / created_at / arguments / sql / prompt / LLM response /
            密码 / API key / DATABASE_URL / exception message / traceback）
新增元信息仅 limit / offset（无 total_count）
分页不改变数据边界（不新增可查字段 / 无 SELECT * / 无 JOIN）
```

## 9. Tests

```text
tests/test_tool_execution_repository.py           46 passed（+6 分页用例）
    默认 OFFSET 0 / OFFSET 出现在 SQL 且排序不变 /
    invalid offset（-1/-100/"0"/1.5/True）→ ValueError 且不触达 DB /
    超大 offset 合法 / 空页编译为 OFFSET / 无 COUNT(

tests/test_tool_execution_persistent_query_service.py  25 passed（+5 分页用例）
    默认 offset 透传 / offset 透传（0/2/100/999999999）/
    invalid offset 不触达 repository / 分页一致性（三页拼接 == 一次取全）/
    offset 超界 → []

tests/test_tool_observability_api.py              56 passed（+7 分页用例）
    默认 {items:[], limit:100, offset:0} / limit 1·100·1000 /
    offset 0·1·100 / limit+offset 组合 / 空页 200 /
    分页一致性（API 三页拼接 == 一次取全）/ invalid limit 422 /
    invalid offset 422 且 Service 0 次调用 / 超大 offset 合法

tests/test_tool_observability_history_api_db.py   13 passed（DB-gated；+5）
    5 条 synthetic：limit=2 offset=0/2/4 → 与 limit=5 offset=0 完全一致
    （无重复 / 无遗漏 / 顺序稳定）/ Response 元信息（无 total_count）/
    空页 200 / 同一 started_at 的 tie-breaker 分页（id DESC）/ invalid offset 422

tests/test_tool_observability_persistence_architecture.py  63 passed（含 C37 = 9）
全量 no DB                                    3657 passed / 379 skipped（0 failed）
  （Step 30 基线 3631 / 374 → +26；skipped +5 = 新增 DB 用例）
```

## 10. DB residue

```text
0（teardown TRUNCATE **本表** RESTART IDENTITY，并断言 residue = 0）
未 TRUNCATE ai_ops.llm_usage_record（只读断言未被本测试修改）
未触碰 public.* / 未删除 ai_ops schema
测试数据 synthetic（step31-test-* / get_inventory / test-project）
未读取真实库存 / 工单 / WMS 数据
```

## 11. Limitations

```text
* 仅 LIMIT + OFFSET；**无** cursor / keyset pagination（为未来性能做提前实现被刻意排除）
* **无** total_count / COUNT(*)（属后续 Metrics / Query metadata）
* **无** filter（project_id / tool_name / success / 时间范围 / keyword）
* **无** Persistent Metrics（COUNT / SUM / AVG / success_rate）
* **无** retention / TTL / cleanup / 归档
* 深分页（offset 很大）随数据量线性变慢 —— 本阶段接受（不提前优化）
* Runtime 与 Persistent 视图不合并（刻意）
* 无缓存 / 无 ETag；DB 不可用 → 502（不回退内存）
* Dashboard / Prometheus / OpenTelemetry = NOT IMPLEMENTED
* lint unavailable（环境未安装 ruff / flake8）
```

---

## 12. 修改边界

```text
新增：docs/evaluation/Phase 3.11.31 — …md
修改：backend/app/db/tool_execution_repository.py（list_recent + offset；
          _validate_recent_offset；build_recent_select + offset）
      backend/app/services/tool_execution_persistent_query_service.py
          （list_recent + offset；_validate_recent_offset；
           DEFAULT_RECENT_OFFSET）
      backend/app/api/tool_observability.py（+ offset Query 参数；
          Response + limit / offset 回显）
      tests/{test_tool_execution_repository, test_tool_execution_persistent_query_service,
             test_tool_observability_api, test_tool_observability_history_api_db}.py
          （+ 分页用例；少量既有断言同步）
      tests/test_tool_chat_architecture_contract.py（C27.20：limit + offset；
          Runtime 端点仍无参数）
      tests/test_tool_observability_persistence_architecture.py
          （+C37（9）；C35.7 / C36.6 / C36.7-8 同步）
      docs/api.md（§2.7 分页）/ docs/architecture.md（§8.49）/
      docs/decisions/ADR-3.11.26-…md（Implementation Status）
未修改：ToolExecutionService / ToolExecutionRecord / Observer / Composite /
        PersistenceAdapter / PersistenceService / InMemoryCollector /
        ToolObservabilityQueryService / ORM Model / main.py / Runtime 端点行为
```
