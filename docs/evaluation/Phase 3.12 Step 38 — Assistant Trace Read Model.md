# Phase 3.12 Step 38 — Assistant Trace Read Model

> Step 35～37 分别建立了：Assistant Trace ID（响应透出）、LLM Usage 写入关联、
> LLM Usage 只读查询。本阶段把它们**组合**成一个应用层只读 Read Model：
>
> ```text
> Assistant Trace A
>         │
>         ├── LLM Usage[]
>         │
>         └── Tool Execution[]
>                 ↓
>         AssistantTraceView
> ```
>
> **这不是 Trace System**：不新增表 / 不新增 Repository / 不写库 / 无 Span /
> 无 HTTP API；只是把两个**已有**只读边界的结果按同一个 trace id 组合。

---

## 1. Scope

```text
新增
    backend/app/services/assistant_trace_query_service.py
        · AssistantTraceView（frozen；tuple 集合）
        · AssistantTraceQueryService.get_trace(assistant_request_id)
        · _validate_assistant_request_id（复用 assistant_trace 的长度常量）
    tests/test_assistant_trace_query_service.py        （33）
    tests/test_assistant_trace_query_service_db.py     （6；DB-gated）
    docs/evaluation/…（本文件）· docs/architecture.md（§8.55）

明确不做
    Trace Repository / assistant_trace 表 / migration / Span / Trace Parent /
    HTTP API（/api/trace · /api/assistant/trace · /api/usage/by-request）/
    聚合 RAG 或 Conversation / Conversation / Memory / Agent / MCP /
    Streaming / Dashboard / OpenTelemetry / Prometheus / Redis / Kafka / Celery

明确不修改
    AIOrchestrator · AI Router · RAG · ToolExecutionService · ToolExecutionRecord ·
    Tool Observability（Collector / Query Service / Snapshot / API）·
    LLM Provider · Text-to-SQL · SQL Validator / Executor ·
    LLM Usage 写入逻辑（Step 36）· LLM Usage 读边界（Step 37）·
    全部既有 HTTP 端点
```

## 2. Read Model

```text
@dataclass(frozen=True)
class AssistantTraceView:
    assistant_request_id: str
    llm_usage: tuple[LLMUsageTraceRecordView, ...]      # Step 37 DTO（9 字段）
    tool_executions: tuple[ToolExecutionSnapshot, ...]  # Step 22 DTO（11 字段）

AssistantTraceQueryService(
    tool_observability_query_service=…,     # 必填：由调用方注入（**不创建 Collector**）
    llm_usage_query_service=…,              # 可选：默认 LLMUsageQueryService()
).get_trace(assistant_request_id) → AssistantTraceView
    · validate（先于任何下游调用）
    · llm_usage  ← LLMUsageQueryService.list_by_assistant_request_id(A)
    · tools      ← ToolObservabilityQueryService.snapshots_by_request_id(A)
    · 不排序 / 不去重 / 不聚合 / 不补全 / 不按 route 猜测

read-only snapshot：两个集合一律 tuple（调用方无法 append / 就地修改）
Collector 唯一创建点仍是 api/orchestrator_chat.py（本服务不创建 Collector）
```

## 3. Data Sources

```text
LLM Usage  → PostgreSQL（ai_ops.llm_usage_record）
             经 LLMUsageQueryService（Step 37：精确匹配 assistant_request_id）
Tool       → Runtime 内存（InMemoryToolExecutionCollector，Application lifetime）
             经 ToolObservabilityQueryService.snapshots_by_request_id
             （Phase 3.11 Step 21/22 既有只读边界；11 字段安全 Snapshot）

**不合并** Runtime 与 Persistent Tool Records（避免 duplicate Tool Execution）：
    Persistent tool history 由 /api/observability/tools/history 独立提供；
    本阶段 Tool 数据源固定为 Runtime 读边界（与 Step 35 的关联实测同一来源）
数据源不同源是**有意**的，并在测试中显式体现：
    unit  → 两个 Fake / 真实内存读边界
    DB    → LLM 真实 PostgreSQL + Tool 真实内存 Collector
```

## 4. Dependency Boundary

```text
允许（唯一允许的依赖方向）
    AssistantTraceQueryService
        ├── LLMUsageQueryService            （只读 Query Service）
        └── ToolObservabilityQueryService   （只读 Query Service）

禁止（C43 静态断言）
    ✗ LLMUsageRepository / ToolExecutionRepository / 任何 Repository
    ✗ sqlalchemy / Session / Engine / select / execute
    ✗ ORM Model
    ✗ ToolExecutionService / AIRouter / RagService / LLM Client（不执行任何业务逻辑）
    ✗ 生成 request_id（uuid / new_request_id）
    ✗ HTTP API（本模块不被 api/** import）
```

## 5. Empty Semantics

```text
Case A  LLM = 2, Tool = 0   → 合法（如 RAG）
Case B  LLM = 1, Tool = 1   → 合法（如 TOOL）
Case C  LLM = 0, Tool = 0   → 合法（LLM usage 未接线 / Tool 未执行）
        返回 AssistantTraceView(A, (), ()) —— **不是错误**
不存在 ID  → 同样返回 AssistantTraceView(id, (), ())（不抛 not-found）
```

