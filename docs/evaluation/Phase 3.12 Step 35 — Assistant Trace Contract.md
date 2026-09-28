# Phase 3.12 Step 35 — Assistant Trace Contract

> 基于 Step 34 审计结论（`/api/ai/chat` 已是 Canonical Assistant 入口；
> 唯一缺口 = 调用方拿不到可关联的 trace id），本阶段只做一件事：
> 把 Orchestrator 内部**已经生成**的 `request_id` 透出到成功响应。
> **不新增 Chat API / 不改路由 / 不改 Tool 执行语义 / 不改数据库。**

---

## 1. Scope

```text
修改
    backend/app/services/ai_orchestrator_service.py
        · execute() 把新生成的 request_id 透传给三条路径
        · _run_rag / _run_tool / _run_text_to_sql 的 metadata 增加 request_id
          （T2S refusal 结果同样携带）
    backend/app/api/orchestrator_chat.py
        · 仅文档：ChatResponse.metadata 语义 + 端点 Trace Contract 说明
          （**无逻辑改动**：metadata 原本就原样透传）
测试
    tests/test_chat_api.py（C40 = 16）
    tests/test_ai_orchestrator.py（+6 service 层 trace 用例）
    tests/test_ai_orchestrator_tool_observability.py / test_text_to_sql_context.py /
    test_semantic_schema_filter.py / test_tool_observability_composition.py
    （既有断言按 Step 35 新契约同步）
文档
    docs/api.md（§2.9）/ docs/architecture.md（§8.52）/ 本文件

不做
    新 Chat API / Conversation / Message / Memory / Session / Agent / LangGraph /
    MCP / Streaming / WebSocket / Dashboard / Prometheus / OpenTelemetry /
    Redis / Kafka；不改 Router 核心行为 / RAG / Tool Registry /
    ToolExecutionService / ToolExecutionRecord / Tool Observability /
    Text-to-SQL / SQL Validator / SQL Executor / LLM Usage Repository / DB 结构；
    不改 /api/chat · /api/rag/answer · /api/chat/with-tools
```

## 2. Current Problem

```text
Step 34 审计（现状）

    AIOrchestratorService.execute()
        request_id = new_request_id()   ← 已存在（Phase 3.11 Step 18）
            ↓ ToolExecutionContext / ToolExecutionRecord（落库、可查询）
            ↓ ✗ 不进 API response

    结果：一次 /api/ai/chat 请求结束后，调用方**无法**把这次 Assistant 请求
          与 ai_ops.tool_execution_record 中的观测记录对齐（只能靠时间近似）。
```

## 3. Trace ID Source

```text
唯一生成点：AIOrchestratorService.execute()（一次 execute 一个）
生成函数：new_request_id()（项目既有；uuid4 字符串，36 字符）
            **未重新设计 ID 格式**（无 chat_ / assistant_ / trace_ / timestamp_ 前缀）

数据流：

    execute()  ─ request_id ─┬─ _run_rag(...)          → metadata["request_id"]
                             ├─ _run_tool(...)         → metadata["request_id"]
                             │      └─ ToolExecutionContext(request_id=...)
                             │             └─ ToolExecutionRecord.request_id
                             └─ _run_text_to_sql(...)   → metadata["request_id"]
                                                        （refusal 亦携带）

API 层（orchestrator_chat.py）
    · 不 import uuid / 不调用任何 ID 生成器（C40.3，静态断言）
    · metadata 原样透传（dict(result.metadata)）；Orchestrator 未给则不补齐

同一 ID（TOOL 路径，实测）：
    response.metadata.request_id == ToolExecutionRecord.request_id
```

## 4. Response Contract

```text
成功响应（三条路由一致；示例为 TOOL）

{
  "route": "tool",
  "content": "material_code=MAT-001 qty=120",
  "data": { "tool_name": "get_inventory", "success": true, "data": {...}, "error": null },
  "metadata": {
    "decision_source": "tool_match",
    "route_reason": "命中 get_inventory 别名",
    "tool_name": "get_inventory",
    "tool_success": true,
    "request_id": "3f0b2c1e-…-9a7d"        ← 本阶段唯一新增键
  }
}

· AIOrchestrationResult 字段结构不变（route / content / data / metadata）
  —— request_id 只进 metadata，**不**新增 DTO 顶层字段（C40.4）
· metadata 既有键全部保留、语义不变（decision_source / route_reason /
  knowledge_scope / tool_name / tool_success / sql / row_count / truncated /
  execution_time_ms / selected_tables / project_id / rag_used_chunks / refused / …）
· request schema 不变（question / project_id）；请求体传入 request_id 会被忽略
```

## 5. Tool Correlation

```text
HTTP → Orchestrator → 执行边界 → Record（全部真实对象，0 LLM / 0 真实 Tool Handler）

    POST /api/ai/chat {"question": "查询物料 MAT-001 当前库存",
                       "project_id": "project-a"}
        ↓
    AIOrchestratorService.execute()  → request_id = R
        ↓ TOOL 路径 → ToolExecutionService（真实边界）
        ↓ ToolExecutionContext(request_id=R, round=1, tool_call_id=None)
        ↓ InMemoryToolExecutionCollector（observer）

断言（tests/test_chat_api.py::TestC40AssistantTraceContract）：
    response.metadata.request_id == collector.records()[0].request_id     ✅
    records[0].round == 1                                                 ✅
    records[0].tool_call_id is None                                       ✅
    records[0].tool_name == "get_inventory"                               ✅

→ 调用方拿到 request_id 后即可查 `GET /api/observability/tools/history`
  （响应项含 request_id）或按需扩展 Repository 的 get_by_request_id。
```

