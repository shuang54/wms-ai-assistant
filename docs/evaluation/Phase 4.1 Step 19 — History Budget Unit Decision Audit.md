# Phase 4.1 Step 19 — History Budget Unit Decision Audit

- 阶段：Phase 4.1（Conversation / Chat 会话层）
- 类型：**Audit only**（只比较"用什么作为 History Budget 的度量单位"；**不选择最终单位**）
- 前置：Step 16（Budget Audit）+ Step 17（Selection Contract）+ Step 18（Policy / Budget Ownership）
- 状态：

```text
Production Code = 0
History Budget Unit = Deferred
Tokenizer = Deferred
ContextSelector = Deferred
Truncation = Deferred
DB = 0 · Network = 0 · LLM = 0
```

核心问题：

> 如果未来要限制 Conversation History，到底先用什么作为 Budget？

---

## 1. Current State

真实代码核实（AST + 全文搜索）：

```text
Conversation ORM           5 列（conversation_id / project_id / created_at / updated_at / status）
ConversationRepository     无预算概念
ConversationService        5 方法（无 budget 参数）
ConversationContextBuilder build_context(turns)（无 budget 参数）
ChatApplicationService     全量 previous turns → Builder（无预算 / 无选择层）
AIOrchestratorService      execute(question, *, context)（无 budget 参数）
```

搜索结论：

```text
context_budget / selection_policy / token_budget / max_turns   → 零命中
context_window                                                 → 零命中（无 model window 来源）
max_tokens                                                     → 仅"未配置 (client default)"记录
tokenizer（tiktoken / tokenizers / sentencepiece）              → 零命中
```

### 1.1 已存在的其它约束（如实记录；**均不是** History Budget）

```text
services/context_builder.py            DEFAULT_MAX_CONTEXT_CHARS = 12000   # RAG chunk 序列化
services/schema_serializer_service.py  DEFAULT_MAX_CHARS = 12000           # Schema 序列化
services/database_context_composer.py  DEFAULT_CONTEXT_MAX_CHARS = 12000   # Text-to-SQL DB context
services/sql_validator_service.py      DEFAULT_MAX_ROWS                    # SQL 行数限制
config.py ToolSettings                 ToolCallingBudgetExceededError      # Tool round 预算
```

### 1.2 唯一的 tokenizer 相关事实（与 History 无关）

```text
backend/app/reranker/client.py   懒加载 transformers（torch + transformers，可选依赖；
                                 缺失时保持 None，不 import 失败）
```

如实说明：

```text
* 这是 Reranker 的可选模型依赖，不是 Conversation History 的 tokenizer；
* Conversation 链路（Builder / Service / Repository / AppService / Orchestrator）
  不引用 reranker；
* requirements.txt 未声明 tiktoken / transformers。
```

---

## 2. max_turns

```text
最多保留最近 N 个历史 Turn
```

特点：

```text
无需 tokenizer · O(1) 规则 · 确定性强 · 实现简单
```

问题：

```text
不同 Turn 长度差异巨大（20 个短 Turn ≠ 20 个长 Turn）
无法直接映射 LLM Context Window
```

定位：**Engineering Guardrail**（工程保护），不是 Capacity Constraint。

---

## 3. max_chars

```text
最多 M characters
```

特点：

```text
Builder 已可直接观察字符长度（Step 16 已实测）
无需 tokenizer · 确定性
```

问题：

```text
characters ≠ tokens（中文 / 英文 / 代码 tokenization 差异巨大）
不能直接代表模型 context capacity
```

冻结口径：

```text
max_chars 可以作为工程保护阈值，但不能声称它是 Token Budget
```

---

## 4. max_tokens

```text
最多 K tokens
```

特点：

```text
最接近真实 LLM Context Window
```

问题（引入成本）：

```text
必须引入 tokenizer
需要模型 / tokenizer 对应关系
需要处理 tokenizer 不可用（缺依赖 / 版本漂移）
需要明确 system / project / tool / RAG / output reserve 的分配
```

当前：

```text
Tokenizer = Deferred
```

因此本 Step **不实现**。

---

## 5. Hybrid

```text
例：max_turns = 50 + max_chars = 12000
（同时满足：turn_count <= 50 且 character_count <= 12000）
```

优点：

```text
可以提供硬保护（条数 + 体量双限）
```

缺点：

```text
增加 policy complexity
需要定义优先级（超限时先裁哪一侧？）
容易产生多个预算概念并存
```

本 Step 不实现。

---

## 6. Engineering Guardrail vs Capacity Constraint

| 维度 | Engineering Guardrail | Capacity Constraint |
| --- | --- | --- |
| 回答 | "别让请求失控" | "模型能否装下" |
| 候选 | max_turns / max_chars | max_tokens |
| 依赖 | 无（纯计数） | tokenizer / 模型映射 |
| 现状 | 可用（但未实现） | 不可用（Deferred） |

冻结：

```text
不要把 max_chars 描述成 token budget；
不要把 max_turns 描述成 context window。
```

---

## 7. Step 16 Evidence

复用 Step 16 已实测的字符增长证据（**不重新计算、不引入 tokenizer**）：

| turn_count | context_length（chars） |
| ---: | ---: |
| 1 | 26 |
| 10 | 294 |
| 50 | 1474 |
| 100 | 2949 |
| 500 | 14749 |
| 1000 | 29499 |

结论：History length grows linearly（详见 Step 16 §4 公式）。

注意：**不得**从 chars 推导 tokens（无长度比例换算；见 §8）。

---

## 8. Tokenizer Decision

审计 6 个前置条件：

