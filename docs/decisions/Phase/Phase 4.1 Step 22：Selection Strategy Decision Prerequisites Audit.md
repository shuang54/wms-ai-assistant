你现在开始执行：

# Phase 4.1 Step 22：Selection Strategy Decision Prerequisites Audit

## 一、阶段目标

基于 Phase 4.1 Step 17～21：

```text
Context Selection Contract
        ↓
Policy / Budget Ownership
        ↓
Budget Unit Audit
        ↓
Strategy Audit
        ↓
WMS Conversation Evidence
```

当前结论：

```text
Selection Strategy = Deferred
Budget Unit = Deferred
Tokenizer = Deferred
ContextSelector = Deferred
Truncation = Deferred
```

本阶段不再继续比较 Strategy A/B/C/D。

唯一目标：

> **明确：未来什么证据和什么基础设施条件满足后，才允许正式冻结 Selection Strategy。**

最终形成：

```text
Selection Strategy Decision Gate
```

而不是：

```text
Selection Strategy
```

---

# 二、严格禁止

禁止修改：

```text
backend/app/services/
backend/app/db/
backend/app/dto/
backend/app/api/
```

禁止实现：

```text
ContextSelector
SelectionPolicy production DTO
Budget production DTO
Truncation
Tokenizer
Summarizer
Memory
```

禁止：

```text
DeepSeek
SiliconFlow
PostgreSQL
Network
真实 LLM
```

禁止修改：

```text
Conversation ORM
ConversationRepository
ConversationService
ChatApplicationService
ConversationContextBuilder
AIOrchestrator
```

---

# 三、必须先阅读

先阅读：

```text
docs/evaluation/Phase 4.1 Step 17 — Context Selection Contract.md
docs/evaluation/Phase 4.1 Step 18 — Selection Policy & Budget Ownership Audit.md
docs/evaluation/Phase 4.1 Step 19 — History Budget Unit Decision Audit.md
docs/evaluation/Phase 4.1 Step 20 — History Selection Strategy Decision Audit.md
docs/evaluation/Phase 4.1 Step 21 — WMS Conversation Selection Evidence.md

tests/test_conversation_context_selection_contract.py
tests/test_conversation_context_policy_audit.py
tests/test_conversation_context_budget_unit_audit.py
tests/test_conversation_context_selection_strategy_audit.py
tests/test_conversation_context_selection_evidence.py
```

必须基于真实已有证据。

---

# 四、Decision Gate

建立一个明确的：

```text
SelectionStrategyDecisionGate
```

只作为：

```text
test-local / documentation concept
```

不要创建 production DTO。

Gate 至少检查以下条件：

```text
G1：真实/脱敏 WMS 多轮对话数据
G2：对话长度分布
G3：关键约束标注
G4：历史丢失影响标注
G5：Candidate Strategy 离线比较
G6：Budget Unit 决策
G7：Model Context Window 来源
G8：Tokenizer 可用性
G9：Output / RAG / Tool Reserve 定义
G10：Failure Semantics 已冻结
```

---

# 五、G1：Conversation Dataset

当前已有：

```text
12 synthetic WMS-realistic cases
```

本阶段明确：

```text
synthetic dataset ≠ production evidence
```

正式冻结 Strategy 前必须至少具备：

```text
真实脱敏 WMS 多轮会话样本
```

如果暂时不能取得真实数据：

```text
G1 = BLOCKED
```

不要用 synthetic dataset 冒充生产证据。

---

# 六、G2：Conversation Length Distribution

未来正式决策前需要知道：

```text
conversation turn count distribution
conversation character length distribution
```

至少能够观察：

```text
P50
P90
P95
P99
Max
```

注意：

这些只是：

```text
conversation shape evidence
```

不是 token estimate。

禁止：

```text
chars / 4
chars / 2
```

禁止任何字符→token推算。

如果只有 synthetic dataset：

```text
G2 = INSUFFICIENT
```

---

# 七、G3：关键约束标注

未来真实 Dataset 至少需要能够标注：

```text
early_constraint
middle_decision
recent_context
old_topic
standalone
```

但：

