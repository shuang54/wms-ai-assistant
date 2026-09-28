# Phase 3.12 Step 43 — RAG Runtime Observability

> 建立 **RAG Runtime Observability Boundary**（进程内、可选、best-effort）：
> `RagService` 执行 → `RagExecutionObservation` → `InMemoryRagExecutionCollector`
> → `RagObservabilityQueryService`。
>
> **不进入 PostgreSQL**：无表 / 无 Repository / 无 Migration / 无 HTTP API /
> 不改 Assistant Trace / 不改 RAG 核心算法 / 0 real LLM / 0 网络。
>
> 前置：Step 42 Audit（RAG 无持久化执行记录；关联键在 Scope 内可读但无记录点）。

---

## 1. Goal

```text
当前（Step 42 结论）：
    RAG 执行期间 current_assistant_request_id() == A（可读）
    但没有**任何** RAG 执行观测记录 → Assistant Trace 无法重建 RAG 事实

本阶段目标：
    RAG
     ↓
    Runtime Observation（进程内、有界、字段白名单）
     ↓
    assistant_request_id（与 /api/ai/chat metadata.request_id 同一 ID）
```

明确**不做**：RAG Persistence / rag 表 / Assistant Trace RAG 段 / 新 HTTP API /
Span·TraceId / metrics·聚合 / TTL / 后台任务 / Conversation·Memory·Agent·MCP。

## 2. Data Model

`RagExecutionObservation`（`frozen dataclass`；13 字段 = 安全白名单）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `request_id` | `str` | Assistant Trace ID（非空；≤128；**不生成**，来自 contextvar） |
| `started_at` / `finished_at` | `datetime`(UTC) | 诊断时间戳（不参与稳定性断言） |
| `duration_ms` | `float` | 端到端耗时（Vector Search → LLM） |
| `result_count` | `int` | Vector Search 候选条数（rerank 前） |
| `used_chunks_count` | `int` | 实际纳入 Context 的片段数 |
| `top_k` | `int` | 本次生效的 top_k（reranker 开关决定来源） |
| `context_truncated` | `bool` | Context 是否截断 |
| `context_chars` | `int` | Context 字符数 |
| `reranker_used` | `bool` | Reranker 是否参与（配置开关为准） |
| `rerank_elapsed_ms` | `float \| None` | Rerank 耗时；未使用 → `None` |
| `chunk_ids` | `tuple[int, ...]` | 去重 + **首次出现顺序**（不排序 / 不 set） |
| `document_ids` | `tuple[int, ...]` | 同上 |

契约（DTO 自身强制）：

```text
· request_id 非空 / 非纯空白 / ≤ 128（不生成 / 不推断 / 不截断）
· 计数 >= 0；duration_ms >= 0；finished_at >= started_at
· chunk_ids / document_ids：**去重且保持首次出现顺序**（__post_init__ 规范化）
· to_dict() = **显式字段映射**（不用 vars() / __dict__ / asdict / model_dump）
· **无** similarity（Step 42：第一版保持最小）
```

## 3. Lifecycle

```text
RagService.answer()                    ← 公开签名完全不变
        │  observer is None 或 current_assistant_request_id() is None → disabled（0 观测 / 0 开销）
        ↓ _ObservationDraft（enabled）
RagService._answer_pipeline()
        ├── mark_started（与日志 elapsed_ms 同一基准）
        ├── top_k 解析 → draft.top_k / draft.reranker_used
        ├── 1. Vector Search        → draft.result_count
        ├── 2. 空检索 → draft 保持 0 → **仍然产生 Observation**
        ├── 3. Reranker（可选）      → draft.rerank_elapsed_ms
        ├── 4. ContextBuilder       → draft.context_truncated / context_chars
        ├── 5. LLM
        └── 6. sources 映射         → draft.used_chunks_count / chunk_ids / document_ids
        ↓
_finish_observation(draft)
        ├── 成功 / 空检索：record(observation)     ← best-effort
        └── 异常：record(observation) → **re-raise 原异常**（`except BaseException`）
```

错误隔离（两层）：

```text
1) RAG 原始异常：先 `_finish_observation` 再 `raise`（同一实例，不被包装 / 不被替换）
2) 观测自身失败（observer.record 抛错 / DTO 构造异常）：只记 warning
   （仅记录 error_type，不带任何 payload）→ **RAG 成功不会变成失败**
3) 未绑定 Trace（旧链路 / 直连 Service）：**不记录**（无关联键的观测只会是噪声）
```

## 4. Correlation

```text
AIOrchestratorService.execute()
        request_id = A（唯一生成点；Step 35/36）
        with assistant_trace_scope(A)          ← contextvar
                ↓
        RagService.answer()                    ← 本阶段：读取（不是新增参数）
                ↓ current_assistant_request_id()
        RagExecutionObservation.request_id = A
                ↓
        == 响应 metadata.request_id（实测：service 层 Orchestrator E2E 断言）
        == ai_ops.llm_usage_record.assistant_request_id（Step 36，同一 Scope）
        == ai_ops.tool_execution_record.request_id（Tool 路由；RAG 路由 0 条）
```

## 5. Security

