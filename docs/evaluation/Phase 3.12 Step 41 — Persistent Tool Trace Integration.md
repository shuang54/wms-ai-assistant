# Phase 3.12 Step 41 — Persistent Tool Trace Integration

> Step 40 验证了全链路 correlation，但 Tool 侧数据源是 **Runtime 内存**。
> 本阶段把 Assistant Trace 的 Tool 数据源切换为 **持久化 PostgreSQL**
> （复用 Phase 3.11 已有的 Persistent Tool Read Boundary），
> 使 Trace 在进程重启 / 多 worker / Runtime Collector 淘汰后仍可查询。
>
> **未新增 Trace DB / Trace Repository / OpenTelemetry**；Runtime API 与
> History API 语义不变；Trace API endpoint / response contract 不变。

---

## 1. Architecture

```text
Assistant Request A
       │
       ├── LLM Usage
       │      ↓
       │   ai_ops.llm_usage_record          （PostgreSQL）
       │
       └── Tool Execution
              ↓
           ai_ops.tool_execution_record     （PostgreSQL；持久化读边界）
              ↓
        AssistantTraceQueryService
        ├── LLMUsageQueryService                 → PostgreSQL
        └── ToolExecutionPersistentQueryService  → PostgreSQL（**Step 41 切换**）
              ↓
        AssistantTraceView
              ↓
        GET /api/observability/assistant-trace/{A}
```

## 2. Data Sources

```text
LLM  → ai_ops.llm_usage_record        （Step 36/37：assistant_request_id）
Tool → ai_ops.tool_execution_record   （Step 28 起生产装配已写入；
                                        Step 41 起 Trace 从这里读）
读路径：LLMUsageQueryService.list_by_assistant_request_id(A)
        Tool 观测 Persistent 读边界.list_by_request_id(A)
不合并：Runtime 内存 Collector **不参与** Trace（避免 duplicate Tool Execution）
```

## 3. Persistent Tool Read Boundary

```text
新增（最小扩展，复用已有 Repository 方法）
    ToolExecutionPersistentQueryService.list_by_request_id(request_id)
        · validate（非空 str / strip 非空 / ≤128；DB 之前）
        · Repository.get_by_request_id(request_id)   ← **Phase 3.11 既有方法**
          （显式列 + WHERE request_id = :param + ORDER BY id ASC；无 SELECT *）
        · Row → ToolExecutionSnapshot（既有显式映射；11 安全字段）
        · 空 → []（不是错误）；DB 失败 → ToolExecutionRepositoryError（不降级为 []）
未新增：Repository 方法 / 表 / 迁移 / SQL 构造点
排序：复用 Repository 既有稳定排序 ``id ASC``（同一 request 内落库顺序 =
      执行顺序）；Trace 组合层**不重排**
```

## 4. Runtime vs Persistent

```text
Runtime API     GET /api/observability/tools               → 内存 Collector（未变）
                GET /api/observability/tools/metrics       → 内存 Collector（未变）
History API     GET /api/observability/tools/history       → PostgreSQL（未变）
                GET /api/observability/tools/metrics/persistent → PostgreSQL（未变）
Assistant Trace GET /api/observability/assistant-trace/{A} → LLM + Tool **均 PostgreSQL**
                （Step 41 变更点；endpoint / response contract 不变）
同一 Record 仍通过 Composition Root 的 fan-out 同时进入 Collector 与
Persistence Adapter（Step 28 装配未改）。
```

## 5. LLM + Tool Trace

```text
POST /api/ai/chat（TOOL 路径）
        ↓ request_id = A
    Tool：真实 ToolExecutionService → CompositeToolExecutionObserver
          （Collector + Persistence Adapter）→ ai_ops.tool_execution_record
        ↓ GET /api/observability/assistant-trace/A
    tool_executions[].request_id == A（round=1；tool_call_id=None）
    llm_usage[]（若该请求内发生 LLM 调用）assistant_request_id == A
```

## 6. Cross Request Isolation

```text
A（tool）/ B（tool）两次请求
    Trace A：tool_executions ⊆ {A}；不含 B 的任何 ID
    Trace B：tool_executions ⊆ {B}；不含 A 的任何 ID
（DB 实测：两张表按 request_id 精确过滤，无跨请求污染）
```

## 7. Restart-like Test

```text
POST /api/ai/chat
        ↓ 持久化 Tool Record（PostgreSQL）
Runtime Collector.clear()          ← 模拟进程重启 / retention 淘汰 / 多 worker
新建 ToolExecutionPersistentQueryService + 新 AssistantTraceQueryService
        ↓
get_trace(A) → tool_executions ≥ 1（来自 PostgreSQL）

并且：GET /api/observability/tools == {"records": []}（Runtime 视图已空）
      GET /api/observability/assistant-trace/A → tool_executions ≥ 1
      ⇒ Trace ≠ Runtime Memory（本阶段核心证明）
```

