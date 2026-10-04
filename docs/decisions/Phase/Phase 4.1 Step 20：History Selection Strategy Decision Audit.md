你现在开始执行：

# Phase 4.1 Step 20：History Selection Strategy Decision Audit

## 一、阶段目标

基于 Phase 4.1 Step 17～19 已完成的：

```text
Conversation History
        ↓
Context Selection Contract
        ↓
SelectionPolicy
        ↓
Budget
```

本阶段只解决：

> **Conversation History 应该采用什么 Selection Strategy。**

本阶段仍然是：

```text
Design / Audit Only
```

**不要实现 ContextSelector。**

**不要实现 Truncation。**

**不要引入 Tokenizer。**

**不要修改生产代码。**

最终需要形成一个明确的：

```text
History Selection Strategy Decision
```

但如果当前证据不足以冻结最终策略，也必须明确：

```text
Strategy = Deferred
```

不能为了结束阶段而强行选择。

---

# 二、严格范围

## 允许修改

只允许：

```text
tests/
docs/evaluation/
```

如果确实需要补充纯离线测试辅助代码，只允许放在：

```text
tests/
```

---

## 禁止修改

禁止：

```text
backend/app/services/chat_application_service.py
backend/app/services/conversation_service.py
backend/app/services/conversation_context_builder.py
backend/app/services/ai_orchestrator_service.py
backend/app/db/
backend/app/dto/
```

禁止新增：

```text
ContextSelector
SelectionPolicy production DTO
Budget production DTO
Truncation
Summarizer
Tokenizer
Memory
```

禁止：

```text
DeepSeek
SiliconFlow
PostgreSQL
网络请求
数据库写入
```

---

# 三、必须先阅读

先阅读现有：

```text
tests/test_conversation_context_selection_contract.py
tests/test_conversation_context_policy_audit.py
tests/test_conversation_context_budget_unit_audit.py
tests/test_conversation_context_budget_architecture.py

docs/evaluation/Phase 4.1 Step 17 — Context Selection Contract.md
docs/evaluation/Phase 4.1 Step 18 — Selection Policy & Budget Ownership Audit.md
docs/evaluation/Phase 4.1 Step 19 — History Budget Unit Decision Audit.md

backend/app/services/conversation_context_builder.py
backend/app/services/chat_application_service.py
```

以及必要时：

```text
backend/app/db/models/conversation_turn.py
```

必须以当前真实代码为准。

---

# 四、Selection Strategy 候选

本阶段只分析以下四种：

```text
Strategy A：Recent N
Strategy B：Recent N + First Turn
Strategy C：Relevance / Token Budget Selection
Strategy D：Hybrid
```

不要新增第五种复杂策略。

---

# 五、Strategy A：Recent N

定义：

```text
保留最近 N 个历史 Turn
```

例如：

```text
T1 T2 T3 T4 T5 T6 T7 T8

N = 4

→ T5 T6 T7 T8
```

必须分析：

### 优点

```text
简单
确定性强
无需 tokenizer
容易测试
性能稳定
```

### 缺点

```text
早期关键约束可能丢失
早期业务背景可能丢失
N 与真实 token size 无直接关系
长文本 turn 会造成容量不可控
```

特别验证：

```text
N = 10
```

不能被描述成：

```text
context window = 10 turns
```

---

# 六、Strategy B：Recent N + First Turn

定义：

```text
保留：

First Turn
+
Recent N Turns
```

例如：

```text
T1 T2 T3 T4 T5 T6 T7 T8

N = 3

→ T1 T6 T7 T8
```

必须分析：

### 优点

```text
保留最初业务目标
保留最近上下文
实现仍然简单
无需 tokenizer
```

### 缺点

```text
中间关键决策可能丢失
First Turn 可能非常长
仍然没有 token-level guarantee
```

特别分析：

```text
第一轮用户问题是否一定具有长期价值？
```

不能默认认为：

```text
First Turn = permanent memory
```

---

# 七、Strategy C：Relevance / Token Budget Selection

定义：

```text
根据当前问题相关性
+
Token Budget
选择历史 Turn
```

概念：

```text
History
   ↓
Candidate Turns
   ↓
Relevance
   ↓
Budget
   ↓
Selected Turns
```

必须明确：

> 本项目当前不具备完整实现条件。

当前已确认：

```text
Tokenizer = Deferred
Model context window = 未确定
Budget unit = Deferred
Relevance model = 未定义
Embedding / reranker 不是 Conversation History Selector
```

因此：

**本阶段不能实现真正的 relevance/token-budget selector。**

只能分析其未来能力与复杂度。

---

# 八、Strategy D：Hybrid

只分析一种明确 Hybrid：

```text
Recent N
+
First Turn
+
未来 Token Budget
```

例如：

```text
先保证：

First Turn
+
Recent N

如果超过未来 Token Budget：

再进行裁剪
```

必须分析：

### 优点

```text
长期目标
+
近期上下文
+
未来可接入 token budget
```

### 缺点