```text
安全字段（= DTO 全部 13 字段；与 Step 42 Trace-safe 分类一致）：
    request_id · started_at · finished_at · duration_ms · result_count ·
    used_chunks_count · top_k · context_truncated · context_chars ·
    reranker_used · rerank_elapsed_ms · chunk_ids · document_ids

禁止（永不进入 DTO / Collector）：
    query 原文 · answer · chunk content · document content · embedding vector ·
    similarity · prompt · LLM messages / raw response · SQL · DB 连接 ·
    凭据 / API Key / Authorization · traceback · project_id · tool_call_id

测试证据：
    · 字段白名单 == 13（dataclasses.fields + to_dict 键集合）
    · 敏感 sentinel（chunk 正文 / query / answer / DSN）不出现在 to_dict repr
    · 日志 extra 仍**不含** request_id（观测只在内存 DTO；日志语义未变）
    · 新模块静态：无 sqlalchemy / backend.app.db import；无
      insert·commit·rollback·flush·session·engine·repository 标识符；
      无 vars() / __dict__ / asdict / model_dump
```

## 6. Runtime Only

```text
No PostgreSQL / No Persistence：
    · rag 表：0（Base.metadata 无任何含 "rag" 的表）
    · Repository / Migration / ORM Model：0
    · 存储 = InMemoryRagExecutionCollector（deque(maxlen=1000) + 单锁）
    · Capacity：默认 1000（FIFO 淘汰最旧；实测 1001 → 保留最新 1000）
    · clear() = 唯一显式清空；无 TTL / 无后台线程 / 无定时任务
    · 生产装配**未接线**（observer 默认 None）：接线需要改 Orchestrator 或
      API 模块（两者均在本阶段禁止范围）→ 留待后续阶段单独决定
```

## 7. API

```text
No new HTTP API：
    · /api/observability/rag · /rag/history · /rag/metrics = 不存在
    · 观测端点集合与 Step 42 完全一致（5 条，无变化）
    · RAG 相关端点仍只有历史 /api/rag/answer
    · AssistantTraceResponse 仍严格 {assistant_request_id, llm_usage, tool_executions}
    · RagObservabilityQueryService 仅供内部测试 / 后续组合使用

旧链路（/api/rag/answer · /api/chat）行为不变：
    未绑定 Trace → 0 观测、0 行为变化（observer 也未被装配）
```

## 8. Tests

```text
tests/test_rag_runtime_observability.py    44 passed（0 DB / 0 real LLM / 0 网络）
    Lifecycle（10）：成功观测 / request_id == metadata.request_id（Orchestrator E2E）/
        空检索仍观测 / reranker disabled=false / reranker enabled 记录耗时与 top_k /
        观测失败隔离（record 抛错 → RAG 仍成功）/ RAG 异常原样抛出且产生观测 /
        observer=None → 0 观测 / 未绑定 scope → 0 观测 / observer 类型校验
    Identifier（2）：chunk 1,2,1 → (1,2) · document 10,10,30 → (10,30)；
        DTO 直接构造时同样规范化
    Collector（6）：默认 1000 有界（1001 → 保留最新 1000，最旧被淘汰）/
        显式 max_records FIFO / clear / records_by_request_id 精确匹配（含非法值）/
        非 Observation → TypeError / capacity 类型与取值校验
    Query Boundary（5）：lookup + latest / **校验先于查询**（None·123·bytes·""·"   "·
        >128 全部拒绝且 collector 0 访问）/ 128 长度边界通过 / 只读（无 record·clear）/
        collector 契约校验
    Runtime Isolation（2）：A / B 不串 / 退出 scope 后不再记录
    Security（4）：字段白名单 13 / 敏感 sentinel 不泄露 / 显式映射 + 无 DB import /
        新模块无持久化标识符
    Determinism（1）：同输入两次 → 稳定字段完全一致（时间 / 耗时除外）
    Runtime-only 边界（5）：无新 HTTP API / Assistant Trace schema 不变 /
        RAG 公开契约不变（answer 签名 + RagResponse 字段 + observer 默认 None）/
        无 rag 表 / _finish_observation 必须 try 包裹且不得 raise
Step 42 审计测试同步（tests/test_rag_trace_coverage_audit.py）：22 passed
    · 原有"全 RAG 模块无 request_id" → 收窄为"**下游核心**（VectorSearch /
      ContextBuilder / Reranker）无 request_id"（保持结论可验证）
    · 新增：RagService 的 request_id 仅用于 Runtime Observation
      （无函数参数 / 无日志 extra / 无持久化）
```

## 9. Limitations

```text
* 生产装配未接线（observer 默认 None）：接线需改 Orchestrator 的默认 RagService
  构造或 API 装配层 —— 均超出本阶段允许范围（Step 44+ 决定）
* 无聚合（无 count / 平均耗时 / 成功率）；无 metrics 边界
* 无持久化：进程重启即丢失（Step 42 已知缺口未消除，只是补齐 Runtime 侧）
* 未进入 Assistant Trace（Step 43 刻意保持 /assistant-trace 不变）
* 无 similarity（按 Step 42 结论保持最小白名单）
* RAG 观测只在 assistant_trace_scope 内产生：旧链路（/api/rag/answer）无观测
* lint unavailable（环境限制；未安装 ruff / flake8，未新增工具）
```
