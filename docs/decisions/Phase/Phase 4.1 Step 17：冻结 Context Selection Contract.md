继续 Phase 4.1。

当前状态：

```text
Step 13 Context Builder = COMPLETE
Step 14 Context 接入 ChatApplicationService = COMPLETE
Step 15 Multi-turn DB E2E = COMPLETE
Step 16 Context Budget Audit = COMPLETE
```

当前已经确认：

```text
Conversation History
        ↓
ChatApplicationService
        ↓
ConversationContextBuilder
        ↓
LLM Context
```

并且：

```text
Context 当前线性增长
Tokenizer = 未引入
Truncation = 未实现
Summary = 未实现
Memory = 未实现
```

本 Step **只冻结 Context Selection Contract**。

**不要实现 truncation。**
**不要引入 tokenizer。**
**不要修改 production backend。**

---

# 一、Step 17 唯一目标

建立：

```text
Conversation History
        ↓
Context Selection
        ↓
Selected Turns
        ↓
ConversationContextBuilder
        ↓
LLM Context
```

明确：

> 到底由哪一层决定“哪些历史 Turn 可以进入 LLM Context”。

本阶段只做：

```text
阅读
→ Contract Audit
→ Boundary Design
→ Selection Input/Output Design
→ Failure Semantics
→ Security
→ Determinism
→ Tests
→ Documentation
→ STOP
```

---

# 二、严格禁止

本阶段禁止修改：

```text
backend/app/services/conversation_context_builder.py
backend/app/services/chat_application_service.py
backend/app/services/conversation_service.py
backend/app/db/conversation_repository.py
backend/app/services/ai_orchestrator_service.py
```

禁止：

```text
tokenizer
tiktoken
transformers
sentencepiece
token counting
actual truncation
summary
memory
embedding
RAG
Tool
LLM
DeepSeek
SiliconFlow
```

禁止新增：

```text
DB table
migration
repository
HTTP API
API field
global mutable state
```

---

# 三、开始前阅读

必须阅读真实代码：

```text
backend/app/services/conversation_context_builder.py
backend/app/services/chat_application_service.py
backend/app/services/conversation_service.py
backend/app/db/conversation_repository.py
```

以及：

```text
tests/test_conversation_context_builder.py
tests/test_chat_application_service.py
tests/test_conversation_multiturn_db_e2e.py
```

同时阅读：

```text
docs/evaluation/Phase 4.1 Step 16 — Context Budget Audit.md
```

不要根据之前阶段报告猜接口。

---

# 四、冻结当前 Context Builder Contract

必须验证当前 Builder 保持：

```text
build_context(turns)
        ↓
str | None
```

规则：

```text
empty history
    → None
```

USER：

```text
user: <content>
```

ASSISTANT：

```text
assistant: <content>
```

多个 Turn：

```text
user: ...
assistant: ...
user: ...
```

必须：

```text
保持输入顺序
保持 content 原样
不 trim
不 normalize
不 truncate
不排序
```

Builder 不负责：

```text
selection
budget
token counting
summary
memory
```

本 Step 不修改 Builder。

---

# 五、Context Selection 的职责

设计一个未来独立的概念：

```text
Context Selector
```

职责只有：

> 从 Conversation History 中选择哪些 Turn 可以进入 Context。

它不负责：

```text
字符串格式化
LLM
数据库
tokenizer
summary
RAG
Tool
```

目标：

```text
ConversationTurnView[]
        ↓
ContextSelector
        ↓
ConversationTurnView[]
        ↓
ConversationContextBuilder
        ↓
str
```

---

# 六、为什么 Selector 不应该放进 Builder

必须在测试/文档中明确：

### Builder

负责：

```text
selected turns
    ↓
formatted context
```

### Selector

负责：

```text
all previous turns
    ↓
selected turns
```

这样以后：

```text
Recent N
Recent N + First
Token Budget
Summary
Memory
```

都可以替换 Selector，而不修改 Builder。

---

# 七、为什么 Selector 不应该放进 ConversationService

ConversationService 当前职责：

```text
create
get
archive
append_turn
list_turns
```

它负责：

```text
Persistence
Ordering
Conversation lifecycle
```

不应该知道：

```text
LLM context window
token budget
prompt budget
model
system prompt
RAG
Tool
```

因此冻结：

```text
ConversationService
    ≠
Context Selection
```

---

# 八、ChatApplicationService 的责任

当前：