**不要把这些变成 production DTO。**

只允许作为：

```text
evaluation annotation
```

例如：

```yaml
annotations:
  - type: early_constraint
    turn_index: 0
```

如果真实数据还没有人工标注：

```text
G3 = BLOCKED
```

---

# 八、G4：历史丢失影响

这是最重要的一项。

不能只证明：

```text
turn 被删除
```

还需要知道：

> **删除这个 Turn 后，Assistant 的业务答案是否受到影响。**

因此未来需要：

```text
full_history_answer
selected_history_answer
```

之间的人工/离线质量比较。

但：

本阶段：

**不要调用真实 LLM。**

这里只定义：

```text
required evidence
```

不实现评测平台。

---

# 九、G5：Candidate Strategy Comparison

未来正式决策前至少比较：

```text
Recent N
Recent N + First
Hybrid
Relevance-based
```

但是：

```text
Relevance-based
```

必须在 Relevance Model 真正存在后才能进入。

当前：

```text
G5 = BLOCKED
```

因为目前只有结构性模拟，没有真实质量标注。

---

# 十、G6：Budget Unit Decision

必须先回答：

```text
max_turns
max_chars
max_tokens
Hybrid
```

到底哪一个承担：

```text
hard guardrail
capacity constraint
```

允许：

```text
Guardrail + Capacity Constraint
```

双层存在。

但必须明确：

```text
谁负责什么
谁优先
超限如何处理
```

当前：

```text
G6 = BLOCKED
```

因为 Step 19 已明确 Budget Unit Deferred。

---

# 十一、G7：Model Context Window Source

必须找到可信的：

```text
model
context_window
```

来源。

允许：

```text
provider documentation
model configuration
trusted static capability metadata
```

禁止：

```text
猜测
硬编码未知值
通过 API response 猜测
```

如果模型是动态切换：

必须有：

```text
model → capability
```

映射。

当前如果没有：

```text
G7 = BLOCKED
```

---

# 十二、G8：Tokenizer

只有当：

```text
max_tokens
```

真的进入生产 Capacity Constraint 时，才允许引入 Tokenizer。

因此：

```text
Tokenizer prerequisite
```

不是：

```text
Phase 4.1 必须立即安装 tokenizer
```

而是：

```text
Strategy decision requires token-level enforcement
        ↓
Tokenizer becomes required dependency
```

当前：

```text
G8 = BLOCKED
```

但：

```text
Tokenizer = Deferred
```

保持不变。

---

# 十三、G9：Context Reserve

未来必须明确：

```text
Model Context Window
    -
System Prompt
    -
Project Context
    -
Tool Definitions
    -
Current User
    -
RAG Context
    -
Tool Results
    -
Output Reserve
    ↓
History Budget
```

不能只给：

```text
Conversation History
```

一个孤立预算。

特别注意：

```text
RAG Context
Tool Results
```

都是动态容量。

因此未来必须定义：

```text
reserve strategy
```

当前：

```text
G9 = BLOCKED
```

---

# 十四、G10：Failure Semantics

这一项目前已经基本满足。

必须保持：

```text
invalid policy
    ≠
runtime selection failure
```

以及：

```text
No fallback full history
No ignore budget
No silent degradation
```

因此：

```text
G10 = READY
```

---

# 十五、Gate 状态

建立一个简单矩阵：

| Gate | Condition             | Current      |
| ---- | --------------------- | ------------ |
| G1   | Real WMS dataset      | BLOCKED      |
| G2   | Length distribution   | INSUFFICIENT |
| G3   | Constraint annotation | BLOCKED      |
| G4   | Loss impact evidence  | BLOCKED      |
| G5   | Strategy comparison   | BLOCKED      |
| G6   | Budget unit           | BLOCKED      |
| G7   | Context window source | BLOCKED      |
| G8   | Tokenizer             | BLOCKED      |
| G9   | Reserve allocation    | BLOCKED      |
| G10  | Failure semantics     | READY        |

最终：

```text
Selection Strategy Decision = BLOCKED
```

这是**正确结果**。

不要因为 Gate BLOCKED 就实现一个临时策略。

