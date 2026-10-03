继续 Phase 4.1。

当前状态：

```text
Step 13 Context Builder = COMPLETE
Step 14 Context 接入 = COMPLETE
Step 15 Multi-turn DB E2E = COMPLETE
Step 16 Context Budget Audit = COMPLETE
Step 17 Context Selection Contract = COMPLETE
Step 18 SelectionPolicy / Budget Ownership Audit = COMPLETE
```

当前已经冻结：

```text
ConversationService
    = Persistence / Ordering / Lifecycle

ChatApplicationService
    = Workflow / Policy Composition

ContextSelector（未来）
    = Selection

ConversationContextBuilder
    = Formatting

SelectionPolicy
    = 怎么选

Budget
    = 最多允许多少

Model Context Window
    = Capability Upper Bound
```

本 Step：

> **只决定 History Budget 的第一版“度量单位与执行口径”。**

**不要实现 ContextSelector。**
**不要实现 truncation。**
**不要引入 tokenizer。**
**不要修改 production backend。**

---

# 一、Step 19 唯一目标

解决：

> 如果未来要限制 Conversation History，到底先用什么作为 Budget？

候选：

```text
A. max_turns
B. max_chars
C. max_tokens
D. 混合策略
```

必须基于当前项目实际情况进行比较。

---

# 二、严格禁止

禁止修改：

```text
backend/app/services/conversation_context_builder.py
backend/app/services/chat_application_service.py
backend/app/services/conversation_service.py
backend/app/db/conversation_repository.py
backend/app/services/ai_orchestrator_service.py
```

禁止：

```text
tiktoken
transformers
tokenizers
sentencepiece
LLM
DeepSeek
SiliconFlow
RAG
Tool
Memory
Summary
actual truncation
ContextSelector implementation
```

禁止：

```text
DB migration
HTTP API
Conversation ORM 字段
Project config production field
```

---

# 三、开始前必须阅读

阅读真实实现：

```text
backend/app/services/conversation_context_builder.py
backend/app/services/chat_application_service.py
backend/app/services/conversation_service.py
backend/app/services/ai_orchestrator_service.py
```

以及：

```text
docs/evaluation/Phase 4.1 Step 16 — Context Budget Audit.md
docs/evaluation/Phase 4.1 Step 17 — Context Selection Contract.md
docs/evaluation/Phase 4.1 Step 18 — Selection Policy & Budget Ownership Audit.md
```

重点搜索当前已有：

```text
DEFAULT_MAX_CONTEXT_CHARS
DEFAULT_MAX_CHARS
DEFAULT_CONTEXT_MAX_CHARS
DEFAULT_MAX_ROWS
max_tokens
context_window
```

这些现有限制**只做事实记录，不修改**。

---

# 四、先明确四种 Budget

## A. max_turns

例如：

```text
最多保留最近 20 个历史 Turn
```

特点：

```text
无需 tokenizer
O(1) 规则
确定性强
实现简单
```

问题：

```text
不同 Turn 长度差异巨大
20 个短 Turn ≠ 20 个长 Turn
无法直接映射 LLM Context Window
```

---

## B. max_chars

例如：

```text
最多 12000 characters
```

特点：

```text
当前 Builder 可以直接观察字符长度
无需 tokenizer
确定性
```

问题：

```text
characters ≠ tokens
中文/英文/代码/tokenization 差异
不能直接代表模型 context capacity
```

特别注意：

> `max_chars` 可以作为工程保护阈值，但不能声称它是 Token Budget。

---

## C. max_tokens

例如：

```text
最多 6000 tokens
```

特点：

```text
最接近真实 LLM Context Window
```

问题：

```text
必须引入 tokenizer
需要模型/tokenizer 对应关系
需要处理 tokenizer 不可用
需要明确 system/project/tool/RAG/output reserve
```

当前：

```text
Tokenizer = Deferred
```

因此本 Step 不实现。

---

## D. Hybrid

例如：

```text
max_turns = 50
max_chars = 12000
```

表示：

```text
同时满足：
turn_count <= 50
character_count <= 12000
```

优点：

```text
可以提供硬保护
```

缺点：

```text
增加 policy complexity
需要定义优先级
容易产生多个预算概念
```

本 Step 不实现。

---

# 五、必须区分“业务规则”与“安全保护”

分析：

```text
max_turns
```

和：

```text
max_chars
```

可以属于：

> Engineering Guardrail

而：

```text
max_tokens
```

更接近：

> LLM Capacity Constraint

