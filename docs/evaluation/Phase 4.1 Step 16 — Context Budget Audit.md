# Phase 4.1 Step 16 — Context Budget Audit

- 阶段：Phase 4.1（Conversation / Chat 会话层）
- 类型：**Audit only**（只审计 + 冻结设计边界；**不实现** truncation / budget）
- 前置：Step 13（Context Builder）+ Step 14（接线）+ Step 15（Multi-turn PostgreSQL E2E，15 passed）
- 状态：`Production Code = 0` · `DB = 0` · `Network = 0` · `LLM = 0` · `Tokenizer = 0`

核心问题：

> 当 Conversation 有几十、几百、几千条历史消息时，当前 Context Builder 是否会无限增长，
> 以及未来应该在哪一层建立 Context Budget / Truncation 边界？

---

## 1. Current State

真实链路（Step 14 接线后，**本 Step 零修改**）：

```text
Conversation DB（ai_ops.conversation_turn）
        ↓
ConversationRepository.list_turns_by_conversation_id()   # ORDER BY created_at ASC, turn_id ASC
        ↓                                                  # （conversation_repository.py:316）
ConversationService.list_turns()                           # （conversation_service.py:261）
        ↓
ChatApplicationService.execute_message()
        ├── append USER turn（TX1 commit）
        ├── 读取历史 + 按 turn_id 排除 current USER turn   # （chat_application_service.py:269）
        └── context = build_context(previous_turns)        # （conversation_context_builder.py:144）
        ↓
AIOrchestratorService.execute(question, *, context=...)
```

当前语义（无任何选择 / 预算 / 截断）：

```text
Current context = full previous history
Token budget = not implemented
Truncation = not implemented
Summary = not implemented
Memory = not implemented
Tokenizer = not introduced
```

即：**previous turns 全量进入 context**，context 字符长度与会话历史条数线性增长
（见 §4 实测）。

---

## 2. Future State (Design)

未来应在 **Context Builder 之前**引入独立的 Selection / Budget 层（本 Step 只冻结边界，不实现）：

```text
Conversation History（全量，持久化事实）
        ↓
Context Selection / Budget        ← 未来新增层（本 Step 不创建）
        ↓
ConversationContextBuilder        ← 只负责格式化（现状职责不变）
        ↓
AIOrchestrator
```

关键原则：

```text
Selection 决定"给哪些 turns"
Builder   决定"如何把 turns 变成 context 字符串"
```

两者**不得**合并成"Builder 内部隐式截断"——否则 context 内容将变得依赖隐蔽预算参数，
破坏当前确定性契约（§11）。

---

## 3. Context Contract (Frozen)

以下行为由 `tests/test_conversation_context_builder.py` /
`tests/test_conversation_context_budget_audit.py` 双重锁定，**本 Step 不允许改变**：

```text
1.  USER → "user"
2.  ASSISTANT → "assistant"
3.  顺序保持（= 输入顺序；Builder 不排序）
4.  content 原样（不 strip / truncate / normalize / escape）
5.  current USER turn 不进入 context（排除由 ChatApplicationService 按 turn_id 负责）
6.  context 不含 metadata
7.  context 不含 request_id
8.  context 不含 timestamp
9.  context 不含 SQL / RAG chunks / Tool payload
10. 空历史 → context = None（不伪造 "" / {"messages": []}）
```

真实 contract：`build_context(turns) -> str | None`（`AIOrchestratorService.execute`
的 `context` 参数即 `str | None`）。

---

## 4. Context Growth Evidence

离线实测（无 DB / 无 LLM；`ConversationTurnView` + 真实 `build_context`；
每条 content = 20 字符，交替 USER / ASSISTANT）：

| turn_count | context_length（字符） |
| ---: | ---: |
| 1 | 26 |
| 10 | 294 |
| 50 | 1474 |
| 100 | 2949 |
| 500 | 14749 |
| 1000 | 29499 |

精确公式（测试按此逐点断言）：

```text
context_length(n) = Σ_{i=0..n-1} len(role_i + ": ") + n × 20 + (n - 1) 个分隔换行
```

