继续 Phase 4.1。

当前状态：

```text
Step 10 COMPLETE
Step 11 COMPLETE
Step 12 COMPLETE
Step 13 COMPLETE
```

当前已经存在：

```text
ConversationService
ChatApplicationService
ConversationContextBuilder
Conversation API
AIOrchestrator
```

现在只执行：

## Phase 4.1 Step 14：ChatApplicationService 接入 Conversation Context

## 一、唯一目标

把 Step 13 已完成的：

```text
Conversation History
        ↓
ConversationContextBuilder
        ↓
str | None
```

真正接入：

```text
ChatApplicationService
        ↓
ConversationService.list_turns()
        ↓
Context Builder
        ↓
AIOrchestrator.execute(
    question,
    context=context
)
```

最终形成：

```text
POST /api/conversations/{conversation_id}/messages
        ↓
ChatApplicationService
        ↓
读取历史
        ↓
构建 Context
        ↓
AIOrchestrator
        ↓
ASSISTANT Turn
```

---

# 二、严格范围

允许修改：

```text
backend/app/services/chat_application_service.py
tests/test_chat_application_service.py
```

如果当前 Context Builder 的 import / 类型需要极小调整，可以修改：

```text
backend/app/services/conversation_context_builder.py
```

但原则上：

> Step 13 的 Context Builder 已完成，本 Step 不重新设计它。

---

# 三、严格禁止

本阶段禁止修改：

```text
ConversationService
ConversationRepository
Conversation ORM
Conversation API
Conversation DTO
AIOrchestrator
AIRouter
RAG
Tool Framework
TextToSQL
SQLValidator
SQLExecutor
LLMProvider
LLMClient
Prompt
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
```

禁止：

```text
DeepSeek
SiliconFlow
真实 PostgreSQL
```

不要修改：

```text
POST /api/conversations/{conversation_id}/messages
```

的 HTTP Contract。

---

# 四、开始前必须阅读

先阅读：

```text
backend/app/services/chat_application_service.py
backend/app/services/conversation_service.py
backend/app/services/conversation_context_builder.py
backend/app/services/ai_orchestrator_service.py

tests/test_chat_application_service.py
tests/test_conversation_context_builder.py
```

确认真实：

```text
ConversationService.list_turns()
ConversationContextBuilder.build_context()
AIOrchestrator.execute()
```

签名。

不要假设接口。

---

# 五、Context 接线位置

Step 11 当前流程：

```text
get conversation
    ↓
status guard
    ↓
append USER
    ↓
AI execute
    ↓
append ASSISTANT
```

修改为：

```text
get conversation
    ↓
status guard
    ↓
append USER
    ↓
list history
    ↓
exclude current USER turn
    ↓
build_context(history)
    ↓
AI execute(question, context=context)
    ↓
append ASSISTANT
```

注意：

**Context Builder 不负责排除 current turn。**

这是 ChatApplicationService 的职责。

---

# 六、Current USER Turn

非常重要。

流程：

```text
append USER
```

之后：

```text
list_turns(conversation_id)
```

会得到：

```text
历史 USER/ASSISTANT
+
刚刚追加的当前 USER
```

因此必须：

```text
history = turns[:-1]
```

或者使用更明确的：

```text
exclude current turn_id
```

推荐：

> 根据刚刚 append USER 返回的 `ConversationTurn` 对象/turn_id 排除当前 Turn。

不要依赖：

```text
turns[-1]
```

来猜测当前 Turn，除非当前 ConversationService contract 明确保证 ordering。

---

# 七、Context 不包含 Current Question

最终调用：

```python
orchestrator.execute(
    user_turn.content,
    context=context,
)
```

因此：

```text
question
```

是当前用户问题。

而：

```text
context
```

只包含：

```text
previous conversation history
```

不要把当前 USER 同时放进：

```text
question
+
context
```

造成重复。

---

# 八、First Turn

新 Conversation：

```text
USER: 查询库存
```

执行时：

```text
history = []
```

因此：

```text
context = None
```

调用：

```text
execute(
    "查询库存",
    context=None,
)
```

必须保持。

不要：

```text
context="None"
```

不要：

```text
context=""
```

不要：

```text
context="user: 查询库存"
```

---

# 九、Second Turn

已有：

```text
USER: 查询库存
ASSISTANT: 当前库存...
```

用户发送：

```text
查询其中库存最低的10个
```

执行时：

```text
context
=
"user: 查询库存\nassistant: 当前库存..."
```

具体格式：

**必须复用 Step 13 `build_context()` 当前真实输出。**

ChatApplicationService 不自己拼字符串。

---

# 十、Context Builder 唯一入口

ChatApplicationService 只能调用：

```text
build_context(history)
```

不要：

```text
build_messages(history)
```

