# Phase 4.2 Step 7G — Conversation E2E + Security + Regression

> 性质：**验证阶段**（`Test the architecture. Do not redesign the architecture.`）。
> 目标：用真实 Conversation Runtime E2E + Security + Regression 证明 7A～7F 的完整链路成立。
> 本阶段 `生产代码修改 = 0` · `DB schema = 0` · `API contract = 0` · `Prompt = 0` · `真实 LLM = 0`。

```text
新增测试   : tests/test_conversation_context_e2e.py            （13）
             tests/test_conversation_context_security_e2e.py   （12）
             tests/test_conversation_context_e2e_db.py         （ 9，RUN_DB_TESTS=1）
被测链路   : POST /messages → Runtime → Idempotency → History → Selection → Builder
             → AIOrchestrator → Router → {RAG | Text-to-SQL} → AI Result → Assistant Turn
被替换的层 : 持久化介质（内存 Fake）/ AI 执行 / LLM Provider / Vector Search /
             SQL Executor / Schema Provider —— **不替换** Selection · Builder ·
             Idempotency · Router 规则 · RAG 装配 · Text-to-SQL 装配 · SQL Validator
```

---

## 1. Runtime E2E 覆盖

| 场景 | 期望 | 实测 | 结果 |
| ---- | ---- | ---- | ---- |
| Multi-turn（U1 A1 U2 A2 → U3） | DB 四行 + U3 context = U1 A1 U2 A2（oldest → newest） | 一致 | PASS |
| Current-turn exclusion | 当前消息只作 question，绝不进自己的 context | 一致（3 轮逐一验证） | PASS |
| Context Window（turns） | 25 条历史 → 最新 20 条 → USER-anchor ⇒ 19 行 | 一致（离线 + HTTP 26 轮） | PASS |
| Context Window（chars） | 累计 > 12000 ⇒ 连续后缀停止（离线 6000+6000；HTTP 6000×3） | 一致（无空洞） | PASS |
| Oversized newest | 最新历史 turn 豁免上限 ⇒ 完整保留（不截断 / 不摘要） | 一致（13000 字符原样） | PASS |
| Oversized older | 超限 turn 及其更旧历史整体不入选（不留洞） | 一致 | PASS |
| Idempotency replay | 200 · `route=null` · `data=null` · `metadata.idempotent_replay=true` · request_id 来自 ASSISTANT Turn | 一致（离线 + 真 DB） | PASS |
| Idempotency replay 短路 | 第二次：AI = 0 · Selection = 0 · Builder = 0 · RAG = 0 · Vector = 0 · 无新 Turn | 一致（计数器 + 记录器双重验证） | PASS |
| Idempotency conflict | 同 key 异 payload ⇒ 409 · AI = 0 · 无第二条 USER | 一致（离线 + 真 DB + HTTP 409 文案） | PASS |
| Retry（NOT_COMPLETED） | 复用既有 USER Turn · AI retry · 生成 ASSISTANT · context 不含当前 turn | 一致（离线 + 真 DB 500→200） | PASS |
| EMPTY | USER 保留 · ASSISTANT absent · 下一条 context = `user: <原文>`（无内部状态） | 一致（离线 + 真 DB） | PASS |
| FAILED（异常） | USER 保留 · ASSISTANT absent · 下一条可使用 · context 无异常/密钥 | 一致 | PASS |
| Archived | 任何消费之前拒绝（AI = 0 · Selection = 0 · Builder = 0） | 一致 | PASS |
| Conversation isolation | B 的 context 只含 B（A1/A2 不出现），反之亦然 | 一致 | PASS |
| Project isolation | project 绑定来自 conversation（project-a/project-b 各自正确），context 不跨项目 | 一致 | PASS |
| Secret / Metadata boundary | context 无 conversation_id / request_id / turn_id / idempotency_key，只有 `role: content` 行；响应无 URL / 密码 / 异常栈 | 一致 | PASS |
| RAG context 分离 | `CONVERSATION HISTORY`（untrusted）与 `【CONTEXT】`（KB 检索）明确分离，历史不进 system prompt | 一致（真实 RagService） | PASS |
| RAG retrieval 隔离 | `vector.queries == [当前问题]`（history 不改写检索 query；OD-35 未实现） | 一致 | PASS |
| Text-to-SQL context | 历史进入生成 prompt（`【MAX ROWS】` 之后、`【QUESTION】` 之前），真实执行链正常 | 一致（真实 TextToSQLService） | PASS |
| Text-to-SQL retry placement | initial / retry 同一插入位置（同一 helper） | 一致 | PASS |
| Text-to-SQL injection | 恶意历史 + `DELETE FROM public.inventory` ⇒ 真实 Validator 拒绝 · Executor 0 调用 · DB unchanged | 一致（`AIOrchestratorExecutionError`） | PASS |
| Allowed tables security | 恶意历史 + `SELECT * FROM secret_users` ⇒ 拒绝 · Executor 0 调用 | 一致 | PASS |
| MAX_ROWS security | 恶意历史 + `LIMIT 999999999` ⇒ 拒绝 · Executor 0 调用；prompt 的 MAX ROWS 仍为 100 | 一致 | PASS |
| Router security（rule） | 恶意历史不改写规则路由（仍 RAG）· LLM fallback 调用 = 0 | 一致 | PASS |
| Router security（fallback） | 仅真正需要 fallback 时进入 LLM；历史只作 reference；未新增 Tool capability | 一致 | PASS |
| Tool boundary | `_run_tool` / `ToolArgumentExtractor.extract` 无 `context` 参数；调用点不传 context；Tool 层无 conversation 参数 | 一致（签名 + AST） | PASS |
| API contract | 200 / duplicate 200 / 409（conflict、archived）/ 422（key > 128、content > 10000） | 一致（真 DB + HTTP） | PASS |
| Residue | conversation / conversation_turn 计数复原；4 张 observability 表不变；无 schema 变更 | 一致 | PASS |

