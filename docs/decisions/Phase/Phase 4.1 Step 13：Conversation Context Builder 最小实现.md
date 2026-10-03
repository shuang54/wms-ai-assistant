继续 Phase 4.1。

当前状态：

```text
Step 10 COMPLETE
Step 11 COMPLETE
Step 12 COMPLETE
```

当前链路已经是：

```text
POST /api/conversations/{conversation_id}/messages
        ↓
Conversation API
        ↓
ChatApplicationService
        ↓
ConversationService + AIOrchestrator
```

现在只执行：

# Phase 4.1 Step 13：Conversation Context Builder 最小实现

## 一、唯一目标

建立一个**纯内存、纯函数式的 Conversation Context Builder**。

目标：

```text
Conversation Turns
        ↓
Context Builder
        ↓
LLM Context
```

本阶段只解决：

> 如何把 Conversation History 转换成 AIOrchestrator 可以接受的 context。

**不修改 AIOrchestrator。**

**不修改 ConversationService。**

**不修改 API。**

---

# 二、开始前必须阅读

先阅读真实代码：

```text
backend/app/services/chat_application_service.py
backend/app/services/conversation_service.py
backend/app/services/ai_orchestrator_service.py

backend/app/db/models/conversation_turn.py
backend/app/dto/
tests/test_chat_application_service.py
```

重点确认：

```text
AIOrchestrator.execute()
```

当前真实：

```text
question
context
```

参数结构。

不要假设 context 类型。

---

# 三、允许新增

原则上只允许新增：

```text
backend/app/services/conversation_context_builder.py
tests/test_conversation_context_builder.py
```

如果当前项目已有 Context Builder / Context DTO：

**优先复用。**

不要创建重复实现。

---

# 四、严格禁止

本阶段禁止修改：

```text
AIOrchestrator
ConversationService
ConversationRepository
Conversation ORM
Conversation API
ChatApplicationService
RAG
Tool Framework
TextToSQL
Validator
Executor
LLM Provider
Prompt
Trace
Timeline
Outcome
LLM Usage
```

禁止：

```text
Memory
Summary
Conversation Summary
Regenerate
Pagination
Auth
Streaming
Token counting
Embedding
Reranker
DeepSeek
SiliconFlow
DB query
Network
```

特别禁止修改：

```text
POST /api/conversations/{conversation_id}/messages
```

本阶段 API 保持 Step 12 状态。

---

# 五、Context Builder 职责

Context Builder 只负责：

```text
ConversationTurn[]
        ↓
LLM Context
```

不负责：

```text
数据库读取
Conversation 查询
AI 调用
Prompt 模板
Token 计算
Summary
Memory
```

因此推荐接口：

```python
build_context(turns) -> ...
```

输入已经是：

```text
ConversationTurn DTO
```

而不是：

```text
conversation_id
```

禁止 Context Builder 自己调用：

```text
ConversationService
Repository
SQLAlchemy
```

---

# 六、Context 类型

首先根据当前：

```text
AIOrchestrator.execute(..., context=...)
```

真实类型决定。

如果当前 context 已经有 DTO / TypedDict / dataclass：

**直接复用。**

如果当前 context 接受普通：

```text
list[dict]
```

则保持现有 contract。

不要为了 Step 13 重新设计大型：

```text
ConversationContext
```

---

# 七、Turn → Context Mapping

只允许：

```text
USER
ASSISTANT
```

进入 context。

映射原则：

```text
USER
→ role=user
→ content=turn.content

ASSISTANT
→ role=assistant
→ content=turn.content
```

只复制：

```text
role
content
```

不要加入：

```text
turn_id
conversation_id
assistant_request_id
created_at
```

除非当前 AIOrchestrator context contract 明确要求。

默认不要加入。

---

# 八、Security

Context Builder 不得把：

```text
assistant_request_id
conversation_id
turn_id
```

发送给 LLM。

也不得生成：

```text
system prompt
tool definitions
SQL
RAG chunks
API key
credentials
```

Context Builder 只是：

```text
历史对话内容
```

转换器。

---

# 九、Empty History

输入：

```python
[]
```

结果必须符合当前 Orchestrator context contract。

如果当前 contract 支持：

```text
None
```

优先返回：

```text
None
```

如果必须是：

```text
[]
```

则返回：

```text
[]
```

不要创建：

```text
{"messages": []}
```

除非当前真实 contract 明确要求。

---

# 十、USER-only History

例如：

```text
USER: 查询库存
```

生成：

```text
[
  {"role": "user", "content": "查询库存"}
]
```

不能：

```text
assistant_request_id
```

进入 context。

---

# 十一、USER + ASSISTANT

例如：

```text
USER: 查询库存
ASSISTANT: 当前库存为...
```

生成：

```text
[
  {"role": "user", "content": "..."},
  {"role": "assistant", "content": "..."}
]
```

顺序必须保持：

```text
created_at ASC
turn_id ASC
```

Context Builder 自己**不要排序**。

输入顺序就是业务层提供的顺序。

---

# 十二、当前 Turn 的处理

非常重要。

Step 11 当前 workflow：

```text
append USER
    ↓
AI execution
    ↓
append ASSISTANT
```

Step 13 不应该让当前 USER turn 重复进入 context。

推荐未来调用：

```text
list_turns(conversation_id)
        ↓
remove / exclude current USER turn
        ↓
build_context(history)
        ↓
AIOrchestrator.execute(current_question, context=context)
```

但是：

**本 Step 不修改 ChatApplicationService。**

因此本 Step 只定义 Builder 行为：

> Builder 对输入的 turns 全部进行一对一转换，不负责判断哪个 turn 是 current turn。

未来 Step 再由 Application Service 决定传入：

```text
history
```

还是：

