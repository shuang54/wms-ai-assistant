继续 Phase 4.1。

当前状态：

```text
Step 13 Context Builder = COMPLETE
Step 14 Context 接入 = COMPLETE
Step 15 Multi-turn DB E2E = COMPLETE
Step 16 Context Budget Audit = COMPLETE
Step 17 Context Selection Contract = COMPLETE
```

当前已经冻结：

```text
ConversationService
    ↓
previous turns

ContextSelector（未来）
    ↓
selected turns

ConversationContextBuilder
    ↓
formatted context

AIOrchestrator
```

Step 17 已经明确：

```text
ContextSelector = 未实现
SelectionPolicy = 未实现
Tokenizer = 未引入
Truncation = 未实现
```

本 Step **只审计 SelectionPolicy / Budget Ownership**。

**不要实现 ContextSelector。**
**不要实现 truncation。**
**不要引入 tokenizer。**
**不要修改 production backend。**

---

# 一、Step 18 唯一目标

回答一个问题：

> **未来“Context 应该保留多少历史”这个决策，到底由谁拥有？**

需要比较：

```text
Conversation-level
Project-level
Model-level
Global-level
Request-level
```

以及：

```text
SelectionPolicy
Budget
Context Window
```

之间的责任边界。

本 Step 只完成：

```text
阅读
→ 真实代码审计
→ Policy Boundary
→ Budget Ownership
→ Override 规则设计
→ Validation
→ Tests
→ Documentation
→ STOP
```

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
ContextSelector production implementation
Tokenizer
tiktoken
transformers
sentencepiece
actual token counting
actual truncation
summary
memory
RAG
Tool
LLM
DeepSeek
SiliconFlow
```

禁止：

```text
DB schema
migration
HTTP API
new API fields
global mutable config
```

---

# 三、开始前阅读

必须阅读：

```text
backend/app/services/conversation_context_builder.py
backend/app/services/chat_application_service.py
backend/app/services/conversation_service.py
backend/app/services/ai_orchestrator_service.py
backend/app/dto/
backend/app/core/
```

以及：

```text
tests/test_conversation_context_selection_contract.py
tests/test_conversation_context_selection_architecture.py
tests/test_chat_application_service.py
tests/test_conversation_multiturn_db_e2e.py
```

同时阅读：

```text
docs/evaluation/Phase 4.1 Step 16 — Context Budget Audit.md
docs/evaluation/Phase 4.1 Step 17 — Context Selection Contract.md
```

必须以真实代码为准。

---

# 四、先确认当前没有 Budget

验证：

```text
Conversation
Project
AIOrchestrator
ChatApplicationService
ContextBuilder
```

当前都没有正式：

```text
max_turns
max_chars
max_tokens
context_budget
selection_policy
```

如果发现已经存在类似配置：

**只记录，不修改。**

---

# 五、SelectionPolicy 的概念

未来建议存在一个纯数据概念：

```text
SelectionPolicy
```

但本 Step：

**不创建 production DTO。**

只冻结概念。

SelectionPolicy 的职责：

> 告诉 ContextSelector “允许如何选择历史”。

例如未来可能表达：

```text
strategy = recent_n
limit = N
```

或者：

```text
strategy = recent_plus_first
recent_n = N
```

或者：

```text
strategy = token_budget
budget = X
```

但本 Step 不冻结具体字段。

---

# 六、Policy ≠ Budget

必须明确区分：

### SelectionPolicy

回答：

> **怎么选？**

例如：

```text
Recent N
Recent + First
Token Budget
```

### Budget

回答：

> **最多允许多少？**

例如未来：

```text
max_turns
max_tokens
```

所以：

```text
SelectionPolicy
    +
Budget
    ↓
Context Selection
```

不要把两者混成一个“大配置对象”。

---

# 七、Budget Ownership 候选方案

比较以下五种：

## A. Conversation-level

```text
Conversation
    ↓