| # | 条件 | 当前状态 |
| --- | --- | --- |
| 1 | 是否已需要真实 token-level enforcement？ | 否（History 预算尚未实现） |
| 2 | 是否已有明确 model / tokenizer mapping？ | 否（`context_window` 零命中） |
| 3 | 是否已定义 system / project / tool / RAG / output reserve？ | 否（Step 17 §13 仅冻结分段概念） |
| 4 | 是否已决定 model context window source？ | 否 |
| 5 | 是否已有 tokenizer dependency？ | 否（仅 reranker 可选懒加载，与 History 无关） |
| 6 | 是否引入新 runtime dependency？ | 是（若引入 → 新依赖 + 版本管理成本） |

结论：

```text
Tokenizer = Deferred
```

不因为"未来需要"而在本 Step 引入。

---

## 9. Budget Ownership

延续 Step 18（未修改）：

```text
Project   → Default SelectionPolicy（含 budget 引用）
Request   → Optional internal override
Model     → Capability cap（上限约束）
```

仍然：

```text
Conversation ORM / Repository / Service 不拥有 Budget
```

---

## 10. Policy / Budget Composition

未来概念组合（不冻结字段名）：

```text
SelectionPolicy
    ├── strategy
    └── budget reference

Budget
    ├── unit
    └── limit
```

明确：**不创建** `max_chars` / `max_turns` / `max_tokens` production DTO（本 Step）。

---

## 11. Zero / Negative

未来语义（只比较，不实现）：

```text
negative → invalid policy（配置阶段拒绝；例如 limit = -1）

zero → valid but selects empty history（合法边界：不选择任何 previous turn）
```

推荐（记录为设计结论，不写入 production code）：

```text
negative：invalid policy（ValueError 家族，和"策略非法"同类）
zero：合法最小值（与"无历史"完全相同的结果）
```

---

## 12. Oversized Budget

```text
requested budget = 1,000,000
available budget = 6,000
```

冻结契约（延续 Step 18）：

```text
effective budget = min(requested, available)
```

即：**有效预算不超过模型侧剩余空间**；不能把 requested 原样使用。

本 Step 只记录 Contract，不计算实际值（无 tokenizer）。

---

## 13. Builder Boundary

无论未来选择 `max_turns` / `max_chars` / `max_tokens`：

```text
Selected Turns
    ↓
ConversationContextBuilder.build_context()
```

Builder 不知道：

```text
budget / policy / model / context window
```

签名保持只有 `turns`（AST 与 inspect 双重锁定）。

---

## 14. Persistence Boundary

Conversation ORM 仍只表达：

```text
conversation_id / project_id / created_at / updated_at / status
```

不增加：

```text
context_budget / max_turns / max_chars / max_tokens
```

原因：这会把 **Conversation Persistence** 与 **LLM Runtime Policy** 耦合
（Step 18 §8 冻结）。

---

## 15. Failure Semantics

```text
Budget invalid（配置阶段）
    → invalid policy

Valid budget + Selector runtime failure（运行阶段）
    → business failure
```

禁止：

```text
fallback full history
ignore budget
silent degradation
```

（与 Step 17 / Step 18 完全一致。）

---

## 16. Security

Budget 只允许表达：

```text
unit / limit / strategy
```

禁止包含：

```text
API key / Authorization / DATABASE_URL / password
SQL / prompt / messages / RAG chunks / Tool arguments / LLM response / DB session
```

---

## 17. Determinism

未来：

```text
same history + same policy + same budget + same model capability
    ↓
same selected turns
```

不得依赖：

```text
current time / random / LLM / DB iteration order
```

---

## 18. Deferred Decision

本 Step **不选择最终单位**。最终明确：

```text
History Budget Unit = Deferred
Tokenizer = Deferred
ContextSelector = Deferred
Truncation = Deferred
```

候选（未决策）：

| Option | 组合 | 依赖 |
| --- | --- | --- |
| 1 | Recent N Turns（max_turns） | 无 |
| 2 | Recent Turns + Character Guardrail（max_turns + max_chars） | 无 |
| 3 | Token Budget（max_tokens） | tokenizer（Deferred） |

决策前必须回答（开放问题）：

```text
1. 第一版是"业务预算"还是"安全护栏"？（§6 区分）
2. 是否接受 Option 2 的双预算复杂度（优先级如何定义）？
3. 何时满足 §8 的 6 个前置条件（从而解锁 max_tokens）？
4. unit 的默认值与覆盖链（Project / Request / Model）如何组合？
5. zero 是否允许作为"禁用历史"的正式语义？
```

---

## 附：审计证据（真实代码锚点）

```text
backend/app/db/models/conversation.py            5 列（无预算字段）
backend/app/db/conversation_repository.py        无 budget / tokenizer / selector
backend/app/services/conversation_service.py     5 方法（无 budget 参数）
backend/app/services/conversation_context_builder.py  build_context(turns)（无 budget）
backend/app/services/chat_application_service.py    全量 previous turns → Builder
backend/app/reranker/client.py                   可选懒加载 transformers（与 History 无关）
requirements.txt                                 无 tiktoken / transformers 声明
```

测试：

```text
tests/test_conversation_context_budget_unit_audit.py
    → 四单位区分 / max_chars ≠ token budget / Step 16 证据复用 /
      ownership·persistence·builder 边界 / zero·negative / oversized min() Contract /
      tokenizer 缺席（含 reranker 事实记录）/ security / failure / determinism / 文档
tests/test_conversation_context_budget_architecture.py
    → AST：ORM / Repository / Service / Builder（无 budget·tokenizer·selector）/
      AppService（允许依赖 + 当前无 budget·selector）/ 全链路 tokenizer 缺席
```