不要把：

```text
max_chars
```

描述成：

```text
token budget
```

也不要把：

```text
max_turns
```

描述成：

```text
context window
```

---

# 六、结合 Step 16 实测数据

使用 Step 16 已经得到的真实结果：

```text
1 turn    → 26 chars
10 turns  → 294 chars
50 turns  → 1474 chars
100 turns → 2949 chars
500 turns → 14749 chars
1000 turns → 29499 chars
```

本 Step：

**不要重新创建 tokenizer。**

可以引用已有字符增长证据，说明：

```text
History length grows linearly.
```

不要根据：

```text
chars
```

推导：

```text
tokens
```

---

# 七、第一版 Budget 的候选原则

只进行设计比较：

### Option 1

```text
Recent N Turns
```

Budget：

```text
max_turns
```

---

### Option 2

```text
Recent Turns
+
Character Guardrail
```

Budget：

```text
max_turns
+
max_chars
```

---

### Option 3

```text
Token Budget
```

依赖：

```text
Tokenizer
```

---

# 八、重点回答：当前项目是否应该立即引入 Tokenizer

从以下角度审计：

```text
1. 当前是否已经需要真实 token-level enforcement？
2. 当前是否已经存在明确 model/tokenizer mapping？
3. 当前是否已经定义 system/project/tool/RAG/output reserve？
4. 当前是否已经决定 model context window source？
5. 当前是否已有 tokenizer dependency？
6. 是否会因为 tokenizer 引入新的 runtime dependency？
```

如果这些条件尚未满足：

记录：

```text
Tokenizer remains Deferred
```

不要因为“未来需要”就在本 Step 引入。

---

# 九、Budget Ownership 再确认

结合 Step 18：

未来建议：

```text
Project
    ↓
Default SelectionPolicy

Request
    ↓
Optional internal override

Model
    ↓
Capability cap
```

但：

```text
Conversation ORM
ConversationRepository
ConversationService
```

仍然：

```text
不拥有 Budget
```

---

# 十、Policy / Budget 最小组合

本 Step 只设计，不实现。

未来概念：

```text
SelectionPolicy
    ├── strategy
    └── budget reference

Budget
    ├── unit
    └── limit
```

但不要现在冻结具体字段名。

特别不要现在直接创建：

```text
max_chars
max_turns
max_tokens
```

production DTO。

---

# 十一、Zero / Negative Budget

必须定义未来行为：

```text
0
```

以及：

```text
negative
```

例如：

```text
max_turns = 0
```

意味着：

```text
不选择任何 previous turns
```

还是：

```text
invalid policy
```

本 Step 必须进行语义比较，但**不要实现**。

推荐分析：

```text
negative → invalid policy
zero → valid but selects empty history
```

但不要直接写入 production code。

---

# 十二、Oversized Budget

例如：

```text
requested budget = 1,000,000
model available history budget = 6,000
```

未来必须：

```text
effective budget <= available budget
```

继续保持 Step 18：

```text
effective budget
=
min(requested, available)
```

这里只是 Contract。

不计算实际值。

---

# 十三、Budget 不应该改变 Builder Contract

无论未来选择：

```text
max_turns
max_chars
max_tokens
```

最终仍然：

```text
Selected Turns
    ↓
ConversationContextBuilder.build_context()
```

Builder 不知道：

```text
budget
policy
model
context window
```

---

# 十四、Budget 不应该进入 Conversation ORM

确认：

```text
Conversation
```

仍然只表达：

```text
conversation_id
project_id
created_at
updated_at
status
```

不要增加：

```text
context_budget
max_turns
max_chars
max_tokens
```

因为这会把：

```text
Conversation Persistence
```

与：

```text
LLM Runtime Policy
```

耦合。

---

# 十五、Failure Semantics

未来：

```text
Budget invalid
    →
invalid policy
```

而：

```text
Valid budget
+
Selector runtime failure
    →
business failure
```

禁止：

```text
fallback full history
```

禁止：

```text
ignore budget
```

禁止：

```text
silent degradation
```

---

# 十六、Security

Budget 只允许表达：

```text
unit
limit
strategy
```

禁止包含：

```text
API key
Authorization
DATABASE_URL
password
SQL
prompt
messages
RAG chunks
Tool arguments
LLM response
DB session
```

---

# 十七、Determinism

未来：

```text
same history
+
same policy
+
same budget
+
same model capability
=
same selected turns
```

不得依赖：

```text
current time
random
LLM
DB iteration order
```

