# Phase 3.12 Step 60 — Assistant Trace 多路径关联回归审计

> **Multi-path correlation audit · 无生产实现**
> 新增：`tests/test_assistant_trace_multi_path_e2e.py`（2 离线 + 7 DB-gated）。
> 入口统一 `POST /api/ai/chat`；只替换外部边界（LLM transport / 检索 / Tool handler / SQL 执行）。
> **Production code changes = 0 · API contract changes = 0 · DB schema changes = 0 ·
> Index changes = 0 · Prompt changes = 0**（未发现 production bug）

---

## 1. Scope

```text
三条 AI 业务路径在同一个 /api/ai/chat 入口下的 Assistant Trace 关联完整性：
    RAG（LLM Usage + RAG Execution）· TOOL（Tool Execution）· TEXT_TO_SQL（LLM Usage + 重试）
核心问题：一个 assistant_request_id 是否**只能**关联本次请求产生的 Observability records？
（+ Empty / Failure / Provider ID 边界 / payload 安全）
离线：复用 Step 40 既有装配（`tests/test_assistant_trace_correlation_e2e.py` 的 e2e fixture
      + 内存 Usage / Tool Trace 替身；0 DB / 0 网络）
DB：真实生产装配（app / orchestrator / Router / Trace API / PostgreSQL）+ 测试边界
真实 LLM = OFF（全部 MockTransport；不依赖 RUN_REAL_LLM_TEST）
```

## 2. RAG（Path A）

```text
POST /api/ai/chat（RAG 规则问题）→ route = **rag** · metadata.request_id = **A**
Assistant Trace(A)：
    assistant_request_id = A            ✅
    llm_usage            = **1 条**（provider request_id = step60-llm-rag-1 ≠ A）✅
    rag_executions       = **1 条**（request_id == **A**）✅
    tool_executions      = **0 条** ✅
（RAG 记录来自真实 RagExecutionPersistenceAdapter；LLM usage 来自真实 accounting sink）
```

## 3. Tool（Path B）

```text
POST /api/ai/chat（Tool 规则问题：查询物料 MAT-001 当前库存）→ route = **tool** ·
    metadata.request_id = **B** · handler 调用 1 次
Assistant Trace(B)：
    assistant_request_id = B            ✅
    tool_executions      = **1 条**，且 ``request_id == B``（== assistant_request_id，
                           不是 provider request_id，也不是其它 assistant request_id）✅
    llm_usage            = **0 条**（当前 Tool 路径不调用 LLM）✅
    rag_executions       = **0 条** ✅
```

## 4. Text-to-SQL（Path C）

```text
POST /api/ai/chat（"统计最近7天的入库单数量"）→ route = **text_to_sql** ·
    metadata.request_id = **C** · metadata.row_count = 3（假执行器返回）
真实重试：第 1 次生成非法 SQL（DROP TABLE x）→ Validator 拒绝 → 第 2 次合法 SQL → 通过
    ⇒ **2 次实际 LLM 调用**（未修改 retry 行为）
Assistant Trace(C)：
    assistant_request_id = C            ✅
    llm_usage            = **2 条**，每条 assistant_request_id = C；
                           provider request_id = step60-llm-t2sql-1 / -2（**互不相同，未合并**）✅
    tool_executions      = **0 条** ✅
    rag_executions       = **0 条** ✅
执行器只收到 **1** 条 SQL（= 最终合法 SQL，含哨兵注释）—— 被拒绝的尝试不进入执行器
```

## 5. Cross-request Isolation

```text
A（RAG）→ B（TOOL）→ C（TEXT_TO_SQL）依次执行；三 id 互不相同（len({A,B,C}) == 3）

严格集合断言（DB-gated；离线版本同形）：
    Trace(A): llm_usage.assistant_request_id == {A} · rag_executions.request_id == {A} ·
              tool_executions == []
    Trace(B): tool_executions.request_id == {B} · llm_usage == [] · rag_executions == []
    Trace(C): llm_usage.assistant_request_id == {C} · tool_executions == [] · rag_executions == []

DB 侧交叉验证：
    SELECT DISTINCT assistant_request_id ... WHERE provider = 'step60-provider'
        ⊆ {A, B, C} 且 **B ∉ 该集合**（Tool 路径不产生 usage）
    无 A records 含 B/C · 无 B records 含 A/C · 无 C records 含 A/B  ⇒ **无跨请求污染**
```

## 6. Empty / Failure

```text
Empty Trace：未知 id（step60-not-exist-…）→ **200** · assistant_request_id = 该 id ·
    llm_usage = [] · tool_executions = [] · rag_executions = []（空 Trace 合法，非 404）

Failure Trace（RAG 的 LLM 上游返回 500）：
    HTTP = **500**（既有错误语义不变；不错误地"成功返回"）
    失败请求的 Trace：llm_usage = **[]**（usage=None → 不落库）· rag_executions = **1 条**
        （Step 43 设计：异常路径同样记录 RAG observation）· tool_executions = []
    同测试中先前**成功**请求的 Trace 不受影响（llm_usage 全为自身 id）
    ⇒ 失败不会把其他 request 的 record 挂到当前 trace
```

## 7. Security

```text
三段 payload 均不含：
    SQL 哨兵（STEP60-SQL-SENTINEL —— 被执行器执行的 SQL 文本）·
    chunk 正文哨兵（STEP60-CHUNK-CONTENT）· 凭据（"test-key"）·
    base_url（step60.fake / step40.fake）· DATABASE_URL（"postgresql://"）
usage 行：只含白名单列；provider request_id ≠ assistant_request_id（互不覆盖）
DTO 边界（字段集合逐字锁定）由 Step 54/59 审计测试持续覆盖；本阶段未扩大 payload
```

## 8. Final

```text
RAG correlation        = PASS
Tool correlation       = PASS
Text-to-SQL correlation = PASS
Cross-request isolation = PASS
Security               = PASS
```

## 9. Limitations

```text
* 三条路径均使用 MockTransport / Fake 边界（真实 DeepSeek 见 Step 58 独立 smoke；
  本阶段刻意 Real LLM = OFF）
* TEXT_TO_SQL 的 SQL 执行使用假执行器（不触达业务表）；因此验证的是**关联与路由**，
  而非 SQL 结果正确性（属既有 Text-to-SQL 评测范围）
* 多轮 Tool / Agent / 统一 timeline 不在当前架构内，未评估
* 未做并发（多请求同时）下的关联验证 —— 属未来阶段
* DB-gated 使用本地开发库；生产库未接入
```
