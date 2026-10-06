# Phase 4.2 Step 7A — Conversation Context Boundary Audit & Contract

> 性质：**只读审计 + 契约冻结**（审计 → 契约 → 边界决策 → 回归契约）。
> 本 Step **未修改**任何生产代码 / 测试 / DB / API：
>
> ```text
> backend = 0 · tests = 0 · DB = 0 · API = 0
> ```
>
> 前置：Phase 4.2 Step 6 已提交（`66a69c6 feat: Phase 4.2 Step 6 message idempotency`），
> 当前分支 `phase4.1-3`，HEAD 已包含 `66a69c6`（merge `7f50f70`），working tree clean。

---

## 1. Current Runtime Flow

真实链路（逐行阅读确认；行号为当前 HEAD 实测）：

```text
POST /api/conversations/{id}/messages
    ↓  api/conversations.py::execute_conversation_message
ChatApplicationService.execute_message(conversation_id, content, idempotency_key)   # L356
    ↓
Conversation 读 + ARCHIVED 守卫（写入前拒绝）
    ↓
_resolve_user_turn()                                                              # L275
    ├─ key 为空 → append_turn(USER, idempotency_key=None)（TX1 提交）
    ├─ 命中同 key → find_turn_by_idempotency_key                                   # L301
    │      ├─ payload 不一致 → 409
    │      ├─ find_assistant_turn_after ≠ None → MessageReplay（**不再往下走**）    # L320/L331
    │      └─ 其后无 ASSISTANT → 复用既有 USER Turn（retry；不新建）
    ↓
history = list_turns(conversation_id)                                             # L418
    （Repository: ORDER BY created_at ASC, turn_id ASC —— conversation_repository.py:417）
    ↓
previous_turns = history 排除 user_turn.turn_id                                   # L423
    ↓
context = build_context(previous_turns)   → str | None                            # L429
    ↓
AIOrchestratorService.execute(user_turn.content, context=context)                 # L434
    ↓
    ├─ Router.route(question, context=context)                                    # ai_orchestrator_service.py:583
    │      └─ ★ context 在 Router 内**从未被使用**（见 §10）
    ├─ _run_rag(decision, question, request_id)        ← **无** context 参数
    ├─ _run_tool(decision, question, request_id)       ← **无** context 参数
    └─ _run_text_to_sql(decision, question, request_id)← **无** context 参数
    ↓
AIOrchestrationResult
    ↓
条件式 ASSISTANT Turn（content.strip() 非空 ∧ metadata.request_id 合法）→ TX2
```

---

## 2. Context Builder Contract（`services/conversation_context_builder.py`）

| 维度 | 真实实现 | 证据 |
| ---- | -------- | ---- |
| Input | `Sequence[ConversationTurnLike]`（duck-type；**只读** `role` / `content`） | L91-98 / L130-139 |
| Output | `build_context → str \| None`；`build_messages → tuple[dict[str,str], ...]` | L113 / L144 |
| 渲染 | 每行 `"role: content"`，`"\n"` 连接 | L84-85 / L156-159 |
| 空输入 | → `None`（**不伪造** `""` / `{"messages": []}`） | L154-155 |
| Role 映射 | `USER → user` · `ASSISTANT → assistant`；未知 role → `ValueError`（**不**静默转换） | L101-110 |
| SYSTEM / TOOL | **不存在**（仅两态；未知即拒绝） | L107-110 |
| 排序 | **不排序** —— 顺序 = 输入顺序（由调用方保证） | docstring L32-33 / L119-120 |
| content | **原样**（不 strip / truncate / normalize / escape） | L136-140 |
| 纯函数 | stateless / 无 IO / 无环境依赖 / 无全局可变状态 | 类无字段（L162-173） |
| 元数据 | 不读 `turn_id` / `conversation_id` / `assistant_request_id` / `created_at` | L34-35；测试毒化属性即断言不可达 |

---

## 3. Current Turn Boundary（第 1 / 2 问）

```text
Request content
    ↓
USER Turn persisted（TX1 提交）      ← 因此**会**被后续 history query 读出
    ↓
history query（含 current USER Turn）
    ↓
previous_turns = [t for t in history if t.turn_id != user_turn.turn_id]     # L423
    ↓
build_context(previous_turns)
    ↓
AI（question = user_turn.content；context = 仅历史）
```

