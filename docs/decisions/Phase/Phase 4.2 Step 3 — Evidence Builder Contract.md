你现在开始执行：

# Phase 4.2 Step 3 — Evidence Builder Contract

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段目标

本阶段只定义：

**AI Result → Evidence Builder 的稳定 Contract。**

不要实现 Evidence Builder。

不要修改生产代码。

不要修改数据库。

不要修改 API。

不要修改 Conversation Runtime。

不要解决 Step 4 的 Runtime Integration。

本阶段最终产物只有：

```text
Evidence Builder Contract
        ↓
Input
        ↓
Eligibility
        ↓
Evidence Mapping
        ↓
Provenance
        ↓
Identity
        ↓
Lifecycle
        ↓
Output
```

核心问题：

> 什么样的 `AIOrchestrationResult` 可以产生 Evidence，以及如何把它安全、确定地转换成 Phase 4.1 已冻结的 Evidence Domain Object。

---

# 二、必须先阅读

先不要修改任何代码。

阅读真实代码：

```text
backend/app/services/ai_orchestrator_service.py
backend/app/services/chat_application_service.py

backend/app/db/models/evidence.py
backend/app/db/models/conversation_turn.py
backend/app/db/models/conversation.py
backend/app/db/models/conversation_evidence.py

backend/app/services/rag_service.py
backend/app/services/text_to_sql_service.py
backend/app/services/sql_executor_service.py
backend/app/services/relevant_table_selector.py
backend/app/services/database_context_composer.py

backend/app/
tests/
```

同时阅读：

```text
docs/decisions/Phase/Phase 4.2 Roadmap v1.0.md

docs/evaluation/Phase 4.2 Step 0 — Roadmap Freeze Audit.md
docs/evaluation/Phase 4.2 Step 1 — Message Idempotency Contract.md
docs/evaluation/Phase 4.2 Step 1A — OD-22 Idempotency Architecture Decision.md
docs/evaluation/Phase 4.2 Step 2 — AI Result Assistant Turn Semantics.md
```

以及 Phase 4.1 中与 Evidence 相关的：

```text
Evidence identity
Evidence provenance
Evidence lifecycle
Annotation
ConversationEvidence
```

重点：

**不要根据本 Prompt 猜 Evidence Model。**

必须以当前真实代码 + Phase 4.1 冻结 Contract 为准。

---

# 三、Phase 4.1 Evidence Contract 不得修改

以下内容全部视为冻结。

## 1. Evidence identity

Evidence identity：

```text
dataset_version
source_type
```

或者以当前 Phase 4.1 实际冻结实现为准。

但必须满足：

Evidence identity / provenance：

**绝对不能包含：**

```text
conversation_id
turn_id
assistant_request_id
provider_request_id
idempotency_key
```

这些都是运行时 / 关联上下文，不属于 Evidence 本身身份。

---

# 四、Evidence 与 Conversation 的关系

必须保持：

```text
AI Result
 ├──→ Assistant Turn
 │       ↓
 │    User-visible message
 │
 └──→ Evidence Builder
         ↓
      Evidence
```

不是：

```text
AI Result
 ↓
Evidence
 ↓
Assistant Turn
```

也不是：

```text
Assistant Turn
 ↓
Evidence
```

Evidence 是 AI Result 的独立下游产物。

---

# 五、首先回答一个核心问题

必须从真实代码分析：

> `AIOrchestrationResult` 当前到底携带了哪些信息，可以支持 Evidence 构建？

至少分析：

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
metadata.refused
```

或当前代码实际存在的等价字段。

不要假设字段一定存在。

最终报告必须明确：

```text
字段
是否存在
类型
Evidence 是否使用
原因
```

例如：

```text
content
→ 是否作为 Evidence content？

data
→ 是否作为 structured evidence？

route
→ 是否参与 source_type？

metadata
→ 哪些字段允许进入 Evidence？

request_id
→ 明确禁止进入 Evidence

conversation_id
→ 不存在于 AI Result / 禁止进入 Evidence identity

idempotency_key
→ 不存在于 Evidence / 禁止进入 Evidence identity
```

---

# 六、定义 Evidence Eligibility

必须定义：

```text
AIOrchestrationResult
        ↓
Evidence Eligibility
        ↓
0..N Evidence
```

不要默认：

```text
每一个 AI Result 都必须产生 Evidence
```

需要分别分析：

## A. RAG

例如：

```text
Question
 ↓
RAG
 ↓
Answer
```

判断：

* 是否产生 Evidence
* Evidence 来源是什么
* Knowledge Base source 如何表达
* dataset_version 从哪里获得
* source_type 从哪里获得

---

## B. Tool

例如：

```text
Question
 ↓
