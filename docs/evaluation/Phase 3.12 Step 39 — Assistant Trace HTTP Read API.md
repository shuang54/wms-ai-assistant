# Phase 3.12 Step 39 — Assistant Trace HTTP Read API

> Step 37 建立了 LLM Usage 的按 Trace 只读查询，Step 38 把它与 Tool 执行组合成
> ``AssistantTraceView``。本阶段只把该 Read Model 暴露为**一个只读 HTTP 端点**：
>
> ```text
> GET /api/observability/assistant-trace/{assistant_request_id}
>         ↓ AssistantTraceQueryService.get_trace()
>         ├── LLMUsageQueryService          → PostgreSQL
>         └── ToolObservabilityQueryService → Runtime Memory
>         ↓ AssistantTraceView
>         ↓ AssistantTraceResponse（显式字段映射）
>         ↓ JSON
> ```
>
> **没有新增 Trace 存储 / Repository / 表 / migration / Span。**

---

## 1. Scope

```text
新增
    backend/app/api/assistant_trace.py
        · AssistantTraceResponse / LLMUsageTraceResponse / ToolExecutionTraceResponse
        · 显式映射 + GET 端点（Path 参数 1..128）
    tests/test_assistant_trace_api.py          （21）
    tests/test_assistant_trace_api_db.py       （4；DB-gated）
    docs/evaluation/…（本文件）

修改
    backend/app/api/orchestrator_chat.py
        + get_assistant_trace_query_service()（**装配 accessor**；不创建 Collector）
    backend/app/main.py
        + include_router(assistant_trace.router, prefix="/api", tags=["observability"])
    docs/architecture.md（§8.56）· docs/api.md（§2.10）
    tests/test_tool_observability_architecture_audit.py（C25.13 端点白名单 +1）
    tests/test_assistant_trace_query_service.py（C43.8：Step 39 后当前状态）
    tests/test_llm_usage_trace_read.py（C42.7 同）
    tests/test_llm_usage_trace_correlation.py（C41.10 同）

明确不做
    Trace Table / Trace Repository / Trace ORM / migration / Collector /
    Span / OpenTelemetry / Prometheus / Conversation / Memory / Agent / MCP /
    Streaming / WebSocket / Dashboard / 缓存（Redis·LRU·TTL）/ 分页

明确不修改
    AIOrchestrator · AI Router · RAG · ToolExecutionService · ToolExecutionRecord ·
    Tool Observability Core（Collector/Query Service/Snapshot）· LLM Provider ·
    LLM Usage Repository · LLM Usage Query Service · AssistantTraceQueryService ·
    Text-to-SQL · SQL Validator / Executor · 既有端点行为
```

## 2. Endpoint

```text
GET /api/observability/assistant-trace/{assistant_request_id}

Path 参数（唯一）：assistant_request_id（min_length=1, max_length=128）
禁止：route / provider / model / tool_name / from / to / limit / offset /
      page / page_size / cursor / sort（本阶段无过滤 / 分页 / 排序能力）
```

## 3. API DTO

```text
AssistantTraceResponse
    assistant_request_id: str
    llm_usage: list[LLMUsageTraceResponse]        # 9 字段（Step 37 视图）
    tool_executions: list[ToolExecutionTraceResponse]  # 11 字段（Step 22 快照）

LLMUsageTraceResponse（9）
    id · assistant_request_id · request_id（Provider 请求 ID，未改名）·
    provider · model · prompt_tokens · completion_tokens · total_tokens · created_at
ToolExecutionTraceResponse（11）
    request_id · round · tool_name · started_at · finished_at · duration_ms ·
    success · project_id · tool_call_id · error_code · error_type

显式字段映射（无 vars / asdict / __dict__ / model_dump）；
response_model 同时充当字段过滤边界（实测：duck-typed DTO 的额外字段
prompt / messages / arguments / raw_response / data / sql / secret 均不出现）
```

## 4. Service Wiring

```text
api/assistant_trace.py
        ↓ get_assistant_trace_query_service()（Composition Root accessor）
AssistantTraceQueryService
        ├── LLMUsageQueryService()                          → ai_ops.llm_usage_record
        └── get_tool_observability_query_service()           → 应用级 InMemory Collector
                    （_TOOL_EXECUTION_COLLECTOR，唯一创建点仍是本模块）

实测：accessor 返回的组合服务其 tool 读边界 collector **is**
      orchestrator_chat._TOOL_EXECUTION_COLLECTOR（**没有第二个 Collector**）
```

## 5. Empty Trace

```text
A 不存在 / LLM 与 Tool 均为空
    → 200 OK
      {"assistant_request_id": "...", "llm_usage": [], "tool_executions": []}
    → **不是** 404（"没有记录" ≠ resource-not-found）
Case A LLM>0/Tool=0 · Case B LLM>0/Tool>0 · Case C 0/0 全部合法
```