然后自行转换成字符串。

原因：

Step 13 已经确定：

```text
AIOrchestrator context = str | None
```

所以：

```text
build_context()
```

是当前生产接线入口。

`build_messages()` 暂时只是未来结构化 context 的扩展，不接入。

---

# 十一、AI Execution

修改后的真实调用必须是：

```python
orchestrator.execute(
    user_turn.content,
    context=context,
)
```

其中：

```text
project_id
```

继续来自：

```text
conversation.project_id
```

仍然使用 Step 11 的：

```text
orchestrator_factory(project_id)
```

不要重新设计 Project Binding。

---

# 十二、Repository / DB 边界

ChatApplicationService：

允许通过：

```text
ConversationService
```

调用：

```text
get_conversation()
append_turn()
list_turns()
```

但禁止直接：

```text
Repository
Session
SQLAlchemy
```

保持：

```text
ChatApplicationService
        ↓
ConversationService
        ↓
Repository
```

---

# 十三、Transaction Boundary

保持 Step 11：

```text
TX1
append USER
COMMIT
        ↓
list history
        ↓
Context Builder
        ↓
AI execution
        ↓
TX2
append ASSISTANT
COMMIT
```

注意：

```text
list_turns()
```

可以发生在 TX1 commit 后。

Context Builder 是纯内存操作。

AI execution 不应该包在 Conversation DB transaction 中。

---

# 十四、History Read Failure

如果：

```text
append USER
```

成功，

但是：

```text
list_turns()
```

失败：

必须：

```text
AI = 0
ASSISTANT = 0
USER turn = retained
```

异常原样传播。

不要：

```text
→ []
```

不要：

```text
→ context=None
```

不要为了让 AI 继续执行而忽略 History DB failure。

这是非常重要的数据一致性边界。

---

# 十五、Context Builder Failure

如果：

```text
build_context(history)
```

抛出异常：

必须：

```text
AI = 0
ASSISTANT = 0
USER turn = retained
```

异常原样传播。

不要重新包装成：

```text
ContextBuildError
```

除非 Step 13 已经定义了现有异常。

---

# 十六、AI Failure

保持 Step 11：

```text
USER committed
        ↓
history read
        ↓
context built
        ↓
AI failure
        ↓
USER retained
ASSISTANT = 0
```

异常原样传播。

不要 retry。

---

# 十七、ASSISTANT Turn

AI 成功后仍然保持 Step 11：

只有：

```text
result.content is str
AND
result.content.strip()
```

非空时：

```text
append ASSISTANT
```

仍然：

```text
assistant_request_id = result.metadata["request_id"]
```

不要改变。

---

# 十八、REFUSED

保持：

```text
REFUSED
```

如果：

```text
content
```

存在：

创建 ASSISTANT Turn。

如果：

```text
content=None
```

不创建空 Assistant Turn。

Context 接线不得改变 refusal semantics。

---

# 十九、EMPTY

例如：

```text
RAG
outcome=EMPTY
content=None
```

保持：

```text
USER retained
ASSISTANT=0
```

如果有可展示 content，则按照 Step 11 规则决定是否创建 Assistant Turn。

不要把：

```text
EMPTY
```

转成：

```text
FAILED
```

---

# 二十、T2SQL row_count=0

保持：

```text
SUCCESS
```

如果：

```text
content
```

非空：

创建 ASSISTANT Turn。

不要因为：

```text
row_count=0
```

跳过 Assistant Turn。

---

# 二十一、最重要的新测试：History Context

新增测试：

```text
test_second_turn_passes_previous_history_to_orchestrator
```

构造：

```text
USER:
查询库存

ASSISTANT:
当前库存为 100 件
```

然后：

```text
第二次 USER:
查询其中库存最低的10个
```

验证：

```text
orchestrator.execute.call_args
```

必须：

```text
question =
"查询其中库存最低的10个"

context =
Step13 build_context(
    [
        USER("查询库存"),
        ASSISTANT("当前库存为 100 件"),
    ]
)
```

必须：

```text
当前第二次 USER
```

**不能出现在 context。**

---

# 二十二、First Turn Test

验证：

```text
new conversation
↓
USER
↓
list_turns → only current USER
↓
exclude current
↓
context=None
```

断言：

```text
execute(..., context=None)
```

---

# 二十三、History Ordering

构造：

```text
USER1
ASSISTANT1
USER2
ASSISTANT2
USER3(current)
```

最终 context 必须只有：

```text
USER1
ASSISTANT1
USER2
ASSISTANT2
```

顺序完全保持。

---

# 二十四、Metadata Isolation

继续验证：

历史 Turn 中即使存在：

```text
assistant_request_id
turn_id
conversation_id
created_at
```

也不能进入：

```text
context
```

因为 Context Builder 已经保证只读取：

