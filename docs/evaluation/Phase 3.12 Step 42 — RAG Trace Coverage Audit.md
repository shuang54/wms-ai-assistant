# Phase 3.12 Step 42 — RAG Trace Coverage Audit

> **本阶段只做审计（audit-only）**：不新增 RAG Trace 持久化、不新增表 / Repository、
> 不修改 RAG Core / Assistant Trace API / 数据库 schema。
>
> 问题：当前 RAG 执行链路已经有哪些可观测数据？是否足以与
> `assistant_request_id` 建立**可靠关联**？缺口在哪里？
>
> 前置：Step 41 已使 Assistant Trace = LLM（PostgreSQL）+ Tool（PostgreSQL）。

---

## 1. Current Architecture

```text
POST /api/ai/chat
        ↓
AIOrchestratorService.execute()          ← request_id = A（唯一 Assistant Trace ID）
        │  with assistant_trace_scope(A)  ← Step 36（contextvar；覆盖整段执行）
        ↓
AIRouterService.route()
        ↓
_run_rag(decision, question, request_id=A)
        │  metadata += request_id（Step 35）
        ↓
RagService.answer(query, *, top_k, knowledge_scope)
        │  time.perf_counter() → elapsed_ms（仅日志）
        ├── 1. VectorSearchService.search()      [Embedding → knowledge DB 只读查询]
        ├── 2. 空结果 → 直接返回 canned answer（不调 Reranker / 不调 LLM）
        ├── 3. RerankerClient.rerank()           [仅 settings.reranker.enabled=true]
        ├── 4. ContextBuilder.build()            [片段选择 / 截断；无日志]
        ├── 5. LLMClient.chat([system, user])    ← LLM Usage（Step 36 关联 A ✅）
        └── 6. 映射 VectorSearchResult[] → RagSource[]（含 content）
        ↓
RagResponse(answer, sources, used_chunks_count)   ← **无 request_id / 无 latency 字段**
        ↓
AIOrchestrationResult(route, content, data, metadata{…, request_id})
        ↓
ChatResponse.data = { sources[5 字段], used_chunks_count }   ← content 被剥离
```

层标注：

```text
AIOrchestrator      [已有 request_id]  [已有日志]  [无持久化]
RagService          [无 request_id]    [已有日志]  [无持久化]
VectorSearch(检索)   [无 request_id]    [已有日志]  [无持久化]（仅只读知识查询）
Reranker            [无 request_id]    [已有日志]  [无持久化]（默认关闭）
ContextBuilder      [无 request_id]    [无日志]    [无持久化]
LLM（RAG 内）        [间接有 request_id：Step 36 scope → llm_usage_record]  [已持久化]
Tool（RAG 路由）      —（RAG 不执行 Tool；0 条 Tool Record 实测）
```

## 2. Current Observability

| Component | request_id | persistence | observable fields（现状） |
| --- | --- | --- | --- |
| AIOrchestrator | ✅ 生成 + 透出（`metadata.request_id`） | ✗ | route / decision_source / route_reason / knowledge_scope / rag_used_chunks |
| RagService | ✗（**执行期可读**，但不记录） | ✗（仅 logger） | query_length · top_k · result_count · used_chunks_count · context_truncated · context_chars · reranker_used · rerank_elapsed_ms · elapsed_ms |
| VectorSearch（检索） | ✗ | ✗（仅 logger） | query_length · top_k · result_count · elapsed_ms（+ embedding 调用在 EmbeddingClient 内） |
| Reranker | ✗ | ✗（仅 logger，2 处） | 模型加载 / 重排日志（无 request_id） |
| ContextBuilder | ✗ | ✗（**无日志**） | 返回值 `text / used_chunks / total_chars / truncated`（仅供调用方，不落库） |
| LLM（RAG 内调用） | ✅ **间接**（Step 36：`llm_usage_record.assistant_request_id` = A） | ✅ `ai_ops.llm_usage_record` | provider / model / prompt_tokens / completion_tokens / total_tokens / request_id(=Provider ID) |
| Tool（RAG 路由） | —（不产生） | —（0 条 Record，实测） | — |
| Assistant Trace API | ✅ `assistant_request_id` | ✅（读 LLM + Tool 两张表） | `{assistant_request_id, llm_usage[], tool_executions[]}`（**无 RAG 段落**） |

## 3. Current RAG Fields

