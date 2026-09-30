你现在开始执行：

# Phase 3.12 Step 66 — Assistant Timeline 分组投影 Read Model

## 一、阶段目标

基于 Phase 3.12 Step 65 的审计结论：

```text
Assistant Timeline（分组 + 组内有序） → READY
Unified Timeline（单一全局有序事件流） → BLOCKED
```

本阶段只实现：

> **Assistant Timeline 的只读分组投影 Read Model。**

不要尝试解决 Step 65 已确认的：

```text
event_id
sequence
span_id
parent_event_id
统一 started_at
跨钟域排序
Validator Event
Executor Event
```

---

# 二、核心原则

当前数据源：

```text
LLM
Tool
RAG
Outcome
```

仍然保持四个独立来源。

Timeline 只是：

```text
Assistant Trace
        ↓
Timeline Projection
        ↓
LLM Events
Tool Events
RAG Events
Outcome Event
```

不是：

```text
四张表
   ↓
强行 merge
   ↓
伪造全局时间线
```

---

# 三、先阅读

先阅读当前真实实现：

```text
backend/app/services/assistant_trace_query_service.py
backend/app/api/assistant_trace.py

backend/app/db/models/llm_usage_record.py
backend/app/db/models/tool_execution_record.py
backend/app/db/models/rag_execution_record.py
backend/app/db/models/assistant_outcome_record.py

backend/app/services/llm_usage_query_service.py
backend/app/services/tool_execution_query_service.py
backend/app/services/rag_execution_persistence_service.py
backend/app/services/assistant_outcome_query_service.py
```

以及：

```text
tests/test_assistant_trace_multi_path_e2e.py
tests/test_assistant_outcome_persistence.py
tests/test_assistant_timeline_audit.py
```

---

# 四、禁止修改现有 Trace Contract

当前：

```text
GET /api/observability/assistant-trace/{assistant_request_id}
```

继续保持：

```json
{
  "assistant_request_id": "...",
  "outcome": "...",
  "llm_usage": [],
  "tool_executions": [],
  "rag_executions": []
}
```

本阶段：

**不要修改这个 API。**

不要添加：

```text
timeline
```

到现有响应。

---

# 五、新增 Read Model

建议新增：

```text
backend/app/services/assistant_timeline_query_service.py
```

以及 DTO：

```text
backend/app/dto/assistant_timeline.py
```

具体文件位置根据现有项目结构决定。

---

# 六、Timeline Event DTO

定义一个内部只读 DTO，例如：

```text
AssistantTimelineEvent
```

但注意：

### 不要定义：

```text
event_id
sequence
span_id
parent_event_id
```

因为 Step 65 已确认这些字段当前不存在。

建议只包含：

```text
source
source_id
event_type
assistant_request_id

started_at
finished_at
duration_ms
created_at

status
```

其中：

```text
started_at
finished_at
duration_ms
created_at
```

允许为空。

---

# 七、event_type

只允许：

```text
LLM
TOOL
RAG
OUTCOME
```

不要增加：

```text
ROUTER
VALIDATOR
EXECUTOR
PROMPT
EMBEDDING
```

这些当前没有持久化事件源。

---

# 八、source

明确来源：

```text
llm_usage
tool_execution
rag_execution
assistant_outcome
```

不要只依赖 `event_type`。

例如：

```text
source = "llm_usage"
event_type = "LLM"
```

---

# 九、source_id

使用真实数据库主键：

```text
LLM → llm_usage_record.id
Tool → tool_execution_record.id
RAG → rag_execution_record.id
Outcome → assistant_outcome_record.id
```

禁止生成新的 ID。

禁止：

```text
uuid4()
hash()
index()
enumerate()
```

---

# 十、时间字段映射

严格按照 Step 65 审计结果：

### LLM

```text
created_at → created_at
started_at = None
finished_at = None
duration_ms = None
```

### Tool

```text
started_at
finished_at
duration_ms

created_at = None
```

### RAG

```text
started_at
finished_at
duration_ms

created_at = None
```

### Outcome

```text
created_at → created_at

started_at = None
finished_at = None
duration_ms = None
```

不要把：

```text
created_at
```

改名成：

```text
finished_at
```

---

# 十一、status

根据当前真实字段能够确定的状态映射：

### LLM

当前 LLM Usage 表没有 success 字段。

因此：

```text
status = None
```

不要推断：

```text
success
```

---

### Tool

使用现有：

```text
success
```

映射：

```text
True  → "success"
False → "failed"
```

---

### RAG

当前 RAG Execution Record 没有 success 字段。

因此：

```text
status = None
```

---

### Outcome

使用：

```text
SUCCESS
EMPTY
REFUSED
FAILED
```

原样映射。

---

# 十二、分组规则

Timeline Query Service 返回：

```text
AssistantTimeline
```

建议结构：

```text
assistant_request_id

llm_events[]
tool_events[]
rag_events[]
outcome_event
```

其中：

