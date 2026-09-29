# Phase 3.12 Step 59 — Assistant Trace Pagination Boundary Audit

> **Audit only**：未实现 Pagination / Cursor / Unified Timeline；未改 HTTP contract /
> 查询语义 / 排序 / 表结构 / 索引 / Prompt。
> 新增：`tests/test_assistant_trace_pagination_audit.py`（14 离线 + 2 DB-gated）。
> **Production code changes = 0 · DB schema changes = 0 · API contract changes = 0**

---

## 1. Scope

```text
问题：当前 Assistant Trace 的数据规模 / 排序 / 查询方式是否已到必须分页的程度？
范围：只读审计（HTTP contract · 规模上限 · 排序稳定性 · 查询次数 · 索引 · 当前规模 · payload 安全）
非目标：不设计 / 不实现 Pagination；不决定 OFFSET vs cursor；不新增索引 / 参数 / 字段
```

## 2. Current HTTP Contract

```text
GET /api/observability/assistant-trace/{assistant_request_id}
    parameters      = [assistant_request_id (path)]        ← 仅 1 个参数
    query parameters= **无**
    response 字段    = assistant_request_id · llm_usage[] · tool_executions[] · rag_executions[]

Pagination = **NOT IMPLEMENTED**
    limit        ✗     offset      ✗     page / page_size  ✗
    cursor       ✗     total       ✗     has_more         ✗
    next_cursor  ✗     truncated   ✗
（审计测试逐字锁定 4 个顶层字段 + 9/11/13 个子 DTO 字段集合：任何新增字段都会触发失败）
空 Trace（三段皆 []）→ 200（合法）；下游失败 → 502（不降级为空 Trace）
```

## 3. Current Trace Size（来源：真实代码常量与调用结构）

```text
Component | Current Max | Source
----------+-------------+--------------------------------------------------------------
LLM Usage | 见下         | Router 规则优先（默认装配**未注入** llm_client → fallback 不可达）·
          |             | TextToSQLService.DEFAULT_MAX_ATTEMPTS = 3（settings.text_to_sql.max_attempts = 3，
          |             | 构造钳制 [1, 10]）· RagService 每次 answer 1 次 LLM
Tool      | 1           | AIOrchestrator._run_tool：round=1 固定 · "one execute = one Tool
          |             | （无 round loop / 无 multi-step / 无 retry）"
RAG       | 1           | _run_rag → RagService.answer()（1 次；空检索记 1 条但不调 LLM）

按路由（同一请求只走**一条**路由）：
    RAG 路由    LLM 1 · Tool 0 · RAG 1  → 合计 2
    TOOL 路由   LLM 0 · Tool 1 · RAG 0  → 合计 1
    T2SQL 路由  LLM ≤3（重试）· 0 · 0    → 合计 ≤3
单 assistant_request_id 的**当前默认装配**绝对上限 = **3 条**
（代码级更坏情形：Router fallback 若被接线 +1 → ≤4；TextToSQLService 显式 max_attempts=10 → ≤11（+1 = 12））

MAX_LLM_CALLS_PER_ASSISTANT_REQUEST：**无此硬编码常量** ⇒
    "No explicit application-level upper bound"（上限由上述 clamp 常量间接限定）
ToolChat（/api/chat/with-tools）的 max_rounds=5 使用**独立 request_id**（不进入 Assistant Trace scope）
⇒ 不参与本上限
```

```text
Payload 规模（Step 54 实测，真实 DTO + 合成字段值）：
    典型 RAG 请求 ≈ 0.96 KiB · 典型 T2SQL ≈ 1.12 KiB · 上限场景（≤6 条）< 1.5 KiB
（不是生产取样；与"整表数据量"无关 —— 本端点是 **request 作用域**查询）
```

## 4. Ordering

```text
LLM : ORDER BY created_at ASC, id ASC      （LLMUsageRepository.build_trace_select）
Tool: ORDER BY id ASC                      （ToolExecutionRepository.build_request_select）
RAG : ORDER BY id ASC                      （RagExecutionRepository.build_request_select）

四者（三段 + 顶层）均**无** LIMIT / OFFSET；HTTP 层不重排（保留下游顺序）
```

## 5. Ordering Stability

```text
Stable = **YES**
    LLM  : created_at 可能同毫秒 → **id 提供 tie-breaker**（稳定）
    Tool : id 唯一主键（稳定）
    RAG  : id 唯一主键（稳定）
Potential risk = **NONE**（当前实现不含无 tie-breaker 的排序；未发现"同 timestamp 乱序"风险）
（审计测试静态断言三段排序含 id.asc() 且无 .desc() / 无 .limit( / 无 .offset(）
```

## 6. DB Queries

