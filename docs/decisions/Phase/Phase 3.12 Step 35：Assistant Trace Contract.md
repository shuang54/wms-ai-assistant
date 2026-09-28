你现在开始执行：

# Phase 3.12 Step 35：Assistant Trace Contract

## 一、阶段目标

基于 Phase 3.12 Step 34 的审计结果：

当前：

```text
POST /api/ai/chat
        ↓
AIOrchestratorService
        ↓
AI Router
   ├── RAG
   ├── TOOL
   └── TEXT_TO_SQL
        ↓
AIOrchestrationResult
```

已经是当前项目唯一的：

```text
统一入口
+
统一 Router
+
三路由
+
统一 Result Envelope
+
统一错误边界
```

因此：

**本阶段不新增 Chat API。**

唯一目标：

> 将 AIOrchestrator 内部已经生成的 `request_id` 暴露到 `/api/ai/chat` 响应，使一次 Assistant 请求可以与 Tool Execution Observability 关联。

---

# 二、严格范围

允许修改：

```text
backend/app/services/ai_orchestrator_service.py
backend/app/api/orchestrator_chat.py

tests/
docs/api.md
docs/architecture.md
```

如果项目已有最合适的 API contract test 文件，优先修改现有测试。

可以新增：

```text
docs/evaluation/Phase 3.12 Step 35 — Assistant Trace Contract.md
```

---

# 三、明确禁止

本阶段禁止：

```text
新 Chat API
Conversation
Message
Memory
Session
Agent
LangGraph
MCP
Streaming
WebSocket
Dashboard
Prometheus
OpenTelemetry
Redis
Kafka
```

禁止修改：

```text
AI Router 核心路由行为
RAG 核心逻辑
Tool Registry
ToolExecutionService
ToolExecutionRecord
Tool Observability
Text-to-SQL
SQL Validator
SQL Executor
LLM Usage Repository
数据库结构
```

禁止修改：

```text
/api/chat
/api/rag/answer
/api/chat/with-tools
```

这些 API 本阶段保持完全不变。

---

# 四、当前 request_id 行为

先阅读真实代码确认：

```text
AIOrchestratorService.execute()
```

当前已经：

```text
request_id = new_request_id()
```

并用于：

```text
ToolExecutionContext
ToolExecutionRecord
```

但是：

```text
/api/ai/chat
```

目前没有把 request_id 返回给调用方。

因此本阶段只解决：

```text
内部 request_id
        ↓
API response metadata.request_id
```

---

# 五、Response Contract

当前：

```text
AIOrchestrationResult
```

保持不变：

```python
@dataclass(frozen=True)
class AIOrchestrationResult:
    route
    content
    data
    metadata
```

不要新增：

```text
request_id
```

到 DTO 顶层。

不要改变 `AIOrchestrationResult` 的字段结构。

---

# 六、request_id 放置位置

统一放到：

```text
AIOrchestrationResult.metadata["request_id"]
```

最终 `/api/ai/chat`：

```json
{
  "route": "tool",
  "content": "...",
  "data": {
    "tool_name": "get_inventory",
    "success": true
  },
  "metadata": {
    "request_id": "..."
  }
}
```

RAG：

```json
{
  "route": "rag",
  "content": "...",
  "data": {
    "sources": [],
    "used_chunks_count": 1
  },
  "metadata": {
    "request_id": "..."
  }
}
```

Text-to-SQL：

```json
{
  "route": "text_to_sql",
  "content": "...",
  "data": {},
  "metadata": {
    "request_id": "..."
  }
}
```

---

# 七、重要：不要泄露内部信息

`metadata` 原有字段必须继续保留。

只增加：

```text
request_id
```

禁止顺手加入：

```text
tool arguments
SQL
DB connection
LLM prompt
LLM response
API key
password
traceback
session
DB object
```

不要因为本阶段做 trace 就扩大 metadata。

---

# 八、request_id 来源必须唯一

非常重要：

不要在 API 层重新生成：

```python
uuid4()
```

禁止：

```text
API request_id
       ↓
生成一个新的 uuid
```

必须使用：