## 6. RAG / T2S Isolation

```text
RAG        metadata.request_id 存在（非空）；Tool Record = 0
Text-to-SQL metadata.request_id 存在（非空）；Tool Record = 0（含 refusal 结果）
（service 层：tests/test_ai_orchestrator.py::TestAssistantTraceId；
  collector 语义：tests/test_ai_orchestrator_tool_observability.py::TestCollector
  —— RAG / T2S → 0 Record 既有断言保持不变）

并发唯一性：3 次独立 execute → 3 个不同 request_id（轻量 deterministic，无压力测试）
```

## 7. Security

```text
metadata 只能出现既有键 + request_id；实测断言（dict keys 级）不含：
    api_key / password / authorization / database_url / connection_string /
    sqlalchemy / session / prompt / traceback / arguments / tool_result / data

request_id 本身：非空字符串（None / "" 均视为违约）
request_id 不进入：Tool arguments / LLM messages / Prompt / LLM response
（service 层断言：request_id not in json.dumps(handler.last_arguments) 等）

项目作用域未变：project_id 仍由服务器端 ProjectContext / capability 决定，
客户端无法通过 body 开启新能力（Step 34 结论保持；本阶段 0 改动）。
```

## 8. Error Contract

```text
本阶段错误响应**完全不变**：

    400 AIOrchestratorInputError · 403 capability · 404 project 未注册 ·
    422 Pydantic · 502 RouteError · 503 UnavailableError · 500 Execution/未预期

实测：502 响应体 == {"detail": "..."}（无 request_id 键，响应文本亦不含 request_id）
错误 Trace Contract（把 trace id 加入错误 envelope）属未来统一 Error Contract 阶段。
```

## 9. Tests

```text
tests/test_chat_api.py                                          46 passed（+16 C40）
    C40.1/C40.5 三路由（rag / tool / t2s）metadata.request_id 透出（parametrize）
    C40.2/C40.3 唯一来源静态断言（Orchestrator 调用 new_request_id() 1 次；
                 API 无 uuid / new_request_id；无 uuid import）
                 + 行为：Orchestrator 未给 → API 不补齐
    C40.4  AIOrchestrationResult 字段 = route/content/data/metadata
    C40.6  真实链路：HTTP → 真实 Orchestrator → 真实边界 → Collector，
                 response.request_id == record.request_id（round=1 / call_id=None）
    C40.7  RAG 真实链路 → request_id 存在 + 0 Tool Record
    C40.8  请求体 schema = {question, project_id}；客户端传 request_id 被忽略
    C40.9  502 错误 envelope 不变、无 request_id
    C40.10 metadata 无敏感键；request_id 非空
    C40.11 /api/chat 模块无 ID 生成、响应字段不变
    C40.12 /api/chat/with-tools 模块无 ID 生成；/api/rag/answer 同；三处响应模型不变

tests/test_ai_orchestrator.py                                   43 passed（+6）
    TestAssistantTraceId：RAG / TOOL / T2S 三路径 metadata.request_id；
    TOOL：metadata.request_id == ToolExecutionContext.request_id（round=1 /
    tool_call_id=None）；唯一性（3 次 execute → 3 个 ID）；DTO 结构不变；
    request_id 不进入 arguments / 下游调用记录

既有断言同步（当前状态契约，非放宽）
    test_ai_orchestrator_tool_observability.py：Step 18「metadata 不含 request_id」
        → Step 35「metadata 含 request_id 且 == context.request_id」（同时仍断言
          project_id / arguments / 结果数据不出现）
    test_text_to_sql_context.py（3）/ test_semantic_schema_filter.py（1）：
        `_run_text_to_sql(...)` 直接调用点补 `request_id=...`
    test_tool_observability_composition.py：Composition Root 断言由
        「源码不含 new_request_id」改为「不调用 ID 生成器 / 不 import uuid」
        （文档字符串允许说明 Trace Contract）

全量（no DB）                          3754 passed / 406 skipped（0 failed）
DB-gated 定向（真实 DB Tool 链路 + 本阶段测试）  268 passed（0 failed）
0 真实 LLM / 0 新增 DB 写入 / 0 新增网络调用
```

## 10. Limitations

```text
* 错误响应仍不含 trace id（统一 Error Contract = 未定义，属未来阶段）
* LLM usage 仍不可按 chat 请求关联（llm_usage_record.request_id 是 Provider 响应 ID；
  且 create_llm_client() 默认 NoopAccountingSink）—— 需要时另立步骤评估
* RAG 无持久化延迟 / 检索元数据（仅日志；不含 request_id）
* /api/chat/with-tools 与 /api/ai/chat 仍是两套 Tool 执行模型（Step 34 记录，未合并）
* Conversation / Memory / 多轮上下文 = NOT IMPLEMENTED（未在本阶段触碰）
* request_id 目前只透出、未提供按 request_id 过滤的 HTTP 查询参数
  （history 接口的 request_id 过滤属未来检索增强，未实现）
* lint unavailable（环境未安装 ruff / flake8；未新增工具）
```