```text
role
content
```

---

# 二十五、Project Binding Regression

确认：

```text
conversation.project_id = project-A
```

则：

```text
orchestrator_factory("project-A")
```

必须被调用。

即使用户消息中出现：

```text
project-B
```

也不能改变。

---

# 二十六、测试范围

修改：

```text
tests/test_chat_application_service.py
```

至少增加：

```text
1. first turn context=None
2. second turn history context
3. current USER excluded
4. multiple history ordering
5. history read failure
6. context builder failure
7. AI receives context
8. project binding unchanged
9. refusal regression
10. empty regression
11. row_count=0 regression
12. assistant turn regression
13. user append failure regression
14. assistant append failure regression
```

继续保留 Step 11 原有 32 tests。

---

# 二十七、Architecture Audit

增加或扩展：

```text
tests/test_chat_application_service_architecture_audit.py
```

确认：

```text
ChatApplicationService
```

允许依赖：

```text
ConversationService
ConversationContextBuilder
AIOrchestrator / Protocol / Factory
```

禁止：

```text
ConversationRepository
SQLAlchemy
psycopg
RAG
Tool
T2SQL
LLMClient
Prompt
Embedding
Reranker
```

并确认：

```text
ConversationContextBuilder
```

不反向 import：

```text
ChatApplicationService
ConversationService
AIOrchestrator
```

避免循环依赖。

---

# 二十八、不要修改 Context Builder

除非测试发现：

```text
真实 contract 不兼容
```

否则：

```text
conversation_context_builder.py
```

本 Step 不修改。

Step 13 已经完成。

---

# 二十九、不要修改 API

Step 12 API 保持完全不变：

```text
POST /api/conversations/{conversation_id}/messages
```

Request：

```json
{
  "content": "..."
}
```

仍然只有：

```text
content
```

Context 不暴露给客户端。

---

# 三十、DB / Network / LLM

本 Step：

```text
DB = 0
Network = 0
LLM = 0
```

全部使用 Fake：

```text
ConversationService
Context Builder
Orchestrator
```

不要：

```text
RUN_DB_TESTS
```

不要 DeepSeek。

不要 SiliconFlow。

---

# 三十一、测试命令

只运行：

```powershell
python -m pytest -q tests/test_chat_application_service.py
```

然后：

```powershell
python -m pytest -q tests/test_chat_application_service_architecture_audit.py
```

以及：

```powershell
python -m compileall -q backend tests
```

不要：

```text
pytest -q
RUN_DB_TESTS=1
```

---

# 三十二、Git Diff

检查：

```powershell
git status --short
git diff --stat
git diff
```

本 Step 理想修改：

```text
Modified:
backend/app/services/chat_application_service.py
tests/test_chat_application_service.py
tests/test_chat_application_service_architecture_audit.py
```

不要修改：

```text
conversation_service.py
conversation_context_builder.py
conversation_api.py
conversation_api DTO
ai_orchestrator_service.py
```

---

# 三十三、最终验收

必须满足：

```text
□ First turn → context=None
□ Second turn → previous history context
□ Current USER excluded
□ History ordering preserved
□ Context Builder is only history→context converter
□ ChatApplicationService does not manually concatenate history
□ ConversationService owns history read
□ AIOrchestrator receives context keyword
□ Project binding unchanged
□ History read failure → AI=0
□ Context build failure → AI=0
□ AI failure → USER retained / ASSISTANT=0
□ Refusal unchanged
□ EMPTY unchanged
□ T2SQL row_count=0 unchanged
□ Assistant turn unchanged
□ API unchanged
□ No DB
□ No Network
□ No LLM
□ Architecture audit passed
□ Targeted tests passed
□ compileall passed
```

---

# 三十四、最终报告

严格输出：

```text
Phase 4.1 Step 14 完成报告

1. Context Integration
2. First Turn
3. Second Turn
4. Current USER Exclusion
5. History Ordering
6. Context Builder Boundary
7. Project Binding
8. History Read Failure
9. Context Build Failure
10. AI Failure
11. REFUSED
12. EMPTY
13. T2SQL row_count=0
14. Assistant Turn
15. Architecture Audit
16. Tests
17. compileall
18. DB / Network / LLM
19. Git Diff
20. Production Code Changes
21. Current Limitations

Phase 4.1 Step 14 READY
Phase 4.1 Step 14 STOP
```

必须明确：

```text
Conversation History → Context Builder = 已接线
ChatApplicationService → Context = 已接线
AIOrchestrator.execute(context=...) = 已启用
Conversation API = 未改变
Memory = 未实现
Summary = 未实现
Token Budget = 未实现
```

完成后立即 STOP。

不要进入 Step 15。

不要实现 Memory / Summary / Regenerate / Auth / Pagination。
