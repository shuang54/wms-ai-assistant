# Phase 4.1 Step 22 — Selection Strategy Decision Gate

- 阶段：Phase 4.1（Conversation / Chat 会话层）
- 类型：**Audit only**（只建立 Decision Gate；不实现 ContextSelector / Truncation / Tokenizer / Budget）
- 前置：Step 17（Selection Contract）+ Step 18（Policy/Budget Ownership）+ Step 19（Budget Unit）
  + Step 20（Strategy Audit）+ Step 21（WMS Evidence）
- 状态：

```text
Selection Strategy Decision = BLOCKED
Selection Strategy = BLOCKED
ContextSelector = Deferred
Truncation = Deferred
Tokenizer = Deferred
Budget Unit = Deferred
Production Code = 0 · DB = 0 · Network = 0 · LLM = 0
```

---

## 1. Purpose

本阶段不再比较 Strategy A/B/C/D，也不再选择任何临时策略。

唯一目标：

> 明确：**未来什么证据和什么基础设施条件满足后，才允许正式冻结 Selection Strategy。**

产出物是 **Decision Gate**（前置条件门），不是 Strategy 本身：

```text
Selection Strategy Decision Gate（本 Step 定义）
        ↓（全部满足后）
Selection Strategy Freeze（未来 Step）
        ↓
ContextSelector Implementation（未来 Step）
```

---

## 2. Current State

链路（真实代码，**本 Step 零修改**）：

```text
ChatApplicationService
    ↓
ConversationContextBuilder（build_context(turns) → str | None）
    ↓
AIOrchestrator.execute(question, *, context)
```

当前**不存在**（在 production 中均为 absent）：

```text
ContextSelector / SelectionPolicy / Budget / Tokenizer / Truncation /
Summarizer / Memory / DecisionGate 实现
```

已有证据（Step 17～21）：

```text
* Selection Contract（子序列 / 顺序 / current-user 排除 / 确定性 / 不变异）已冻结；
* Policy ≠ Budget；Conversation 不拥有 Budget；Policy 归属（Project 默认 + 内部 override + Model cap）已设计；
* Budget Unit = Deferred（max_turns / max_chars / max_tokens / Hybrid 均未选）；
* Strategy 结构性比较完成（A/B/C/D，无质量标注）；
* 12 个 synthetic WMS-realistic 案例 + 离线 preservation 矩阵（36 行）。
```

---

## 3. Gate Matrix

| Gate | Condition | Current |
| ---- | ---- | ---- |
| G1 | Real WMS dataset | BLOCKED |
| G2 | Length distribution | INSUFFICIENT |
| G3 | Constraint annotation | BLOCKED |
| G4 | Loss impact evidence | BLOCKED |
| G5 | Strategy comparison | BLOCKED |
| G6 | Budget unit | BLOCKED |
| G7 | Context window source | BLOCKED |
| G8 | Tokenizer | BLOCKED |
| G9 | Reserve allocation | BLOCKED |
| G10 | Failure semantics | READY |

```text
Selection Strategy Decision = BLOCKED
```

（状态值域：READY / INSUFFICIENT / BLOCKED —— 无分数、无排名。）

---

## 4. G1–G10 Definitions

### G1：真实/脱敏 WMS 多轮对话数据

```text
synthetic dataset ≠ production evidence
（Step 21 的 12 案例是合成样例，不能冒充生产证据。）
```

要求：真实脱敏 WMS 多轮会话样本。当前 → **BLOCKED**。

### G2：对话长度分布

要求（conversation shape evidence）：

```text
turn count distribution（P50 / P90 / P95 / P99 / Max）
character length distribution（P50 / P90 / P95 / P99 / Max）
```

口径说明：

```text
* 只记录字符口径；禁止字符→token 推算；
* 当前只有 synthetic dataset → INSUFFICIENT。
```

### G3：关键约束标注

要求（evaluation annotation，**不是** production DTO）：

```yaml
annotations:
  - type: early_constraint
    turn_index: 0
```

