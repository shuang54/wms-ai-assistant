# Phase 3.11.30 — Tool Observability Persistent History API

> Step 30 只新增 **Persistent Tool Execution History 的 HTTP Read API**。
> Runtime API 与 Persistent API **并列**（parallel）：不 fallback、不 merge、
> 不去重。

---

## 1. Scope

```text
新增：GET /api/observability/tools/history        （数据库长期历史；只读）
不改：GET /api/observability/tools                （仍读 InMemoryCollector）
      GET /api/observability/tools/metrics        （仍读内存 Metrics）
不改：ToolExecutionService / Record / Observer / Composite / Adapter /
      PersistenceService / Collector / QueryService /
      PersistentQueryService / Repository / ORM / main.py
```

## 2. Endpoint

```text
GET /api/observability/tools/history?limit=100

Method:       GET（无 POST / PUT / PATCH / DELETE）
Auth:         当前项目现有 API 机制（未新增认证）
Side Effect:  None（不写库 / 不触发 Tool 执行 / 不改 Collector）
Persistence:  None（只读现有历史）
Query param:  limit（唯一；1~1000，默认 100；FastAPI Query 校验）
```

## 3. Runtime vs Persistent

```text
Runtime                                       Persistent
──────────────────────────────                ──────────────────────────────
GET /api/observability/tools                  GET /api/observability/tools/history
      ↓                                             ↓
ToolObservabilityQueryService                 ToolExecutionPersistentQueryService
      ↓                                             ↓
InMemoryToolExecutionCollector                ToolExecutionRepository
（process-local；近期观测）                    （ai_ops；长期历史）

parallel —— **not** fallback —— **not** merged —— **no** dedup
```

## 4. Request

```text
limit   int   1 ~ 1000   默认 100

禁止（本阶段）：offset / cursor / page / project_id / tool_name / success /
                from / to / start_time / end_time / keyword
```

## 5. Response

```json
{
  "items": [
    {
      "request_id": "step30-test-001",
      "round": 1,
      "tool_name": "get_inventory",
      "started_at": "2026-09-28T12:00:00+00:00",
      "finished_at": "2026-09-28T12:00:00.007500+00:00",
      "duration_ms": 7.5,
      "success": true,
      "project_id": "test-project",
      "tool_call_id": null,
      "error_code": null,
      "error_type": null
    }
  ]
}
```

```text
最外层只有 items；item 严格 = ToolExecutionSnapshot 的 11 字段
（无 id / created_at / database_name / arguments / data / sql / prompt /
  llm_response / exception_message / traceback）
时间戳 = ISO 8601 字符串（Step 24 Serialization Boundary）
顺序 = Repository 的 started_at DESC, id DESC（API 不重排 / 不过滤 / 不聚合）
```

## 6. Error Semantics

```text
空库                 → 200 {"items": []}（empty database ≠ 异常）
limit 非法（0 / -1 / 1001 / abc）→ 422（FastAPI Query 校验；不进入端点）
DB 未配置 / 查询失败 → 502 {"detail": "Tool 观测历史数据不可用"}
                      （ToolExecutionRepositoryError 映射；
                       不回退内存视图、不吞异常、不返回 []）
其它未预期异常       → 500 {"detail": "Tool 观测数据不可用"}
响应 / detail 不含 SQL / DATABASE_URL / 凭据 / traceback / 内部模块路径
```

## 7. Security Boundary

```text
只读：GET only（AST 断言 router 仅 .get）
API 不接触 Session / SQLAlchemy / ORM Model / Repository 类
    （唯一 db 依赖 = ToolExecutionRepositoryError 类型，用于 502 映射；
      与 api/usage.py 导入 LLMUsageRepositoryError 的既有 precedent 一致）
数据全部经 PersistentQueryService（Composition Root accessor，
    模块级同一实例；不每请求新建）
响应字段白名单 = Snapshot 11 字段（Pydantic DTO 固定）
```

## 8. Tests