```text
AIOrchestrator.execute()
       ↓
内部唯一 request_id
       ↓
metadata.request_id
       ↓
ToolExecutionRecord.request_id
```

即：

```text
API request_id
==
Orchestrator request_id
==
ToolExecutionRecord.request_id
```

对于 Tool 路径必须能够验证这一点。

---

# 九、三条路由都必须返回 request_id

测试：

### RAG

```text
route = RAG
metadata.request_id != None
```

### Tool

```text
route = TOOL
metadata.request_id != None
```

并且：

```text
metadata.request_id
==
ToolExecutionRecord.request_id
```

### Text-to-SQL

```text
route = TEXT_TO_SQL
metadata.request_id != None
```

不要要求 Text-to-SQL 必须产生 Tool Record。

---

# 十、request_id 格式

不要重新设计 ID 格式。

继续复用项目现有：

```text
new_request_id()
```

不要：

```text
chat_
assistant_
trace_
timestamp_
```

重新拼接。

本阶段只负责：

```text
生成 → 透出
```

不负责重新设计 ID。

---

# 十一、错误场景

非常重要：

如果：

```text
Router error
RAG error
Tool error
Text-to-SQL error
```

导致正常的 `/api/ai/chat` HTTP error：

不要为了返回 request_id 而改变现有错误 response。

本阶段：

```text
错误 JSON contract = 不变
HTTP status = 不变
error detail = 不变
```

不要新增：

```json
{
  "request_id": "..."
}
```

到所有错误响应。

原因：

本阶段只建立成功响应的 Trace Contract。

错误 Trace Contract 留到未来统一 Error Contract 阶段处理。

---

# 十二、HTTP API Contract

只修改：

```text
POST /api/ai/chat
```

成功响应的：

```text
metadata
```

新增：

```text
request_id
```

不要修改：

```text
route
content
data
```

不要修改：

```text
request schema
```

不要增加：

```text
request_id
```

到请求 Body。

因此：

```json
{
  "question": "...",
  "project_id": "..."
}
```

仍然保持不变。

---

# 十三、API 测试

修改现有：

```text
tests/test_orchestrator_chat_api.py
```

或者项目中真实对应的 API test。

至少覆盖：

### 1. RAG

验证：

```text
200
metadata.request_id exists
request_id 非空字符串
```

### 2. Tool

验证：

```text
200
metadata.request_id exists
```

并验证：

```text
API metadata.request_id
==
ToolExecutionRecord.request_id
```

### 3. Text-to-SQL

验证：

```text
200
metadata.request_id exists
```

如果当前测试环境已有 deterministic fake generator，直接复用。

不要调用真实 DeepSeek。

---

# 十四、Metadata 不泄露测试

增加明确测试：

```text
metadata
```

只能在原有字段基础上增加：

```text
request_id
```

禁止出现：

```text
api_key
password
authorization
database_url
connection_string
sqlalchemy
session
prompt
traceback
```

特别检查：

```text
request_id
```

本身不能是：

```text
None
""
```

---

# 十五、Tool Observability 对齐测试

这是本 Step 最重要的测试。

构造：

```text
POST /api/ai/chat
question = "查询物料 MAT-001 当前库存"
project_id = "project-a"
```

如果当前 deterministic test infrastructure 已有对应能力：

执行。

然后：

```text
response.metadata.request_id
```

必须等于：

```text
ToolExecutionRecord.request_id
```

并且：

```text
round == 1
```

```text
tool_call_id == None
```

保持现有 Orchestrator Tool contract。

---

# 十六、RAG / T2S 不产生 Tool Record

继续验证：

### RAG

```text
request_id != None
ToolExecutionRecord count = 0
```

### Text-to-SQL

```text
request_id != None
ToolExecutionRecord count = 0
```

不要因为新增 request_id 而改变 Tool Observability。

---

# 十七、并发唯一性

增加一个轻量 deterministic test：

同时执行多个独立 Orchestrator request。

验证：

```text
request_id unique
```

例如：

```text
request 1 → id A
request 2 → id B
request 3 → id C
```

要求：

```text
A != B
B != C
A != C
```

不要做复杂压力测试。

---

