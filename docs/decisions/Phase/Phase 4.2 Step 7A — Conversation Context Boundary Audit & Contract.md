你现在开始执行：

# Phase 4.2 Step 7A — Conversation Context Boundary Audit & Contract

## 一、阶段目标

Phase 4.2 Step 6 已完成并提交：

```text
66a69c6
feat: Phase 4.2 Step 6 message idempotency
```

当前 Step 6 已正式关闭。

现在进入：

```text
Phase 4.2 Step 7
Conversation Context Boundary
```

本次只执行：

```text
现状审计
    ↓
Context Contract
    ↓
Boundary Decision
    ↓
Regression Contract
    ↓
STOP
```

**本步骤禁止直接修改生产代码。**

不要实现 Step 7 Runtime。

---

# 二、Step 7 当前目标

确认当前 Conversation Runtime 的历史上下文边界：

```text
POST Message
    ↓
Current USER Turn
    ↓
Read Previous Turns
    ↓
ConversationContextBuilder
    ↓
AIOrchestrator
```

必须明确：

1. 当前 USER Turn 是否进入本次 history。
2. 当前 USER Turn 是否被重复注入。
3. ASSISTANT Turn 如何进入 context。
4. 历史消息排序规则。
5. 空 conversation 如何处理。
6. 多轮 conversation 如何处理。
7. Retry 如何处理。
8. Idempotent duplicate 如何处理。
9. FAILED / EMPTY 如何影响后续 context。
10. 是否存在 context window / token budget。
11. 是否存在最大 turn 数。
12. context 是否可能无限增长。
13. archived conversation 是否可以继续产生 message。
14. context builder 是否纯函数。
15. context 是否可能包含内部 metadata / request_id。
16. 是否存在敏感内部执行信息进入 prompt 的风险。

---

# 三、开始前必须阅读

先阅读真实代码：

```text
backend/app/services/chat_application_service.py
backend/app/services/conversation_service.py
backend/app/db/conversation_repository.py
backend/app/services/conversation_context_builder.py
backend/app/dto/conversation_api.py
backend/app/db/models/conversation_turn.py
backend/app/api/conversations.py
```

如果实际文件名不同：

先搜索：

```text
ConversationContextBuilder
build_context
previous_turns
execute_message
ConversationTurn
```

同时阅读：

```text
tests/
```

重点寻找：

```text
conversation persistence tests
conversation message tests
idempotency tests
context builder tests
chat application tests
```

不要假设接口。

---

# 四、必须检查 Step 6 现有行为

Step 6 已经定义：

```text
USER Turn
    ↓
AI
    ↓
ASSISTANT Turn
```

以及：

```text
retry:
existing USER Turn
    ↓
exclude original USER Turn from context
    ↓
AI retry
```

必须确认 Step 7 不破坏：

```text
Idempotency
Duplicate Replay
Retry
Conflict
Concurrency
```

特别验证：

```text
same key duplicate
```

不会重新进入 AI。

---

# 五、当前 Context Contract Audit

检查当前：

```text
ConversationContextBuilder.build_context(previous_turns)
```

明确记录：

### Input

当前输入是什么？

例如：

```text
list[ConversationTurn]
```

还是：

```text
ConversationTurn[]
```

### Output

当前输出是什么？

例如：

```text
str | None
```

### Ordering

确认：

```text
oldest → newest
```

还是：

```text
newest → oldest
```

必须以真实代码为准。

### Roles

确认是否支持：

```text
USER
ASSISTANT
```

以及是否可能出现：

```text
SYSTEM
TOOL
```

如果不存在，不要新增。

---

# 六、Current Turn Boundary

必须明确当前消息的生命周期：

```text
Request content
    ↓
USER Turn persistence
    ↓
history query
    ↓
Context Builder
    ↓
AI
```

重点确认：

```text
当前 USER Turn 是否已经提交后又被 history query 查询出来？
```

如果已经存在：

```text
exclude_turn_id
```

或者等价机制：

记录真实实现。

如果不存在：

记录风险：

```text
CURRENT_TURN_DUPLICATION_RISK
```

不要立即修。

---

# 七、Retry Boundary

使用 Step 6 已确认的行为：

```text
USER Turn exists
ASSISTANT Turn absent
        ↓
reuse USER Turn
        ↓
AI retry
```

必须验证：

retry context 不包含：

```text
当前 USER Turn
```

只能包含：

```text
更早历史 USER/ASSISTANT turns
```

例如：

```text
U1
A1
U2
```

第一次执行 U2：

```text
context = [U1, A1]
```

如果 U2 retry：

```text
context = [U1, A1]
```

不得：

```text
[U1, A1, U2]
```

也不得：

```text
[U1, A1, U2, U2]
```

---

# 八、Duplicate Replay Boundary

确认：

```text
completed duplicate
```

不会：

```text
read history
build context
call AI
```

即：

```text
duplicate
    ↓
persisted ASSISTANT Turn
    ↓
Message Replay
```

不重新进入 Context Builder。

如果当前实现符合：

记录为：

```text
PASS
```

不要修改。

---

# 九、Conversation Ordering Contract

确认历史 Turn 的顺序。

目标语义建议：

```text
Conversation:
U1
A1
U2
A2
U3
A3
```

当 U3 执行：

```text
context:
U1
A1
U2
A2
```

不得：

```text
A2
U2
A1
U1
```

不得依赖：

```text
database natural order
```

必须存在明确：

```text
ORDER BY created_at / turn_id
```

如果当前代码使用其他稳定排序：

