继续 Phase 4.1。

当前状态：

```text
Step 10  Chat Application Boundary Audit       COMPLETE
Step 11  ChatApplicationService                COMPLETE
Step 12  Conversation Message API              COMPLETE
Step 13  ConversationContextBuilder             COMPLETE
Step 14  Context → AIOrchestrator 接线          COMPLETE
Step 15  Multi-turn PostgreSQL E2E               COMPLETE
```

Step 15 已经通过：

```text
15 passed
0 failed
真实 PostgreSQL
FastAPI TestClient
真实 Conversation Persistence
真实 Context Builder
Fake AIOrchestrator
```

当前明确限制：

```text
无 Token Budget
无 Context Truncation
无 Summary
无 Memory
```

现在只执行：

# Phase 4.1 Step 16：Conversation Context Budget / Truncation Audit

本步骤：

**只做 Audit + Contract Design。**

不要实现 truncation。

不要修改 Context Builder。

不要修改 ChatApplicationService。

---

# 一、阶段目标

回答一个核心问题：

> 当 Conversation 有几十、几百、几千条历史消息时，当前 Context Builder 是否会无限增长，以及未来应该在哪一层建立 Context Budget / Truncation 边界？

当前：

```text
Conversation DB
      ↓
ConversationService.list_turns()
      ↓
ChatApplicationService
      ↓
ConversationContextBuilder
      ↓
AIOrchestrator
```

目前是：

```text
全部历史
    ↓
全部进入 context
```

本步骤需要冻结未来设计边界：

```text
Conversation History
        ↓
Context Selection / Budget
        ↓
Conversation Context Builder
        ↓
AIOrchestrator
```

---

# 二、严格禁止

本步骤禁止修改：

```text
backend/app/services/conversation_context_builder.py
backend/app/services/chat_application_service.py
backend/app/services/conversation_service.py
backend/app/db/conversation_repository.py
backend/app/services/ai_orchestrator_service.py
```

禁止：

```text
Memory
Summary
Vector Search
Embedding
Reranker
LLM Summary
Tokenizer
tiktoken
DeepSeek
SiliconFlow
```

禁止：

```text
数据库 Schema 修改
数据库 Migration
数据库写入
API Contract 修改
Conversation API 修改
```

禁止：

```text
新增 Context Builder
新增 Memory Service
新增 Summary Service
```

---

# 三、开始前阅读真实代码

先阅读：

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

确认当前真实：

```text
Turn DTO
Context Builder Contract
ChatApplicationService workflow
History ordering
AIOrchestrator context 参数
```

不要根据之前阶段报告猜接口。

---

# 四、当前 Context Contract

确认：

```python
ConversationContextBuilder.build_context(turns)
```

当前语义：

```text
empty history
    ↓
None

history
    ↓
"user: ...\nassistant: ..."
```

确认：

1. USER → user
2. ASSISTANT → assistant
3. 顺序保持
4. content 原样
5. 当前 USER 不进入 context
6. 没有 metadata
7. 没有 request_id
8. 没有 timestamp
9. 没有 SQL
10. 没有 RAG chunks

本步骤不得改变这些行为。

---

# 五、Context Growth Audit

离线构造：

```text
1 turn
10 turns
50 turns
100 turns
500 turns
1000 turns
```

不要调用 LLM。

不要访问 DB。

只使用：

```text
ConversationTurn
```

或者当前项目已有测试 DTO。

记录：

```text
turn_count
context_length
```

目的只是证明：

```text
context size
```

随历史增长。

不要建立 benchmark。

不要做性能优化。

---

# 六、不要使用假 token 数

非常重要。

本步骤禁止：

```text
len(text) / 4
len(text) / 3
字符数 × 某比例
```

推导：

```text
token count
```

因为不同 tokenizer 会产生不同结果。

本阶段只记录：

```text
character length
```

或者：

```text
context string length
```

如果需要未来 Token Budget：

记录：

```text
Token counting = deferred
```

不要引入 tokenizer。

---

# 七、未来 Context Budget 的责任边界

形成明确设计：

```text
Conversation History
        ↓
Context Budget / Selection
        ↓
ConversationContextBuilder
        ↓
AIOrchestrator
```

明确：

### ConversationService

负责：

```text
保存历史
读取历史
排序
```

不负责：

```text
LLM context truncation
token counting
memory
summary
```

---

### ConversationContextBuilder

当前职责：

```text
Turn DTO
    ↓
LLM-readable context string
```

未来可以继续负责：

```text
selected turns
    ↓
context formatting
```

但：

> 不建议让 Builder 同时负责复杂的 token budget strategy。

---

### ChatApplicationService

未来更适合作为：

```text
History
 ↓
Context Selection
 ↓
Context Builder
 ↓
AI
```

的 orchestration 层。

但：

**本步骤不实现。**

---

# 八、Truncation Policy Audit

比较至少三种未来策略：

## Strategy A：最近 N turns

```text
最新历史
    ↓
保留最近 N 条
```

优点：

```text
简单
确定性
低成本
```

缺点：

```text
早期业务上下文可能丢失
```

---

## Strategy B：最近 N + 首条

```text
第一轮
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
```

---

## Strategy C：Token Budget

```text
从最近历史开始
    ↓
逐条加入
    ↓
达到 token budget
    ↓
停止
```

优点：

```text
更接近真实 LLM context limit
```

缺点：

```text
需要 tokenizer / token estimation
```

本阶段只比较，不实现。

---

# 九、System / User / Assistant Boundary