---

# 十八、测试

新增：

```text
tests/test_conversation_context_budget_unit_audit.py
```

只做离线审计。

至少覆盖：

### 1

max_turns / max_chars / max_tokens 是不同概念。

### 2

max_chars 不得被描述成 token budget。

### 3

Conversation ORM 没有 budget 字段。

### 4

ConversationService 没有 budget 参数。

### 5

ChatApplicationService 是未来 budget composition 边界。

### 6

Tokenizer 当前不存在。

### 7

zero / negative budget 的未来语义被文档明确。

### 8

oversized budget 的 `min(requested, available)` Contract 被记录。

### 9

Budget 不进入 Builder。

### 10

Budget 不进入 Persistence。

### 11

Failure semantics 明确。

### 12

Security boundary 明确。

---

# 十九、Architecture Audit

新增：

```text
tests/test_conversation_context_budget_architecture.py
```

AST 检查：

### Conversation ORM

不得出现：

```text
context_budget
max_turns
max_chars
max_tokens
```

### ConversationRepository

不得出现：

```text
budget
tokenizer
ContextSelector
```

### ConversationService

不得出现：

```text
budget
tokenizer
LLM
ContextSelector
```

### ContextBuilder

不得出现：

```text
budget
tokenizer
ContextSelector
```

### ChatApplicationService

允许作为未来：

```text
budget composition
```

边界。

当前：

```text
production budget = absent
production selector = absent
```

---

# 二十、Documentation

新增：

```text
docs/evaluation/Phase 4.1 Step 19 — History Budget Unit Decision Audit.md
```

至少包含：

```text
1. Current State
2. max_turns
3. max_chars
4. max_tokens
5. Hybrid
6. Engineering Guardrail vs Capacity Constraint
7. Step 16 Evidence
8. Tokenizer Decision
9. Budget Ownership
10. Policy / Budget Composition
11. Zero / Negative
12. Oversized Budget
13. Builder Boundary
14. Persistence Boundary
15. Failure Semantics
16. Security
17. Determinism
18. Deferred Decision
```

最终明确：

```text
History Budget Unit = Deferred
Tokenizer = Deferred
ContextSelector = Deferred
Truncation = Deferred
```

本 Step **不要选择最终单位**。

---

# 二十一、Production Code

必须：

```text
backend/app/**/*.py
```

**0 修改。**

允许：

```text
tests/
docs/
```

---

# 二十二、测试命令

运行：

```powershell
python -m pytest -q tests/test_conversation_context_budget_unit_audit.py
```

然后：

```powershell
python -m pytest -q tests/test_conversation_context_budget_architecture.py
```

然后：

```powershell
python -m compileall -q backend tests
```

禁止：

```text
RUN_DB_TESTS
pytest -q
DeepSeek
SiliconFlow
Tokenizer
```

---

# 二十三、完成标准

必须满足：

```text
□ max_turns 已分析
□ max_chars 已分析
□ max_tokens 已分析
□ Hybrid 已分析
□ Engineering Guardrail vs Capacity Constraint 已区分
□ Step 16 growth evidence 已复用
□ Tokenizer 仍 Deferred
□ Budget Ownership 冻结
□ Policy / Budget 概念保持分离
□ Zero / Negative 语义已记录
□ Oversized Budget Contract 已记录
□ Builder Boundary 保持
□ Persistence Boundary 保持
□ Failure Semantics 保持
□ Security 保持
□ Determinism 保持
□ Production Code = 0
□ DB = 0
□ Network = 0
□ LLM = 0
□ Tests PASS
□ compileall PASS
```

---

# 二十四、最终报告

严格：

```text
Phase 4.1 Step 19 完成报告

1. max_turns
2. max_chars
3. max_tokens
4. Hybrid
5. Guardrail vs Capacity Constraint
6. Step 16 Evidence
7. Tokenizer
8. Budget Ownership
9. Policy / Budget Composition
10. Zero / Negative
11. Oversized Budget
12. Builder Boundary
13. Persistence Boundary
14. Failure Semantics
15. Security
16. Determinism
17. Tests
18. Architecture Audit
19. compileall
20. Production Code Changes
21. DB / Network / LLM
22. Documentation
23. Current Limitations

Phase 4.1 Step 19 READY
Phase 4.1 Step 19 STOP
```

**完成后立即停止。**

不要实现 Budget。
不要实现 ContextSelector。
不要实现 truncation。
不要引入 tokenizer。
不要进入 Step 20。