允许类型：`early_constraint` / `middle_decision` / `recent_context` / `old_topic` / `standalone`。
当前无人工标注 → **BLOCKED**。

### G4：历史丢失影响

最重要的一项：不能只证明"turn 被删除"，还需要知道：

> 删除这个 Turn 后，Assistant 的业务答案是否受到影响。

要求证据：

```text
full_history_answer  vs  selected_history_answer
（人工 / 离线质量比较；本阶段不调用真实 LLM）
```

当前 → **BLOCKED**。

### G5：Candidate Strategy 离线比较

要求：`Recent N` / `Recent N + First` / `Hybrid` / `Relevance-based` 的离线比较；
其中 `Relevance-based` 必须等 Relevance Model 真正存在后才能进入。
当前只有结构性模拟（无质量标注）→ **BLOCKED**。

### G6：Budget Unit 决策

要求回答：

```text
max_turns / max_chars / max_tokens / Hybrid
    ↓ 谁承担 hard guardrail？
    ↓ 谁承担 capacity constraint？
    ↓ 谁优先？超限如何处理？
```

允许 guardrail + capacity constraint 双层并存，但必须明确各自职责。
Step 19 已判定 Budget Unit = Deferred → **BLOCKED**。

### G7：Model Context Window Source

要求可信来源：

```text
provider documentation / model configuration / trusted static capability metadata
```

禁止：猜测 / 硬编码未知值 / 从 API response 反推。
若模型动态切换，必须有 `model → capability` 映射。当前无 → **BLOCKED**。

### G8：Tokenizer

只有当 `max_tokens` 真的进入生产 Capacity Constraint 时才允许引入 Tokenizer：

```text
Strategy decision requires token-level enforcement
        ↓
Tokenizer becomes required dependency
```

当前 → **BLOCKED**；`Tokenizer = Deferred` 保持不变。

### G9：Context Reserve

未来必须定义完整分配（不能只给 Conversation History 一个孤立预算）：

```text
Model Context Window
    - System Prompt
    - Project Context
    - Tool Definitions
    - Current User
    - RAG Context
    - Tool Results
    - Output Reserve
    ↓
History Budget
```

特别注意：`RAG Context` 与 `Tool Results` 都是**动态容量**，因此必须定义 reserve strategy。
当前 → **BLOCKED**。

### G10：Failure Semantics

已满足（Step 17/18/19/21 冻结并测试）：

```text
invalid policy ≠ runtime selection failure
No fallback full history
No ignore budget
No silent degradation
```

当前 → **READY**。

---

## 5. Current Status

```text
G1  BLOCKED        （无真实数据集）
G2  INSUFFICIENT   （无长度分布）
G3  BLOCKED        （无约束标注）
G4  BLOCKED        （无丢失影响证据）
G5  BLOCKED        （无质量级策略比较）
G6  BLOCKED        （Budget Unit 未决策）
G7  BLOCKED        （无 window source）
G8  BLOCKED        （Tokenizer Deferred）
G9  BLOCKED        （无 reserve 分配）
G10 READY          （失败语义已冻结）
```

因此：

```text
Selection Strategy Decision = BLOCKED
```

这是**正确结果**：Gate BLOCKED 时不得实现临时策略（不得为了"完成 Phase"而硬选 Recent N）。

---

## 6. Required Evidence

| Gate | 所需证据 | 阻塞来源 |
| --- | --- | --- |
| G1 | 真实脱敏多轮会话样本（6 类场景覆盖） | 数据获取渠道 / 脱敏流程 |
| G2 | turn count + character length 分布（P50/P90/P95/P99/Max） | 依赖 G1 |
| G3 | 人工标注（5 类 annotation） | 标注规范 + 人力 |
| G4 | full vs selected 回答影响比较 | 依赖 G3 + 离线评测流程 |
| G5 | 4 类 Strategy 的离线比较 | 依赖 G1–G4（Relevance 另需模型） |
| G6 | guardrail / capacity 职责与优先级 | 依赖 G2 + 产品决策 |
| G7 | model → context_window 可信映射 | provider 文档 / 能力元数据 |
| G8 | token-level enforcement 进入生产的明确需求 | 依赖 G6 + G7 |
| G9 | reserve strategy（含 RAG / Tool 动态容量） | 依赖 G7 |
| G10 | 已冻结（无新增要求） | —— |

