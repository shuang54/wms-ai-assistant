你现在开始执行：

# Phase 4.2 Step 2 — AI Result → Assistant Turn Semantics

项目：

`D:\coding\ai\wms-ai-assistant`

---

# 一、阶段目标

本 Step 只解决：

```text
OD-16：AI Result 持久化语义
OD-19：AI 失败 / 拒答语义
```

核心问题：

> 当前 `AIOrchestrationResult` 返回以后，什么时候应该创建 ASSISTANT ConversationTurn？什么情况下不应该创建？失败、拒答、空结果分别如何处理？

必须把：

```text
AIOrchestrationResult
        ↓
ConversationTurn
```

这一条边界定义清楚。

---

# 二、严格模式

本 Step 默认：

```text
ONLY CONTRACT ANALYSIS
```

允许：

* 阅读真实代码
* 分析现有行为
* 设计持久化契约
* 新增 Evaluation / Decision 文档
* 设计测试矩阵

禁止：

```text
❌ 修改 ChatApplicationService
❌ 修改 AIOrchestrationResult
❌ 修改 AIOrchestrator
❌ 修改 Router
❌ 修改 RAG
❌ 修改 Tool
❌ 修改 Text-to-SQL
❌ 修改 Evidence
❌ 修改 ConversationEvidence
❌ 修改 ConversationTurn ORM
❌ 修改 ConversationRepository
❌ 修改数据库
❌ 新增 migration
❌ 新增 API
❌ 实现 Idempotency-Key
❌ 实现 Evidence Builder
❌ 实现 Evidence Runtime
❌ Step 3
```

不要因为发现当前行为不完美，就直接修生产代码。

如果发现必须修改才能满足契约：

**停止并报告，不自行实现。**

---

# 三、必须先阅读真实实现

阅读：

```text
backend/app/services/chat_application_service.py
backend/app/services/ai_orchestrator_service.py
backend/app/db/models/conversation_turn.py
backend/app/db/conversation_repository.py
backend/app/api/
```

继续阅读：

```text
tests/
```

重点搜索：

```text
AIOrchestrationResult
route
content
data
metadata
outcome
refused
refusal
FAILED
ERROR
empty
ConversationTurn
assistant_request_id
execute_message
```

同时阅读：

```text
docs/evaluation/Phase 4.2 Step 1 — Message Idempotency Contract.md
docs/evaluation/Phase 4.2 Step 1A — OD-22 Idempotency Architecture Decision.md
docs/decisions/Phase/Phase 4.2 Roadmap v1.0.md
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
```

---

# 四、首先冻结当前真实行为

必须根据真实代码画出：

```text
User Message
    ↓
USER Turn
    ↓
AIOrchestrator.execute()
    ↓
AIOrchestrationResult
    ↓
Conditional ASSISTANT Turn
    ↓
HTTP Result
```

明确当前：

```text
CURRENT
MISSING
UNDEFINED
```

特别确认当前 `ChatApplicationService` 对以下情况的实际行为：

```text
content 非空
content 为空
request_id 存在
request_id 不存在
Orchestrator 正常返回
Orchestrator 抛异常
RAG 正常
Tool 正常
Text-to-SQL 正常
Text-to-SQL refusal
```

不要猜。

---

# 五、AIOrchestrationResult Contract

必须重新确认当前 DTO 的真实结构。

当前 Phase 4.2 不允许修改：

```text
AIOrchestrationResult
```

重点确认：

```text
route
content
data
metadata
```

以及：

```text
metadata.request_id
metadata.outcome
```

或者当前代码实际使用的等价字段。

必须回答：

> 哪些字段能够可靠地判断一个 AI Result 是否可以持久化成 ASSISTANT Turn？

---

# 六、定义 Assistant Turn 的目的

不要简单认为：

```text
AIOrchestrationResult != None
→ ASSISTANT Turn
```

必须先定义：