结论：**context 随历史条数线性增长，当前无任何上限**（500 turns ≈ 14.7K 字符；
1000 turns ≈ 29.5K 字符，尚未包含 System Prompt / RAG / Tool 上下文）。

> 本表只证明"增长"这一事实，**不是 benchmark**，也不做任何性能优化。

---

## 5. Token Counting = Deferred

本 Step **禁止**由字符数推算 token 数：

```text
禁止：len(text) / 4
禁止：len(text) / 3
禁止：字符数 × 任意比例
禁止：引入 tokenizer（tiktoken / transformers / tokenizers / sentencepiece）
```

原因：不同 tokenizer（模型 / 版本 / 语言配比）结果不同，字符比例只是近似，
把近似值当预算依据会导致无法复现的行为漂移。

冻结口径：

```text
审计口径 = character length（len(context)）
Token counting = deferred（未来若需要，必须引入真实 tokenizer 并作为显式决策）
```

---

## 6. Future Budget Responsibility

| 层 | 负责 | 不负责 |
| --- | --- | --- |
| ConversationService | 保存历史 / 读取历史 / 排序（`created_at ASC, turn_id ASC`） | LLM context truncation / token counting / memory / summary |
| （未来）Selection / Budget 层 | 从 previous turns 中选出"进入 context 的 turns" | 格式化 / AI 调用 / DB 读写 |
| ConversationContextBuilder | 已选 turns → context 字符串（现状职责不变） | **复杂 token budget strategy**（不建议） |
| ChatApplicationService | orchestration：history → selection → builder → AI | 具体选择算法（应委托给独立层 / 策略对象） |

即未来形态（**本 Step 不实现**）：

```text
History
 ↓
Context Selection
 ↓
Context Builder
 ↓
AI
```

不建议让 Builder 同时承担"选择 + 格式化"：Builder 保持"纯函数 + 无策略参数"
是当前可确定性测试的基础（§11）。

---

## 7. Truncation Strategies

三种候选策略比较（**本 Step 不选择最终实现方案**）：

### Strategy A：最近 N turns

```text
保留最近 N 条（按 created_at ASC, turn_id ASC 取尾部）
```

- 优点：简单 / 确定性 / 零成本（无 tokenizer）；
- 缺点：早期业务上下文可能丢失（例如首轮定义的目标 / 约束）；
- 风险：固定 N 与真实 LLM 限制无对应关系。

### Strategy B：最近 N + 首条

```text
首轮（USER + 可选 ASSISTANT）
+ 最近 N 条
```

- 优点：保留初始上下文（目标 / 场景）；
- 缺点：仍是固定规则；"首条"与"最近 N"之间可能出现语义断层（中间被丢弃）；
- 风险：拼接边界需要明确标记，否则历史被误读为连续对话。

### Strategy C：Token Budget

```text
从最近历史开始，逐条加入，到达 token budget 停止
```

- 优点：更接近真实 LLM context limit；
- 缺点：需要 tokenizer / token estimation（当前被 §5 明确禁止，属未来显式决策）；
- 风险：不同模型 / 版本 budget 不同；需要额外的可复现性保证。

共同约束（无论未来选哪种）：

```text
* 输入必须是 previous turns（已排除 current USER —— §9）；
* 不得改变 §3 的格式化契约（首尾边界除外）；
* 必须是确定性的（同输入 + 同配置 → 同输出 —— §11）；
* 失败不得静默降级（§10）。
```

---

## 8. System / User / Assistant Boundary

当前 Conversation DB **只有**：

```text
USER
ASSISTANT
```

因此：

```text
system prompt        → 不在 Conversation History
project context      → 不在 Conversation History
tool definitions     → 不在 Conversation History
current user message → 不在 Conversation History（只作 question 传入）
```

未来 Context Budget 的适用范围必须明确：

```text
System Context（独立管理）
+ Conversation Context（本预算作用域）
+ Current User Message（独立管理）
```

即：**Conversation History Budget 不得吞掉 System Prompt / Project Context /
Tool Definitions** 的预算；三类上下文分别管理（未来决策，本 Step 不实现）。

