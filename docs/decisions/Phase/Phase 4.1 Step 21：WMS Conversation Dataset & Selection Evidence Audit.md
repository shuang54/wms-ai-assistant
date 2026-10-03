你现在开始执行：

# Phase 4.1 Step 21：WMS Conversation Dataset & Selection Evidence Audit

## 一、阶段目标

基于 Phase 4.1 Step 19 / Step 20 的结论：

```text
Budget Unit = Deferred
Selection Strategy = Deferred
Tokenizer = Deferred
ContextSelector = Deferred
Truncation = Deferred
```

当前最大缺口已经不是架构讨论，而是：

> **缺少真实/接近真实的 WMS 多轮 Conversation 数据。**

因此本阶段只建立：

```text
WMS Conversation Dataset
        ↓
Offline Selection Simulation
        ↓
History Preservation Evidence
        ↓
Strategy Evidence
        ↓
STOP
```

本阶段不是生产实现。

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
真实 Embedding
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

# 三、Dataset 文件

新增：

```text
tests/fixtures/conversation_context/wms_multiturn_conversations.yaml
```

如果项目现有 fixture 目录更适合：

```text
tests/fixtures/
```

则遵循现有结构。

不要创建 production data。

这是：

```text
test/evaluation fixture
```

不是：

```text
database seed
```

---

# 四、Dataset Schema

保持极简。

每个 Conversation：

```yaml
- id: inventory_followup_001
  project_id: vietnam-wms
  turns:
    - role: user
      content: "查询越南一厂当前库存"
    - role: assistant
      content: "越南一厂当前库存约为1000件。"
    - role: user
      content: "其中A01仓库有多少？"
    - role: assistant
      content: "A01仓库有320件。"
    - role: user
      content: "那B01呢？"
```

只允许：

```text
id
project_id
turns
role
content
```

不要加入：

```text
token_count
embedding
relevance_score
expected_sql
model
```

---

# 五、第一版 Dataset

至少建立：

```text
12 个 Conversation Cases
```

不要机械制造 12 个相似案例。

必须覆盖：

### Group A：连续追问

至少 2 个：

```text
库存 → 仓库 → 具体物料
```

---

### Group B：早期业务约束

至少 2 个：

例如：

```text
越南一厂
国产原材料
某仓库
某业务范围
```

后续问题必须依赖早期约束。

---

### Group C：中间决策

至少 2 个：

例如：

```text
先查询库存
→ 再限定仓库
→ 再限定物料
→ 再继续追问
```

用于验证：

```text
Recent N + First
```

是否会丢掉中间约束。

---

### Group D：长 Turn

至少 2 个。

构造：

```text
一个较长业务问题
+
多个短问题
```

目的：

再次证明：

```text
turn count ≠ context capacity
```

不要人工估算 token。

可以记录字符长度，但：

```text
char ≠ token
```

---

### Group E：回到旧主题

至少 2 个：

```text
主题 A
→ 多轮讨论 B
→ 多轮讨论 C
→ 回到 A
```

验证：

```text
Recent N
```

是否会丢失早期主题。

---

### Group F：无历史依赖

至少 2 个：

```text
当前问题本身已经完整
```

用于验证：

```text
历史并不是永远都必须保留。
```

---

# 六、不要使用真实生产数据

Dataset 必须使用：

```text
synthetic but WMS-realistic
```

例如：

```text
A01
B01
越南一厂
原材料
采购入库
销售出库
库存
PMC
```

但：

**不要复制真实客户、供应商、订单号、真实库存数量或生产数据。**

---

# 七、建立 Selection Simulation

新增：

```text
tests/test_conversation_context_selection_evidence.py
```

这里可以实现：

```text
offline-only
test-local
```

的简单模拟函数。

注意：

这不是 production ContextSelector。

不要放：

```text
backend/app/
```

只放：

```text
tests/
```

---

# 八、模拟 Strategy A

实现测试辅助：

```text
recent_n(turns, n)
```

规则：

```text
保留最后 N 个历史 Turn
```

注意：

当前用户 Turn 不应该进入 candidate history。

---

# 九、模拟 Strategy B

实现：

```text
recent_n_plus_first(turns, n)
```

规则：

```text
first historical turn
+
last N historical turns
```

必须处理：

```text
history <= N
```

不能重复 First Turn。

---

# 十、Strategy C

不要实现真正的 relevance/token selection。

只建立：

```text
NotImplemented / Deferred
```

证据：

```text
Tokenizer unavailable
Model context window unavailable
Relevance model unavailable
Budget unit unavailable
```

不能：

```text
chars / 4
```

不能：

```text
chars / 2
```

不能：

```text
fake token count
```

---

# 十一、Strategy D

只模拟：

```text
Recent N
+
First
```

不要真正执行 Token Budget。

只验证：

```text
hybrid structure
```

并明确：

```text
future token budget required for overflow resolution
```

---

# 十二、建立 Preservation Metrics

不要做模型评分。

只统计**事实型指标**。

每个 Conversation：

### 1. Early Constraint Preservation

```text
早期关键 Turn 是否仍被保留
```

结果：

```text
true / false
```

---

### 2. Middle Decision Preservation

```text
中间关键 Turn 是否仍被保留
```

结果：

```text
true / false
```

---

### 3. Recent Context Preservation

最后相关 Turn 是否保留：

```text
true / false
```

---

### 4. Old Topic Preservation

当最后一个问题重新引用早期主题：

```text
早期主题 Turn 是否仍存在
```