```text
history + current user
```

不要在 Builder 内部猜测。

---

# 十三、Content

保持：

```text
turn.content
```

原样。

不要：

```text
strip()
truncate()
summarize()
normalize()
escape()
```

Context Builder 不负责内容策略。

---

# 十四、Role

只接受当前：

```text
USER
ASSISTANT
```

如果出现未知 role：

```text
SYSTEM
TOOL
UNKNOWN
```

必须明确处理。

优先：

```text
ValueError
```

或者复用当前项目已有角色异常。

不要静默转换成：

```text
user
```

---

# 十五、Immutability

Context Builder：

```text
input turns
```

不能被修改。

测试：

```text
original_turns == turns_after
```

同时：

```text
result
```

不能通过修改影响原 Turn DTO。

---

# 十六、Pure Function

Builder 必须：

```text
deterministic
stateless
no IO
```

禁止：

```text
DB
Network
LLM
Environment
Time
Random
Global mutable state
```

同样输入：

```text
same output
```

---

# 十七、测试

新增：

```text
tests/test_conversation_context_builder.py
```

至少覆盖：

### 1

empty turns

### 2

single USER

### 3

single ASSISTANT

### 4

USER + ASSISTANT

### 5

multiple turns

### 6

ordering preserved

### 7

same timestamp ordering already provided by input

### 8

unknown role rejected

### 9

empty content

如果当前 Turn DTO 允许空字符串，则保持原样。

不要在 Builder 内增加新的业务校验。

### 10

whitespace content

保持原样。

### 11

metadata isolation

Turn 中不存在 metadata，但构造毒化对象确认不会读取：

```text
assistant_request_id
conversation_id
turn_id
created_at
```

### 12

input immutability

### 13

result isolation

### 14

determinism

### 15

no DB imports

### 16

no network imports

### 17

no LLM imports

### 18

no ConversationService dependency

### 19

no Repository dependency

### 20

no Prompt dependency

---

# 十八、AST Architecture Audit

新增：

```text
tests/test_conversation_context_builder_architecture_audit.py
```

使用 AST。

不要全文字符串扫描。

Builder executable AST 不得 import：

```text
sqlalchemy
psycopg
backend.app.db
ConversationRepository
ConversationService
AIOrchestratorService
LLMClient
RagService
ToolChatService
TextToSQLService
```

允许：

```text
typing
dataclasses
collections.abc
现有 Context DTO
ConversationTurn DTO
```

根据实际实现调整。

---

# 十九、不要修改 ChatApplicationService

这是本 Step 最重要的边界：

```text
chat_application_service.py
```

**禁止修改。**

虽然未来它需要：

```text
ConversationService.list_turns()
        ↓
ContextBuilder
        ↓
AIOrchestrator.execute(..., context=...)
```

但这些属于下一步。

本 Step 只建立并验证：

```text
Turn[] → Context
```

---

# 二十、不要读取数据库

本 Step：

```text
DB = 0
Network = 0
LLM = 0
```

直接构造：

```text
ConversationTurn
```

作为测试输入。

不要：

```text
RUN_DB_TESTS
```

不要 PostgreSQL。

---

# 二十一、不要修改 API

保持：

```text
POST /api/conversations/{conversation_id}/messages
```

Step 12 的行为完全不变。

不要让：

```text
context
history
messages
```

成为 HTTP Request 字段。

---

# 二十二、测试命令

只运行：

```powershell
python -m pytest -q tests/test_conversation_context_builder.py
python -m pytest -q tests/test_conversation_context_builder_architecture_audit.py
```

然后：

```powershell
python -m compileall -q backend tests
```

不要：

```text
pytest -q
RUN_DB_TESTS=1
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

本 Step 理想修改：

```text
Added:
backend/app/services/conversation_context_builder.py
tests/test_conversation_context_builder.py
tests/test_conversation_context_builder_architecture_audit.py
```

不要修改：

```text
chat_application_service.py
conversation_service.py
conversations.py
conversation_api.py
ai_orchestrator_service.py
```

---

# 二十四、最终验收

必须满足：

```text
□ Context Builder 存在
□ 输入 ConversationTurn[]
□ 输出符合当前 AIOrchestrator context contract
□ USER → user
□ ASSISTANT → assistant
□ content 原样
□ 输入顺序保持
□ Builder 不排序
□ unknown role 明确拒绝
□ 不读取 turn metadata
□ 不读取 conversation_id
□ 不读取 turn_id
□ 不读取 assistant_request_id
□ 不读取 created_at
□ 不访问 DB
□ 不访问 Network
□ 不调用 LLM
□ 不依赖 ConversationService
□ 不依赖 Repository
□ 不依赖 Prompt
□ input immutable
□ output isolated
□ deterministic
□ AST dependency audit 通过
□ targeted tests 通过
□ compileall 通过
```

---

# 二十五、完成报告

严格输出：

```text
Phase 4.1 Step 13 完成报告

1. Context Builder
2. Context Contract
3. Turn Mapping
4. Empty History
5. Ordering
6. Current Turn Boundary
7. Role Validation
8. Content Semantics
9. Security
10. Immutability
11. Purity
12. Architecture Audit
13. Tests
14. compileall
15. DB / Network / LLM
16. Git Diff
17. Production Code Changes
18. Current Limitations

Phase 4.1 Step 13 READY
Phase 4.1 Step 13 STOP
```

必须明确：

```text
Conversation History → Context Builder = 已完成
ChatApplicationService → Context Builder = 未接线
AIOrchestrator Context = 未改变
Conversation API = 未改变
Memory = 未实现
Summary = 未实现
```

完成后立即 STOP。

不要进入：

```text
Step 14
Memory
Summary
Regenerate
Auth
Pagination
```
