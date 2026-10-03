# Phase 4.1 Step 20 — History Selection Strategy Decision Audit

- 阶段：Phase 4.1（Conversation / Chat 会话层）
- 类型：**Audit only**（只比较 Selection Strategy；不实现 ContextSelector / Truncation / Tokenizer）
- 前置：Step 17（Selection Contract）+ Step 18（Policy / Budget Ownership）+ Step 19（Budget Unit）
- 状态：

```text
Production Code = 0
ContextSelector = Deferred
Truncation = Deferred
Tokenizer = Deferred
Budget Unit = Deferred
Final Selection Strategy = Deferred
DB = 0 · Network = 0 · LLM = 0
```

核心问题：

> **Conversation History 应该采用什么 Selection Strategy？**

---

## 1. Current State

```text
Full History
    ↓
ContextBuilder
    ↓
LLM
```

```text
Current History Budget = Unlimited
```

（Step 16 实测：context 字符长度随历史条数**线性增长**，无任何上限；
Step 14 已接线 previous turns → Builder → AIOrchestrator，无选择层。）

当前契约锚点（真实代码，**本 Step 零修改**）：

```text
ConversationService.list_turns()          → previous turns（created_at ASC, turn_id ASC）
ChatApplicationService.execute_message()  → 排除 current USER（按 turn_id）→ build_context → execute
ConversationContextBuilder.build_context(turns) → str | None（只格式化）
```

---

## 2. Candidate Strategies

冻结为四种（不新增第五种）：

```text
Strategy A: Recent N                        （保留最近 N 个历史 Turn）
Strategy B: Recent N + First Turn           （First Turn + 最近 N 个历史 Turn）
Strategy C: Relevance / Token Budget Selection（相关性 + Token 预算选择）
Strategy D: Hybrid                          （Recent N + First Turn + 未来 Token Budget）
```

定义示例（A / B）：

```text
T1 T2 T3 T4 T5 T6 T7 T8

A: N = 4             → T5 T6 T7 T8
B: N = 3             → T1 T6 T7 T8
```

---

## 3. Selection Quality Matrix

说明：本矩阵是**架构判断**，不是 benchmark 排名 —— 只使用：

```text
Good fit / Weak fit / Requires future capability / Risk
```

### Scenario 1：连续追问

| 策略 | Early Constraint Preservation | Recent Context Preservation | Middle Decision Preservation | Long Turn Safety | Determinism | Implementation Complexity | Tokenizer Dependency |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Strategy A: Recent N | Weak fit | Good fit | Weak fit | Risk | Good fit | Good fit | Good fit |
| Strategy B: Recent N + First Turn | Weak fit | Good fit | Weak fit | Risk | Good fit | Good fit | Good fit |
| Strategy C: Relevance / Token Budget Selection | Requires future capability | Requires future capability | Requires future capability | Requires future capability | Requires future capability | Risk | Requires future capability |
| Strategy D: Hybrid | Weak fit | Good fit | Weak fit | Requires future capability | Good fit | Risk | Requires future capability |

### Scenario 2：早期业务约束

| 策略 | Early Constraint Preservation | Recent Context Preservation | Middle Decision Preservation | Long Turn Safety | Determinism | Implementation Complexity | Tokenizer Dependency |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Strategy A: Recent N | Risk | Good fit | Weak fit | Risk | Good fit | Good fit | Good fit |
| Strategy B: Recent N + First Turn | Good fit | Good fit | Weak fit | Risk | Good fit | Good fit | Good fit |
| Strategy C: Relevance / Token Budget Selection | Requires future capability | Requires future capability | Requires future capability | Requires future capability | Requires future capability | Risk | Requires future capability |
| Strategy D: Hybrid | Good fit | Good fit | Weak fit | Requires future capability | Good fit | Risk | Requires future capability |

### Scenario 3：中间决策

| 策略 | Early Constraint Preservation | Recent Context Preservation | Middle Decision Preservation | Long Turn Safety | Determinism | Implementation Complexity | Tokenizer Dependency |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Strategy A: Recent N | Weak fit | Good fit | Risk | Risk | Good fit | Good fit | Good fit |
| Strategy B: Recent N + First Turn | Weak fit | Good fit | Risk | Risk | Good fit | Good fit | Good fit |
| Strategy C: Relevance / Token Budget Selection | Requires future capability | Requires future capability | Requires future capability | Requires future capability | Requires future capability | Risk | Requires future capability |
| Strategy D: Hybrid | Weak fit | Good fit | Requires future capability | Requires future capability | Good fit | Risk | Requires future capability |

