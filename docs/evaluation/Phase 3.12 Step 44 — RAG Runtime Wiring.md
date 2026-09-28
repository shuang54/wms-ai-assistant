# Phase 3.12 Step 44 — RAG Runtime Wiring

> 把 Step 43 的 RAG Runtime Observation 边界接入**真实生产装配**
> （`/api/ai/chat` → 默认 Orchestrator → 应用级 RagService）。
>
> **无** PostgreSQL / 表 / Repository / 新 HTTP API；Assistant Trace、
> LLM Usage、Tool Persistence、DB Schema、RAG 核心、Router 核心全部未改。
> 0 DeepSeek / 0 网络 / 0 数据库写入。

---

## 1. Before（Step 43 结束时）

```text
RagService(observer=None)                       ← 默认值（未接线）
        ↓
RAG Runtime Observation 边界存在，但**生产链路不产生任何观测**
        ↓
InMemoryRagExecutionCollector / RagObservabilityQueryService 仅测试可达

约束（为什么不能"顺手接线"）：
  · api/orchestrator_chat.py（Composition Root）**不得** import RagService
    （既有纵深防御契约 test_api_module_does_not_import_forbidden_services）；
  · Composition Root **不得**使用 get_default_* 工厂
    （既有契约 test_no_collector_factory_helper）；
  · AIOrchestrator 核心执行逻辑（execute / 三条路由）不得修改。
```

## 2. After（本阶段）

```text
backend/app/services/rag_observability_runtime.py（新；Service 层 runtime 装配）
    _RAG_EXECUTION_COLLECTOR = InMemoryRagExecutionCollector()   ← 唯一创建点
    _RAG_SERVICE = RagService(observer=_RAG_EXECUTION_COLLECTOR)  ← 应用级单实例
    get_observed_rag_service()          （取引用，**非**工厂；无 get_default_*）
    get_rag_execution_collector()
    get_rag_observability_query_service()（只读 Facade；无 HTTP API）

backend/app/api/orchestrator_chat.py（Composition Root；**2 行 + 注释**）
    _default_orchestrator = AIOrchestratorService(
        ...,
        rag_service=get_observed_rag_service(),      ← Step 44 接线
    )

链路（真实生产路径）：
    POST /api/ai/chat
        ↓
    AIOrchestratorService.execute()（真实）→ request_id = A + assistant_trace_scope(A)
        ↓
    AIRouterService（真实；规则命中 RAG）
        ↓
    RagService（**应用级已接线实例**；observer = 应用级 Collector）
        ↓
    RagExecutionObservation.request_id = A
        ↓
    InMemoryRagExecutionCollector（进程内；有界 1000）
        ↓
    RagObservabilityQueryService（内部只读；无新端点）
```

项目级路径同样生效（**无需**改 Factory）：

```text
_build_orchestrator_for_project_id(project_id)
    → build_orchestrator_for_project(project_id, base=_default_orchestrator)
    → rag_service=base._rag          ← 复用**同一个**已接线实例（Factory 未改）
```

## 3. Old API（刻意不接线）

```text
/api/rag/answer   → api/rag._rag_service（模块级实例；observer=None）→ 0 观测
/api/chat         → ChatService._rag_service（默认实例；observer=None）→ 0 观测

理由（Step 43 §六 + 本阶段 §六）：
    旧链路没有 assistant_request_id（无 trace scope）→ 即使注入 observer
    也不会记录；**不为"统一"给旧 API 伪造 request ID**，也不改其响应结构。
实测：E2E 断言两个旧端点仍 200 + 应用级 Collector 0 观测 +
     两个实例的 _observer is None（未接线身份）。
```

## 4. Correlation

```text
POST /api/ai/chat（RAG 路由）
        response.metadata.request_id = A
                ==
        RagExecutionObservation.request_id = A      （应用级 Collector 内）
                ==
        同一 Scope 内的 ai_ops.llm_usage_record.assistant_request_id（Step 36）

实测（tests/test_rag_runtime_observability_e2e.py）：
    真实 create_app() → /api/ai/chat → 真实 Composition Root / Orchestrator /
    Router / RagService → 观测自动产生（**不是**手工注入 collector）
    同一 Collector 跨请求复用；A / B 请求互不串；clear() 后查询为空
```