> ConversationTurn 中的 ASSISTANT message 表示什么？

候选语义：

### Option A

只表示：

```text
用户最终看到的 assistant message
```

### Option B

表示：

```text
AI Runtime 曾经执行过一次
```

### Option C

同时表示：

```text
AI Runtime result
+
用户可见 assistant content
```

必须结合当前系统真实设计判断。

---

# 七、重点分析 SUCCESS

正常情况：

```text
USER
 ↓
AIOrchestrationResult
 ↓
content = "当前库存..."
route = TEXT_TO_SQL
outcome = success
 ↓
ASSISTANT
```

必须定义：

```text
ASSISTANT.content
```

来自：

```text
AIOrchestrationResult.content
```

还是：

```text
data
```

或者其他字段。

如果：

```text
content != ""
```

是否必然创建 ASSISTANT Turn？

必须给出明确答案。

---

# 八、重点分析 EMPTY

考虑：

```text
AIOrchestrationResult(
    route=...
    content=None / ""
    data=...
)
```

例如：

```text
SQL 查询有 data
但 content 为空
```

必须回答：

### Case A

```text
data 非空
content 为空
```

是否创建 ASSISTANT Turn？

### Case B

```text
data 为空
content 为空
```

是否创建？

### Case C

```text
content = whitespace
```

是否视为 empty？

必须定义：

```text
EMPTY
```

的精确定义。

不要简单依赖：

```python
if content:
```

除非确认这就是最终契约。

---

# 九、重点分析 REFUSED

Phase 3 已存在 Text-to-SQL refusal 语义。

当前可能存在：

```text
route = text_to_sql
status = refusal
```

或者通过：

```text
metadata.outcome
```

表达。

必须根据真实代码确认。

例如：

```text
用户：
删除库存数据
```

AI Result：

```text
当前 AI 数据查询服务仅支持只读查询……
```

必须回答：

> 这个拒答消息是否属于用户应该看到的 Assistant Turn？

需要明确：

```text
REFUSED
→ ASSISTANT Turn = YES / NO
```

如果 YES：

它是否：

```text
正常保存 content
```

还是需要特殊 role/status？

当前 `ConversationTurn` 是否存在 status 字段？

如果不存在：

**不要增加字段。**

只描述最小可行语义。

---

# 十、重点分析 FAILED

考虑：

```text
AIOrchestrator.execute()
```

直接抛异常：

```text
LLM timeout
Provider error
RAG exception
Tool exception
SQL generation exception
```

当前代码可能：

```text
USER Turn 已提交
AI exception
ASSISTANT Turn 不创建
exception bubble
```

必须确认真实行为。

然后定义：

```text
FAILED
```

是否创建 ASSISTANT Turn。

优先考虑：

```text
AI execution failure
→ no ASSISTANT Turn
```

但不要直接假设。

必须结合：

```text
ConversationTurn
```

的语义判断。

---

# 十一、失败是否应该持久化错误消息

必须明确：

例如：

```text
LLM timeout
```

是否创建：

```text
ASSISTANT:
"系统暂时无法处理，请稍后重试。"
```

如果当前系统没有正式的：

```text
error response contract
```

不要自行新增。

需要明确：

```text
FAILED
→ USER Turn retained
→ ASSISTANT Turn absent
→ exception returned to API layer
```

还是其他行为。

---

# 十二、Refusal 与 Failure 必须严格区分

这是本 Step 的核心。

必须形成：

```text
REFUSED
=
AI 正常执行
+
AI 正常返回
+
业务上拒绝请求
```

而：

```text
FAILED
=
AI Runtime 未能正常产生业务结果
```

例如：

```text
DELETE request
→ Text-to-SQL refusal
```

属于：

```text
REFUSED
```

而：

```text
DeepSeek timeout
```

属于：

```text
FAILED
```

两者不能混为：

```text
ERROR
```

必须根据现有实际 contract 判断。

---

