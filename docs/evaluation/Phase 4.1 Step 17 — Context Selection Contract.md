# Phase 4.1 Step 17 — Context Selection Contract

- 阶段：Phase 4.1（Conversation / Chat 会话层）
- 类型：**Contract only**（只冻结"哪些历史 Turn 进入 LLM Context"的边界；不实现 Selector）
- 前置：Step 16（Context Budget Audit：线性增长已实测，Tokenizer = Deferred）
- 状态：

```text
Production Code = 0
Context Selector = not implemented
Truncation = not implemented
Tokenizer = not introduced
DB = 0 · Network = 0 · LLM = 0
```

核心问题：

> 到底由哪一层决定"哪些历史 Turn 可以进入 LLM Context"？

---

## 1. Current Architecture

真实链路（Step 14 接线后，**本 Step 零修改**）：

```text
Conversation DB（ai_ops.conversation_turn）
        ↓
ConversationRepository.list_turns_by_conversation_id()
        ↓  ORDER BY created_at ASC, turn_id ASC（conversation_repository.py:316）
ConversationService.list_turns()                     # （conversation_service.py:261）
        ↓
ChatApplicationService.execute_message()
        ├── append USER turn（TX1 commit）
        ├── 排除 current USER（按 turn_id；chat_application_service.py:269）
        └── context = build_context(previous_turns)  # （conversation_context_builder.py:144）
        ↓
AIOrchestratorService.execute(question, *, context=...)
```

当前：**previous turns 全量** → Builder → context（无选择层，线性增长已由 Step 16 实测）。

---

## 2. Selection Responsibility

未来独立概念：**Context Selector**（本 Step 不创建）。

职责**只有**一件：

```text
从 Conversation History（previous turns）中选择哪些 Turn 可以进入 Context
```

```text
ConversationTurnView[]（previous）
        ↓
Context Selector（未来新增）
        ↓
ConversationTurnView[]（selected ⊆ previous）
        ↓
ConversationContextBuilder
        ↓
str | None
```

Selector 不负责：

```text
字符串格式化（Builder 的职责）
LLM 调用 / tokenizer / token counting
数据库读写（输入已由上层读好）
summary / memory / RAG / Tool
```

**为什么 Selector 不放进 Builder**（§6 冻结）：

```text
Builder    : selected turns → formatted context（现状职责，保持不变）
Selector   : all previous turns → selected turns（未来职责）

收益：Recent N / Recent N + First / Token Budget / Summary / Memory
      都可以替换 Selector，而不修改 Builder。
```

若把 selection 塞进 Builder，context 内容将依赖隐蔽预算参数，
破坏"Builder 是纯函数 + 无策略参数"的确定性测试基础（Step 16 §11）。

---

## 3. Builder Responsibility

`ConversationContextBuilder`（Step 13 实现，本 Step 零修改）：

```text
build_context(turns) -> str | None
build_messages(turns) -> tuple[dict[str, str], ...]   # 未接线（未来）
```

规则（冻结，测试双重锁定）：

```text
empty history → None
USER          → "user: <content>"
ASSISTANT     → "assistant: <content>"
多 Turn        → 逐行连接（"\\n"）
保持输入顺序 / content 原样 / 不 trim / 不 normalize / 不 truncate / 不排序
```

Builder 不负责：selection / budget / token counting / summary / memory。

---

## 4. ConversationService Responsibility

当前职责（Step 3 冻结，未修改）：

```text
create_conversation / get_conversation / archive_conversation /
append_turn / list_turns
```

负责：Persistence · Ordering（created_at ASC, turn_id ASC）· Conversation lifecycle。

**不应该知道**：

```text
LLM context window / token budget / prompt budget
model / system prompt / RAG / Tool
```

冻结结论：

```text
ConversationService ≠ Context Selection
```

---

## 5. ChatApplicationService Responsibility

当前（Step 14 接线，本 Step 零修改）：

```text
Conversation → list_turns → exclude current USER → ContextBuilder → AIOrchestrator
```

未来应演化为（**本 Step 不实现**）：

```text
Conversation → list previous turns → ContextSelector → ContextBuilder → AIOrchestrator
```

ChatApplicationService 只负责 **workflow orchestration**；
具体算法（Recent N / Token Budget / Summary）**不得**写进 ChatApplicationService，
必须委托独立 Selector（策略对象），保持可替换。

---

## 6. Selector Input

未来输入契约（概念冻结）：

```text
previous ConversationTurnView[]        # 已排除 current USER
+ 显式 SelectionPolicy / Budget（由上层显式传入）
        ↓
Selected History
```

本 Step **不创建**任何预算配置类型：

```text
✗ max_tokens
✗ max_chars
✗ max_turns
（以上均为未来实现阶段的显式决策，不在本 Step 落地）
```

---

## 7. Selector Output

输出契约（冻结）：

```text
ConversationTurnView[]（selected）
```

必须满足：

```text
selected ⊆ previous（每个 selected 与 input 为同一对象身份）
不修改 Turn / 不创建 Turn / 不删除 DB Turn
不改变 role / content / turn_id
```

测试以 FakeContextSelector（测试内）验证：identity subset、frozen 不可变、
content / role / turn_id 不变、只减不增。

---

## 8. Current USER Boundary

Selector 的输入**必须已经是 previous turns**：

```text
ChatApplicationService
        ↓
exclude current USER（按 append 返回的 turn_id）
        ↓
Selector（纯历史选择器）
```

禁止 Selector：

```text
自己通过 turn_id / content 猜测哪个是 current user
接收 conversation_id 去回查 DB
```

