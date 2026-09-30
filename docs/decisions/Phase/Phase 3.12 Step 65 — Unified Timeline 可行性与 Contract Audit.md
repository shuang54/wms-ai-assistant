# Phase 3.12 Step 65 — Unified Timeline 可行性与 Contract Audit

## 一、阶段目标

基于 Phase 3.12 Step 64 已完成的：

```text
Assistant Outcome
    ↓
Assistant Trace
    ├── LLM Usage
    ├── Tool Execution
    ├── RAG Execution
    └── Outcome
```

本阶段只做一件事：

> **审计是否已经具备建立 Unified Assistant Timeline 的最小条件。**

注意：

**本阶段只 Audit / Contract Design，不实现 Unified Timeline。**

---

# 二、为什么先 Audit

之前 Phase 3.12 Step 49 已经发现：

```text
LLM
Tool
RAG
Outcome
```

当前记录结构不同：

```text
LLM      → created_at
Tool     → execution timestamps / id
RAG      → started_at / finished_at
Outcome  → created_at
```

并且当前不存在统一：

```text
event_id
parent_event_id
sequence
span_id
```

因此本阶段必须先回答：

```text
1. 是否能够可靠排序？
2. 是否能够表达一次 Assistant Request 内的完整事件？
3. 是否需要统一事件模型？
4. 最小事件字段是什么？
5. 哪些字段当前无法可靠获得？
```

不要为了“做 Timeline”而伪造时间或顺序。

---

# 三、严格禁止

本阶段禁止修改：

```text
AIOrchestrator
RAG
Tool Framework
TextToSQL
LLM Provider
LLM Client
Validator
Executor
Assistant Outcome
LLM Usage persistence
Tool Execution persistence
RAG Execution persistence
Assistant Trace API
```

禁止：

```text
新增数据库表
新增数据库 Index
新增 API
新增 migration
新增全局状态
新增 event_id 运行时逻辑
新增 sequence 运行时逻辑
修改 Prompt
调用 DeepSeek
真实数据库写入
OpenTelemetry
Langfuse
Prometheus
Kafka
Redis
```

---

# 四、Step 1：阅读当前真实实现

先阅读：

```text
backend/app/services/assistant_trace_query_service.py
backend/app/api/assistant_trace.py

backend/app/db/models/llm_usage_record.py
backend/app/db/models/tool_execution_record.py
backend/app/db/models/rag_execution_record.py
backend/app/db/models/assistant_outcome_record.py

backend/app/services/llm_usage_persistence_service.py
backend/app/services/tool_execution_persistence_service.py
backend/app/services/rag_execution_persistence_service.py
backend/app/services/assistant_outcome_persistence_service.py

backend/app/services/assistant_outcome_query_service.py
```

以及：

```text
tests/test_assistant_trace_multi_path_e2e.py
tests/test_assistant_outcome_persistence.py
```

确认真实字段，不要根据历史设计文档猜测。

---

# 五、Step 2：建立当前事件字段矩阵

只在测试 / Audit 中整理，不修改 production。

建立：

```text
LLM Usage
Tool Execution
RAG Execution
Assistant Outcome
```

字段矩阵。

至少记录：

```text
event source
request_id
assistant_request_id
started_at
finished_at
created_at
duration_ms
id
provider
model
route
success
outcome
```

标记：

```text
AVAILABLE
NOT_AVAILABLE
DERIVED
UNSAFE_TO_DERIVE
```

---

# 六、Step 3：Correlation Audit

验证当前四类记录是否可以通过：

```text
assistant_request_id
```

或者：

```text
request_id
```

正确关联。

重点确认：

### LLM

```text
assistant_request_id = Assistant Request
provider request_id = Provider Request
```

两个 ID 不能混淆。

---

### Tool

确认：

```text
request_id
```

是否就是：

```text
assistant_request_id
```

还是存在独立 Tool request scope。

---

### RAG

确认：

```text
request_id
```

是否就是：

```text
assistant_request_id
```

---

### Outcome

确认：

```text
assistant_request_id
```

是否能够唯一对应：

```text
Assistant Request
```

---

# 七、Step 4：Ordering Audit

重点判断当前数据是否能够可靠得到：

```text
事件 A
    ↓
事件 B
    ↓
事件 C
```

分别测试：

### 1. 同一 request 的不同事件

例如：

```text
LLM
RAG
Outcome
```

---

### 2. T2SQL Retry

例如：

```text
LLM attempt 1
Validator reject
LLM attempt 2
Executor
Outcome
```

当前 Trace 中能否可靠知道：

```text
attempt 1 < attempt 2
```

不能只根据：

```text
created_at
```

做未经验证的推断。

---

### 3. Tool

确认：