```text
AVAILABLE（当前真实存在）
    · request_id（Assistant Trace ID）          来源：Orchestrator metadata（Step 35）
    · knowledge_scope（仅 namespace 回显）      来源：Orchestrator metadata（Phase 3.8.4）
    · rag_used_chunks                          来源：Orchestrator metadata
    · used_chunks_count                        来源：RagResponse / HTTP data
    · sources[]：chunk_id / document_id / chunk_index / similarity / metadata
                                              来源：/api/ai/chat RAG data
    · 检索事实（仅日志，不返回、不落库）：
        query_length · top_k · result_count · used_chunks_count ·
        context_truncated · context_chars · reranker_used ·
        rerank_elapsed_ms · elapsed_ms
    · RAG 内 LLM 调用的 usage → ai_ops.llm_usage_record.assistant_request_id = A ✅

NOT AVAILABLE
    · retrieval_count / top_k 的**响应或持久化**形态        （只在日志）
    · retrieval_duration / embedding_duration / reranker_duration 的持久化
      （仅有日志 elapsed_ms / rerank_elapsed_ms）
    · chunk_ids / document_ids（**命中明细不可回放**；同名 ID 仅在响应 sources 中）
    · reranker 前后分数变化 / 被丢弃候选
    · "是否命中 RAG" 的持久化事实（无法从 DB 重建"这次请求走了 RAG"）
    · RAG 与 assistant_request_id 的**持久化关联**（Scope 内可读，但没有记录点）
```

关键事实（实测）：

```text
· RAG 执行期间 `current_assistant_request_id()` **已经返回 A**
  （tests/test_rag_trace_coverage_audit.py::test_rag_runs_inside_assistant_trace_scope
   —— Fake RAG 在 answer() 内捕获到的值 == 响应 metadata.request_id）
  ⇒ 关联键"读得到"，缺的只是"记录点"
· RAG 路由 0 条 Tool Record（tool_executions 不因 RAG 产生数据）
· RAG metadata 只有 5 个键（含 request_id），无任何耗时 / 命中明细
· HTTP data 只有 sources + used_chunks_count（无 retrieval / latency 字段）
```

## 4. Security Classification

```text
Trace-safe（可考虑作为未来诊断元数据；本阶段**不实现**）
    · used_chunks_count / result_count / top_k
    · context_truncated / context_chars
    · elapsed_ms / rerank_elapsed_ms / reranker_used（数值型耗时）
    · chunk_id / document_id / chunk_index（identifier；需评估是否需要脱敏）
    · similarity（数值；可能与内容推断相关，需按产品策略决定）

Potentially sensitive（默认**不外泄**；仅可在受控内部路径评估）
    · 用户原始 query（本审计确认：日志只记 query_length，不记原文 ✅）
    · similarity + 小语料库组合（可能反推文档内容）
    · metadata（知识库自定义字段，内容不可控）

Must never expose（保持现状禁止）
    · chunk content / document content（知识库原文）
    · embedding vector / distance 原始值
    · LLM prompt / messages / raw response
    · database connection / SQL / credentials / API key / authorization
    · ORM Session / Engine / Repository 对象

判定要点：**Knowledge Chunk 原文不得默认视为 Trace-safe**
（`RagSource.content` 存在于服务层 DTO，但 `/api/chat` 与 `/api/ai/chat`
在 HTTP 层显式剥离；`/api/rag/answer` 历史上返回 content —— 见 §5）。
```

## 5. API Comparison

| 维度 | `POST /api/rag/answer` | `POST /api/chat` | `POST /api/ai/chat`（RAG 路由） |
| --- | --- | --- | --- |
| 入口 Service | `RagService.answer` | `ChatService` → `RagService.answer` | `AIOrchestratorService` → `RagService.answer` |
| 同一 RagService 类 | ✅ | ✅ | ✅ |
| Assistant Trace ID | ✗（无 metadata，模块内无 request_id） | ✗（无 metadata） | ✅ `metadata.request_id` |
| trace scope（Step 36） | ✗（旧链路 → `llm_usage_record.assistant_request_id = NULL`） | ✗（同上） | ✅（A） |
| 检索 / 耗时元数据 | ✗（仅 `answer` / `sources[含 content]` / `used_chunks_count`） | ✗ | ✗（仅 sources 元数据 + used_chunks_count） |
| sources 字段 | chunk_id · document_id · chunk_index · **content** · similarity · metadata | chunk_id · document_id · chunk_index · similarity · metadata | 同 `/api/chat` |
| Tool Record | 0 | 0 | 0（RAG 不执行 Tool） |
| 错误语义 | 共享 `_rag_error_mapping`（400 / 422 / 503 / 502 / 500） | 同左 | Orchestrator 族（400 / 403 / 404 / 422 / 502 / 503 / 500） |
| 可持久化重建 | ✗ | ✗ | 部分：LLM usage ✅ / 检索 ✗ / Tool —（0 条） |

## 6. Gap