```text
策略复杂
优先级复杂
budget 未定义时无法真正落地
需要明确超限时谁被裁剪
```

禁止实现。

---

# 九、核心问题：Selection Strategy ≠ Budget Unit

本阶段必须明确锁定：

```text
Selection Strategy
```

和：

```text
Budget Unit
```

是两个不同决策。

例如：

```text
Recent N
```

是 Strategy。

而：

```text
max_turns
max_chars
max_tokens
```

是 Budget Unit。

因此：

```text
Recent N + max_chars
```

是合法组合。

以及：

```text
Recent N + First + future max_tokens
```

也是合法设计。

不要因为 Step 19 没有选择 Budget Unit，就认为 Selection Strategy 也必须 Deferred。

---

# 十、必须分析实际 WMS 对话场景

不要只做抽象理论。

使用至少 5 个离线构造的 WMS 对话场景。

例如：

### Scenario 1：连续追问

```text
U1：查询当前库存
A1：当前库存 100 件
U2：其中 A01 仓库多少？
A2：A01 有 60 件
U3：那 B01 呢？
```

分析哪些历史必须保留。

---

### Scenario 2：早期业务约束

```text
U1：只查询越南一厂库存
A1：...
U2：查询库存
```

验证：

如果只保留 Recent N，是否可能丢失：

```text
越南一厂
```

这个约束。

---

### Scenario 3：中间决策

```text
U1：查询库存
A1：...
U2：按仓库统计
A2：...
U3：只看 A01/A02
A3：...
U4：继续看差异
```

分析：

```text
First + Recent
```

是否会丢掉：

```text
A01/A02
```

这样的中间约束。

---

### Scenario 4：长文本 Turn

构造：

```text
一个超长 USER Turn
+
多个短 Turn
```

分析：

```text
Recent N
```

虽然 Turn 数量少：

```text
token / char 仍然可能非常大
```

因此再次验证：

```text
max_turns ≠ context capacity
```

---

### Scenario 5：重新回到早期主题

例如：

```text
U1：我们讨论采购入库
...
U20：回到刚才采购入库的问题
```

分析：

```text
Recent N
```

是否能够保留足够信息。

---

# 十一、建立 Selection Quality Matrix

创建一个离线矩阵：

```text
Scenario
        Recent N
        Recent + First
        Relevance + Token
        Hybrid
```

每个场景分析：

```text
Early Constraint Preservation
Recent Context Preservation
Middle Decision Preservation
Long Turn Safety
Determinism
Implementation Complexity
Tokenizer Dependency
```

不要打分。

不要做：

```text
8/10
9/10
BEST
WINNER
```

这是架构决策，不是 benchmark 排名。

只写：

```text
Good fit
Weak fit
Requires future capability
Risk
```

---

# 十二、必须分析 Selection Invariants

无论未来选择哪种 Strategy，都必须保持：

## 1. Subsequence

```text
selected ⊆ previous turns
```

不能创造不存在的 Turn。

---

## 2. Ordering

Selected turns 必须保持：

```text
repository order
```

不能重新排序。

---

## 3. Current User Exclusion

当前 User Turn 已经在：

```text
ChatApplicationService
```

中排除。

Selector 不应该重新决定：

```text
是否包含当前 User
```

---

## 4. Determinism

同样：

```text
history
+
policy
+
budget
+
capability
```

必须：

```text
same selection
```

---

## 5. No Semantic Mutation

Selector 不应该修改：

```text
role
content
turn_id
assistant_request_id
created_at
```

---

# 十三、Selection Failure Semantics

继续沿用 Step 18：

```text
invalid policy
    ≠
runtime selection failure
```

### Invalid policy

例如：

```text
recent_n = -1
```

属于：

```text
configuration / validation error
```

---

### Runtime selection failure

例如未来：

```text
Selector execution failed
```

必须：

```text
business failure
```

不能：

```text
fallback full history
```

不能：

```text
ignore budget
```

不能：

```text
silent degradation
```

---

# 十四、不要选择最终实现策略，除非证据足够

本阶段最终允许：

```text
Selection Strategy = Deferred
```

但必须给出：

```text
why
what evidence is missing
what next experiment is needed
```

例如：

```text
需要真实多轮 WMS 对话集
需要评估早期约束保留率
需要确定未来 Tokenizer / Model Context Window
```

不要为了“完成 Phase”而硬选：

```text
Recent N
```

---

# 十五、Tokenizer 保持 Deferred

本阶段不得引入：

```text
tiktoken
transformers
tokenizers
sentencepiece
```

不要：

```text
chars / 4
chars / 2
```

估算 token。

不要因为需要比较 Strategy C 就安装 tokenizer。

Strategy C 只做架构分析。

---

# 十六、Persistence Boundary

确认：

```text
Conversation ORM
ConversationRepository
ConversationService
```

仍然不拥有：

```text
selection strategy
budget
tokenizer
context window
```

不能修改：

```text
conversation
conversation_turn
```

数据库结构。

---

# 十七、Production Boundary

确认当前：

