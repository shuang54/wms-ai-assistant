# Phase 4.2 Step 7E：Context Selection Implementation

你现在开始执行：

**Phase 4.2 Step 7E — Conversation Context Selection Implementation**

项目：

```text
D:\coding\ai\wms-ai-assistant
```

当前分支：

```text
phase4.1-3
```

当前 HEAD：

```text
7f50f70
```

Step 6：

```text
66a69c6
```

Step 7A / 7B / 7C / 7D 已完成。

---

# 一、阶段目标

本阶段只实现：

```text
Conversation History
        ↓
Current Turn Exclusion
        ↓
History Ordering
        ↓
Context Selection
        ↓
ConversationContextBuilder
```

也就是：

> **把 Phase 4.2 Step 7C 已经冻结的 Context Window / Selection Contract 真正实现。**

本阶段**不接入 Router / RAG / Text-to-SQL**。

本阶段结束后：

```text
Selection Layer
```

应该已经可以独立、确定性地返回：

```text
Selected Conversation Turns
```

供下一阶段 Prompt Consumption 使用。

---

# 二、严格范围

## 允许修改

优先限制在：

```text
backend/app/db/conversation_repository.py
backend/app/services/chat_application_service.py
backend/app/services/conversation_context_builder.py
```

如果当前项目结构存在更自然的 Selection Service：

可以新增：

```text
backend/app/services/conversation_context_selection_service.py
```

或者：

```text
backend/app/services/conversation_context_selector.py
```

但：

**只有真实需要才新增。**

允许新增：

```text
tests/test_conversation_context_selection.py
```

或者根据当前测试结构修改已有：

```text
tests/test_conversation_context_builder.py
```

允许新增：

```text
docs/evaluation/Phase 4.2 Step 7E — Context Selection Implementation.md
```

如果项目已有更合适的 evaluation 文档目录，则遵循现有结构。

Roadmap：

```text
docs/decisions/Phase/Phase 4.2 Roadmap v1.0.md
```

只允许：

```text
append-only
```

---

# 三、严格禁止

本阶段禁止修改：

```text
AI Router
RAG Service
TextToSQLService
TextToSQLContext
SQL Validator
SQL Executor
Tool Framework
Business Semantic
Project Context
Prompt files
Prompt templates
DeepSeek Provider
LLM Provider
```

禁止：

```text
Router Context Consumption
RAG Context Consumption
Text-to-SQL Context Consumption
Tool Context Consumption
```

禁止：

```text
OD-35 Query Understanding
OD-36 Context Placement
OD-38 configurable window
OD-39 observability
Memory
Summary
Embedding
Reranker
LLM history selector
Agent
LangGraph
MCP
Workflow
```

禁止修改：

```text
Step 6 Idempotency Contract
```

禁止修改：

```text
MAX_TURNS = 20
MAX_CONTEXT_CHARS = 12000
```

禁止新增环境变量。

禁止新增数据库字段。

禁止 migration。

禁止修改数据库结构。

禁止调用真实 LLM。

禁止网络请求。

---

# 四、Step 7C 冻结 Contract

必须严格实现：

```text
MAX_TURNS = 20
MAX_CONTEXT_CHARS = 12000
```

单位：

```text
turn
```

而不是：

```text
pair
message pair
token
```

---

# 五、Selection Pipeline

严格按照：

```text
All Conversation Turns
        ↓
Exclude current turn_id
        ↓
ORDER BY created_at ASC, turn_id ASC
        ↓
History Selection
        ↓
Selected Turns
        ↓
ConversationContextBuilder
```

不能：

```text
All Turns
 ↓
Select Window
 ↓
Exclude Current Turn
```

顺序必须保持：

```text
Exclude
→ Order
→ Select
```

---

# 六、Current Turn Exclusion

当前 USER Turn 必须在 Selection 之前排除。

例如：

```text
U1
A1
U2 ← current turn
```

History：

```text
U1
A1
```

不能出现：

```text
U2
```

---

# 七、Retry Contract

Step 6 已经规定：

同一个 `Idempotency-Key` retry：

```text
reuse existing USER turn
```

因此 Selection 时：

```text
current turn_id
```

仍然必须被排除。

例如：

