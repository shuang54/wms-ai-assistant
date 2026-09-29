# Phase 3.12 Step 49 — Assistant Trace Unified Timeline Design

> **Design / Contract Audit only**：未实现 Unified Events；未修改
> `AssistantTraceResponse` / `AssistantTraceQueryService` / 任何 Repository /
> 任何持久化 / 任何 Runtime 组件；未新增 DB 表 / 字段 / Event Bus /
> OpenTelemetry / Streaming。Production Code = 0。
>
> 依据：真实代码（Step 48 后的当前状态），非历史文档。

---

## 1. Current State

```text
GET /api/observability/assistant-trace/{assistant_request_id}
        ↓
AssistantTraceQueryService.get_trace(A)
        ↓
AssistantTraceResponse{
    assistant_request_id,
    llm_usage[],        ← LLMUsageQueryService（ai_ops.llm_usage_record）
    tool_executions[],  ← ToolExecutionPersistentQueryService（ai_ops.tool_execution_record）
    rag_executions[]    ← RagExecutionPersistentQueryService（ai_ops.rag_execution_record）
}

三段各自独立排序；组合层**不重排 / 不去重 / 不合并**；
空段 = []（不是错误）；DB 失败 → 502（不是 []）。
```

当前架构事实（决定"是否需要时间线"）：

```text
一次 /api/ai/chat = 一次 Orchestrator.execute()
        → Router（规则 / LLM fallback）→ **单一** route：RAG | TOOL | TEXT_TO_SQL
        → RAG 内部调用 LLM 一次；TOOL 路径无多轮 Function Calling
⇒ 没有并行 RAG+Tool、没有多步 Agent、没有 RAG↔Tool 交错
```

## 2. Existing Read Boundaries

| 段 | Query Service | Repository / 表 | 排序（真实） | 时间字段（真实） | 主键可见性 |
| --- | --- | --- | --- | --- | --- |
| `llm_usage` | `LLMUsageQueryService.list_by_assistant_request_id()` | `LLMUsageRepository` / `ai_ops.llm_usage_record` | `created_at ASC, id ASC` | **仅** `created_at`（记录写入时点 ≈ 完成时刻） | 有 `id` |
| `tool_executions` | `ToolExecutionPersistentQueryService.list_by_request_id()` | `ToolExecutionRepository` / `ai_ops.tool_execution_record` | `id ASC`（= 落库顺序） | `started_at` + `finished_at` + `duration_ms` | **无** id（Snapshot 11 字段） |
| `rag_executions` | `RagExecutionPersistentQueryService.list_by_request_id()` | `RagExecutionRepository` / `ai_ops.rag_execution_record` | `id ASC`（= 落库顺序） | `started_at` + `finished_at` + `duration_ms` | **无** id（Trace View 13 字段） |

```text
Read Model 字段（真实）：
    LLMUsageTraceRecordView：id · assistant_request_id · request_id(Provider) ·
        provider · model · prompt_tokens · completion_tokens · total_tokens · created_at
    ToolExecutionSnapshot：request_id · round · tool_name · started_at · finished_at ·
        duration_ms · success · project_id · tool_call_id · error_code · error_type
    RagExecutionTraceView：request_id · started_at · finished_at · duration_ms ·
        result_count · used_chunks_count · top_k · context_truncated · context_chars ·
        reranker_used · rerank_elapsed_ms · chunk_ids · document_ids
```

## 3. Proposed Event Concept（设计草图，**未实现**）

```text
AssistantTraceEvent（仅概念；由组合层从三个既有读边界**投影**派生）
    event_type      ∈ {llm, tool, rag}
    occurred_at     单一时间（见 §5；不凭空生成）
    duration_ms     Tool / RAG 原值；LLM → None（**不推算**）
    source_ref      段内稳定序号（Tool / RAG 读模型无 id → 只能用段内索引）
    payload         该段既有安全字段的**投影**（不新增任何数据）
```

```text
明确不做：
    · 不新增 event_id / span_id / trace_id（无持久化依据；也无法表达父子/并行）
    · 不新增 ai_ops.assistant_trace_event 表 / 列 / migration
    · 不在 Event 里内嵌完整 Row（避免"方便调试"式越权暴露）
```

## 4. Event Types