### Scenario 4：长文本 Turn

| 策略 | Early Constraint Preservation | Recent Context Preservation | Middle Decision Preservation | Long Turn Safety | Determinism | Implementation Complexity | Tokenizer Dependency |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Strategy A: Recent N | Weak fit | Good fit | Weak fit | Risk | Good fit | Good fit | Good fit |
| Strategy B: Recent N + First Turn | Weak fit | Good fit | Weak fit | Risk | Good fit | Good fit | Good fit |
| Strategy C: Relevance / Token Budget Selection | Requires future capability | Requires future capability | Requires future capability | Requires future capability | Requires future capability | Risk | Requires future capability |
| Strategy D: Hybrid | Weak fit | Good fit | Weak fit | Requires future capability | Good fit | Risk | Requires future capability |

### Scenario 5：重新回到早期主题

| 策略 | Early Constraint Preservation | Recent Context Preservation | Middle Decision Preservation | Long Turn Safety | Determinism | Implementation Complexity | Tokenizer Dependency |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Strategy A: Recent N | Risk | Good fit | Weak fit | Risk | Good fit | Good fit | Good fit |
| Strategy B: Recent N + First Turn | Weak fit | Good fit | Weak fit | Risk | Good fit | Good fit | Good fit |
| Strategy C: Relevance / Token Budget Selection | Requires future capability | Requires future capability | Requires future capability | Requires future capability | Requires future capability | Risk | Requires future capability |
| Strategy D: Hybrid | Good fit | Good fit | Requires future capability | Requires future capability | Good fit | Risk | Requires future capability |

非对称结论（论证而非排名）：

```text
A / B 的共同缺口：无法保证"中间关键决策"与"长 Turn 体量"（Risk）；
C 的全部价值维度当前都是 Requires future capability（4 个前置缺失，见 §4）；
D 引入 budget 后复杂度上升（Risk），但预算未定 → 无法落地。
```

---

## 4. WMS Scenarios

五个离线场景（测试构造并断言保留行为）：

| 场景 | 构造（previous turns） | 观察 |
| --- | --- | --- |
| Scenario 1：连续追问 | U1 查询当前库存 → A1 100 件 → U2 A01 多少 → A2 60 件 | A（N=2）保留直接语境（A01）；两种简单策略均 Good fit |
| Scenario 2：早期业务约束 | U1 只查询越南一厂库存 → A1 仅越南一厂 → U2 查询库存 → A2 库存 80 件 | A（N=2）**丢失**"越南一厂"；B 保留（Good fit） |
| Scenario 3：中间决策 | U1 查询库存 → A1 … → U2 按仓库统计 → A2 … → U3 只看 A01/A02 → A3 … | B（N=1）**丢失**中间约束（A01/A02）；A 保留近期 |
| Scenario 4：长文本 Turn | 短问题/短回答 + 一个超长 Turn（约 800 字符）×2 | 同 turn 数（N=2）下体量相差 >10x → 再次证明 max_turns ≠ capacity |
| Scenario 5：重新回到早期主题 | U1 我们讨论采购入库流程 → …（9 轮填充）… → U20 回到刚才采购入库的问题 | A（N=4）无法带回早期主题（Risk）；B 仅保留首轮，语义可能断层 |

特别说明（不得默认）：

```text
First Turn ≠ permanent memory
（第一轮用户问题不一定具有长期价值；B 的收益依赖场景假设。）
```

---

## 5. Invariants

无论未来选择哪种 Strategy，以下不变量必须保持（测试锁定）：

```text
1. Subsequence              selected ⊆ previous turns（不创造不存在的 Turn）
2. Ordering                 selected 保持 repository order（created_at ASC, turn_id ASC；不重排）
3. Current USER Exclusion   当前 USER 已由 ChatApplicationService 排除；
                            Selector 不重新决定"是否包含当前 User"
4. Determinism              same history + policy + budget + capability → same selection
5. No Semantic Mutation     不修改 role / content / turn_id / assistant_request_id / created_at
```