```text
Assistant Trace 今天**可以**重建：
    · 这次请求走了哪条路由（metadata.route）
    · 这次请求的 LLM 调用与 token（llm_usage_record.assistant_request_id = A）✅
    · 这次请求的 Tool 执行（tool_execution_record.request_id = A）✅

Assistant Trace 今天**不可以**重建：
    · 这次请求是否走了 RAG（无 RAG 记录）
    · 检索到了什么（chunk_id / document_id / similarity 均未持久化）
    · 检索耗时 / 重排耗时 / 是否截断（仅在日志中，且日志无 request_id → 无法按请求检索）
    · RAG 执行与 assistant_request_id 的持久化关联（Scope 内可读但无人写入）
    · 跨进程 / 多 worker 的 RAG 事实回放（日志不可查询、无留存保证）

根本原因（三个，且都不是 bug）：
    1. RagService / VectorSearch / Reranker / ContextBuilder **没有 request_id 入参**，
       也不读取 assistant_trace scope（设计上"纯业务"，观测由外层负责）；
    2. RagResponse 是**回答契约**（answer / sources / used_chunks_count），
       不含耗时与检索统计（统计只在 logger）；
    3. 检索层**没有持久化边界**：Current RAG has no persistent execution record。
```

## 7. Recommendation

```text
Recommendation: **Defer implementation.**

理由：
    · Step 41 已使 Assistant Trace 覆盖 LLM + Tool；RAG 检索事实属于
      "更细粒度诊断"而不是"链路关联"——当前关联能力（metadata.request_id +
      llm_usage）已足以定位"哪些请求走了 RAG" *（前提：RAG 的 LLM 调用已接线；
      生产默认仍是 NoopAccountingSink）*；
    · 若要做，最小形态应当是 **retrieval metadata only**（Trace-safe 集合），
      且必须新开阶段明确：数据源（日志 vs 持久化）、安全白名单、
      与 Runtime / Persistent 隔离原则（对齐 Step 33 / Step 41 的既有结论）；
    · 本阶段**不**设计 RAGExecutionRecord / rag_execution_record 表 / Repository /
      Trace DTO（§九：只允许 Current State / Gap / Potential Boundary）。

Potential Boundary（仅记录，不实现）：
    Future RAG observability could expose retrieval metadata only
    （used_chunks_count / top_k / elapsed_ms / rerank_elapsed_ms /
      chunk_id · document_id · similarity），
    以与 assistant_request_id 关联为唯一目的，不落 chunk 原文。
```

## 8. Audit Tests

```text
tests/test_rag_trace_coverage_audit.py    22 passed（0 DB / 0 real LLM / 0 网络）
    TestRagInsideTraceScope（3）
        RAG 执行期 current_assistant_request_id() == 响应 metadata.request_id ✅
        RAG 路由 0 Tool Record（collector + /api/observability/tools 双证）
        RAG metadata 现状 5 个键；无耗时 / 命中明细键
    TestRagHttpExposure（2）
        sources 严格 5 字段（无 content）；chunk 正文不出现在响应文本
        data 无 retrieval / latency / top_k 字段（缺口记录）
    TestRagServiceCurrentState（4 + 参数化）
        RAG / Context / Reranker / VectorSearch 模块源码**无** request_id
        RAG / Context / Reranker 无 SQLAlchemy / DB import / 无写动词
        RagResponse / RagSource 字段现状锁定（无 latency / 无 trace id / 无 top_k）
        全 backend 无 rag_execution_record / rag_trace / retrieval_record / rag_repository
    TestRagLoggingCoverage（2）
        rag_service：日志 extra 覆盖 9 个检索事实键，且**无** request_id /
        无 query·answer·context·embedding（键名级断言）
        vector_search_service：4 个键，无 request_id
    TestLegacyRagEndpointCurrentState（4）
        /api/rag/answer 无 trace id（源码 + DTO）；含 chunk content（历史行为）
        /api/ai/chat 自带 5 字段 source 模型（元数据白名单）
        两个端点共享 RagService 实现（但不共享 request context / 持久化）
    TestAssistantTraceHasNoRagSection（2）
        Trace schema 文本无 rag / retrieval / chunk / reranker / similarity
        Trace 响应仍严格 3 个顶层字段（无 RAG 段落）
```

## 9. Regression

```text
python -m pytest -q tests/test_rag_trace_coverage_audit.py   → 22 passed
全量（no DB）                                                → 见汇报
DB-gated（RUN_DB_TESTS=1）                                   → 见汇报（只读；0 写入）
python -m compileall -q backend tests                        → 0 errors
LSP（新增文件）                                              → 0 error / 0 warning
lint → unavailable（未安装 ruff / flake8；未新增工具）
```

## 10. Limitations

```text
* 审计基于静态阅读 + 现有测试 + 无 DB 的 fake 链路；未做真实检索压测
* Reranker 默认关闭（settings.reranker.enabled=false），其日志字段来自代码阅读
* "RAG 是否持久化" 的结论基于当前仓库（无 rag_* 表 / 无 Repository / 无 migration）
* 生产 LLM accounting sink 仍未接线（默认 Noop）→ RAG 的 LLM 关联在生产默认不可用
* 未评估 RAG 事实在**多 worker / 日志聚合**层面的可检索性（属未来阶段）
* 本阶段 0 生产代码改动、0 DB 变更、0 新端点、0 新表
```