```text
LLM query count = 1   （LLMUsageRepository.list_by_assistant_request_id → 1 × session.execute）
Tool query count = 1  （ToolExecutionRepository.get_by_request_id  → 1 × session.execute）
RAG query count = 1   （RagExecutionRepository.get_by_request_id   → 1 × session.execute）
Total = **3 次 SELECT / 每 Trace 请求**（三段独立 Query Service；顺序组合，非 JOIN）

无 N+1：三个 ORM Model 均 **无 relationship**（零 lazy loading；columns = 9 / 12 / 14）
组合层（AssistantTraceQueryService）只调用 3 次下游读边界（审计测试用计数替身断言各 1 次）
```

## 7. Index Audit

| 段 | Query Predicate | Existing Index | Type | Unique | Valid |
| --- | --- | --- | --- | --- | --- |
| LLM | `assistant_request_id = :p` | `ix_llm_usage_record_assistant_request_id` | btree | No | Yes |
| Tool | `request_id = :p` | `ix_tool_execution_record_request_id` | btree | No | Yes |
| RAG | `request_id = :p` | `ix_rag_execution_record_request_id` | btree | No | Yes |

```text
同表其它（非本端点谓词，仅记录）：created_at（LLM）· started_at（Tool / RAG）·
    pkey(id) · uq request_id（LLM，幂等键）
**未新增索引**（三个谓词列在 Step 52 之前 / 同期既已建索引）
```

## 8. Current DB Volume

```text
ai_ops.llm_usage_record      rows = 0
ai_ops.tool_execution_record rows = 0
ai_ops.rag_execution_record  rows = 0

Current persistent volume = **0**（本地开发库；Step 56~58 的测试与 smoke 均已精确清理）
⚠ 不要把"整表数据量（0）"与"单 assistant_request_id 的 Trace 数据量（≤3 条）"混为一谈：
   本端点是 request 作用域查询，与表规模无强相关（谓词列已有索引）
```

## 9. Pagination Decision

```text
**DEFER**（暂不需要 Pagination）—— 依据：

 1) 单请求 Trace 上限 = 3 条（默认装配；代码级最坏 12 条）→ 不存在"大列表"
 2) Payload < 1.5 KiB（典型 < 1.2 KiB）→ 距任何网关/客户端阈值两个数量级
 3) 排序稳定（三段均含 tie-breaker）+ 谓词已有索引 + 仅 3 次 SELECT
 4) 三段排序键不同、Tool/RAG 不暴露主键 → 现在定分页契约（尤其 cursor）会先于需求固化错误设计
 5) 开发库 volume = 0，无真实分布支撑 page_size / 阈值

若采用分页（未来），设计方向（**本阶段不实现、不选择**）：
    A. independent pagination per component（每段独立 limit/cursor —— 与三段独立数据源一致）
    B. unified timeline pagination（需先有统一事件序 = 明确不在本项目范围）

触发条件（proposed engineering trigger；当前未满足）：
    * 单 assistant_request_id > 10 条 · 单段 > 100 条 · Trace JSON > 64 KiB
    * 客户端需要"浏览完整历史"（跨请求）· 出现 multi-round Tool（/api/ai/chat）·
      出现 Agent / loop · 出现真正统一 timeline · Trace 读路径 DB 耗时显著上升

PostgreSQL 事实（记录用，非决策）：LIMIT/OFFSET 可做分页，但大 OFFSET 仍需数据库
计算并跳过前列记录 → 若未来 Trace 规模显著增长，需重新评估 OFFSET vs keyset/cursor。
**本阶段不实现 cursor pagination。**
```

## 10. Security

```text
三段 payload 只含诊断 metadata（不含业务原文）：
    LLM  : id · assistant_request_id · request_id(provider) · provider · model ·
           prompt/completion/total_tokens · created_at          —— 无 prompt / messages / raw response
    Tool : request_id · round · tool_name · started_at / finished_at · duration_ms ·
           success · project_id · tool_call_id · error_code / error_type
           —— 无 arguments / ToolResult.data / SQL / traceback
    RAG  : request_id · started_at / finished_at · duration_ms · result_count ·
           used_chunks_count · top_k · context_truncated / context_chars ·
           reranker_used / rerank_elapsed_ms · chunk_ids / document_ids
           —— 无 query / answer / chunk 正文 / similarity / embedding
    secrets：无 API key / Authorization / DATABASE_URL / 凭据（DTO 字段集合被审计测试逐字锁定）
```

## 11. Limitations

```text
* 规模上限来自**静态阅读 + 常量**（未做真实混合流量采样；生产 volume 未接入）
* Payload 体积为 Step 54 的真实 DTO + 合成字段值测量（非生产取样）
* 未做延迟压测（本阶段禁止压力测试）；"3 次 SELECT" 为代码级事实，非实测耗时
* 未评估未来事件（multi-round Tool / Agent / unified timeline）的规模增长
* OFFSET / cursor 的最终选择被显式推迟（缺乏数据支撑）
```