## 6. Ordering

```text
llm_usage       保持 LLMUsageQueryService 的顺序：created_at ASC, id ASC
                （Step 37 的确定性排序；本层**不重排**）
tool_executions 保持 Collector 写入顺序（round 递增；snapshots_by_request_id
                的既有语义；本层**不二次排序**）
实测（DB 测试）：view.tool_executions == collector.snapshots_by_request_id(A)
```

## 7. Security

```text
Trace View 字段 = 两个既有安全 DTO 的并集，再无其它：
    LLM（9）：id · assistant_request_id · request_id（Provider 请求 ID）·
              provider · model · prompt_tokens · completion_tokens ·
              total_tokens · created_at
    Tool（11）：request_id · round · tool_name · started_at · finished_at ·
               duration_ms · success · project_id · tool_call_id ·
               error_code · error_type

禁止（AST / 字段级断言）：
    prompt · messages · raw_response · tool arguments · ToolResult.data ·
    SQL · DB connection · Session / Engine · API key · password ·
    authorization · database URL · secret
显式投影：不使用 vars() / asdict() / __dict__ / model_dump()
```

## 8. Error Propagation

```text
下游异常**原样透传**，绝不降级为 []：
    LLMUsageRepositoryError / LLMUsageQueryInputError（LLM 侧）
    Tool 读边界的异常（原样）
系统错误 ≠ 没有 Trace：DB 不可用时抛错，由调用方决定如何呈现
输入非法 → ValueError（在**任何下游调用之前**失败；下游零调用）
首个下游失败即停止（不再继续查第二条数据源），避免半成品 View
```

## 9. Tests

```text
tests/test_assistant_trace_query_service.py      33 passed（0 DB / 0 LLM）
    Test 1 LLM only（2/0）· Test 2 Tool only（0/1）· Test 3 LLM+Tool（2/1）
    Test 4 Empty（0/0，非错误）· Test 5 不存在 → AssistantTraceView(id, (), ())
    Test 6 不串 Trace（A 结果不含 B；Tool 侧真实过滤）
    Test 7 LLM ordering 保持（created_at ASC, id ASC）· Test 8 Tool ordering 保持
            （与 collector.snapshots_by_request_id 完全一致）
    Test 9 输入校验 8 组（None·""·空白·非 str·bytes·list·超长）→ 下游零调用；
            128 边界接受且不改写
    Test 10 下游异常透传（LLM / Tool 两侧；不返回 []；首个失败即停止）
    附加：注入校验 / View 校验 / 冻结与 tuple / 安全字段与 AST

tests/test_assistant_trace_query_service_db.py    6 passed（RUN_DB_TESTS=1）/ 6 skipped
    LLM only（真实 PostgreSQL：2 条 usage，id 升序）· LLM+Tool（1/1）
    Tool only · Empty · 不串 Trace（A/B 各 1）·
    历史 NULL usage 不被粘到任何 Trace
    清理：按 provider 前缀 step38- 定向 DELETE（**不用 TRUNCATE**）→ 归零

C43（tests/test_assistant_trace_query_service.py::TestC43AssistantTraceReadModel）
    C43.1  只依赖 Query Service                 ✅
    C43.2  不依赖 Repository（AST 标识符/import）✅
    C43.3  不依赖 SQLAlchemy                    ✅
    C43.4  不访问 Session / Engine / execute     ✅
    C43.5  不生成 request_id                    ✅
    C43.6  不执行 Tool（无 registry / handler）  ✅
    C43.7  不执行 LLM（无 client / generate）    ✅
    C43.8  不新增 HTTP endpoint（含 404 实测）   ✅
    C43.9  immutable result（frozen + tuple）    ✅
    C43.10 不暴露 secrets / 无 trace_id 命名     ✅

全量（no DB）                          3862 passed / 426 skipped（0 failed）
  （Step 37 基线 3820 / 420 → +42 = 33 + 6 + 3 其它；skipped +6）
DB-gated 定向（Trace 家族 + LLM Usage 家族）  以实际运行结果为准（见汇报）
```

## 10. Limitations

```text
* 无 HTTP API：应用层组合能力（Trace API 属未来阶段）
* Tool 数据源 = Runtime 内存（进程内 retention window，默认 1000 条）：
  进程重启 / 多 worker 场景不覆盖；Persistent Tool Records 未参与组合
* 不做 Runtime + Persistent 的合并 / 去重（刻意，避免 duplicate Tool Execution）
* 不聚合（无 total_tokens / 调用次数汇总；无跨 Trace 统计）
* 不联接 RAG 元数据 / Conversation（无第三数据源）
* 不排序 / 不去重 / 不做时间过滤（保留下游语义）
* LLM Usage 默认仍未接线（create_llm_client 默认 NoopAccountingSink）
* 组合层无缓存（每次 get_trace 直连两个下游读边界）
* lint unavailable（环境未安装 ruff / flake8；未新增工具）
```