```text
支持：llm · tool · rag
    —— 仅这三个来源当前有**持久化数据**支撑。

不支持（当前无持久化事实，禁止提前加入）：
    router（路由决策未落库；LLM fallback 仅表现为一条 llm usage）
    embedding / reranker（RAG 内部细节；目前只有 reranker_used /
        rerank_elapsed_ms 两个聚合字段，无独立事件）
    validator / executor / database（Text-to-SQL 子步骤未落库）
    project / capability / conversation（不存在相应记录）

注：`provider` / `model` / `tool_name` 是 **属性**，不是 event_type；
     不引入 `llm.chat` / `tool.execute` 之类二级类型（无数据支撑）。
```

## 5. Timestamp Semantics（关键矛盾）

```text
LLM：
    只有 created_at（DB 行写入时刻 ≈ 请求完成时刻）→ **没有开始时间**。
    用作 occurred_at 时，语义是"完成/入库时刻"，**不是**事件起点。
Tool / RAG：
    started_at（+ finished_at）→ 用作 occurred_at = 事件起点。

因此统一时间线必然把"完成时刻"与"开始时刻"放在同一维度比较：

    t=0.050  LLM 开始（不可知，未持久化）
    t=0.000  RAG 开始   → occurred_at = 0.000
    t=0.900  LLM 完成   → occurred_at = 0.900（created_at）
    t=0.100  Tool 开始  → occurred_at = 0.100
    ⇒ 时间线显示：RAG(0.000) → Tool(0.100) → LLM(0.900)
      但真实因果是：LLM 从 0.050 就与 RAG 并行/嵌套执行
```

```text
结论：
    · 不存在统一的"事件开始时间"—— LLM 侧缺失 started_at；
    · 补全需要给 llm_usage_record 增加开始时间列 → 属 LLM Usage 持久化改动，
      本阶段及当前范围**均不允许**；
    · 因此任何统一时间线只能做到"近似顺序"，无法保证因果顺序。
```

## 6. Tie-breaking（同时间戳 / 同段）

```text
排序键（设计，若未来实现）：
    1) occurred_at ASC
    2) source_priority ASC：rag(0) → tool(1) → llm(2)
       理由：容器（route 级：RAG / Tool）先于被包含者（LLM 是 RAG 内部调用）；
             仅用于**确定性**，不代表因果关系。
    3) 段内序号 ASC（各段 Read Model 返回顺序：LLM = created_at,id；
       Tool / RAG = id ASC 落库顺序 —— 两者读模型无 id，只能用返回序号）

限制：Tool / RAG 的 Trace 读模型**不暴露数据库主键**（Step 48 刻意裁剪），
     所以 `source_id` 并不可靠可用 —— 只能以段内序号作为稳定 tie-breaker。
```

## 7. Duration Semantics

```text
Tool / RAG：duration_ms 原样透传（**绝不**用 finished_at - started_at 重算）
LLM：无 duration 字段 → duration_ms = **None**
     （不得用相邻 created_at 之差估算；不得填 0 —— 沿用 Step 43 "NULL ≠ 0"）
```

## 8. Correlation

```text
所有 Event：event.request_id == A（assistant_request_id）
不新增 trace_id / span_id / event_id / parent_id
⇒ 无法表达"LLM 嵌套在 RAG 内"这类父子关系（只能靠时间推断）
```

## 9. Security Boundary

```text
可进入 Event（既有安全字段的投影）：
    event_type · occurred_at · duration_ms · request_id · provider · model ·
    prompt_tokens / completion_tokens / total_tokens · tool_name · round ·
    success · error_code · error_type · project_id（Tool 既有）·
    result_count · used_chunks_count · top_k · context_truncated · context_chars ·
    reranker_used · rerank_elapsed_ms · chunk_ids · document_ids

永不进入 Event：
    query / question · answer · chunk content · document content · similarity ·
    embedding · prompt · messages · raw_response · SQL · database_url · password ·
    api_key · authorization · credentials · ORM / Session / Connection ·
    traceback · 内部模块路径

红线：Unified Event **不得**因为"方便调试"而重新暴露当前已被禁止的数据；
      payload 必须是既有安全 DTO 的**子集**，不是"更方便的完整对象"。
```

## 10. Concurrency Analysis