---

## 2. Invariant Audit（I-01 … I-24）

```text
I-01 current turn excluded            ✅ test_conversation_context_e2e::TestMultiTurnE2E
I-02 retry current turn excluded      ✅ TestIdempotencyWithContextE2E::test_retry_reuses...
I-03 duplicate 不重建 context          ✅ test_replay_skips_selection_builder_and_ai（计数器）
I-04 history ordering deterministic   ✅ TestMultiTurnE2E + TestContextBoundary
I-05 max turns = 20                   ✅ TestContextWindowE2E::test_window_uses_latest_20_turns
I-06 max chars = 12000                ✅ TestContextWindowE2E::test_char_cap_12000...（离线 + HTTP）
I-07 newest oversized preserved       ✅ TestContextWindowE2E::test_oversized_newest...（13000 原样）
I-08 older oversized stops            ✅ TestContextWindowE2E::test_older_oversized_stops_without_hole
I-09 complete turn boundary           ✅ 同上（无空洞 · 整 turn 入选）
I-10 builder pure                     ✅ test_conversation_context_builder（Step 13/7E 冻结）
I-11 selection 无 DB 写                ✅ test_conversation_context_selection（7E：no selection residue）
I-12 selection 无 LLM                 ✅ test_conversation_context_selection（7E：无 LLM/network import）
I-13 context is untrusted             ✅ security E2E（MALICIOUS_HISTORY 全场景）
I-14 system prompt boundary preserved ✅ TestRagConversationE2E（历史不进 system prompt）+ 7F 三处
I-15 capability boundary preserved    ✅ TestRouterSecurity（Tool 能力不因历史新增）
I-16 allowed tables unchanged         ✅ TestTextToSqlConversationE2E（ALLOWED TABLES 段 + selected_tables）
I-17 MAX_ROWS unchanged               ✅ 同上（MAX ROWS 段 = 100；LIMIT 越界被拒）
I-18 read-only boundary unchanged     ✅ test_injected_delete_is_rejected_by_real_validator
I-19 Tool 不消费 context              ✅ TestToolBoundary（签名 + 调用点 AST）
I-20 context=None behaviour preserved ✅ 7F 字节等价测试（716 行锚点）+ 7G 复用
I-21 retry prompt placement identical ✅ 7F::test_retry_uses_same_placement + 7G T2SQL E2E
I-22 conversation isolation           ✅ TestIsolationE2E + DB E2E 多轮
I-23 project isolation                ✅ TestIsolationE2E（project-a / project-b）
I-24 idempotency replay short-circuit ✅ TestDuplicateReplayShortCircuit（Router/RAG/Vector/Executor 全 0）
```

---

## 3. 核心问题（Q1 … Q10）