* **结论 1**：当前 USER Turn **已提交**，**会**被 history query 查出；
* **结论 2**：通过 `turn_id` **精确排除**（不是 `turns[-1]` 猜测），**不存在** `CURRENT_TURN_DUPLICATION_RISK`；
* 排除依据 = `append_turn` 返回值 / 复用行的 `turn_id`（retry 路径同样成立，见 §4）；
* 契约测试：`tests/test_chat_application_service.py`（`contexts == [None]` / 当前问题不在 context）、
  `tests/test_conversation_multiturn_db_e2e.py:392-393`。

---

## 4. Retry Boundary（第 7 问）

Step 6 语义（`_resolve_user_turn`，L275-353）：

```text
USER Turn exists + ASSISTANT Turn absent
    ↓ 复用既有 USER Turn（**不**新建）
    ↓ find_assistant_turn_after 仍在执行前被判定为 None
    ↓ history → 排除该 turn_id（L423）
    ↓ AI retry
```

| 场景 | 期望 context | 实测结果 |
| ---- | ------------ | -------- |
| `U1 A1 U2` 首次执行 U2 | `[U1, A1]` | ✅ `"user: U1\nassistant: A1"` |
| U2 retry | `[U1, A1]` | ✅ 同值（`test_conversation_message_idempotency.py::test_retry_context_does_not_duplicate_user_turn` / `..._keeps_prior_history`） |
| 禁止 | `[U1, A1, U2]` / `[U1, A1, U2, U2]` | ✅ 均未出现 |

---

## 5. Duplicate Boundary（第 8 问）

```text
completed duplicate（同 key + 同 payload + ASSISTANT Turn 存在）
    ↓ MessageReplay（chat_application_service.py:331）
    ↓ 直接返回
```

**不**执行：`list_turns`（history 读）· `build_context` · `AIOrchestrator.execute`。

结论：**PASS**（duplicate 不重新进入 Context Builder；Step 6 §8 语义保持不变）。

---

## 6. Ordering（第 4 问）

| 层 | 排序来源 | 证据 |
| -- | -------- | ---- |
| Repository | `ORDER BY created_at ASC, turn_id ASC`（**显式**，非 DB 自然顺序） | `conversation_repository.py:417` |
| Service | 保持 Repository 顺序（不重排） | `conversation_service.py::list_turns` |
| Builder | **不排序**，保持输入顺序 | `conversation_context_builder.py:207-214`（已测试反向输入保持原序） |

⇒ `U1 A1 U2 A2` → context = `user: U1 / assistant: A1 / user: U2 / assistant: A2`；
**不存在** `CONTEXT_ORDERING_GAP`。

---

## 7. EMPTY / FAILED（第 9 问）

| 结果 | Turn 事实 | 是否进入**下一次**请求的 context |
| ---- | --------- | ------------------------------- |
| SUCCESS | USER + ASSISTANT | ✅ 两条都进入（ASSISTANT 作为 `assistant:` 行） |
| EMPTY（无 ASSISTANT） | USER 存在 | ✅ **该 USER 文本进入后续 context**（本就代表用户问过这句话） |
| REFUSED + empty | USER 存在 | ✅ 同上 |
| FAILED（exception） | USER 存在 | ✅ 该 USER 文本进入后续 context（已由 `test_conversation_multiturn_db_e2e.py:600-608` 锁定：`contexts[-1] == "user: 会失败的问题"`） |
| TX2 写失败 | USER 存在 | ✅ 同上 |

* 当前实现 = **保留全部 USER 文本**（不因 AI 失败而丢弃用户说过的话）；
* 这是**既定语义**（Phase 4.1 Step 14/15 + Phase 4.2 E2E 已锁定），本 Step **不修改**；
* 但"失败/空 USER turn 是否应进入 context"**尚无正式契约条文** → 记为 GAP-7A-3（见 §12）。

---

## 8. Archived Conversation（第 13 问）