结果：

```text
true / false
```

---

# 十三、不要做“正确率”

禁止：

```text
Strategy A = 82%
Strategy B = 91%
Strategy C = 95%
```

不要：

```text
best strategy
winner
score
ranking
```

因为：

> 当前 Dataset 不是模型质量 benchmark。

本阶段只记录：

```text
preserved
lost
risk
```

---

# 十四、建立 Evidence Matrix

输出：

```text
Conversation
Strategy
Early Constraint
Middle Decision
Recent Context
Old Topic
Long Turn Risk
```

例如：

```text
inventory_001
Recent N
preserved
lost
preserved
lost
risk

inventory_001
Recent + First
preserved
lost
preserved
preserved
risk
```

禁止总分。

---

# 十五、Length Evidence

复用 Step 16 的已有证据：

```text
1 → 26
10 → 294
50 → 1474
100 → 2949
500 → 14749
1000 → 29499
```

本阶段不要重新 benchmark。

Dataset 只记录：

```text
turn_count
character_count
```

禁止：

```text
estimated_tokens
```

---

# 十六、测试 Invariants

继续保持：

### Subsequence

```text
selected turns ⊆ history
```

### Ordering

```text
selected order == original order
```

### No duplication

First Turn + Recent N：

```text
不能重复同一个 turn
```

### Determinism

同样输入：

```text
same result
```

### No mutation

原始 Dataset 不发生修改。

---

# 十七、Dataset Loader Contract

增加简单 loader：

```text
load_wms_multiturn_conversations()
```

要求：

```text
missing id → error
missing project_id → error
empty turns → error
invalid role → error
empty content → error
duplicate conversation id → error
```

允许：

```text
user
assistant
```

不允许：

```text
system
tool
```

因为本阶段只研究 Conversation History。

---

# 十八、Project Isolation

验证：

```text
project_id
```

只作为 Dataset metadata。

禁止：

```text
Strategy
```

修改：

```text
Conversation.project_id
```

也不能：

```text
conversation A
```

读取：

```text
conversation B
```

的 turns。

---

# 十九、Architecture Audit

新增：

```text
tests/test_conversation_context_selection_evidence_architecture.py
```

AST 检查：

```text
tests/test_conversation_context_selection_evidence.py
```

不得 import：

```text
sqlalchemy
psycopg
redis
celery
kafka
backend.app.db
```

不得 import：

```text
backend.app.services.conversation_context_selector
backend.app.services.context_selector
```

因为这些本阶段不应该存在。

---

# 二十、Documentation

新增：

```text
docs/evaluation/Phase 4.1 Step 21 — WMS Conversation Selection Evidence.md
```

必须包含：

## 1. Dataset Purpose

说明：

```text
synthetic WMS-realistic
not production data
```

---

## 2. Dataset Categories

```text
Continuous Follow-up
Early Constraint
Middle Decision
Long Turn
Return to Old Topic
No History Dependency
```

---

## 3. Selection Simulation

```text
Recent N
Recent + First
Hybrid
Relevance/Token = Deferred
```

---

## 4. Preservation Evidence

只记录：

```text
preserved
lost
risk
```

禁止：

```text
score
ranking
winner
```

---

## 5. Key Findings

回答：

```text
哪些场景 Recent N 容易失败？

哪些场景 Recent + First 容易失败？

哪些场景必须依赖未来 Relevance？

哪些问题与 Budget Unit 无关？

哪些问题必须等待 Tokenizer？
```

---

## 6. Final Decision

允许：

```text
Selection Strategy = Deferred
```

如果数据仍不足。

或者，如果证据足够：

只能选择：

```text
Recent N
```

或：

```text
Recent N + First
```

或：

```text
Hybrid
```

但必须有明确证据。

**禁止因为“实现简单”就选择 Recent N。**

---

# 二十一、禁止事项

本阶段禁止：

```text
生产代码修改
ContextSelector
SelectionPolicy production DTO
Budget production DTO
Tokenizer
Truncation
Summarizer
Memory
```

禁止：

```text
DeepSeek
SiliconFlow
DB
Network
```

禁止：

```text
token estimation
chars / 4
chars / 2
```

禁止：

```text
score
ranking
winner
benchmark
```

---

# 二十二、测试命令

只运行：

```powershell
python -m pytest -q tests/test_conversation_context_selection_evidence.py
```

然后：

```powershell
python -m pytest -q tests/test_conversation_context_selection_evidence_architecture.py
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
Production code = 0
DB schema = 0
DB writes = 0
Network = 0
LLM = 0
Tokenizer = 0
```

---

# 二十四、最终报告

严格：

```text
Phase 4.1 Step 21 完成报告

1. Dataset
2. Dataset Categories
3. Strategy Simulation
4. Preservation Metrics
5. Evidence Matrix
6. WMS Findings
7. Selection Invariants
8. Tokenizer
9. Architecture Audit
10. Tests
11. compileall
12. Git Diff
13. Production Code Changes
14. DB / Network / LLM
15. Final Selection Strategy Decision
16. Current Limitations

Phase 4.1 Step 21 READY
Phase 4.1 Step 21 STOP
```

---

# 二十五、硬停止

完成后立即停止。

不要进入：

```text
Step 22 ContextSelector Implementation
Step 23 Truncation
Step 24 Tokenizer
Step 25 Summary
Memory
Agent
MCP
```

只完成：

```text
WMS Conversation Selection Evidence Audit
```

等待下一步指令。