Tool
 ↓
Tool Result
```

判断：

* 是否产生 Evidence
* Tool Result 是否足以形成 Evidence
* Tool name 是否可以作为 source_type
* Tool execution metadata 是否应该进入 Evidence

注意：

不要为了方便把：

```text
request_id
conversation_id
tool_call_id
```

塞入 Evidence identity。

---

## C. Text-to-SQL

例如：

```text
Question
 ↓
Text-to-SQL
 ↓
SQL
 ↓
Validator
 ↓
Executor
 ↓
Database Result
```

重点分析：

### Evidence 应该代表什么？

候选：

```text
SQL
```

或者：

```text
Database Result
```

或者：

```text
SQL + Database Result
```

必须根据当前 Phase 4.1 Evidence Model 的实际字段判断。

不要设计无法落入现有 Evidence Schema 的结构。

---

# 七、必须区分 Answer 与 Evidence

非常重要。

例如：

```text
Assistant.content =
"当前库存共有 1,280 件。"
```

这只是：

```text
Assistant Turn
```

不能自动认为它就是 Evidence。

Evidence 应该代表：

```text
支持这个回答的可追溯事实 / 来源 / 数据结果
```

因此分析：

```text
content
data
source
provenance
```

各自的职责。

---

# 八、Evidence Content Contract

根据当前真实 Evidence Model，明确：

```text
Evidence.content
```

到底应该保存什么。

候选可能包括：

### RAG

```text
Knowledge chunk content
```

### Tool

```text
Tool result
```

### Text-to-SQL

```text
Structured database result
```

或者：

```text
SQL + result
```

但：

**不要凭感觉选择。**

必须根据：

```text
Evidence Model
Phase 4.1 Contract
当前已有 tests
```

判断。

如果当前 Schema 无法完整表达某类 Evidence：

不要修改 Schema。

记录：

```text
CURRENT LIMITATION
```

---

# 九、source_type Contract

必须定义允许的：

```text
source_type
```

至少分析：

```text
RAG
TOOL
TEXT_TO_SQL
```

是否应该映射成：

```text
knowledge
tool
database
```

还是当前项目已有固定枚举。

**优先复用已有定义。**

不要为了 Step 3 新增一套枚举。

必须明确：

```text
source_type = ?
```

的来源：

```text
route
tool name
database
knowledge base
```

以及是否允许任意字符串。

---

# 十、dataset_version Contract

这是本 Step 的重点之一。

Evidence provenance 已经冻结：

```text
dataset_version
```

必须分析：

### RAG

dataset_version 从哪里获得？

例如：

```text
Knowledge Base version
```

### Tool

Tool 是否有 dataset_version？

如果没有：

```text
N/A
```

还是：

```text
不能生成 Evidence
```

### Text-to-SQL

数据库查询结果是否存在：

```text
dataset_version
```

如果没有：

是否应该：

```text
使用 DB snapshot version
```

还是：

```text
当前无法生成正式 Evidence
```

**禁止在本阶段发明新的 dataset_version 生成规则。**

如果当前系统没有足够信息：

明确记录：

```text
Dataset version source unresolved
```

并保持 Step 3 Contract 为：

```text
BLOCKED / DEFERRED field
```

不要偷偷使用：

```text
timestamp
request_id
conversation_id
random UUID
```

作为 dataset_version。

---

# 十一、Evidence Identity 与 Idempotency 完全分离

必须特别验证：

```text
Idempotency-Key
```

绝对不能参与：

```text
Evidence identity
Evidence provenance
```

即使 Step 1A 已决定：

```text
conversation_id + idempotency_key
```

用于请求幂等，也不能把它带入 Evidence。

最终必须得到：

```text
Request identity
≠
Conversation identity
≠
Evidence identity
```

---

# 十二、一个 AI Result 是否可以产生多个 Evidence

必须明确：

```text
1 AI Result → 0..N Evidence
```

是否成立。

例如 RAG：

```text
Answer
 ├── Chunk A
 ├── Chunk B
 └── Chunk C
```

如果当前 Evidence Model 支持：

```text
1 result → N evidence
```

必须明确。

如果当前 Contract 只能安全表达：

```text
1 result → 0..1
```

也要明确记录。

不要因为“未来可能需要多 Evidence”就修改数据库。

---

# 十三、Evidence Lifecycle

必须确认新建 Evidence 的初始状态。

Phase 4.1 已冻结：

```text
IMPORTED
    ↓
PERSISTED
    ↓
ANNOTATED
    ↓