```text
POST Message（ARCHIVED）
    ↓ execute_message: conversation.status == ARCHIVED → ConversationArchivedError（409）
    ↓ API: 409（**写入前**拒绝；HTTP 状态断言见 Step 6 契约测试）
```

* 拒绝点早于 `_resolve_user_turn` ⇒ **AI = 0**、**Turn 写入 = 0**、**context 构建 = 0**；
* `ConversationService.append_turn` 亦有第二道 ARCHIVED 守卫（Repository 事务内 SELECT status）；
* 结论：context 层**没有**绕过 archive guard 的路径（PASS）。

---

## 9. Context Window / Token Budget（第 10 / 11 / 12 问）

全量检索 `max_context` / `context_window` / `token_budget` / `max_tokens` / `history_limit` /
`max_turns` / `truncate` / `trim` / `window` 于 `backend/app/**`：

```text
命中模块均为**其他领域**：
    settings.rag.max_context_chars      ← RAG chunk 上下文（VectorSearchResult → Context）
    text_to_sql / sql_executor max_rows ← SQL 结果行数
    usage / trace / observability 查询窗口（时间范围）
Conversation 历史侧命中 = 0（无任何上限 / 截断 / 选择 / 摘要 / tokenizer）
```

实测结论：

| 项 | 真实状态 |
| -- | -------- |
| Context Window | **无** ⇒ `NO_CONTEXT_WINDOW_POLICY` |
| 最大 turn 数 | **无** |
| 截断 / 摘要 / Memory | **无**（Phase 4.1 Step 16/17 已冻结为 Deferred） |
| 增长特性 | **线性**：`context_length(n) ≈ Σ len("role: ") + Σ len(content) + (n-1)`（Phase 4.1 Step 16 §4 实测：500 turns ≈ 14.7K 字符） |
| Token counting | **未引入**（Phase 4.1 Step 16 §5 明确禁止字符数折算 token） |
| 每轮额外成本 | 每消息 1 次 `list_turns`（全量历史读，O(n) 行）+ 全量渲染 |

---

## 10. Prompt Boundary（第 14 / 15 / 16 问）

### 10.1 内容边界（PASS）

```text
context 内容 = 仅 "role: content" 行
禁止项实测：
    request_id / conversation_id / turn_id / assistant_request_id  ✗ 不出现
    metadata / outcome / route / SQL / RAG chunk / Tool payload     ✗ 不出现
    DB 连接串 / 异常信息 / API 凭据                                  ✗ 不出现
```

证据：`tests/test_conversation_context_builder.py::TestIsolation`（毒化 `turn_id` / `conversation_id` /
`assistant_request_id` / `created_at` 属性并断言 Builder 从不访问）+ `test_20`（含秘密的 turn 不进 context）；
`test_conversation_message_idempotency.py::TestDuplicateResponseContract`（replay metadata 白名单）。

另：请求 DTO 仅 `content`，客户端**无法**注入伪造历史（历史只能来自 DB）。

⇒ 无 `CONTEXT_INFORMATION_LEAK`。

### 10.2 消费边界（★ 本次审计核心发现）

```text
ChatApplicationService → orchestrator.execute(question, context=...)     ← 已接线
                              ↓
                       router.route(question, context=context)           ← 透传
                              ↓
                       _route_via_llm(question, context)                 ← ★ context 从未被使用
                              ↓
                     user_prompt = template.safe_substitute(question=...)  ← 只用 question
```

AST 实测（当前 HEAD）：

| 函数 | `context` 形参 | `context` 值引用次数 |
| ---- | :------------: | :------------------: |
| `AIRouterService.route` | ✅ | 1（仅 `self._route_via_llm(normalized, context)` 透传） |
| `AIRouterService._route_via_llm` | ✅ | **0** |
| `AIOrchestratorService.execute` | ✅ | 1（仅 `context=context` 透传给 Router） |
| `_run_rag` / `_run_tool` / `_run_text_to_sql` | ❌ 无该参数 | 0 |

⇒ **Conversation Context 在真实 AI 执行中不产生任何功能效果**：
规则路由只看 `question`；LLM 路由 fallback prompt 只替换 `question`；
RAG / Tool / Text-to-SQL 从未收到 context。

