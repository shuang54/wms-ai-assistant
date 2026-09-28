# Phase 3.12 Step 40 — Assistant Trace Correlation Smoke

> 本阶段**不新增架构能力**：只验证 Phase 3.12 已建立的 ``assistant_request_id``
> 能否从 ``/api/ai/chat`` 一路贯穿到 LLM Usage 与 Tool Execution，并可通过
> Assistant Trace HTTP API 查询回来。
>
> **0 生产代码修改**（未发现 bug）；未新增 DTO / 端点 / 表 / Repository。

---

## 1. Test Architecture

```text
POST /api/ai/chat
      ↓  真实 AIOrchestratorService（真实 AIRouterService；Rule-first，LLM fallback 关闭）
assistant_request_id = A            ← new_request_id()（Step 35，真实）
      │  assistant_trace_scope(A)   ← Step 36，真实（contextvar）
      ├── LLM Usage
      │     真实 OpenAICompatibleClient（provider 传输 = httpx.MockTransport）
      │       + 真实 DatabaseLLMAccountingSink → 真实 LLMUsagePersistenceService
      │       → Repository（无 DB 用例：内存替身 / DB 用例：真实 PostgreSQL）
      │     assistant_request_id = A
      └── Tool Execution
            真实 ToolExecutionService → 真实应用级 InMemory Collector
            （Handler 为只读替身；Orchestrator / Router / 执行边界真实）
              request_id = A
      ↓
GET /api/observability/assistant-trace/{A}
     真实 AssistantTraceQueryService + 真实 Trace API
     （无 DB 用例：真实组合服务 + 真实 LLM 读边界（注入内存仓储）；
       DB 用例：**真实 accessor / 真实生产装配**，未 patch）
      ↓
AssistantTraceResponse
```

“允许 Fake”与“禁止 Fake”的边界（§十二）：

```text
允许 Fake：LLM Provider 传输层（httpx.MockTransport）、Tool Handler（只读替身）
禁止 Fake：assistant_request_id 生成与传播 · LLM Usage correlation ·
          Tool request_id · AssistantTraceQueryService · Assistant Trace API
          （全部为真实对象；DB 用例连 Repository 都是真实 PostgreSQL）
真实 LLM 调用 = 0（MockTransport 拦截，无网络）
```

## 2. Cases

```text
Case A  LLM Only      「采购入库的操作步骤是什么」→ route=rag
        trace.llm_usage >= 1（每行 assistant_request_id == A；request_id == Provider ID ≠ A）
        trace.tool_executions == []
        写入侧证据：内存 Repository 收到 assistant_request_id == A

Case B  Tool Path     「查询物料 MAT-001 当前库存」→ route=tool
        trace.tool_executions >= 1（每行 request_id == A；round=1；tool_call_id=None）
        trace.llm_usage == []（真实链路：Tool 路径不调用 LLM —— 不人为要求 >0）

Case C  LLM + Tool    同一请求内：Tool Handler 执行期间调用 LLM
        （模拟“工具内部使用 LLM”；真实 Client + 真实 Sink → 同一 Scope）
        trace.llm_usage >= 1 且全部 assistant_request_id == A
        trace.tool_executions >= 1 且全部 request_id == A
        → 一个 A 同时贯穿两类数据（本阶段核心 Case）

Cross Request         A（tool 路径）与 B（rag 路径）两次请求
        Trace A 只含 A 的数据；Trace B 只含 B 的数据
        A 的响应文本不含 B 的 ID；B 的响应文本不含 A 的 ID

Security              真实 application Trace 的 HTTP 响应：键级断言无
        prompt / messages / arguments / raw_response / secret / password /
        api_key / authorization / database_url / sql / session / connection /
        traceback / internal_debug / data / handler / registry；
        且不含 base_url / api_key 值 / 原始问题文本
Envelope              /api/ai/chat 响应仍为 {route, content, data, metadata}
        （metadata 仅新增 request_id；无新字段）
Regression            /observability/tools · /metrics · /history ·
        /metrics/persistent 状态码与结构未变
```

## 3. Correlation

```text
                 A（assistant_request_id）
                 │
       ┌─────────┴─────────┐
       ↓                   ↓
   LLM Usage          Tool Execution
assistant_request_id   request_id
       = A                 = A
       │                   │
       └─────────┬─────────┘
                 ↓
          Assistant Trace

字段对应（只记录 ID 与结构，不记录任何凭据 / 提示 / 参数）：
    /api/ai/chat                   → metadata.request_id            = A
    ai_ops.llm_usage_record        → assistant_request_id          = A
                                   → request_id（Provider 请求 ID）  = P ≠ A
    ai_ops.tool_execution_record   → request_id（Trace ID）          = A
                                   → round = 1；tool_call_id = None
    GET …/assistant-trace/{A}      → assistant_request_id           = A
                                   → llm_usage[].assistant_request_id = A
                                   → tool_executions[].request_id    = A
```

## 4. Tests

```text
tests/test_assistant_trace_correlation_e2e.py       7 passed（0 real LLM / 0 DB）
    test_llm_only_correlation · test_tool_correlation ·
    test_llm_and_tool_correlation · test_cross_request_isolation ·
    test_trace_security · test_chat_response_envelope_unchanged ·
    test_existing_apis_still_work

tests/test_assistant_trace_correlation_e2e_db.py    4 passed（RUN_DB_TESTS=1）/ 4 skipped
    test_llm_only_correlation_persists_to_postgres ·
    test_llm_and_tool_correlation_e2e · test_cross_request_isolation_e2e ·
    test_empty_trace_from_real_wiring
    （真实 PostgreSQL 落库 + **真实生产装配**的 Trace API；无 accessor patch）

DB 残留：provider 前缀 step40- 定向 DELETE → 0；Collector 清空；
未使用 TRUNCATE、未删除真实数据、未修改 schema。
```

## 5. Limitations

```text
* 未做真实 LLM（DeepSeek / SiliconFlow）smoke：项目无 real_llm 标记基础设施，
  未新增（§十三：没有可靠基础设施就不新增）
* 检索层（VectorSearch / Reranker）不在本 Smoke 范围：RAG 侧只验证
  “LLM 调用 → usage 关联”，未启动向量检索 / 重排
* Tool 数据源 = Runtime 内存（进程内 retention window）：跨进程 / 重启不覆盖；
  Persistent Tool History 未与 Trace 合并（Step 38/39 设计保持）
* 未验证多 worker 并发下的 Trace 聚合（无 Redis / 无共享存储）
* 未做性能 / 压力 / 并发基准（本阶段明确不优化性能）
* lint unavailable（环境未安装 ruff / flake8；未新增工具）
```