```text
ChatApplicationService
    ↓
ConversationContextBuilder
    ↓
AIOrchestrator
```

仍然保持。

未来如果实现：

```text
ContextSelector
```

目标应该是：

```text
ChatApplicationService
    ↓
Conversation History
    ↓
ContextSelector
    ↓
ConversationContextBuilder
    ↓
AIOrchestrator
```

但本阶段：

```text
ContextSelector = NOT IMPLEMENTED
```

---

# 十八、测试文件

新增：

```text
tests/test_conversation_context_selection_strategy_audit.py
```

建议覆盖：

1. Recent N definition
2. Recent N + First definition
3. Strategy C prerequisites missing
4. Hybrid definition
5. subsequence invariant
6. ordering invariant
7. current user exclusion
8. determinism
9. long-turn scenario
10. early-constraint scenario
11. middle-decision scenario
12. return-to-old-topic scenario
13. no token estimation
14. no tokenizer dependency
15. no production selector
16. no production budget DTO
17. persistence boundary
18. builder boundary
19. failure semantics
20. matrix completeness

全部离线。

---

# 十九、Architecture Audit

新增：

```text
tests/test_conversation_context_selection_strategy_architecture.py
```

使用 AST。

确认：

```text
conversation.py
conversation_turn.py
conversation_repository.py
conversation_service.py
conversation_context_builder.py
chat_application_service.py
```

没有新增：

```text
ContextSelector
SelectionPolicy production implementation
Budget production implementation
Tokenizer
```

也不能出现：

```text
max_turns
max_chars
max_tokens
```

进入 production DTO / ORM。

注意：

docstring / comments 中的设计文字不算 executable dependency。

---

# 二十、Documentation

新增：

```text
docs/evaluation/Phase 4.1 Step 20 — History Selection Strategy Decision Audit.md
```

必须包含：

## 1. Current State

```text
Full History
    ↓
ContextBuilder
    ↓
LLM
```

以及：

```text
Current History Budget = Unlimited
```

---

## 2. Candidate Strategies

```text
Recent N
Recent N + First
Relevance + Token Budget
Hybrid
```

---

## 3. Selection Quality Matrix

使用：

```text
Good fit
Weak fit
Requires future capability
Risk
```

禁止评分/排名。

---

## 4. WMS Scenarios

记录至少 5 个场景。

---

## 5. Invariants

```text
subsequence
ordering
current-user exclusion
determinism
no mutation
```

---

## 6. Failure Semantics

明确：

```text
Invalid policy ≠ runtime selection failure
```

以及：

```text
No fallback full history
No ignore budget
No silent degradation
```

---

## 7. Budget Separation

明确：

```text
Strategy ≠ Budget Unit
```

例如：

```text
Recent N + future max_tokens
```

是合法组合。

---

## 8. Deferred

必须明确：

```text
ContextSelector = Deferred
Truncation = Deferred
Tokenizer = Deferred
Budget Unit = Deferred
Final Selection Strategy = Deferred
```

如果证据不足。

---

# 二十一、禁止事项

本阶段禁止：

```text
修改生产代码
新增 ContextSelector
新增 SelectionPolicy production DTO
新增 Budget production DTO
新增 max_turns
新增 max_chars
新增 max_tokens
新增 tokenizer
新增 summarizer
新增 memory
修改 Conversation ORM
修改 ConversationRepository
修改 ConversationService
修改 ChatApplicationService
```

禁止：

```text
DeepSeek
SiliconFlow
DB
Network
```

---

# 二十二、测试命令

只运行：

```powershell
python -m pytest -q tests/test_conversation_context_selection_strategy_audit.py
```

然后：

```powershell
python -m pytest -q tests/test_conversation_context_selection_strategy_architecture.py
```

然后：

```powershell
python -m compileall -q backend tests
```

不要：

```text
RUN_DB_TESTS
全量 pytest
```

---

# 二十三、Git Diff

检查：

```powershell
git status --short
git diff --stat
git diff
```

要求：

```text
Production code changes = 0
DB schema changes = 0
DB writes = 0
Network = 0
LLM = 0
Tokenizer = 0
```

---

# 二十四、最终报告

严格：

```text
Phase 4.1 Step 20 完成报告

1. Candidate Strategies
2. Selection Quality Matrix
3. WMS Scenarios
4. Selection Invariants
5. Failure Semantics
6. Strategy vs Budget
7. Tokenizer
8. Persistence Boundary
9. Production Boundary
10. Tests
11. Architecture Audit
12. compileall
13. Git Diff
14. Production Code Changes
15. DB / Network / LLM
16. Final Decision
17. Current Limitations

Phase 4.1 Step 20 READY
Phase 4.1 Step 20 STOP
```

---

# 二十五、硬停止

完成后立即停止。

不要进入：

```text
Step 21 ContextSelector
Step 22 Truncation
Step 23 Tokenizer
Step 24 Summary
Memory
Agent
MCP
```

只完成：

```text
History Selection Strategy Decision Audit
```

等待下一步指令。