本 Step 不修改 AIOrchestrator（`context` 仍只承载会话历史文本）。

---

## 9. Current USER Boundary

现状（Step 14 已接线并 E2E 验证）：

```text
USER turn 写 DB（TX1）
      ↓
读取 previous turns（list_turns）
      ↓
排除 current USER（按 append 返回的 turn_id）
      ↓
build_context(previous turns)
      ↓
execute(current content, context)
```

未来 truncation 必须基于 **previous turns**，而不是：

```text
current user + previous turns
```

否则会产生：

```text
current question duplicated
（question 与 context 同时包含当前问题）
```

该边界已写成契约测试（Service 源码按 turn_id 排除 + builder 层面 current 内容不出现）。

---

## 10. Failure Semantics

未来 Context Selection / Budget 失败时的冻结原则：

```text
Context selection failure
    ↓
business failure（异常上抛）
```

明确禁止：

```text
✗ 自动调用 LLM（例如让模型帮忙摘要后继续）
✗ 自动降级成"全部历史"（静默改变上下文语义）
✗ 吞掉异常后继续执行 AI
```

与当前接线一致：Step 14 中 history read failure / context build failure
都会导致 `AI = 0` 且异常原样上抛（USER turn 保留）。

---

## 11. Determinism

未来必须满足：

```text
相同 turn history + 相同 budget 配置
    ↓
same selected history
    ↓
same context
```

不得依赖：

```text
current time / random / LLM / DB iteration order
```

当前基础已具备：

```text
* 排序由 Repository 固定：ORDER BY created_at ASC, turn_id ASC
  （conversation_repository.py:316）；
* Builder 是纯函数（deterministic / stateless / 无 IO）；
* 同一输入两次调用输出严格相等（测试锁定）。
```

---

## 12. Security

Context Budget / Truncation 不得引入以下内容到 context：

```text
API key / Authorization / DATABASE_URL / password
SQLAlchemy Session / raw DB connection
assistant metadata / request_id / provider request_id
tool raw result / RAG chunks / SQL
```

允许保留（业务内容）：

```text
USER content
ASSISTANT content
```

当前 Contract（Step 13 冻结）继续保持：context 只含 role 与 content
（无 metadata / 无 timestamp / 无 request_id）。

---

## 13. Deferred Implementation

本 Step **不实现**（全部延期）：

```text
Context Selection 层
Token budget / tokenizer / token counting
Truncation（Strategy A / B / C 均未选择）
Summary（会话摘要）
Memory（长期记忆）
System Prompt / Project Context / Tool Definitions 的联合预算
Budget 配置项 / 环境变量 / 管理接口
```

未来实现前必须回答（记录为开放问题）：

```text
1. budget 口径（字符 / token / 条数）？
2. 超预算时保留头部还是尾部（或 A/B/C 变体）？
3. 截断标记是否需要（避免历史被误读为连续）？
4. budget 配置属于 Conversation、Project 还是全局？
5. 与 System Prompt / RAG / Tool 预算的分配关系？
```

---

## 附：审计证据（真实代码锚点）

```text
conversation_repository.py:316    ORDER BY created_at ASC, turn_id ASC
conversation_service.py:261       list_turns() -> tuple[ConversationTurnView, ...]
chat_application_service.py:269   turn.turn_id != user_turn.turn_id（排除 current）
conversation_context_builder.py:144  build_context(turns) -> str | None（无预算参数）
```

测试：

```text
tests/test_conversation_context_budget_audit.py
    → growth（1/10/50/100/500/1000 字符长度精确断言 + 增长表）
    → contract（role / ordering / current 排除 / 无 metadata / 无 SQL·RAG·Tool 词汇）
    → no token estimation（builder 与本审计文件均无 tokenizer）
    → AST 依赖边界（禁 sqlalchemy / psycopg / openai / deepseek / siliconflow /
      tiktoken / transformers / tokenizers / redis / celery / kafka / httpx /
      requests / backend.app.db）
    → 文档完整性
```