## 5. Security

```text
13-field whitelist unchanged（Step 43 契约逐字保持）：
    request_id · started_at · finished_at · duration_ms · result_count ·
    used_chunks_count · top_k · context_truncated · context_chars ·
    reranker_used · rerank_elapsed_ms · chunk_ids · document_ids
（**无** similarity / query / content / prompt / SQL / 凭据）

HTTP response 契约未变：{route, content, data, metadata}；未新增
    rag / retrieval / observations / chunks / latency 等键；
    RAG data 仍只有 sources（5 字段）+ used_chunks_count；
    chunk 正文（sentinel）不出现于响应文本。
```

## 6. Persistence

```text
NO
    · 无表（Base.metadata 无任何 "rag" 表）· 无 Repository · 无 Migration
    · 存储 = 进程内 Collector（重启即丢失；Runtime Only 语义不变）
    · 无 TTL / 无后台任务 / 无新依赖（Redis / Kafka / OTel 均未引入）
```

## 7. Assistant Trace

```text
UNCHANGED
    · AssistantTraceResponse 仍严格 {assistant_request_id, llm_usage, tool_executions}
    · /api/observability/assistant-trace/{id} 未加入 RAG / retrieval / sources
    · Assistant Trace 与 RAG Runtime 观测**并列**（未来是否合并由后续阶段决定）
```

## 8. Failure Isolation（生产装配下的复核）

```text
Collector.record 抛异常 → RagService 收敛为 warning：
    POST /api/ai/chat → 200 · route=rag · content 正常 · metadata.request_id 存在
    → 观测失败**不**产生业务失败（Step 43 隔离契约在真实链路实测）
```

## 9. Tests

```text
tests/test_rag_runtime_observability_e2e.py   16 passed（0 DB / 0 网络 / 0 real LLM）
    ProductionWiring（3）：RAG E2E 产生观测 + 字段与 Fake 边界一致 /
        QueryService 关联查询 / 装配身份（默认 Orchestrator._rag == 应用级实例；
        旧链路实例 observer=None）
    Lifecycle（4）：同一 Collector 跨请求 / A·B 隔离 / clear / 重复请求确定性
    NonRag（3）：TOOL 路由 0 RAG 观测（且 Tool Record 正常 1 条 + handler 被真实
        执行边界调用）/ 旧 /api/rag/answer 0 观测 / 旧 /api/chat 0 观测
    Failure（1）：record 抛错 → HTTP 200 + RAG 成功
    Contract·Security（5）：响应键集合 / 无敏感值 / 观测仍 13 字段 /
        无新 HTTP API / 无 rag 表 + 无 Repository·Session
Fake 边界仅：Vector Search（embedding + DB）· LLM transport ·（Tool 用例）
    Tool Handler 的 DB 边界 + Tool 持久化 Adapter 的 DB **写**边界
    （计数 no-op；fan-out 仍真实发生 → DB writes = 0）；
    Orchestrator / Router / RagService / Collector /
    QueryService / Composition Root **全部为真实代码**。
既有契约测试保持：test_tool_observability_composition（31，含
    "Composition Root 无 get_default_* 工厂"）· test_chat_api（47）·
    Step 43 单测（44）· Step 42 审计（22）
```

## 10. Limitations

```text
* 观测仍**只在进程内**（多 worker / 重启后不可查询；持久化属未来阶段）
* 未接入 Assistant Trace（两个读模型并列；合并需单独阶段）
* 无 metrics / 聚合（count / 平均耗时等未实现）
* 无 HTTP 读端点（QueryService 仅供内部使用）
* 项目级路径的 Tool/RAG 观测共享同一 Application lifetime（多 worker 下
  每个 worker 各一份内存视图 —— 既有 Tool Runtime 观测的同一限制）
* lint unavailable（环境限制；未安装 ruff / flake8）
```