理由：否则会与 Step 14 的排除语义重复，并可能产生
`current question duplicated`（question 与 context 同时包含当前问题）。

---

## 9. Ordering

Selector 必须保持 Conversation Repository 顺序：

```text
created_at ASC, turn_id ASC
```

允许：选择子集；**禁止**重新排序。例：

```text
input  : A B C D
select : B D        → 输出必须 B D（子序列）
禁止   : D B
```

测试断言：任意 policy 下 selected 在 input 中的下标序列严格非降（子序列）。

---

## 10. Determinism

冻结：

```text
same history + same policy
    ↓
same selected turns
```

禁止依赖：random / current time / LLM / DB iteration order / hash randomization。

（当前排序已由 Repository 固定；Builder 已是纯函数；Selector 未来必须同样满足。）

---

## 11. Failure Semantics

```text
Context selection failure
    ↓
business failure（异常原样上抛）
```

明确禁止：

```text
✗ fallback to full history（静默改变上下文语义）
✗ skip context silently（无 context 继续执行）
✗ call LLM summary（用模型"救场"）
✗ continue without context
```

与 Step 14 保持一致：

```text
context failure
    ↓
AI call = 0
USER turn retained
no ASSISTANT turn
```

本 Step 只冻结，不实现。

---

## 12. Security Boundary

Selector 只允许处理：

```text
ConversationTurnView
SelectionPolicy / Budget（未来显式类型）
```

禁止接触：

```text
API key / Authorization / DATABASE_URL / password
SQLAlchemy Session / DB Connection
LLMResponse / raw provider response
RAG chunks / Tool arguments / Tool raw result
prompt / system prompt / credentials
```

尤其：

> Selector 不应该为了决定历史而读取数据库以外的业务数据。

---

## 13. System / Project / Tool / History Separation

未来总上下文由多个 **Segment** 组成：

```text
System Prompt
+ Project Context
+ Tool Definitions
+ Current User Message
+ Selected Conversation History     ← 本 Contract 的作用域
+ RAG Context
+ Tool Results
```

冻结：

```text
Conversation History Selection 不得吞掉其他 Segment 的预算。
```

未来若引入 Token Budget，形态应为：

```text
Total Context Budget
    ├── System
    ├── Project
    ├── Tools
    ├── User
    ├── Conversation History
    └── RAG / Tool Context
```

但本 Step：**不实现总预算分配**（也不实现单段预算）。

---

## 14. Strategy Comparison

三种未来策略（**不选择最终方案**）：

| 策略 | 形态 | 优点 | 缺点 |
| --- | --- | --- | --- |
| A. Recent N Turns | 保留最近 N 个历史 Turn | 简单 / 确定 / 无需 tokenizer | 早期上下文容易丢失 |
| B. Recent N + First Turn | 第一条历史 + 最近 N 条 | 保留初始上下文 | 仍是固定规则；可能出现语义断层 |
| C. Token Budget | 按真实 token 数选择历史 | 更接近真实 LLM Context Window | 需要 tokenizer / 模型-分词器对应关系 / 预算来源决策 |

共同约束：

```text
* 输入 = previous turns（§8）
* 输出 ⊆ 输入且顺序保持（§7 / §9）
* 确定性（§10）
* 失败 = business failure（§11）
```

---

## 15. Tokenizer Deferred

延续 Step 16：**Tokenizer = not introduced**。

明确禁止以字符长度模拟 token：

```python
len(text) / 4
len(text) / 3
len(text) * 0.25
```

这些只能作为粗略工程估算，**不能成为项目 Token Contract**。

若未来选择 Strategy C，必须作为显式决策引入真实 tokenizer
（并同时确定模型 / 版本 / 预算来源）。

---

## 16. Implementation Deferred

本 Step **不实现**（全部延期）：

```text
Context Selector（production）
SelectionPolicy / Budget 类型（max_turns / max_chars / max_tokens）
Truncation（A / B / C 均未选择）
Tokenizer / token counting
Summary / Memory
ChatApplicationService 接入 Selector
总上下文预算分配（System / Project / Tools / History / RAG）
```

实现前必须回答（开放问题）：

```text
1. policy 归属（Conversation / Project / 全局 / 每请求）？
2. 口径（条数 / 字符 / token）？
3. 截断是否标记（避免历史被误读为连续）？
4. 与其他 Segment 的预算分配关系？
5. 失败时的用户可见语义（错误码 / 文案）？
```

---

## 附：证据锚点

```text
conversation_repository.py:316      ORDER BY created_at ASC, turn_id ASC
conversation_service.py:261         list_turns() -> tuple[ConversationTurnView, ...]
chat_application_service.py:269     turn.turn_id != user_turn.turn_id（排除 current）
conversation_context_builder.py:144 build_context(turns) -> str | None（无选择参数）
```

测试：

```text
tests/test_conversation_context_selection_contract.py
    → Fake Selector：identity subset / immutable / content·role·turn_id 不变 /
      empty / determinism / failure=business failure / 子序列（不重排）/
      pipeline 组合（selected → build_context 行数 = len(selected)）/
      current USER 边界 / 无 production selector / 无 tokenizer / 文档完整性
tests/test_conversation_context_selection_architecture.py
    → AST：Builder（无 tokenizer·DB·LLM·RAG·Tool）/ ConversationService（无 LLM·tokenizer·RAG·Tool）/
      Repository（无 LLM·Builder·AI）/ ChatApplicationService（允许依赖 + 当前无 Selector）
```