## 6. Error Handling

```text
400  assistant_request_id 非法（服务层 ValueError：纯空白等）→ "非法输入: …"
422  路径参数长度越界（FastAPI Path 校验；>128 / 长度为 0）
404  路径完全缺失（/assistant-trace/）
502  LLM Usage 数据源不可用（LLMUsageRepositoryError）→ "助手链路观测数据不可用"
500  其它未预期错误（**不暴露** traceback / 异常消息 / SQL / 内部模块路径）

绝不降级：DB 故障 **不会** 变成 200 + 空 Trace（`except Exception: return []` 缺席）
下游异常原样透传（不吞、不包装成新异常体系）
```

## 7. Security

```text
响应仅含 3 个顶层键 + 9 / 11 字段；键级断言确认无：
    prompt · messages · arguments · raw_response · secret · password ·
    api_key · authorization · database_url · sql · session · connection ·
    traceback · handler · registry
额外字段的值亦不外泄（SYSTEM-PROMPT-LEAK / RAW-LEAK / sk-LEAK / SELECT 1 /
top-secret / LEAK 实测均不出现）
API 模块静态无：vars / asdict / model_dump / session / engine / select /
execute / collector / repository / InMemoryToolExecutionCollector
OpenAPI schema（本端点 + 3 个 schema 子集）无 password / api_key /
arguments / raw_response / secret
```

## 8. Tests

```text
tests/test_assistant_trace_api.py           21 passed（0 DB）
    Test 1 LLM only（2/0；字段白名单 9 + ISO 时间戳）
    Test 2 Tool only（0/1；字段白名单 11）
    Test 3 LLM + Tool（2/1；全部属于 A）
    Test 4 Empty（not-exist）→ 200 + [] + []（**不是 404**）
    Test 5 校验：纯空白（%20%20 / %09 / %20）→ 400/422 且 LLM 下游零调用；
            129 字符 → 422 且下游零调用；缺 id → 404
    Test 6 错误：LLMUsageRepositoryError → 502（仅 {"detail"}，无 llm_usage）；
            RuntimeError → 500（无 Traceback / 无异常消息）；ValueError → 400
    Test 7 安全键级断言（见 §7）
    Test 8 显式映射：duck-typed DTO（含 prompt / messages / arguments /
            raw_response / api_key / data / sql / secret / internal_debug）
            的额外字段与取值全部不外泄
    附加：API 模块无隐式投影 / 不创建 Collector；accessor 复用应用级 Collector；
            顺序保持（LLM created_at ASC,id ASC；Tool round 1,2 写入顺序）；
            不串 Trace；参数仅 assistant_request_id（无过滤 / 分页 / 排序）
    Test 9 既有端点回归：/observability/tools（200 + {"records": []}）·
            /metrics（200 + 8 字段）· /history、/metrics/persistent（200/502）·
            /api/ai/chat 空体（422）· /api/usage/analytics（200/422/502）
    OpenAPI：路径存在；200 schema = AssistantTraceResponse；三个 DTO 字段精确

tests/test_assistant_trace_api_db.py         4 passed（RUN_DB_TESTS=1）/ 4 skipped
    真实 PostgreSQL + 真实应用级 Collector → HTTP：
    · LLM+Tool correlation（2 条 usage + 1 条 tool；round=1；tool_call_id=None；
      键级安全断言）
    · 跨请求隔离（A / B 各 1 条 LLM + 1 条 Tool，互不混）
    · 空 Trace → 200 + 空数组
    · 历史 NULL usage 不被粘到任何 Trace
    清理：清空 Collector + 按 provider 前缀 step39- 定向 DELETE（**不用 TRUNCATE**）

全量（no DB）                                    3876 passed / 430 skipped（0 failed）
  （Step 38 基线 3855 / 426 → +21；skipped +4 = 新增 DB 文件）
```

## 9. Limitations

```text
* 无分页 / 无过滤 / 无时间范围（assistant_request_id 本身即一个 Trace scope）
* Tool 数据源 = Runtime 内存（进程内 retention window，默认 1000 条）：
  进程重启 / 多 worker 不覆盖；Persistent Tool Records 未参与组合
* LLM 与 Tool 来自不同数据源（PostgreSQL / Runtime）；不做合并 / 去重 / 聚合
* 无缓存（每次请求直连两个读边界）
* 不返回 RAG 元数据 / Conversation（无第三数据源）
* 无认证 / 授权设计（沿用项目现有 API 机制；Trace 端点与其它观测端点一致）
* LLM Usage 默认仍未接线（create_llm_client 默认 NoopAccountingSink）
* 无 Span / OpenTelemetry / Dashboard / 实时流
* lint unavailable（环境未安装 ruff / flake8；未新增工具）
```