REVIEWED
    ↓
FINALIZED
```

必须分析：

Evidence Builder 生成的新 Evidence：

```text
INITIAL STATE = ?
```

例如可能是：

```text
IMPORTED
```

或者当前实现定义的其他合法状态。

不要新增 lifecycle state。

不要改变 FINALIZED terminal 语义。

---

# 十四、Evidence 与 Annotation

必须确认：

```text
Evidence Builder
```

是否负责：

```text
Annotation
```

默认原则：

**不要。**

Builder 负责：

```text
AI Result
→ Evidence
```

Annotation 属于独立后续生命周期。

因此建议验证：

```text
Evidence Builder
    ↓
Evidence
    ↓
Annotation / Review
```

而不是：

```text
Evidence Builder
    ↓
Evidence + Annotation
```

除非 Phase 4.1 真实 Contract 已经明确要求。

---

# 十五、REFUSED / EMPTY / FAILED

结合 Step 2 的真实结论分析：

## SUCCESS

```text
content 非空
request_id 合法
```

Assistant Turn 创建。

Evidence：

必须判断是否有 Evidence。

---

## EMPTY

```text
content 空
```

当前不创建 Assistant Turn。

必须判断：

```text
是否允许 Evidence？
```

原则上不要因为 `data` 存在就绕过 Step 2 的 user-visible semantics。

但如果当前 AI Result 有：

```text
data
```

且它本身是可追溯业务事实：

需要明确记录：

```text
是否允许 Evidence Builder 独立消费 data
```

不要在这里自行修改 Step 2。

---

## REFUSED

例如：

```text
只读查询拒绝
```

有拒绝说明：

```text
Assistant Turn = YES
```

但：

```text
Evidence = ?
```

通常拒答本身不是业务事实 Evidence。

必须明确：

```text
REFUSED → no Evidence
```

还是存在特殊 Evidence。

不要为了覆盖率而生成无意义 Evidence。

---

## FAILED

异常：

```text
AIOrchestrator raises
```

当前：

```text
Assistant Turn = NO
```

因此：

```text
Evidence Builder
```

不应该从异常对象伪造 Evidence。

必须定义：

```text
FAILED exception → no Evidence
```

除非未来有正式 failure result contract。

---

# 十六、Evidence Builder Input Contract

最终必须形成一个明确的输入 DTO / conceptual contract。

例如：

```text
EvidenceBuildInput
```

但本阶段：

**不要真的创建 Python DTO。**

只在文档中定义概念结构。

必须包含当前真实需要的字段，例如：

```text
AIOrchestrationResult
project context
source context
dataset context
```

但必须逐字段判断。

不要加入：

```text
conversation_id
turn_id
idempotency_key
```

除非只是明确标记为：

```text
runtime correlation context
```

并且：

**不得进入 Evidence identity/provenance。**

---

# 十七、Evidence Builder Output Contract

定义：

```text
EvidenceBuildResult
```

概念结构。

至少说明：

```text
evidence_count
evidence_items
skipped_reason
```

但不要真的创建 DTO。

需要支持：

```text
0 Evidence
1 Evidence
N Evidence
```

如果当前阶段无法确定 N 的上限：

记录：

```text
N = bounded by source/result cardinality
```

不要发明 arbitrary limit。

---

# 十八、Determinism

Evidence Builder Contract 必须保证：

同一个：

```text
AI Result
+
same source context
+
same dataset version
```

应该产生相同的 Evidence identity。

不得使用：

```text
random UUID
timestamp
request_id
conversation_id
idempotency_key
```

作为 identity 输入。

如果 Evidence 主键当前由数据库 UUID 生成：

明确区分：

```text
DB primary key
```

和：

```text
business Evidence identity
```

不要混淆。

---

# 十九、Security Boundary

必须分析：

Evidence Builder 不能把以下信息复制进入 Evidence：

```text
API key
password
database URL
connection string
Authorization header
LLM provider secret
internal DB session
SQLAlchemy connection
```

Text-to-SQL：

可以记录业务 SQL / result 的前提：

```text
当前 Evidence schema 允许
+
不存在敏感信息泄漏
```

但本阶段不要新增脱敏系统。

如果当前没有可靠脱敏机制：

记录：

```text
Sensitive-data redaction is outside Step 3.
```

---

# 二十、不要解决 OD-26

Step 2 新发现：

```text
OD-26 = EMPTY 是否视为 idempotency completed
```

本阶段：

**不要关闭 OD-26。**

不要修改：

```text
Step 1
Step 1A
```

只需要记录：

```text
OD-26 remains OPEN
```

并确认：

Evidence Builder 不依赖该决定。

---

# 二十一、必须设计 Contract Test Matrix

至少设计：

```text
E1 RAG + valid evidence source
E2 RAG + no source
E3 Tool + structured result
E4 Tool + empty result
E5 Text-to-SQL + valid DB result
E6 Text-to-SQL + refusal
E7 REFUSED
E8 EMPTY
E9 FAILED exception
E10 missing dataset_version
E11 multiple RAG sources
E12 forbidden runtime identifiers
```

每一个 Case 只做：

```text
Input
Expected Evidence Count
Expected source_type
Expected provenance
Expected identity
```

不要真正执行。

---

# 二十二、必须分析当前已有 Evidence 测试

查找：

```text
tests/
```

所有：

```text
Evidence
ConversationEvidence
Annotation
```

相关测试。

建立：

```text
Existing Contract
vs
Step 3 Proposed Contract
```

确认：

```text
0 contract conflict
```

如果发现冲突：

**不要修改测试。**

记录：

```text
CONTRACT CONFLICT
```

并停止。

---

# 二十三、严格禁止

本阶段禁止：

```text
❌ 新增 Evidence Builder Python 实现
❌ 修改 ChatApplicationService
❌ 修改 AIOrchestrator
❌ 修改 Evidence Model
❌ 修改 ConversationEvidence
❌ 修改 ConversationTurn
❌ 修改数据库
❌ 新增 migration
❌ 修改 API
❌ 新增 Idempotency-Key
❌ 关闭 OD-26
❌ 修改 Phase 4.1 contract
❌ 修改 Step 1 / Step 1A
❌ 实现 Evidence Runtime
❌ 写入 Evidence
❌ 写入 ConversationEvidence
```

特别注意：

**Step 3 是 Contract Design，不是 Implementation。**

---

# 二十四、唯一允许新增文件

只允许：

```text
docs/evaluation/Phase 4.2 Step 3 — Evidence Builder Contract.md
```

如果该文件已经存在：

只更新该文档。

除此之外：

```text
Backend = 0
Tests = 0
DB = 0
API = 0
```

---

# 二十五、发现冲突时的处理

如果发现：

```text
Evidence Model 无法表达 RAG source
Evidence Model 无法表达 dataset_version
Text-to-SQL result 无法形成 provenance
Tool result 无法形成稳定 source_type
```

不要修改 Schema。

不要修改 Runtime。

不要“临时兼容”。

报告：

```text
发现问题
影响
当前 Contract 限制
建议后续 Step
```

然后停止。

---

# 二十六、Step 3 完成判定

只有当以下全部明确后，Step 3 才能 COMPLETE：

```text
1. Evidence Eligibility
2. Evidence Content
3. source_type
4. dataset_version
5. Evidence identity
6. 0..N cardinality
7. lifecycle initial state
8. Annotation boundary
9. RAG mapping
10. Tool mapping
11. Text-to-SQL mapping
12. SUCCESS
13. EMPTY
14. REFUSED
15. FAILED
16. security boundary
17. deterministic identity
18. Input Contract
19. Output Contract
20. Contract Test Matrix
```

如果其中任何一个关键项无法确定：

```text
Step 3 = BLOCKED
```

不要为了“完成阶段”强行关闭。

---

# 二十七、最终报告格式

完成后严格按照：

```text
【Phase 4.2 Step 3 COMPLETE】

1. Current Evidence Model
2. AIOrchestrationResult → Evidence
3. Evidence Eligibility
4. Evidence Content Contract
5. source_type
6. dataset_version
7. Evidence Identity
8. Cardinality
9. Lifecycle
10. Annotation Boundary
11. RAG Mapping
12. Tool Mapping
13. Text-to-SQL Mapping
14. SUCCESS
15. EMPTY
16. REFUSED
17. FAILED
18. Security Boundary
19. Determinism
20. Input Contract
21. Output Contract
22. Contract Test Matrix
23. Existing Test Compatibility
24. OD-26
25. 新增文件
26. 修改文件
27. Backend
28. Tests
29. DB
30. API
31. Step 4 Readiness
```

最终明确：

```text
OD-26 = OPEN
```

然后判断：

```text
Step 4 = READY
```

或者：

```text
Step 4 = BLOCKED
```

---

# 最终 STOP

如果：

```text
Step 3 = COMPLETE
Step 4 = READY
```

立即停止。

**不要实现 Evidence Builder。**

**不要修改 ChatApplicationService。**

**不要创建 Evidence。**

**不要写数据库。**

**不要进入 Step 4。**

只完成：

```text
Step 3
Evidence Builder Contract
```