记录真实实现。

如果没有稳定排序：

记录：

```text
CONTEXT_ORDERING_GAP
```

不要修。

---

# 十、Empty / Failed Turn

根据 Step 2：

### EMPTY

AI 正常执行，但：

```text
content = None / empty
```

不创建 ASSISTANT Turn。

因此下一条消息的 context：

```text
previous USER turns
```

是否应该包含这个 EMPTY USER Turn？

必须根据当前语义明确记录。

不要自行推断。

### FAILED

AI exception：

```text
USER Turn persisted
ASSISTANT Turn absent
```

下一次正常消息：

确认是否会把 failed USER Turn 作为历史 context。

记录当前行为。

本步骤不要修改。

---

# 十一、Archived Conversation

确认：

```text
ARCHIVED
```

是否：

```text
cannot send new message
```

Step 6 已存在 archive guard。

本步骤只确认 context 层没有绕过 archive guard。

---

# 十二、Context Window / Token Budget

必须搜索当前项目是否存在：

```text
max_context
context_window
token_budget
max_tokens
history_limit
max_turns
truncate
trim
window
```

如果存在：

记录真实配置和行为。

如果不存在：

明确：

```text
NO_CONTEXT_WINDOW_POLICY
```

不要新增配置。

本步骤不解决。

---

# 十三、Prompt Boundary

确认 Context Builder 输出最终如何进入：

```text
AIOrchestrator
```

检查：

```text
conversation context
```

是否与：

```text
user content
```

清晰分离。

确认不存在：

```text
request_id
conversation_id
turn_id
idempotency_key
database connection
internal exception
API credential
```

被拼入 AI context。

如果当前没有泄露：

记录 PASS。

如果发现：

记录：

```text
CONTEXT_INFORMATION_LEAK
```

立即 STOP，不修改。

---

# 十四、Context Immutability

确认：

```text
ConversationContextBuilder
```

是否只读：

```text
ConversationTurn
```

不得：

```text
修改 Turn
修改 Conversation
写数据库
```

确认：

```text
build_context()
```

是：

```text
pure / side-effect free
```

如果存在 DB write：

立即报告。

---

# 十五、Step 7 Contract

在不修改代码的情况下，形成一个明确的 Step 7 Contract。

至少包含：

```text
1. Current Turn Exclusion
2. Historical Ordering
3. Role Mapping
4. Retry Context
5. Duplicate Context
6. Empty Turn
7. Failed Turn
8. Archived Conversation
9. Context Window
10. Internal Metadata Boundary
11. Context Builder Side Effects
```

每项：

```text
CURRENT BEHAVIOR
EXPECTED SEMANTICS
GAP
```

不要在这里直接解决 GAP。

---

# 十六、必须新增 Audit 文档

新增：

```text
docs/evaluation/Phase 4.2 Step 7A — Conversation Context Boundary Audit.md
```

内容至少：

```text
1. Current Runtime Flow

2. Context Builder Contract

3. Current Turn Boundary

4. Retry Boundary

5. Duplicate Boundary

6. Ordering

7. EMPTY / FAILED

8. Archived

9. Context Window

10. Prompt Boundary

11. Side Effects

12. Gaps

13. Step 7 Contract

14. Recommendation
```

---

# 十七、测试要求

本步骤只允许：

```text
阅读现有测试
必要时设计测试
```

禁止修改测试。

除非发现：

```text
Step 6 regression
```

如果发现 Step 6 regression：

立即停止并报告。

---

# 十八、严格禁止

本步骤禁止：

```text
❌ 修改 backend
❌ 修改 API
❌ 修改 DB schema
❌ 新增 DB column
❌ 修改 Prompt
❌ 修改 AI Router
❌ 修改 AI Orchestrator
❌ 修改 RAG
❌ 修改 Tool
❌ 修改 Text-to-SQL
❌ 修改 Validator
❌ 修改 Executor
❌ 修改 Idempotency
❌ 修改 Evidence
❌ 修改 ConversationEvidence
❌ 新增 Context Config
❌ 新增 Tokenizer
❌ 新增 Memory
❌ 新增 Agent
```

也不要：

```text
❌ 自动进入 Step 7B
❌ 自动修复发现的问题
❌ 自动提交 Git
```

---

# 十九、Git 要求

开始前确认：

```powershell
git status --short
git branch --show-current
git log -1 --oneline
```

预期：

```text
working tree clean
current branch = 当前新的 Step 7 分支
HEAD = 66a69c6 或其后仅有授权的分支提交
```

本步骤结束时：

允许新增：

```text
docs/evaluation/Phase 4.2 Step 7A — Conversation Context Boundary Audit.md
```

除此之外：

```text
Backend changes = 0
Test changes = 0
DB changes = 0
API changes = 0
```

---

# 二十、最终报告

严格输出：

```text
【Phase 4.2 Step 7A COMPLETE】

1. Branch
2. HEAD
3. Working Tree

4. Current Context Flow

5. Current Turn Exclusion
6. Historical Ordering
7. Retry Context
8. Duplicate Context
9. EMPTY / FAILED
10. Archived Conversation
11. Context Window
12. Prompt Boundary
13. Side Effects

14. Contract Gaps
15. Step 7 Contract

16. New Files
17. Backend Changes
18. Test Changes
19. DB Changes
20. API Changes

21. Step 6 Regression
22. Recommendation

23. STOP
```

最后：

```text
Phase 4.2 Step 7A 到此停止。
不要进入 Step 7B。
不要修改任何生产代码。
```