未来 Context Budget 必须明确：

当前：

```text
system prompt
```

不在 Conversation History。

Conversation DB 只有：

```text
USER
ASSISTANT
```

因此：

```text
Conversation History Budget
```

不应该吞掉：

```text
System Prompt
Project Context
Tool Definitions
```

未来应该：

```text
System Context
+
Conversation Context
+
Current User Message
```

分别管理。

本步骤不修改 AIOrchestrator。

---

# 十、Current USER Boundary

再次确认：

```text
ChatApplicationService
```

已经：

```text
USER turn 写 DB
      ↓
读取 previous turns
      ↓
排除 current USER
      ↓
build context
      ↓
AI execute(current content, context)
```

因此未来 truncation 也必须基于：

```text
previous turns
```

而不是：

```text
current user + previous turns
```

否则会产生：

```text
current question duplicated
```

必须写成 Contract Test。

---

# 十一、Failure Semantics

未来 Context Budget 失败时：

不要：

```text
自动调用 LLM
```

不要：

```text
自动降级成全部历史
```

不要：

```text
吞掉异常
```

需要冻结未来原则：

```text
Context selection failure
    ↓
business failure
```

但：

本步骤只记录设计，不实现。

---

# 十二、Determinism

相同：

```text
turn history
budget configuration
```

未来必须：

```text
same selected history
same context
```

不能依赖：

```text
current time
random
LLM
DB iteration order
```

当前历史顺序已经由：

```text
created_at ASC
turn_id ASC
```

保证。

---

# 十三、Security

Context Budget / Truncation 不得引入：

```text
API key
Authorization
DATABASE_URL
password
SQLAlchemy Session
raw DB connection
```

Conversation context 可以包含：

```text
USER content
ASSISTANT content
```

这是允许的业务内容。

但是：

```text
assistant metadata
request_id
provider request_id
tool raw result
RAG chunks
```

当前都不能自动进入 context。

保持 Step 13 Contract。

---

# 十四、测试

新增：

```text
tests/test_conversation_context_budget_audit.py
```

本步骤只做 offline tests。

建议：

```text
8～12 tests
```

至少：

### Test 1

1 turn context length。

### Test 2

10 turns context length。

### Test 3

100 turns context length。

### Test 4

history ordering deterministic。

### Test 5

current USER exclusion。

### Test 6

USER / ASSISTANT role mapping。

### Test 7

context does not contain request_id。

### Test 8

context does not contain timestamp。

### Test 9

context does not contain SQL / RAG chunk / tool metadata。

### Test 10

no token estimation。

### Test 11

no DB dependency。

### Test 12

no network / LLM dependency。

如果已有测试已经覆盖某项：

复用，不重复堆测试。

---

# 十五、Architecture Audit

增加：

```text
tests/test_conversation_context_budget_architecture_audit.py
```

或者合并到：

```text
tests/test_conversation_context_budget_audit.py
```

根据现有项目习惯。

AST 检查：

```text
conversation_context_builder.py
```

不得新增：

```text
sqlalchemy
psycopg
openai
deepseek
siliconflow
tiktoken
transformers
redis
celery
kafka
```

注意：

不要使用全文字符串扫描。

docstring / comment 中出现词语不能算依赖。

---

# 十六、Documentation

新增：

```text
docs/evaluation/Phase 4.1 Step 16 — Context Budget Audit.md
```

记录：

## Current

```text
Conversation History
    ↓
Context Builder
    ↓
AI
```

## Future

```text
Conversation History
    ↓
Context Selection / Budget
    ↓
Context Builder
    ↓
AI
```

记录：

```text
Current context = full previous history
Token budget = not implemented
Truncation = not implemented
Summary = not implemented
Memory = not implemented
Tokenizer = not introduced
```

比较：

```text
Recent N
Recent N + First
Token Budget
```

不要选择最终实现方案。

本阶段只是 Audit。

---

# 十七、Regression

本步骤只运行：

```powershell
python -m pytest -q tests/test_conversation_context_budget_audit.py
```

然后：

```powershell
python -m compileall -q backend tests
```

不要：

```text
RUN_DB_TESTS=1
pytest -q
DeepSeek
SiliconFlow
PostgreSQL
```

---

# 十八、Git Diff

完成后：

```powershell
git status --short
git diff --stat
git diff -- backend
```

原则：

```text
backend production code = unchanged
```

允许：

```text
tests/
docs/
```

---

# 十九、最终报告

严格：

```text
Phase 4.1 Step 16 完成报告

1. Current Context Contract
2. Context Growth Audit
3. Token Counting
4. Current USER Exclusion
5. Future Budget Responsibility
6. Truncation Strategies
7. System/User/Assistant Boundary
8. Failure Semantics
9. Determinism
10. Security
11. Tests
12. Compileall
13. Production Code
14. DB / Network / LLM
15. Git Diff
16. Documentation
17. Current Limitations

Phase 4.1 Step 16 READY
Phase 4.1 Step 16 STOP
```

必须明确：

```text
Conversation Persistence = 已完成
Multi-turn E2E = 已完成
Context Builder = 已完成
Context Budget = Audit only
Truncation = 未实现
Summary = 未实现
Memory = 未实现
Tokenizer = 未引入
```

---

# 二十、硬停止

完成后立即 STOP。

不要进入：

```text
Step 17 Context Truncation
Step 18 Summary
Step 19 Memory
Regenerate
Streaming
Pagination
Auth
Agent
MCP
```

等待下一步指令。