---

# 十六、明确“允许冻结”的条件

最终文档必须写清楚：

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

并且：

```text
G8
```

如果 Strategy 需要 token-level enforcement，则必须：

```text
READY
```

才允许进入：

```text
ContextSelector Implementation
```

---

# 十七、不要把 G2 的统计结果当 Strategy

例如未来发现：

```text
P95 = 18 turns
```

不能直接推出：

```text
max_turns = 18
```

同样：

```text
P95 chars = 12000
```

不能推出：

```text
max_chars = 12000
```

统计数据只是：

```text
evidence
```

不是：

```text
policy
```

---

# 十八、不要把成本当唯一依据

未来 Strategy 决策不能只看：

```text
LLM cost
```

还需要考虑：

```text
early constraint retention
middle decision retention
recent context
old topic
long turn safety
determinism
latency
implementation complexity
security
```

但：

**本阶段不要打分。**

---

# 十九、Production Boundary

必须继续确认：

```text
ChatApplicationService
    ↓
ConversationContextBuilder
    ↓
AIOrchestrator
```

没有：

```text
ContextSelector
Budget
Tokenizer
```

---

# 二十、Architecture Audit

新增：

```text
tests/test_conversation_context_selection_decision_gate_architecture.py
```

使用 AST 检查：

```text
backend/app/services/
backend/app/db/
backend/app/dto/
```

不得出现新的：

```text
ContextSelector
SelectionPolicy
Budget
Tokenizer
Truncation
Summarizer
Memory
```

同时检查：

```text
Conversation ORM
ConversationRepository
ConversationService
ChatApplicationService
Builder
```

保持现状。

---

# 二十一、Decision Gate Test

新增：

```text
tests/test_conversation_context_selection_decision_gate.py
```

至少测试：

1. current gate = BLOCKED
2. G1 blocked
3. G2 insufficient
4. G3 blocked
5. G4 blocked
6. G5 blocked
7. G6 blocked
8. G7 blocked
9. G8 blocked
10. G9 blocked
11. G10 ready
12. cannot transition to READY when blocked
13. no production selector
14. no tokenizer
15. no budget DTO
16. no persistence changes
17. deterministic gate state
18. no DB/network/LLM

---

# 二十二、Documentation

新增：

```text
docs/evaluation/Phase 4.1 Step 22 — Selection Strategy Decision Gate.md
```

必须包含：

```text
1. Purpose
2. Current State
3. Gate Matrix
4. G1~G10 Definitions
5. Current Status
6. Required Evidence
7. Transition to READY
8. Production Boundary
9. Security
10. Failure Semantics
11. Deferred
```

最终明确：

```text
Selection Strategy = BLOCKED
```

不是：

```text
Recent N
```

不是：

```text
Recent + First
```

不是：

```text
Hybrid
```

---

# 二十三、测试命令

只运行：

```powershell
python -m pytest -q tests/test_conversation_context_selection_decision_gate.py
```

然后：

```powershell
python -m pytest -q tests/test_conversation_context_selection_decision_gate_architecture.py
```

然后：

```powershell
python -m compileall -q backend tests
```

不要：

```text
RUN_DB_TESTS
全量 pytest
DeepSeek
SiliconFlow
```

---

# 二十四、Git Diff

要求：

```text
Production code = 0
DB schema = 0
DB writes = 0
Network = 0
LLM = 0
Tokenizer = 0
```

只允许：

```text
tests/
docs/
```

---

# 二十五、最终报告

严格：

```text
Phase 4.1 Step 22 完成报告

1. Decision Gate
2. G1~G10
3. Current Gate State
4. Required Evidence
5. Transition Conditions
6. Production Boundary
7. Architecture Audit
8. Decision Gate Tests
9. compileall
10. Git Diff
11. Production Code Changes
12. DB / Network / LLM
13. Final Decision
14. Current Limitations

Phase 4.1 Step 22 READY
Phase 4.1 Step 22 STOP
```

最终必须：

```text
Selection Strategy = BLOCKED
```

这是本阶段预期的正确结果。

硬停止，不进入 Step 23。