```text
U1
A1
U2 ← reused USER turn
```

Retry context：

```text
U1
A1
```

不能：

```text
U1
A1
U2
```

也不能出现：

```text
U2
U2
```

---

# 八、Duplicate Contract

Completed duplicate：

```text
USER + ASSISTANT already exists
```

Step 6 的 MessageReplay 必须继续短路：

```text
resolve duplicate
        ↓
MessageReplay
        ↓
return
```

不能：

```text
duplicate
 ↓
history query
 ↓
context selection
 ↓
builder
 ↓
AI
```

所以必须保证：

```text
Duplicate = 0 context selection
```

不要改变 Step 6 行为。

---

# 九、Ordering Contract

Repository 当前已有：

```sql
ORDER BY created_at ASC, turn_id ASC
```

必须继续保持。

Selection 层：

**不要重新排序。**

也不要使用：

```python
sorted(...)
```

除非真实 Repository 返回顺序无法保证。

理想结构：

```text
Repository
    ↓
already ordered history
    ↓
Selection
    ↓
preserve order
```

---

# 十、Selection Algorithm

严格实现 Step 7C §16.4 的算法：

### 输入

```text
ordered history
current_turn_id
max_turns = 20
max_context_chars = 12000
```

---

### Step 1

排除：

```text
current_turn_id
```

---

### Step 2

从最新历史 turn 开始：

```text
newest → oldest
```

---

### Step 3

逐个判断：

```text
candidate.content length
```

注意：

这里使用：

```python
len(content)
```

即：

**Python Unicode 字符数。**

不要：

```text
bytes
tokens
tiktoken
model tokenizer
```

---

### Step 4

同时满足：

```text
selected_turn_count < 20
```

以及：

```text
selected_content_chars + candidate.content_chars
<= 12000
```

才能加入普通历史 turn。

---

# 十一、Oversized Message

特殊规则：

如果：

```text
candidate.content_chars > 12000
```

并且它是：

```text
newest selected history turn
```

那么：

**必须保留整个 turn。**

不得：

```text
truncate
slice
substring
summary
```

即：

```text
newest oversized turn
        ↓
FULL CONTENT
```

此时允许：

```text
content_chars > 12000
```

但：

```text
不允许截断。
```

---

# 十二、Older Oversized Message

如果一个历史 turn：

```text
content_chars > 12000
```

但它不是当前 newest candidate：

则：

```text
停止选择
```

不能跳过去继续选择更老的消息。

也就是说：

```text
newest
  ↓
candidate too large
  ↓
STOP
```

不能：

```text
newest
  ↓
too large → skip
  ↓
older → continue
```

这样保证：

```text
Selected Turns
```

始终是：

```text
history 的连续后缀
```

不能产生：

```text
U1
A1
U3
```

这种空洞窗口。

---

# 十三、USER-Anchored Window

必须保持：

```text
USER-anchored
```

但这里的含义不是：

```text
只选择 USER
```

而是：

> **窗口从最新历史开始，按照完整 turn 顺序向前选择，不破坏 Conversation Turn 的连续性。**

例如：

```text
U1
A1
U2
A2
U3
A3
U4 current
```

current 排除后：

```text
U1
A1
U2
A2
U3
A3
```

如果预算只能容纳：

```text
U2
A2
U3
A3
```

则结果必须是：

```text
U2
A2
U3
A3
```

恢复为：

```text
oldest → newest
```

---

# 十四、Selected Result Ordering

虽然选择过程是：

```text
newest → oldest
```

最终必须：

```text
oldest → newest
```

例如：

选择：

```text
A3
U3
A2
U2
```

最终：

```text
U2
A2
U3
A3
```

不能直接把 reverse-order 结果交给 Builder。

---

# 十五、EMPTY / FAILED

必须保持 Step 7C Decision：

```text
EMPTY = 保留 USER Turn
FAILED = 保留 USER Turn
```

原因：

当前 DB 没有 execution status：

```text
EMPTY / FAILED USER Turn
```

和普通 USER Turn 在历史结构上不可区分。

所以 Selection：

**不要尝试识别。**

不要新增：

```text
status
execution_status
failure_status
```

不要暴露：

```text
exception
request_id
error_code
internal status
```