---

## 7. Transition to READY

只有至少满足：

```text
G1 = READY
G2 = READY
G3 = READY
G4 = READY
G5 = READY
G6 = READY
G7 = READY
G9 = READY
G10 = READY
```

并且（条件性）：

```text
G8 = READY（仅当 Strategy 需要 token-level enforcement）
```

才允许进入：

```text
ContextSelector Implementation
```

统计与成本不是唯一依据：

```text
* 长度分布 / 成本只是 evidence ≠ policy；
* 例：即使未来观察到 P95 = 18 turns，也不能直接推出 max_turns = 18；
* 例：即使未来观察到 P95 chars = 12000，也不能直接推出 max_chars = 12000；
* 未来决策还必须考虑：early constraint retention / middle decision retention /
  recent context / old topic / long turn safety / determinism / latency /
  implementation complexity / security；
* 本阶段不打分、不排名。
```

---

## 8. Production Boundary

继续确认（AST / 模块存在性审计锁定）：

```text
ChatApplicationService
    ↓
ConversationContextBuilder（仍为 build_context(turns)，单一参数）
    ↓
AIOrchestrator
```

没有：

```text
ContextSelector / Budget / Tokenizer / Truncation / Summarizer / Memory
```

说明（避免误判既有模块）：

```text
* services/relevant_table_selector.py（Text-to-SQL 相关表选择器）为既有模块，与 Conversation History Selection 无关；
* services/in_memory_*_collector.py 为观测 collector（既有）；
* services/text_to_sql_prompt_v2_promotion_gate_*（既有评估 gate）。
以上均不属于本决策域。
```

---

## 9. Security

Decision Gate 只描述"条件与证据"，不引入任何敏感面：

```text
* 不新增 prompt / tool definitions / SQL / RAG chunks / API key 处理；
* 不新增 DB 访问 / 网络访问 / LLM 调用；
* 不修改 Conversation Turn 内容语义（仍只允许 USER content 与 ASSISTANT final content）。
```

---

## 10. Failure Semantics

延续既有冻结（G10 = READY）：

```text
Invalid policy ≠ runtime selection failure

Invalid policy（配置阶段）→ validation error
Runtime selection failure（运行阶段）→ business failure（原样上抛）

No fallback full history
No ignore budget
No silent degradation
```

---

## 11. Deferred

```text
Selection Strategy = BLOCKED（Gate 未满足，禁止冻结策略）
ContextSelector = Deferred
Truncation = Deferred
Tokenizer = Deferred
Budget Unit = Deferred
Summarizer / Memory = Deferred
```

本 Stage 明确不做：

```text
* 不实现任何 ContextSelector / Budget / Tokenizer；
* 不引入 DB schema / migration / API 变更；
* 不调用真实 LLM；
* 不因 Gate BLOCKED 而临时选择 Recent N / Recent + First / Hybrid。
```

---

## 附：证据锚点

```text
docs/evaluation/Phase 4.1 Step 17 — Context Selection Contract.md
docs/evaluation/Phase 4.1 Step 18 — Selection Policy & Budget Ownership Audit.md
docs/evaluation/Phase 4.1 Step 19 — History Budget Unit Decision Audit.md
docs/evaluation/Phase 4.1 Step 20 — History Selection Strategy Decision Audit.md
docs/evaluation/Phase 4.1 Step 21 — WMS Conversation Selection Evidence.md
```

```text
tests/test_conversation_context_selection_decision_gate.py
    → 10 条 Gate 状态 / 迁移条件（含 G8 条件性）/ 生产边界 / 确定性 / 文档完整性
tests/test_conversation_context_selection_decision_gate_architecture.py
    → 模块存在性（Conversation 域 + 全局）/ 链路六文件 AST / 自身审计
```