# 十三、EMPTY 与 REFUSED 的边界

必须特别分析：

```text
content = ""
outcome = refused
```

或者：

```text
content = refusal message
outcome = refused
```

以及：

```text
content = ""
outcome = success
```

必须给出最终矩阵：

| Result State | AI 执行 | content | ASSISTANT Turn |
| ------------ | ----- | ------- | -------------- |
| SUCCESS      | 成功    | 非空      | ?              |
| SUCCESS      | 成功    | 空       | ?              |
| REFUSED      | 成功    | 非空      | ?              |
| REFUSED      | 成功    | 空       | ?              |
| FAILED       | 异常    | 无       | ?              |
| FAILED       | 异常    | 错误信息    | ?              |

不要先填结论。

根据真实代码和语义分析后再冻结。

---

# 十四、ASSISTANT Turn 与 request_id

当前 Phase 4.1 已冻结：

```text
assistant_request_id = CORRELATION ONLY
```

因此必须确认：

```text
ASSISTANT Turn.assistant_request_id
```

是否：

```text
=
AIOrchestrationResult.metadata.request_id
```

如果 request_id：

```text
None
```

则是否创建 ASSISTANT Turn？

必须结合当前代码：

```text
current ChatApplicationService
```

给出明确契约。

禁止：

```text
fabricate request_id
```

禁止：

```text
randomly generate request_id in persistence layer
```

因为 request_id 应来自 AI Runtime correlation。

---

# 十五、EMPTY Assistant Turn

必须明确禁止还是允许：

```text
ASSISTANT
content = ""
```

如果当前系统不允许空 Assistant message：

必须定义：

```text
empty result
→ no assistant turn
```

如果允许：

必须说明为什么。

不要新增：

```text
placeholder
""
"AI 未返回内容"
```

等人工填充内容。

---

# 十六、与 Step 1A 幂等的关系

Step 1A 已经确定：

```text
completed duplicate
→ replay existing result
```

但是当前：

```text
ConversationTurn
```

只有：

```text
USER
ASSISTANT
```

因此必须回答：

> 如果 ASSISTANT Turn 是成功判定的唯一持久化证据，那么哪些 Result 可以让一次 request 进入“completed”？

例如：

```text
SUCCESS + ASSISTANT
→ completed

REFUSED + ASSISTANT
→ completed

FAILED + no ASSISTANT
→ not completed
```

这个关系必须明确。

它会直接影响 Step 1A 的：

```text
duplicate
retry
```

---

# 十七、与未来 Evidence 的关系

本 Step 不实现 Evidence。

但必须明确：

未来：

```text
AI Result
    ↓
Assistant Turn
    ↓
Evidence Builder
```

还是：

```text
AI Result
    ↓
Evidence Builder
    ↓
Assistant Turn
```

哪个是正式顺序。

不能把 Evidence 设计提前做完。

只需要明确：

```text
Step 2 output
```

必须给 Step 3 一个清晰的输入语义。

---

# 十八、建议冻结的最小状态模型

不要新增数据库状态字段。

只在契约层定义：

```text
AI Result Outcome
```

例如：

```text
SUCCESS
REFUSED
FAILED
EMPTY
```

但：

**只有当前代码已经存在的状态才能冻结为正式状态。**

如果代码没有：

```text
EMPTY
```

不要凭空增加 runtime enum。

可以在文档中使用：

```text
Derived classification
```

但必须区分：

```text
Runtime Contract
vs
Evaluation Classification
```

---

# 十九、必须分析 API 行为

当前 API：

```text
POST /api/conversations/{id}/messages
```

如果：

### SUCCESS

应该：

```text
HTTP 200
USER + ASSISTANT persisted
```

### REFUSED

应该：

```text
HTTP 200
USER + ASSISTANT persisted
```

还是：

```text
HTTP 4xx
```

必须根据当前真实 API 行为决定。

### FAILED

应该：