context_budget
```

优点：

```text
每个会话可以不同
```

缺点：

```text
Conversation 持久化模型开始知道 LLM Context
```

可能污染：

```text
ConversationService
```

---

## B. Project-level

```text
ProjectContext
    ↓
context policy
```

优点：

```text
同一个业务项目统一策略
```

缺点：

```text
不同模型 / 请求场景可能需要不同预算
```

---

## C. Model-level

```text
Model
    ↓
context window
```

优点：

```text
接近真实模型能力
```

缺点：

```text
model context window
≠
conversation history budget
```

不能把整个模型窗口都给 Conversation History。

---

## D. Global-level

```text
Application Config
    ↓
context policy
```

优点：

```text
简单
```

缺点：

```text
所有项目 / 所有模型 / 所有场景共用
```

长期扩展性差。

---

## E. Request-level

```text
AI Request
    ↓
SelectionPolicy
```

优点：

```text
最灵活
```

缺点：

```text
调用方必须知道策略
容易把复杂 Context 策略泄漏到 API
```

---

# 八、重点分析：Conversation 不应该拥有 Budget

结合 Step 17：

```text
ConversationService
=
Persistence
+
Ordering
+
Lifecycle
```

因此本 Step 重点验证：

```text
Conversation ORM
ConversationRepository
ConversationService
```

不应该新增：

```text
context_budget
selection_policy
max_tokens
```

除非未来有非常明确的产品需求。

本阶段：

**不要修改数据库模型。**

---

# 九、重点分析：Model Window ≠ History Budget

必须形成一个明确公式：

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
Safety / Output Reserve
    ↓
Available History Budget
```

所以：

```text
model_context_window
```

不能直接等于：

```text
conversation_history_budget
```

这是未来设计必须冻结的原则。

---

# 十、Request-level Override

比较未来是否允许：

```text
default policy
    ↓
request override
```

例如：

```text
Project default:
Recent N = 20

特殊请求：
Recent N = 50
```

但必须评估：

```text
API 是否暴露这个参数？
普通用户是否可以控制？
是否可能造成资源消耗？
```

本 Step 不实现 HTTP 参数。

只做设计。

---

# 十一、建议的未来优先级

只作为候选方案分析，不实现：

```text
Request explicit policy
        ↓
Project default policy
        ↓
Global default policy
        ↓
Model capability constraint
```

但是：

> **Model capability 应该是上限约束，不应该成为业务 SelectionPolicy 本身。**

例如：

```text
Requested history budget = 10000 tokens
Model available history budget = 6000 tokens
```

不能直接发送 10000。

未来 Selector 必须：

```text
effective budget
=
min(requested, available)
```

这里只冻结概念，不实现计算。

---

# 十二、Policy Validation

未来 SelectionPolicy 必须满足：

```text
deterministic
serializable
immutable
validated
```

禁止：

```text
callable
lambda
function
LLM prompt
DB connection
runtime object
```

本 Step 不创建 DTO。

可以用 Fake TypedDict / dataclass 在测试中表达概念。

---

# 十三、Invalid Policy

未来必须区分：

```text
invalid policy
```

和：

```text
selection failed
```

例如：

```text
recent_n = -1
```

属于：

```text
invalid policy
```

不是：

```text
runtime selection failure
```

而：

```text
valid policy
+
history
+
selector exception
```

属于：

```text
selection failure
```

两者未来应该有不同测试。

本 Step 只设计，不实现。

---

# 十四、Failure Semantics

继续保持 Step 17：

```text
Context Selection failure
        ↓
Business Failure
```

禁止：

```text
fallback to full history
```

禁止：

```text
ignore policy
```

禁止：

```text
silent degradation
```

禁止：

```text
LLM summary retry
```

---

# 十五、Security

SelectionPolicy 不允许包含：

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
raw LLM response
```

只允许描述：

```text
selection behavior
budget constraints
```

---

# 十六、Project Isolation

Policy 不得改变：

```text
conversation.project_id
```

也不能：

```text
request.project_id
    ↓