```text
Conversation
    ↓
list_turns
    ↓
exclude current USER
    ↓
ContextBuilder
    ↓
AIOrchestrator
```

未来应该演化为：

```text
Conversation
    ↓
list previous turns
    ↓
ContextSelector
    ↓
ContextBuilder
    ↓
AIOrchestrator
```

ChatApplicationService 只负责：

```text
workflow orchestration
```

不要把具体：

```text
Recent N
Token Budget
Summary
```

算法写进 ChatApplicationService。

---

# 九、Selector Input Contract

未来 Selector 的输入建议只包含：

```text
previous ConversationTurnView[]
```

以及显式：

```text
SelectionPolicy / Budget
```

但：

**本 Step 不实现具体 Budget 类型。**

不要现在创建：

```text
max_tokens
max_chars
max_turns
```

等生产配置。

这里只冻结概念：

```text
History
+
Explicit Selection Policy
→
Selected History
```

---

# 十、Selector Output Contract

Selector 输出：

```text
ConversationTurnView[]
```

必须满足：

```text
selected ⊆ previous
```

并且：

```text
不修改 Turn
不创建 Turn
不删除 DB Turn
不改变 role
不改变 content
不改变 turn_id
```

---

# 十一、Current USER Boundary

非常重要。

Selector 输入必须已经是：

```text
previous turns
```

即：

```text
当前 USER Turn
```

已经排除。

禁止 Selector 自己通过：

```text
turn_id
```

猜测哪个是 current user。

推荐：

```text
ChatApplicationService
    ↓
exclude current USER
    ↓
Selector
```

这样 Selector 是纯历史选择器。

---

# 十二、Ordering Contract

Selector 必须保持：

```text
Conversation Repository order
    ↓
created_at ASC
turn_id ASC
```

Selector 可以：

```text
选择子集
```

但不能：

```text
重新排序
```

例如：

```text
A B C D
```

选择：

```text
B D
```

输出必须：

```text
B D
```

不能：

```text
D B
```

---

# 十三、Determinism

冻结：

```text
same history
+
same policy
=
same selected turns
```

禁止：

```text
random
current time
LLM
DB iteration order
hash randomization
```

---

# 十四、Failure Semantics

Selector 如果未来失败：

```text
Context selection failure
```

必须：

```text
business failure
```

不能：

```text
fallback to full history
```

不能：

```text
skip context silently
```

不能：

```text
call LLM summary
```

不能：

```text
continue without context
```

与 Step 14 保持一致：

```text
context failure
→ AI call = 0
→ USER turn retained
→ no ASSISTANT turn
```

本 Step 只冻结，不实现。

---

# 十五、Security Boundary

Selector 只允许处理：

```text
ConversationTurnView
Selection Policy
```

禁止接触：

```text
API key
Authorization
DATABASE_URL
SQLAlchemy Session
DB Connection
LLMResponse
raw provider response
RAG chunks
Tool arguments
Tool raw result
prompt
system prompt
credentials
```

尤其：

> Selector 不应该为了决定历史而读取数据库以外的业务数据。

---

# 十六、System Prompt / Project Context / Tool Definitions

必须明确预算边界：

```text
System Prompt
        +
Project Context
        +
Tool Definitions
        +
Current User
        +
Selected Conversation History
        +
RAG Context
        +
Tool Results
```

这些属于不同 Context Segment。

本 Step 冻结：

```text
Conversation History Selection
```

不得吞掉其他 Segment 的预算。

以后如果引入 Token Budget：

```text
Total Context Budget
    ├── System
    ├── Project
    ├── Tools
    ├── User
    ├── Conversation History
    └── RAG / Tool Context
```

但本 Step：

**不实现总预算分配。**

---

# 十七、三种未来 Selection Strategy

只做设计比较，不实现。

### A. Recent N Turns

```text
保留最近 N 个历史 Turn
```

优点：

```text
简单
确定
无需 tokenizer
```

缺点：

```text
早期上下文容易丢失
```

### B. Recent N + First Turn

```text
第一条历史
+
最近 N 条
```

优点：

```text
保留初始上下文
```

缺点：

```text
仍然是固定规则
可能出现语义断层
```

### C. Token Budget

```text
按照真实 token 数选择历史
```

优点：

```text
更接近真实 LLM Context Window
```

缺点：

```text
需要 tokenizer
需要模型/tokenizer 对应关系
需要明确预算来源
```

本 Step：

> **不选择最终策略。**

---

# 十八、不要用字符长度模拟 Token