```text
GAP-7A-1  CONVERSATION_CONTEXT_NOT_CONSUMED
```

（注：`_run_tool` 内部的 `context=ToolExecutionContext` 是**另一个** context（观测用），
与 conversation context 无关，不得混淆。）

---

## 11. Side Effects（第 14 问）

| 组件 | 写操作 | 读操作 | 结论 |
| ---- | ------ | ------ | ---- |
| `ConversationContextBuilder` / `build_context` / `build_messages` | **0** | 0（纯内存） | pure / side-effect free ✅ |
| Builder 输入对象 | 不修改（`test_21` 断言快照不变） | — | 不可变 ✅ |
| 返回的 `dict`（`build_messages`） | 修改返回值不影响输入（`test_22`） | — | 隔离 ✅ |
| `ChatApplicationService` | 仅 Turn 写入（TX1/TX2，Step 6 语义） | `list_turns`（只读） | 无额外副作用 ✅ |

不存在 DB write / Session 泄漏 / 环境变量依赖。

---

## 12. Gaps

| ID | Gap | 证据 | 影响 | 归属 |
| -- | --- | ---- | ---- | ---- |
| **GAP-7A-1** | `CONVERSATION_CONTEXT_NOT_CONSUMED`：context 已构建并传给 Orchestrator，但 Router 从不使用、三条执行路径从不接收 ⇒ 多轮历史对 AI 输出**无功能影响** | §10.2 AST 实测 | 高（决定 Step 7 是否"把事情做实"） | Step 7（架构决策） |
| **GAP-7A-2** | `NO_CONTEXT_WINDOW_POLICY`：全量历史、线性增长、无上限 / 无截断 / 无选择层 / 无 tokenizer；每轮 1 次全量历史读 | §9（Phase 4.1 Step 16 §4 实测） | 中高（一旦 context 真被消费，将直接进入 prompt 预算问题） | Step 7（窗口契约 + 未来 Selection 层） |
| **GAP-7A-3** | EMPTY / FAILED 的 USER Turn **会**进入后续 context，但该语义尚无正式契约条文（当前仅由 E2E 断言锁定） | §7 | 中（影响"脏历史"是否应过滤） | Step 7（契约条文化） |
| **GAP-7A-4** | 对话层 context 无观测（长度 / turn 数 / 是否被消费均无记录；RAG 侧仅有自己的 `context_chars`） | §9 / §10.2 | 低（可观测性，非正确性） | Step 7 / 后续 |

**无 Gap（已验证 PASS）**：Current Turn 排除 · 排序 · Role 映射 · Retry 上下文 · Duplicate 短路 ·
Archived 守卫 · Builder 纯度 · Prompt 信息边界。

---

## 13. Step 7 Contract（冻结提案；本 Step 不实现）

> 每项 = CURRENT BEHAVIOR / EXPECTED SEMANTICS / GAP。
> 标 **KEEP** 者 = 现状即目标，Step 7 不得改变；标 **DECIDE** 者 = Step 7 必须给出决策。

### 13.1 Current Turn Exclusion — **KEEP**

```text
CURRENT  当前 USER Turn 已提交并被 history 查出，按 turn_id 精确排除（L423）
EXPECTED 保持：current turn 只作为 question，绝不以历史身份二次进入 context
GAP      无
```

### 13.2 Historical Ordering — **KEEP**

```text
CURRENT  Repository: created_at ASC, turn_id ASC；Builder 不排序、保持输入顺序
EXPECTED 保持双重保证（DB 显式排序 + Builder 顺序透明）
GAP      无
```

### 13.3 Role Mapping — **KEEP**

```text
CURRENT  USER→user · ASSISTANT→assistant · 未知 role → ValueError（不静默转换）
EXPECTED 保持；**不新增** SYSTEM / TOOL 角色（无需求）
GAP      无
```

### 13.4 Retry Context — **KEEP**

```text
CURRENT  retry 复用既有 USER Turn 并将其从 context 排除（Step 6 实现 + 双测试）
EXPECTED 保持：retry 的 context 与首次执行**完全等价**（同 [U1, A1]）
GAP      无
```

### 13.5 Duplicate Context — **KEEP**