override conversation.project_id
```

未来：

```text
Conversation.project_id
    ↓
Project default policy
```

但：

```text
Project binding
≠
Authorization
```

当前仍然没有 auth。

---

# 十七、测试

新增：

```text
tests/test_conversation_context_policy_audit.py
```

只做离线 Contract Audit。

至少覆盖：

### 1

Policy 与 Budget 概念独立。

### 2

ConversationService 不拥有 policy。

### 3

Conversation ORM 不拥有：

```text
context_budget
max_tokens
selection_policy
```

### 4

ChatApplicationService 是未来 policy composition 边界。

### 5

Model context window 不等于 history budget。

### 6

Request override 只是未来设计，不存在 API 参数。

### 7

Invalid policy ≠ selection failure。

### 8

Selection failure = business failure。

### 9

Policy deterministic。

### 10

Policy 不包含：

```text
DB
LLM
RAG
Tool
Secrets
```

---

# 十八、Architecture Audit

新增：

```text
tests/test_conversation_context_policy_architecture.py
```

AST 检查：

### Conversation ORM

不得出现：

```text
context_budget
selection_policy
max_tokens
max_turns
```

### ConversationRepository

不得出现：

```text
LLM
tokenizer
ContextBuilder
ContextSelector
```

### ConversationService

不得出现：

```text
tokenizer
LLM
RAG
Tool
ContextSelector
```

### ChatApplicationService

可以作为未来：

```text
policy composition
```

边界。

但当前：

```text
production selector = absent
production policy = absent
```

---

# 十九、Documentation

新增：

```text
docs/evaluation/Phase 4.1 Step 18 — Selection Policy & Budget Ownership Audit.md
```

必须包含：

```text
1. Current State
2. SelectionPolicy Concept
3. Policy vs Budget
4. Conversation-level
5. Project-level
6. Model-level
7. Global-level
8. Request-level
9. Recommended Boundary
10. Model Window vs History Budget
11. Override Concept
12. Validation
13. Failure Semantics
14. Security
15. Project Isolation
16. Deferred Decisions
```

特别记录：

```text
SelectionPolicy = Deferred
Budget = Deferred
Tokenizer = Deferred
Selector = Deferred
```

---

# 二十、Production Code

本 Step：

```text
backend/app/**/*.py
```

**0 修改。**

只允许：

```text
tests/
docs/
```

---

# 二十一、测试命令

运行：

```powershell
python -m pytest -q tests/test_conversation_context_policy_audit.py
```

然后：

```powershell
python -m pytest -q tests/test_conversation_context_policy_architecture.py
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

# 二十二、完成标准

必须满足：

```text
□ SelectionPolicy 概念冻结
□ Policy ≠ Budget
□ Conversation 不拥有 Budget
□ Project Policy 作为候选
□ Model Window ≠ History Budget
□ Request Override 仅设计
□ Invalid Policy ≠ Selection Failure
□ Selection Failure = Business Failure
□ Security Boundary
□ Project Isolation
□ Tokenizer Deferred
□ Selector Deferred
□ Production Code = 0
□ DB = 0
□ Network = 0
□ LLM = 0
□ Tests PASS
□ compileall PASS
```

---

# 二十三、最终报告

严格：

```text
Phase 4.1 Step 18 完成报告

1. SelectionPolicy Boundary
2. Policy vs Budget
3. Conversation-level
4. Project-level
5. Model-level
6. Global-level
7. Request-level
8. Recommended Boundary
9. Model Window vs History Budget
10. Override
11. Validation
12. Failure Semantics
13. Security
14. Project Isolation
15. Tests
16. Architecture Audit
17. compileall
18. Production Code Changes
19. DB / Network / LLM
20. Documentation
21. Current Limitations

Phase 4.1 Step 18 READY
Phase 4.1 Step 18 STOP
```

**完成后立即停止。**

不要实现 SelectionPolicy。
不要实现 ContextSelector。
不要实现 truncation。
不要引入 tokenizer。
不要进入 Step 19。