明确禁止：

```python
len(text) / 4
len(text) / 3
len(text) * 0.25
```

这些只能作为粗略工程估算，不能成为项目 Token Contract。

Step 16 已经明确：

```text
Token Counting = Deferred
```

继续保持。

---

# 十九、测试

新增：

```text
tests/test_conversation_context_selection_contract.py
```

只测试 Contract，不实现 Selector。

至少覆盖：

### 1

Input history：

```text
A B C
```

理论 selection：

```text
B C
```

确认输出必须保持原顺序。

### 2

Selected 必须是 Input 的子集。

### 3

Turn 对象不可修改。

### 4

Content 不修改。

### 5

Role 不修改。

### 6

Current USER 不应该出现在 Selector 输入 Contract。

### 7

Empty history：

```text
[]
```

### 8

Deterministic：

同输入 → 同输出。

### 9

Failure：

selection failure → business failure。

### 10

Security：

Selector Contract 不允许依赖：

```text
DB
LLM
RAG
Tool
```

如果当前没有 Selector production implementation：

测试可以使用：

```text
Fake Selector
```

验证 Contract。

但：

**不要创建 production Selector。**

---

# 二十、Architecture Audit

新增：

```text
tests/test_conversation_context_selection_architecture.py
```

AST 检查：

```text
conversation_context_builder.py
chat_application_service.py
conversation_service.py
conversation_repository.py
```

确认：

### Builder

没有：

```text
tokenizer
DB
LLM
RAG
Tool
```

### ConversationService

没有：

```text
LLM
tokenizer
RAG
Tool
```

### Repository

没有：

```text
LLM
Context Builder
AI
```

### ChatApplicationService

可以：

```text
ConversationService
ContextBuilder
未来 ContextSelector
AIOrchestrator
```

但当前不要要求 production selector 存在。

---

# 二十一、Documentation

新增：

```text
docs/evaluation/Phase 4.1 Step 17 — Context Selection Contract.md
```

至少包含：

```text
1. Current Architecture
2. Selection Responsibility
3. Builder Responsibility
4. ConversationService Responsibility
5. ChatApplicationService Responsibility
6. Selector Input
7. Selector Output
8. Current USER Boundary
9. Ordering
10. Determinism
11. Failure Semantics
12. Security Boundary
13. System / Project / Tool / History Separation
14. Strategy Comparison
15. Tokenizer Deferred
16. Implementation Deferred
```

---

# 二十二、禁止修改 Production

本 Step：

```text
backend/app/**/*.py
```

生产代码：

**0 修改。**

允许：

```text
tests/
docs/
```

---

# 二十三、测试命令

运行：

```powershell
python -m pytest -q tests/test_conversation_context_selection_contract.py
```

然后：

```powershell
python -m pytest -q tests/test_conversation_context_selection_architecture.py
```

然后：

```powershell
python -m compileall -q backend tests
```

不要：

```text
RUN_DB_TESTS
pytest -q
DeepSeek
SiliconFlow
```

---

# 二十四、完成标准

必须：

```text
□ Context Selector Responsibility 冻结
□ Builder Responsibility 冻结
□ ConversationService Responsibility 冻结
□ ChatApplicationService Responsibility 冻结
□ Current USER Boundary 冻结
□ Ordering 冻结
□ Determinism 冻结
□ Failure Semantics 冻结
□ Security Boundary 冻结
□ System/Project/Tool/History Boundary 冻结
□ Recent N / Recent+First / Token Budget 已比较
□ Tokenizer 仍 Deferred
□ Production code = 0
□ DB = 0
□ Network = 0
□ LLM = 0
□ Tests PASS
□ compileall PASS
```

---

# 二十五、最终报告

严格：

```text
Phase 4.1 Step 17 完成报告

1. Context Selection Boundary
2. Builder Boundary
3. ConversationService Boundary
4. ChatApplicationService Boundary
5. Current USER Boundary
6. Ordering
7. Determinism
8. Failure Semantics
9. Security
10. Context Segment Separation
11. Strategy Comparison
12. Tokenizer
13. Tests
14. Architecture Audit
15. compileall
16. Production Code Changes
17. DB / Network / LLM
18. Documentation
19. Current Limitations

Phase 4.1 Step 17 READY
Phase 4.1 Step 17 STOP
```

**完成后立即停止。**

不要实现 Context Selector。
不要实现 truncation。
不要引入 tokenizer。
不要进入 Step 18。