```text
HTTP 5xx / 4xx
USER persisted
ASSISTANT absent
```

还是其他。

**不要修改 API。**

本 Step 只冻结现有/目标语义。

---

# 二十、测试设计

本 Step 不要求立即实现测试。

必须设计：

### T1

```text
SUCCESS + content
→ assistant turn exists
```

### T2

```text
SUCCESS + empty content
→ expected behavior
```

### T3

```text
REFUSED + content
→ expected behavior
```

### T4

```text
REFUSED + empty
→ expected behavior
```

### T5

```text
FAILED
→ assistant turn absent
```

### T6

```text
request_id valid
→ assistant_request_id persisted
```

### T7

```text
request_id absent
→ no fabricated request_id
```

### T8

```text
completed duplicate
→ Step 1A replay semantics consistent
```

### T9

```text
failed request
→ retry semantics consistent
```

### T10

```text
Assistant Turn contains no Evidence identity/provenance
```

---

# 二十一、必须检查现有 FakeOrchestrator

复用：

```text
Step 15 / Step 46 FakeOrchestrator
```

不要重新创建。

确认它当前能否构造：

```text
SUCCESS
REFUSED
FAILED
EMPTY
```

如果不能：

本 Step 只记录：

```text
Test Infrastructure Gap
```

不要修改 Fake。

---

# 二十二、最终 Decision

必须分别处理：

```text
OD-16 = AI Result Persistence Semantics
OD-19 = AI Failure / Refusal Semantics
```

只有：

```text
Assistant Turn creation rule
Success semantics
Empty semantics
Refusal semantics
Failure semantics
request_id semantics
API behavior
Idempotency completion semantics
```

全部明确，才可以：

```text
OD-16 = CLOSED
OD-19 = CLOSED
```

否则：

```text
OPEN
```

不要为了推进 Step 3 强行关闭。

---

# 二十三、输出文档

如果完成，只允许新增：

```text
docs/evaluation/Phase 4.2 Step 2 — AI Result Assistant Turn Semantics.md
```

默认：

```text
backend = 0
tests = 0
DB schema = 0
DB writes = 0
API = 0
```

不要修改：

```text
Phase 4.2 Roadmap
Step 1
Step 1A
Phase 4.1 contracts
```

---

# 二十四、文档结构

必须包含：

```text
# Phase 4.2 Step 2 — AI Result → Assistant Turn Semantics

## 1. Scope

## 2. Current Runtime Behavior

## 3. AIOrchestrationResult Contract

## 4. Assistant Turn Meaning

## 5. SUCCESS

## 6. EMPTY

## 7. REFUSED

## 8. FAILED

## 9. request_id Semantics

## 10. Result State Matrix

## 11. Idempotency Completion Semantics

## 12. API Behavior

## 13. Evidence Boundary

## 14. Step 3 Input Contract

## 15. Test Matrix

## 16. OD-16 Decision

## 17. OD-19 Decision

## 18. Open Questions
```

---

# 二十五、最终报告格式

完成后严格输出：

```text
【Phase 4.2 Step 2 COMPLETE】

1. Current Runtime
2. AIOrchestrationResult
3. Assistant Turn Semantics
4. SUCCESS
5. EMPTY
6. REFUSED
7. FAILED
8. request_id
9. Result State Matrix
10. Idempotency Completion
11. API Behavior
12. Evidence Boundary
13. Step 3 Input Contract
14. OD-16
15. OD-19
16. 新增文件
17. 修改文件
18. Backend
19. DB
20. API
21. Step 3 Readiness
```

最后：

```text
Phase 4.2 Step 2 = COMPLETE
OD-16 = CLOSED / OPEN
OD-19 = CLOSED / OPEN
Step 3 = READY / BLOCKED
STOP
```

**不要进入 Step 3。**
**不要实现 Evidence Builder。**
**不要修改 ChatApplicationService。**
**不要修改数据库。**