Builder 最终只能看到：

```text
role
content
```

---

# 十六、Builder Responsibility

本阶段不要让 Builder 承担 Selection。

Builder 继续保持：

```text
Selected Turns
        ↓
pure formatting
        ↓
context string
```

Builder 不负责：

```text
DB query
current turn exclusion
window size
char budget
ranking
token counting
summary
```

这是本阶段非常重要的架构边界。

---

# 十七、建议结构

如果需要新增 Selection Service：

推荐：

```python
class ConversationContextSelectionService:
    def select(
        self,
        turns: Sequence[ConversationTurn],
        *,
        current_turn_id: UUID,
        max_turns: int = 20,
        max_context_chars: int = 12000,
    ) -> list[ConversationTurn]:
        ...
```

但：

**不要机械照抄。**

必须先检查当前项目 DTO / Repository / Service 风格。

如果已有自然的 service boundary：

直接扩展现有结构。

---

# 十八、配置来源

本阶段：

**不要新增配置。**

先使用：

```text
MAX_TURNS = 20
MAX_CONTEXT_CHARS = 12000
```

可以：

```python
CONVERSATION_CONTEXT_MAX_TURNS = 20
CONVERSATION_CONTEXT_MAX_CHARS = 12000
```

但必须是代码常量或当前已有配置体系中的常量。

不要：

```text
.env
settings
environment variable
```

本阶段不要实现可配置性。

可配置性属于：

```text
OD-38
```

---

# 十九、测试要求

必须覆盖 Step 7C 的：

```text
T-7C-1
T-7C-2
T-7C-3
T-7C-4
T-7C-5
T-7C-6
T-7C-7
T-7C-8
T-7C-9
T-7C-10
T-7C-11
T-7C-12
```

至少具体验证：

### T-7C-1

current turn 不进入 context。

### T-7C-2

retry 不重复 current USER。

### T-7C-3

duplicate 不执行 selection / builder / AI。

### T-7C-4

历史排序稳定：

```text
created_at ASC
turn_id ASC
```

### T-7C-5

同样输入连续执行两次：

```text
selected turns identical
context identical
```

### T-7C-6

20 turn 限制。

### T-7C-7

12000 chars 限制。

### T-7C-8

oversized newest turn：

```text
完整保留
```

### T-7C-9

oversized older turn：

```text
停止
```

### T-7C-10

EMPTY / FAILED USER：

```text
保留原始 content
```

### T-7C-11

连续后缀：

```text
selected turns
```

不能出现空洞。

### T-7C-12

空历史：

```text
context = None
```

---

# 二十、额外测试

建议增加：

## 1. Exactly 20 turns

```text
20 → all fit
21 → only latest 20
```

## 2. Exactly 12000 chars

```text
12000 → fit
12001 → does not fit
```

## 3. Mixed role

```text
USER
ASSISTANT
USER
ASSISTANT
```

顺序不能改变。

## 4. Same timestamp

多个 turn：

```text
created_at == same
```

必须通过：

```text
turn_id ASC
```

稳定排序。

## 5. Unicode

中文：

```text
查询采购入库单
```

emoji：

```text
📦
```

不能使用 byte length。

## 6. Empty content

如果数据库允许：

```text
content = ""
```

必须保持现有系统语义。

不要自行删除。

---

# 二十一、禁止脆弱测试

不要写：

```python
assert len(context) < 12000
```

作为唯一测试。

因为：

```text
Builder formatting overhead
```

已经被 Step 7C 明确定义。

应该测试：

```text
selected content chars
```

和：

```text
formatted context
```

分别验证。

---

# 二十二、Regression

实现后至少运行：

```powershell
python -m pytest -q tests/test_conversation_context_builder.py
python -m pytest -q tests/test_conversation_context_selection.py
python -m pytest -q tests/test_conversation_message_idempotency.py
```

如果没有新建：

```text
test_conversation_context_selection.py
```

则使用实际测试文件。