```text
CURRENT  completed duplicate 直接 MessageReplay，不读 history / 不建 context / 不调 AI
EXPECTED 保持（Step 6 幂等语义不可被 Step 7 破坏）
GAP      无
```

### 13.6 Empty Turn — **DECIDE**

```text
CURRENT  EMPTY 的 USER Turn 保留，并进入下一次请求的 context
EXPECTED Step 7 必须明确：保留（推荐，用户确实问过）vs 过滤
GAP      GAP-7A-3
```

### 13.7 Failed Turn — **DECIDE**

```text
CURRENT  FAILED(exception) 的 USER Turn 保留，并进入下一次请求的 context
EXPECTED Step 7 必须明确同一策略（与 EMPTY 一致，避免两套规则）
GAP      GAP-7A-3
```

### 13.8 Archived Conversation — **KEEP**

```text
CURRENT  ARCHIVED → 409（写入前拒绝；AI = 0 / context = 0）
EXPECTED 保持；context 层不得出现绕过 archive guard 的路径
GAP      无
```

### 13.9 Context Window — **DECIDE**

```text
CURRENT  无窗口 / 无截断 / 无选择层 / 无 tokenizer（NO_CONTEXT_WINDOW_POLICY）
EXPECTED Step 7 必须冻结**一个**可测试窗口规则（候选：最近 N turns；Selection 层位于 Builder 之前，
         Builder 保持纯函数无策略参数 —— Phase 4.1 Step 16 §6 已冻结职责划分）
GAP      GAP-7A-2
```

### 13.10 Internal Metadata Boundary — **KEEP**

```text
CURRENT  context 只含 role/content；无 id / metadata / 凭据；客户端无法注入历史
EXPECTED 保持；未来若引入摘要/选择，仍不得把 request_id / turn_id / conversation_id 写入 context
GAP      无
```

### 13.11 Context Builder Side Effects — **KEEP**

```text
CURRENT  纯函数（deterministic / stateless / 无 IO / 无环境依赖）；输入不可变
EXPECTED 保持；禁止在 Builder 内引入 DB / 缓存 / 截断策略参数
GAP      无
```

---

## 14. Recommendation

1. **Step 7B 第一决策 = GAP-7A-1（消费点）**：定义 conversation context 应进入哪一层
   （Router prompt / RAG answer prompt / 两者），并明确"仅路由"或"仅回答"的区别；
   该决策决定 `GAP-7A-2` 的紧迫性（context 一旦被 LLM 消费，窗口策略立即成为正确性/成本问题）。
2. **GAP-7A-2 冻结顺序**：先冻结窗口规则与职责划分（Selection 层位于 Builder 之前；
   Builder 保持纯函数），再考虑任何实现；**禁止**引入 tokenizer 或字符数折算 token。
3. **GAP-7A-3 一次冻结两条规则**（EMPTY 与 FAILED 同策），避免出现两套历史过滤语义。
4. **KEEP 项必须写成回归断言**：Current Turn 排除 · 排序 · Retry 等价 · Duplicate 短路 ·
   Archived 拒绝 · Builder 纯度 · 元数据边界 —— 这 7 项是 Step 7 的不可回归底线。
5. **Step 6 语义零退化**：任何 context 变更不得影响 `Idempotency / Duplicate Replay / Retry /
   Conflict / Concurrency`（本 Step 已实测 PASS）。
6. **不建议**在本阶段引入 Memory / Summary / 语义检索历史 / 多 Agent 上下文。

### 回归基线（本 Step 实测）

```text
离线：156 passed（test_conversation_message_idempotency + test_chat_application_service
                  + test_conversation_context_builder + ..._selection_decision_gate）
DB  ： 52 passed（test_conversation_message_idempotency_db + test_conversation_multiturn_db_e2e
                  + test_conversation_api_db_e2e）
Step 6 regression = 0（幂等 / duplicate / retry / conflict / concurrency 全绿）
```

---

## 15. 本 Step 产物与验证

```text
新增文件（唯一）：
    docs/evaluation/Phase 4.2 Step 7A — Conversation Context Boundary Audit.md

Backend = 0 · Tests = 0 · DB = 0 · API = 0
未执行 commit（未授权）
```