---

## 6. Failure Semantics

```text
Invalid policy ≠ runtime selection failure
```

```text
invalid policy（配置阶段）
    例：recent_n = -1
    → configuration / validation error（ValueError 家族）

runtime selection failure（运行阶段）
    例：Selector execution failed
    → business failure（异常原样上抛）
```

禁止（与 Step 17 / 18 / 19 一致）：

```text
No fallback full history
No ignore budget
No silent degradation
```

---

## 7. Budget Separation

```text
Strategy ≠ Budget Unit
```

- Strategy 回答"**怎么选**"（Recent N / Recent + First / Hybrid）；
- Budget Unit 回答"**最多多少**"（Step 19：max_turns / max_chars / max_tokens = Deferred）。

合法组合示例：

```text
Recent N + max_chars                      （工程组合，无需 tokenizer）
Recent N + future max_tokens              （未来组合，依赖 tokenizer 决策）
Recent N + First Turn + future max_tokens （Hybrid 形态）
```

因此：Step 19 未选择 Budget Unit **不**意味着本 Step 必须选择 Deferred —— 但本 Step 的证据仍不足以冻结 Strategy（见 §8）。

---

## 8. Deferred

本 Step **不选择最终实现策略**：

```text
ContextSelector = Deferred
Truncation = Deferred
Tokenizer = Deferred
Budget Unit = Deferred
Final Selection Strategy = Deferred
```

Why（为什么仍 Deferred）：

```text
1. 缺少真实多轮 WMS 对话集（无法量化"早期约束保留率 / 中间决策保留率"）；
2. 缺少"历史必须保留"与"可以丢弃"的业务分级（哪些 turn 是长期约束？）；
3. Budget Unit 未定（Strategy D 的裁剪条件无法定义）；
4. Model Context Window 未确定（context_window 零命中；无 capacity 锚点）；
5. First Turn 的长期价值未经真实数据验证（B 的收益是假设）。
```

What evidence is missing（下一步所需证据）：

```text
* 真实多轮 WMS 对话采样（含 3 类：连续追问 / 早期约束 / 中间决策）；
* 每类场景下"丢失某 turn 后回答质量变化"的人工标注（离线即可，不需要 LLM）；
* 会话长度分布（turn 数 / 字符体量），用于评估 N 与 max_chars 的候选值；
* 未来 tokenizer / model window 决策（解锁 Strategy C / D 的量化比较）。
```

What next experiment is needed（下一阶段建议，不在本 Step 执行）：

```text
1. 构造 WMS 对话集（纯文本 fixture，无 DB / 无 LLM）；
2. 对 A / B 两个可落地策略做 off-line 保留率统计（哪些 turn 被裁掉）；
3. 产出"候选 N / max_chars 配置区间"的审计报告（仍不实现 Selector）；
4. 待 tokenizer 决策后再评估 C / D。
```

---

## 附：审计证据（真实代码锚点）

```text
backend/app/db/models/conversation.py            5 列（无 selector/policy/budget 字段）
backend/app/db/models/conversation_turn.py       6 列（同上）
backend/app/db/conversation_repository.py        无 budget / tokenizer / selector
backend/app/services/conversation_service.py     5 方法（无 budget / policy 参数）
backend/app/services/conversation_context_builder.py  build_context(turns)（无选择参数）
backend/app/services/chat_application_service.py     previous turns → Builder（无选择层）
```

测试：

```text
tests/test_conversation_context_selection_strategy_audit.py
    → A/B/C/D 定义 / C 前置缺失 / D 需 budget / 5 不变量 / 5 WMS 场景 /
      矩阵完整性（维度 × 标签 × 无评分）/ 无 token 估算 / 无 tokenizer /
      无 production selector·budget / persistence·builder·app 边界 / 失败语义 / 文档
tests/test_conversation_context_selection_strategy_architecture.py
    → AST 六文件：ORM（字段冻结）/ Repository·Service·Builder（无预算·选择·tokenizer）/
      AppService（允许依赖 + 签名不变）/ Deferred 模块缺席
```