```text
Q1  多轮对话历史是否真正进入 AI？        ✅ YES（Router fallback / RAG / Text-to-SQL 三处 prompt 实测）
Q2  当前消息是否永远不会进入自己的 history？ ✅ YES（离线 3 轮 + retry + HTTP 26 轮）
Q3  Context Window 是否真实生效？         ✅ YES（turns ≤20 · chars ≤12000 · 连续后缀 · 最新豁免）
Q4  RAG 是否区分 Conversation History 与 Knowledge Context？ ✅ YES（两段独立，KB 仍是【CONTEXT】）
Q5  Text-to-SQL 是否消费 Conversation Context？ ✅ YES（生成 prompt 实测；retry 同位置）
Q6  History 能否突破 SQL 安全边界？        ✅ NO（Validator 拒绝 DELETE / 越权表 / 越界 LIMIT；Executor 0 调用）
Q7  History 能否新增 Tool capability？     ✅ NO（规则路由不受影响；Tool 层无 conversation 输入）
Q8  Duplicate message 是否重新消费 Context / AI？ ✅ NO（Selection / Builder / RAG / Vector / AI 全 0）
Q9  不同 Conversation 是否互相污染？       ✅ NO（conversation + project 双向隔离）
Q10 API contract 是否改变？                ✅ NO（200 / 409 / 422 与 Step 6 冻结语义一致）
```

---

## 4. 关键发现（本阶段记录，非生产修改）

```text
F-1  HTTP DTO 对 content 有 10000 字符上限（Phase 4.1 Step 12 冻结，
     ``MESSAGE_CONTENT_MAX_LENGTH``）⇒ **单条 >12000 的 turn 无法经 HTTP 产生**；
     oversized-newest 豁免（I-07）在 Runtime/Selection 层可达（服务层与 DB 无长度上限，
     历史行 / 其它写入方），在 HTTP 层不可达。
     7G 增加两条回归固化现状：content > 10000 → 422 且 0 写入；
     累计大历史（6000×3）经 HTTP 证明 12000 累计上限真实生效。
     → 判定：**既有契约，非缺陷**；不修改 API。

F-2  ``ToolExecutionService.execute(..., context=ToolExecutionContext)`` 的 ``context``
     是 **Tool 运行时上下文**（request_id / project_id / tool_call_id / round），
     与 Conversation Context（str）语义不同。7G 以签名/注解断言固化该区分，
     避免后续误把 conversation context 注入 Tool 层。
     → 判定：**无需修改生产代码**（Tool 路径仍 DEFERRED）。

生产代码 BUG：0（本阶段未做任何最小修复）。
```

---

## 5. 执行结果（实测）

```text
7G 新增（离线）    : 13 + 12 = 25 passed
7G 新增（DB-gated）: 9 passed
指定既有回归（离线）: consumption 32 · selection 38(+3 skipped) · builder 40 · idempotency 35
指定既有回归（DB）  : idempotency_db 14 · selection(DB) 41 · 新 DB E2E 9 · multiturn_db_e2e 12 = 79 passed
全量离线            : 6061 passed · 5 failed（EXPECTED UNTIL COMMIT：worktree guard + 4 下游）
全量 DB             : 6768 passed · 6 failed（同上 + 1 顺序依赖 residue baseline）
DB residue 单独运行  : PASS（isolated = PASS，full-suite = 已知顺序依赖）
compileall          : 0 errors
真实 LLM 调用        : 0（deepseek / siliconflow / network = 0）
```

失败项区分：

```text
NEW FAILURE                              : 0
PRE-EXISTING / EXPECTED UNTIL COMMIT     : 6（见上；未修改 guard / baseline / residue 断言）
```

---

## 6. 边界（本阶段未触碰）

```text
Selection 算法 / Builder / Idempotency 契约 / Router 行为 / RAG 算法 / Text-to-SQL 生成器
SQL Validator / SQL Executor / Tool Framework / Business Semantic / Project Context
Prompt 文件（含 prompts/v2）· DB schema / migration · API contract · Evidence
ConversationContextBuilder · ConversationRepository 依赖方向（AI Runtime 不 import conversation 层）
Query Understanding / Memory / Summary / Agent / MCP / Workflow = NOT IMPLEMENTED
```

---

## 7. 最终结论

```text
Context Selection      = COMPLETE
Context Consumption    = COMPLETE
Conversation E2E       = COMPLETE
Security Regression    = COMPLETE

Query Understanding = NOT IMPLEMENTED · Memory = NOT IMPLEMENTED · Summary = NOT IMPLEMENTED
Agent = NOT IMPLEMENTED · MCP = NOT IMPLEMENTED · Workflow = NOT IMPLEMENTED

KL-1（AI exactly-once = NOT GUARANTEED）· KL-2（duplicate 不重放 route/data）仍成立
OD-22 / OD-23 / OD-30 / OD-31 / OD-32 / OD-33 / OD-35 / OD-38 / OD-39 = OPEN
```

## 8. STOP

未进入 7H · 未 commit / push / PR · 未扩大 Context Runtime。