## 8. Security

```text
Trace 的 Tool 部分仍只有 ToolExecutionSnapshot 的 11 字段：
    request_id · round · tool_name · started_at · finished_at · duration_ms ·
    success · project_id · tool_call_id · error_code · error_type
HTTP 键级断言确认无：arguments · data · result · sql · prompt · messages ·
    raw_response · secret · password · api_key · database_url · session ·
    connection · traceback · id（数据库主键不外泄）
显式映射：无 vars / asdict / __dict__ / model_dump
```

## 9. API Regression

```text
/api/ai/chat                 → {route, content, data, metadata}（未变）
/api/usage/analytics         → 未变
/api/observability/tools     → Runtime 语义未变（records=[] 断言）
/api/observability/tools/metrics → 未变
/api/observability/tools/history → Persistent 语义未变
/api/observability/tools/metrics/persistent → 未变
/api/observability/assistant-trace/{id} → endpoint / response schema / 空语义 /
    错误语义（400 / 422 / 502 / 500）均未变；502 现同时覆盖
    LLMUsageRepositoryError 与 ToolExecutionRepositoryError（两者都是 PostgreSQL 读边界）
未新增 endpoint。
```

## 10. Tests

```text
tests/test_tool_execution_persistent_query_service.py  50 passed（+5：list_by_request_id）
    Repository 顺序透传 / 空结果 / 非法输入（5 组）先于 DB /
    RepositoryError 透传 / 不使用 Runtime Collector（AST）

tests/test_assistant_trace_query_service.py            36 passed（同步 Step 41）
    Tool 数据源 = Persistent 读边界（Fake Repository → 真实服务 → Snapshot）
    Runtime 内存边界（仅 snapshots_by_request_id）→ TypeError（被拒绝）
    C43.1 断言 llm_usage_record / tool_execution_record 两个持久化来源

tests/test_assistant_trace_api.py                      21 passed（同步 Step 41）
    Tool 行 → 真实 Persistent 服务 + Fake Repository；11 字段映射不变
    accessor：tool boundary is get_tool_execution_persistent_query_service()；
    且 **不是** Runtime 读边界对象

tests/test_assistant_trace_query_service_db.py         6 passed（RUN_DB_TESTS=1）
    LLM 与 Tool **均写入 PostgreSQL**；两张表按前缀定向清理

tests/test_assistant_trace_correlation_e2e.py          8 passed（+1）
    新增 test_tool_trace_survives_runtime_collector_clear：
    Collector 清空后 Trace 仍可读（持久化边界替身）

tests/test_assistant_trace_correlation_e2e_db.py       4 passed（RUN_DB_TESTS=1）
    observer = CompositeToolExecutionObserver(collector, PersistenceAdapter)
    → Tool 行真实落库；清理按捕获 request_id 定向

tests/test_assistant_trace_persistent_tool_db.py       6 passed（RUN_DB_TESTS=1）
    persisted Tool only（Collector 清空后仍可读 + Runtime 视图为空）·
    LLM + persisted Tool · 跨请求隔离 · 空 Trace ·
    restart-like（新读边界 / 新组合服务）· 安全字段（11 字段 + 键级断言）

全量（no DB）                          3894 passed / 440 skipped（0 failed）
DB-gated Trace 家族（含本阶段新文件）      以实际运行结果为准（见汇报）
```

## 11. DB residue

```text
0
    ai_ops.llm_usage_record        （provider 前缀 step41- 定向 DELETE）
    ai_ops.tool_execution_record   （按捕获的 request_id 定向 DELETE；
                                    不用 TRUNCATE；未删除真实数据）
    未修改 schema / 未新增 migration / 未新增表
```

## 12. Limitations

```text
* Tool 读边界返回全部行（无分页）：一次 Assistant 请求内的 Tool 执行数量受
  Tool Calling 预算约束（默认 ≤5，钳制 ≤20），当前不需要分页
* 排序 = Repository 既有 ``id ASC``（落库顺序）；未引入 started_at ASC 的
  新排序语义（避免与既有持久化读路径产生第二套排序）
* Runtime 与 Persistent **不合并 / 不去重**（刻意）；Runtime API 仍是
  "当前进程实时观察"
* Trace 不聚合（不返回 Tool 计数 / 耗时汇总）
* 未引入 OpenTelemetry / Span / TraceId（assistant_request_id 已满足应用层关联）
* 多 worker 下 Trace 可读（数据在 PostgreSQL），但不做跨进程实时推送
* LLM Usage 默认仍未接线（create_llm_client 默认 NoopAccountingSink；
  本阶段未改该默认值）
* lint unavailable（环境未安装 ruff / flake8；未新增工具）
```