```text
llm_events
    created_at ASC
    id ASC

tool_events
    id ASC

rag_events
    id ASC

outcome_event
    0 or 1
```

严格保持 Step 65 已验证的排序语义。

---

# 十三、不要跨组排序

绝对不要：

```python
sorted(
    llm + tool + rag + outcome,
    key=timestamp
)
```

因为：

```text
LLM/Outcome = DB clock
Tool/RAG = App clock
```

跨钟域不可安全比较。

---

# 十四、不要返回 fake sequence

禁止：

```text
sequence = 1
sequence = 2
sequence = 3
```

即使只是：

```text
展示用途
```

也不要生成。

---

# 十五、不要返回 fake event_id

禁止：

```text
event_id = uuid4()
```

必须使用：

```text
source_id
```

作为当前事件身份。

---

# 十六、Cross-request Isolation

查询：

```text
assistant_request_id = A
```

只能返回：

```text
A
```

的：

```text
LLM
Tool
RAG
Outcome
```

不能混入：

```text
B
C
```

---

# 十七、Unknown Request

如果：

```text
assistant_request_id
```

不存在：

返回：

```text
empty timeline
```

不要：

```text
404
```

保持当前 Assistant Trace 的语义。

---

# 十八、历史数据

如果：

```text
assistant_outcome_record
```

不存在：

```text
outcome_event = None
```

不要从：

```text
HTTP status
```

推断 Outcome。

同样：

如果某个历史请求没有：

```text
RAG record
```

就：

```text
rag_events = []
```

不要补造。

---

# 十九、Projection Synthetic Tests

新增：

```text
tests/test_assistant_timeline_projection.py
```

至少覆盖：

### Case A

```text
RAG
```

结果：

```text
rag_events = 1
llm_events = 1
tool_events = 0
outcome = SUCCESS
```

---

### Case B

```text
Tool
```

结果：

```text
tool_events = 1
llm_events = 0
rag_events = 0
outcome = SUCCESS
```

---

### Case C

```text
T2SQL retry
```

结果：

```text
llm_events = 2
```

按照：

```text
created_at ASC
id ASC
```

排列。

不能声称：

```text
attempt=1
attempt=2
```

---

### Case D

```text
RAG failure
```

允许：

```text
rag_events = []
```

或者按现有真实持久化结果：

```text
rag_events = 1
```

但必须使用实际 observation/persistence 语义。

不要补造。

---

### Case E

```text
Refusal
```

结果：

```text
llm_events = 1
tool_events = 0
rag_events = 0
outcome = REFUSED
```

---

### Case F

```text
Historical trace without outcome
```

结果：

```text
outcome = None
```

---

### Case G

```text
Cross-request A/B
```

确保完全隔离。

---

# 二十、Immutability

确认：

```text
AssistantTimelineEvent
AssistantTimeline
```

均为：

```text
frozen
```

不能修改。

---

# 二十一、Security

Timeline Event 禁止包含：

```text
prompt
messages
SQL
RAG chunk content
tool args
tool result
API key
authorization
password
database URL
raw LLM response
exception message
stack trace
```

只允许：

```text
metadata
identity
timing
status
```

---

# 二十二、No DB / Network

Timeline Projection Service 本身：

```text
No network
No HTTP
No Redis
No Kafka
```

数据库访问：

必须通过现有：

```text
Query Service
```

进行。

不要在 Timeline Service 内：

```text
create_engine()
Session()
```

直接访问数据库。

---

# 二十三、不要新增 API

本阶段：

```text
NO HTTP ENDPOINT
```

只建立：

```text
DTO
+
Query Service
+
Unit / projection tests
```

下一阶段再决定是否暴露 HTTP。

---

# 二十四、不要修改生产业务路径

禁止修改：

```text
AIOrchestrator
RagService
ToolChatService
TextToSQLService
LLMClient
Router
Validator
Executor
```

本阶段只是：

```text
Read Model
```

---

# 二十五、测试命令

只执行：

```powershell
python -m pytest -q tests/test_assistant_timeline_projection.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

不要运行：

```text
pytest -q
RUN_DB_TESTS=1
real LLM
```

---

# 二十六、完成报告

严格报告：

```text
Phase 3.12 Step 66 完成报告

1. 新增文件
2. 修改文件
3. Timeline DTO
4. 四类 Event 映射
5. 分组排序
6. Cross-request isolation
7. Historical data behavior
8. Security
9. DB / Network
10. Tests
11. compileall
12. 是否修改生产业务路径
13. 当前限制

结论：
Assistant Timeline Grouped Projection = READY / BLOCKED

Phase 3.12 Step 66 STOP
```

---

# 二十七、硬停止

完成后：

**立即 STOP。**

不要：

```text
Unified Timeline
event_id
sequence
span_id
OpenTelemetry
Pagination
Conversation
Memory
Agent
MCP
```

Step 66 只完成：

```text
四类事件
    ↓
分组
    ↓
组内稳定排序
    ↓
只读 Timeline Projection
```

等待下一步指令。