DB：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_conversation_message_idempotency_db.py
```

然后：

```powershell
python -m pytest -q
```

但如果 full DB 已知存在 Step 6 的历史 residue-order 问题：

必须分别报告：

```text
isolated DB tests
full DB tests
```

不要修改历史 baseline 来“修复”它。

---

# 二十三、编译

执行：

```powershell
python -m compileall backend tests
```

要求：

```text
0 errors
```

---

# 二十四、Scope Audit

完成后执行：

```powershell
git diff -- backend
git diff -- tests
git diff --stat
git status --short
```

确认：

### Backend

只允许：

```text
Conversation Context Selection
```

相关变化。

### Tests

只允许：

```text
Context Selection
```

相关测试。

禁止出现：

```text
Router
RAG
Text-to-SQL
Tool
Prompt
SQL Validator
SQL Executor
```

相关修改。

---

# 二十五、Architecture Invariants

实现完成后必须验证：

```text
I1 current turn excluded before selection
I2 selected turns are complete turns
I3 selected turns form a contiguous suffix
I4 final ordering oldest → newest
I5 max turns <= 20
I6 content budget <= 12000 unless newest oversized
I7 newest oversized turn is never truncated
I8 older oversized turn stops selection
I9 Builder remains pure
I10 no DB write
I11 no LLM
I12 no network
I13 idempotency_key never enters context
I14 request_id never enters context
I15 turn_id never enters context
I16 conversation_id never enters context
```

---

# 二十六、Step 6 Boundary Regression

特别检查：

```text
conversation_turn.idempotency_key
```

不能进入：

```text
ConversationContextBuilder
```

不能出现在：

```text
context
```

不能出现在：

```text
Prompt
```

当前 turn 的：

```text
turn_id
```

只能用于：

```text
selection exclusion
```

不能进入 context 内容。

---

# 二十七、Step 7D Boundary Regression

本阶段：

**不实现 Prompt Consumption。**

因此：

```text
Router = unchanged
RAG = unchanged
Text-to-SQL = unchanged
Tool = unchanged
```

只证明：

```text
Selection Layer
```

可以正确提供：

```text
Selected Turns
```

下一阶段再进行：

```text
Selection
 ↓
Builder
 ↓
Prompt Consumption
```

---

# 二十八、文档

新增：

```text
docs/evaluation/Phase 4.2 Step 7E — Context Selection Implementation.md
```

记录：

```text
1. Scope
2. Selection Architecture
3. Algorithm
4. Current Turn Exclusion
5. Retry
6. Duplicate
7. Window Limits
8. Oversized Message
9. EMPTY / FAILED
10. Builder Boundary
11. Tests
12. Security
13. Step 6 Regression
14. Step 7D Regression
15. Limitations
16. STOP
```

必须明确：

```text
Context Selection implemented.
Prompt Consumption NOT implemented.
```

---

# 二十九、Roadmap

继续：

```text
docs/decisions/Phase/Phase 4.2 Roadmap v1.0.md
```

只允许 append-only。

新增：

```text
§27 Step 7E
```

记录：

```text
Step 7E COMPLETE
Context Selection implemented
```

不得修改：

```text
§24
§25
§26
```

---

# 三十、最终报告

严格按照：

```text
【Phase 4.2 Step 7E COMPLETE】

1. Branch
2. HEAD
3. Working Tree

4. Selection Architecture
5. Algorithm
6. MAX_TURNS
7. MAX_CONTEXT_CHARS
8. Current Turn
9. Retry
10. Duplicate
11. Oversized Message
12. EMPTY / FAILED

13. New / Modified Files

14. Tests
- Context Selection
- Context Builder
- Idempotency
- DB

15. compileall

16. Architecture Invariants
- I1...
- I16...

17. Step 6 Boundary
18. Step 7D Boundary

19. Backend Changes
20. Test Changes
21. DB Changes
22. API Changes
23. Prompt Changes

24. Git Diff

25. Limitations

26. STOP
```

---

# 三十一、STOP

完成后：

**立即停止。**

不要进入下一步。

不要：

```text
修改 Router
修改 RAG
修改 Text-to-SQL
修改 Tool
修改 Prompt
实现 Context Consumption
实现 Query Understanding
实现 Memory
实现 Summary
实现 OD-35
实现 OD-38
实现 OD-39
```

不要 commit。

只返回：

```text
【Phase 4.2 Step 7E COMPLETE】
```

然后停止。