# 十八、Project Context

验证：

```text
project_id
```

仍然由服务器端 ProjectContext / capability 决定。

本阶段：

```text
project_id = 不变
```

不要让客户端通过 request body 开启新的 capability。

---

# 十九、文档

新增：

```text
docs/evaluation/Phase 3.12 Step 35 — Assistant Trace Contract.md
```

至少记录：

```text
1. Scope
2. Current Problem
3. Trace ID Source
4. Response Contract
5. Tool Correlation
6. RAG / T2S Isolation
7. Security
8. Error Contract
9. Tests
10. Limitations
```

更新：

```text
docs/api.md
```

明确：

```text
POST /api/ai/chat
```

成功响应：

```text
metadata.request_id
```

是：

```text
Assistant Trace ID
```

并说明：

```text
ToolExecutionRecord.request_id
```

可以与之关联。

---

# 二十、Architecture Contract C40

增加：

```text
C40
```

至少包含：

### C40.1

`/api/ai/chat` 成功响应包含：

```text
metadata.request_id
```

### C40.2

request_id 来源唯一：

```text
AIOrchestrator.new_request_id()
```

### C40.3

API 不重新生成 request_id。

### C40.4

AIOrchestrationResult DTO 结构不变。

### C40.5

RAG / Tool / T2S 三条成功路径都具有 request_id。

### C40.6

Tool 路径：

```text
response request_id
==
ToolExecutionRecord request_id
```

### C40.7

RAG / T2S 不新增 Tool Record。

### C40.8

request body 不新增 request_id。

### C40.9

错误 HTTP contract 不变。

### C40.10

metadata 不包含敏感信息。

### C40.11

/api/chat 不受影响。

### C40.12

/api/chat/with-tools 不受影响。

---

# 二十一、禁止修改旧 API

明确增加 regression tests：

```text
/api/chat
```

响应结构不变。

```text
/api/chat/with-tools
```

响应结构不变。

```text
/api/rag/answer
```

响应结构不变。

不要因为它们也存在 request_id 需求而顺手修改。

---

# 二十二、测试命令

先运行：

```powershell
python -m pytest tests/test_orchestrator_chat_api.py -q
```

如果真实文件名不同，以项目实际文件为准。

然后：

```powershell
python -m pytest tests/test_tool_observability_api.py -q
python -m pytest tests/test_tool_execution_repository.py -q
python -m pytest -q
```

编译：

```powershell
python -m compileall -q backend tests
```

如果存在相关 architecture tests：

全部运行。

不要调用真实 DeepSeek。

---

# 二十三、失败处理

如果发现：

```text
request_id
```

在现有 Orchestrator 中不是稳定可获取：

不要重新设计整个 Orchestrator。

先报告：

```text
问题：
影响：
根因：
```

只有为了完成 Step 35 必须做的最小修改才允许进行。

禁止顺手修改：

```text
Router
ToolExecutionService
Tool Registry
RAG
Text-to-SQL
```

---

# 二十四、完成标准

必须达到：

```text
Trace Contract = PASS

RAG request_id = PASS
Tool request_id = PASS
T2S request_id = PASS

Tool Record correlation = PASS

Old APIs unchanged = PASS
Error contract unchanged = PASS
Security = PASS

Tests = PASS
compileall = PASS
LSP = 0 diagnostics
```

如果 lint 不存在：

```text
lint unavailable
```

不要安装新工具。

---

# 二十五、最终报告

严格使用：

```text
【Phase 3.12 Step 35 COMPLETE】

1. Trace Contract
2. request_id 来源
3. /api/ai/chat
4. RAG
5. Tool
6. Text-to-SQL
7. Tool Record Correlation
8. Old API Regression
9. Security
10. Architecture C40
11. Tests
12. compile / LSP / lint
13. 修改文件
14. 新增文件
15. 当前限制
16. Architecture
```

最后：

```text
Phase 3.12 Step 35 到此停止。

不要进入 Step 36。
不要开发 Conversation。
不要开发 Memory。
不要开发 Agent。
不要开发 MCP。
不要开发 Streaming。
不要开发 Dashboard。

等待下一步指令。
```