```text
Tool start
Tool finish
Outcome
```

能否可靠排序。

---

# 八、Step 5：Timestamp Semantics Audit

明确区分：

```text
started_at
finished_at
created_at
```

禁止直接假设：

```text
created_at == started_at
```

也禁止：

```text
finished_at == created_at
```

对每个来源说明：

```text
时间字段含义
精度
时区
是否数据库生成
是否应用生成
```

---

# 九、Step 6：最小 Timeline Event Contract

**只设计，不实现。**

建议评估一个最小结构：

```text
AssistantTimelineEvent
```

候选字段：

```text
assistant_request_id
event_type
started_at
finished_at
duration_ms
source_id
```

其中：

```text
event_type
```

候选：

```text
LLM
TOOL
RAG
OUTCOME
```

但是：

**不要现在冻结最终 DTO。**

本阶段只回答：

```text
哪些字段已经有可靠数据来源？
哪些字段未来必须增加？
```

---

# 十、禁止伪造顺序

尤其注意：

禁止使用：

```python
enumerate(...)
```

把查询结果简单变成：

```text
sequence=1
sequence=2
sequence=3
```

如果数据库当前没有可靠事件顺序：

必须报告：

```text
sequence = NOT AVAILABLE
```

不能把查询顺序冒充业务执行顺序。

---

# 十一、禁止伪造 Event ID

本阶段不要增加：

```text
uuid.uuid4()
```

也不要：

```text
hash(timestamp)
```

生成假的：

```text
event_id
```

如果当前记录没有 event_id：

```text
event_id = NOT AVAILABLE
```

---

# 十二、Projection Audit

使用 Fake / Synthetic 数据构造至少：

### Case A：RAG

```text
LLM
RAG
Outcome
```

### Case B：Tool

```text
Tool
Outcome
```

### Case C：T2SQL Retry

```text
LLM #1
LLM #2
Outcome
```

### Case D：RAG Failure

```text
LLM
RAG
Outcome=FAILED
```

### Case E：Refusal

```text
LLM
Outcome=REFUSED
```

验证：

```text
是否能够构造一个无歧义 Timeline
```

如果不能：

明确指出缺失字段。

---

# 十三、测试要求

新增：

```text
tests/test_assistant_timeline_audit.py
```

只做 Audit。

至少覆盖：

```text
1. correlation matrix
2. LLM provider_request_id 与 assistant_request_id 不混淆
3. Tool request correlation
4. RAG request correlation
5. Outcome correlation
6. timestamp availability
7. timestamp semantic distinction
8. T2SQL retry ordering feasibility
9. RAG failure ordering feasibility
10. refusal ordering feasibility
11. missing event_id detection
12. missing sequence detection
13. synthetic timeline projection
14. cross-request isolation
15. no DB
16. no network
```

---

# 十四、不要实现 Timeline API

本阶段禁止：

```text
GET /api/observability/assistant-timeline/...
```

不要修改：

```text
assistant_trace.py
```

不要修改：

```text
AssistantTraceResponse
```

当前 Trace API 保持不变：

```text
assistant_request_id
outcome
llm_usage[]
tool_executions[]
rag_executions[]
```

---

# 十五、不要修改数据库

本阶段：

```text
DB schema changes = 0
DB writes = 0
DB migrations = 0
```

可以使用：

```text
Fake DTO
Projection
Static inspection
```

完成 Audit。

---

# 十六、最终运行

只运行：

```powershell
python -m pytest -q tests/test_assistant_timeline_audit.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

不要运行：

```text
完整 pytest
DB pytest
真实 LLM
```

本阶段只验证 Audit 本身。

---

# 十七、最终报告

完成后严格报告：

```text
Phase 3.12 Step 65 完成报告

1. Audit 文件
2. 当前四类 Event 数据源
3. Correlation Audit
4. Timestamp Audit
5. Ordering Audit
6. T2SQL Retry 是否可排序
7. RAG 是否可排序
8. Tool 是否可排序
9. Outcome 是否可排序
10. Event ID 是否存在
11. Sequence 是否存在
12. 最小 Timeline Contract 建议
13. 当前缺失字段
14. 是否可以进入 Timeline Implementation
15. Tests
16. compileall
17. DB writes
18. Network calls
19. Production code changes

Phase 3.12 Step 65 READY / BLOCKED
Phase 3.12 Step 65 STOP
```

---

# 十八、硬停止

完成后立即停止。

不要进入：

```text
Timeline Implementation
Pagination
Conversation
Memory
Agent
MCP
OpenTelemetry
```

如果 Audit 发现：

```text
无法可靠排序
```

也不要自行修复。

只报告：

```text
BLOCKED
```

等待下一步指令。