```text
Case A（LLM → RAG → LLM）：
    当前**不可产生** —— RAG 内部只调用一次 LLM；RAG 之后没有第二次 LLM。
    若未来产生：LLM(内层) 与 RAG 区间重叠，但 LLM 只有完成时刻
    → 时间线只能把它排在 RAG 起点之后，**无法表达"嵌套执行"**。

Case B（LLM → Tool → LLM）：
    当前**部分可产生**：Router LLM fallback（0~1 条 usage）+ Tool 执行（1 条）；
    Tool 之后没有 Function-Calling 回流 → 没有"第三个 LLM"。
    排序可行，但 LLM 的 created_at 使其位置偏晚（完成时刻）。

Case C（LLM → RAG + Tool 并行）：
    当前**不可能**：Router 只输出**一个** route，不存在并行 RAG+Tool。
    未来若引入并行：无 span/parent、无统一开始时间 ⇒
        重叠区间无法表达；同一时间戳只能靠任意的 source_priority 决定顺序。

核心判断：时间排序 ≠ 因果排序。
    缺 started_at（LLM）、缺 span 关系、缺并行标识 ——
    统一时间线只能给出"显示顺序"，会让读者误以为它是因果顺序。
```

## 11. API Compatibility

```text
若未来实现 `events`，只能是 additive：
    { assistant_request_id, llm_usage, tool_executions, rag_executions, events }
    · 旧字段不改名 / 不删除 / 语义不变（与 Step 48 的 rag_executions 同原则）
    · 代价：同一份数据在响应中出现两次（三个列表 + events），
      且两个视图的排序语义不同 → 双份真相，维护成本上升
本阶段结论：**不增加** `events` 字段。
```

## 12. Persistence Impact

```text
派生式实现（推荐，若未来要做）：组合层投影 → **0 DB 改动**（无表 / 无列 / 无 migration）
真正"正确"的实现：需要给 llm_usage_record 增加开始时间列（started_at），
    → 属 LLM Usage 持久化改动（当前范围禁止），且需要回填历史行（NULL 语义）
⇒ 在当前数据模型下，"正确的时间线"无法零成本实现。
```

## 13. Runtime Impact

```text
Event 派生必须只来自三个 **Persistent** 读边界：
    LLMUsageQueryService / ToolExecutionPersistentQueryService /
    RagExecutionPersistentQueryService
不得读取 InMemoryRagExecutionCollector / Runtime Tool Collector
（restart / 多 worker 下会出现"部分来源缺失"的时间线）
```

## 14. Recommendation

```text
推荐：**DEFER**（不实现 Unified Timeline / events 字段）

理由（基于当前真实数据与架构）：
    1. 当前单一 route、无并行、无多步 → 三个独立列表已完整表达一次请求；
    2. LLM 只有 created_at（完成时刻），Tool/RAG 有 started_at（开始时刻），
       统一排序必然混合两种语义 → 顺序≠因果；
    3. 缺 span/parent、缺 event_id → 嵌套（RAG 内 LLM）与并行无法表达；
    4. Tool/RAG Trace 读模型刻意不含主键 → 没有可靠的 source_id/tie-break 依据；
    5. 正确实现需要改 LLM Usage 持久化（新增开始时间列）→ 超出当前边界；
    6. events 与三段列表重复，形成双份真相。

未来触发条件（满足任一再重新评估）：
    a. 出现多步 / Agent / 并行链路（一次请求含多个 Tool/RAG/LLM 步骤）；
    b. UI 明确需要单一时间线视图，且能接受"近似顺序"的语义声明；
    c. LLM Usage 记录具备可靠的开始时间（started_at 落库）。

届时的**最小实现**：
    · 仅在 AssistantTraceQueryService 组合层派生（不改 Repository / 不改表）；
    · event_type ∈ {llm, tool, rag}；occurred_at = created_at / started_at；
      duration_ms = None（llm）或原值；tie-break = (occurred_at, source_priority,
      段内序号)；
    · additive 字段 `events`（旧四字段语义不变）；
    · 与三段列表并列，不替换（避免破坏现有客户端）。

永远不应进入 Timeline 的能力：
    query / question / answer / chunk content / document content / similarity /
    embedding / prompt / messages / raw_response / SQL / 凭据 / ORM / Session /
    traceback / 内部路径 / 数据库主键（Tool/RAG）/ 任何"为了调试方便"的新字段。
```

## 15. Verification

```text
python -m compileall -q backend tests → OK（0 errors）
生产代码 = 0 修改 · DB schema = 0 · Runtime = 0 · API contract = 0
本阶段未新增测试（§十五：0 new tests 允许）
```