```text
tests/test_tool_observability_api.py                    43 passed（+12 History）
    empty → 200 + items == []（默认 limit=100）        one record（11 字段值）
    multiple → 顺序完全保持 Service 顺序               limit=1/100/1000
    invalid limit（0/-1/1001/abc）→ 422 且不进入端点
    DB failure → 502 且**不回退**内存（响应不含内存记录）
    DB failure 响应无内部信息（SELECT / postgresql:// / Traceback / ai_ops）
    field whitelist（无 id / created_at / arguments / sql / prompt / ...）
    GET only（无 post / put / patch / delete）
    runtime isolation：collector=A、DB=B → /history 只返回 B；
                        /tools 与 /metrics 不触达持久链路（service.calls == []）
    /history 不修改 Collector

tests/test_tool_observability_history_api_db.py          8 passed（DB-gated）
    INSERT synthetic → GET /history → 字段 / 顺序 / limit / nullable /
    空库 → [] / invalid limit → 422 / 无内部字段 / llm_usage 未触碰

tests/test_tool_observability_persistence_architecture.py  54 passed（含 C36 = 9）
全量 no DB                                       3631 passed / 374 skipped（0 failed）
  （Step 29 基线 3610 / 366 → +21；skipped +8 = 新增 DB 文件）
```

## 9. DB residue

```text
0（teardown TRUNCATE **本表** RESTART IDENTITY）
未 TRUNCATE ai_ops.llm_usage_record（只读断言其未被本测试修改）
未触碰 public.* / 未删除 ai_ops schema
测试数据 synthetic（step30-test-* / get_inventory / test-project）
未读取真实库存 / 工单 / WMS 数据

说明：本 API 只读；测试期间的写入全部来自测试自身用真实 Repository
      insert 的 synthetic record。
```

## 10. Current limitations

```text
* 无分页（无 offset / cursor / page；只有 limit）
* 无过滤（project_id / tool_name / success / 时间范围 / 关键词均未实现）
* 无持久化 Metrics（COUNT / SUM / AVG / success_rate 未实现）
* 无 retention / TTL / cleanup / 归档
* Runtime 与 Persistent 视图**不合并**（刻意；避免去重 / 排序 / 窗口 / 分页冲突）
* 无缓存 / 无 ETag；每次请求直达数据库
* DB 不可用时该端点 502（**不**回退到内存视图）
* Dashboard / Prometheus / OpenTelemetry = NOT IMPLEMENTED
* 全套 DB-gated 仍受既有 knowledge 数据状态耦合影响（Pre-existing）
* lint unavailable（环境未安装 ruff / flake8）
```

---

## 11. 修改边界

```text
新增：tests/test_tool_observability_history_api_db.py（8，DB-gated）
      docs/evaluation/Phase 3.11.30 — …md
修改：backend/app/api/tool_observability.py（+ History DTO ×2 + 端点）
      backend/app/api/orchestrator_chat.py（+ 模块级 PersistentQueryService
          + accessor；不接触 Repository / SQLAlchemy）
      tests/test_tool_observability_api.py（+12；静态断言同步）
      tests/test_tool_chat_architecture_contract.py（C27.3~7 / C27.10 / C27.20 /
          C27.14~16 同步 —— 当前状态契约）
      tests/test_tool_observability_architecture_audit.py（C25.13 端点白名单 +history）
      tests/test_tool_observability_persistence_architecture.py
          （+C36（9）；C32.1 / C33.4 / C33.6 / C34.9 / C34.11 / C35.8 同步）
      docs/architecture.md（§8.48）/ docs/api.md（§2.7）/ ADR-3.11.26（Status）
未修改：ToolExecutionService / ToolExecutionRecord / Observer / Composite /
        PersistenceAdapter / PersistenceService / InMemoryCollector /
        ToolObservabilityQueryService / ToolExecutionPersistentQueryService /
        ToolExecutionRepository / ORM Model / main.py
```
