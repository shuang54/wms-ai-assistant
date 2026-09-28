你现在开始执行：

# Phase 3.12 Step 34：AI Assistant Chat 主链路现状审计

## 一、阶段目标

Phase 3.11 Tool Observability 已完成。

现在不要继续扩展 Observability。

进入：

```text
Phase 3.12
AI Assistant 产品主链路
```

本 Step **只做现状审计和架构设计，不修改生产逻辑。**

目标：

> 找清楚当前项目已经有哪些 Chat / AI API、Orchestrator、Router、RAG、Tool、Text-to-SQL 能力，以及下一阶段真正缺少的最小 AI Assistant Chat 边界。

最终需要回答：

```text
现在已有的 Chat 能力是什么？
        ↓
哪些可以直接复用？
        ↓
哪些 API 是重复入口？
        ↓
AIOrchestrator 是否已经可以作为统一入口？
        ↓
真正缺少什么？
        ↓
Phase 3.12 Step 35 应该最小实现什么？
```

---

# 二、严格规则

本 Step：

## 允许

只允许：

* 阅读代码
* 阅读测试
* 阅读 API 文档
* 阅读 architecture / ADR
* 做静态架构分析
* 做只读的 API/OpenAPI 检查
* 新增 evaluation / audit 文档
* 新增只读架构测试（如果确实必要）

## 禁止

不要修改：

```text
AIOrchestrator
AI Router
RAG
Tool
ToolChatService
TextToSQL
SQL Validator
SQL Executor
ProjectContext
Conversation
Memory
```

不要新增：

```text
Chat API
Conversation API
Session
Memory
Agent
LangGraph
MCP
```

不要修改数据库。

不要调用真实 DeepSeek。

不要访问生产数据库。

不要修改已有 API contract。

---

# 三、首先阅读这些文件

先检查真实代码，不要假设接口。

重点：

```text
backend/app/main.py

backend/app/api/
    chat.py
    rag.py
    tool_chat.py
    orchestrator_chat.py
    usage.py
    tool_observability.py

backend/app/services/
    ai_orchestrator_service.py
    ai_router_service.py
    rag_service.py
    tool_chat_service.py
    tool_execution_service.py
    text_to_sql_service.py
```

如果目录中实际文件名不同：

以真实代码为准。

同时搜索：

```text
AIOrchestratorService
AIOrchestrationResult
RouteDecision
RouteType
ToolChatService
RagService
TextToSQLService
```

---

# 四、API 全量盘点

检查当前 FastAPI 路由。

至少整理：

```text
method
path
request DTO
response DTO
service
主要能力
是否调用 LLM
是否调用 DB
是否调用 Tool
是否调用 RAG
是否调用 Text-to-SQL
是否经过 AIOrchestrator
```

重点找出：

```text
/api/chat
/api/ai/chat
/api/rag/answer
/api/chat/with-tools
/api/observability/*
```

不要假设一定存在。

---

# 五、建立当前 API Matrix

最终报告必须形成类似：

```text
| API | Entry Service | Orchestrator | RAG | Tool | Text-to-SQL | LLM | DB |
|-----|---------------|--------------|-----|------|-------------|-----|----|
| ... | ... | ... | ... | ... | ... | ... | ... |
```

准确根据代码填写。

如果一个 API 同时存在多个调用路径，明确写出来。

---

# 六、重点分析 `/api/ai/chat`

重点回答：

```text
/api/ai/chat
        ↓
是否直接进入 AIOrchestrator？
        ↓
Router
        ↓
RAG / Tool / Text-to-SQL
```

检查：

1. Request DTO
2. Response DTO
3. project_id
4. request_id
5. route
6. content
7. data
8. metadata
9. error handling
10. Tool execution observability
11. LLM usage observability

确认当前接口能不能已经作为：

```text
AI Assistant
```

的后端统一入口。

不要因为发现问题就修改。

---

# 七、重点分析 `/api/chat`

确认它与：

```text
/api/ai/chat
```

的关系。

回答：

```text
是否重复？
是否调用不同 Service？
是否历史兼容 API？
是否仍然需要？
是否应该保留？
```

注意：

本 Step 不删除、不废弃任何 API。

只是记录：

```text
Current
Candidate future role
```

---

# 八、重点分析 `/api/chat/with-tools`

重点检查：

```text
/api/chat/with-tools
        ↓
ToolChatService
```

确认它是否仍然具有：

```text
独立 LLM Function Calling loop
多轮 Tool Calling
ToolExecutionService
ToolExecutionContext
Tool Observability
ProjectContext
Capability
```

特别回答：

> `/api/chat/with-tools` 和 `/api/ai/chat` 是否已经形成两套不同的 AI Tool 执行模型？

如果是：

明确描述差异。

不要修改。

---

# 九、重点分析 AIOrchestrator

阅读：

```text
backend/app/services/ai_orchestrator_service.py
```

明确列出当前能力：

```text
Input
 ↓
Router
 ↓
RAG
Tool
Text-to-SQL
 ↓
AIOrchestrationResult
```

回答：

### 1. 是否支持 project_id？

### 2. 是否支持 request_id？

### 3. 是否有统一错误边界？

### 4. 是否有 Tool Execution Observability？

### 5. 是否有 LLM Usage Observability？

### 6. 是否支持连续对话？

### 7. 是否保存 conversation？

### 8. 是否保存 message？

### 9. 是否支持上下文历史？

### 10. 是否可以无状态执行单轮问题？

---

# 十、Conversation 能力审计

全局搜索：

```text
conversation
conversation_id
session_id
message
chat_history
history
memory
context
```

确认项目是否已经存在：

```text
Conversation Model
Message Model
Conversation Service
Conversation Repository
Chat History
Memory
```

如果存在：

详细说明。

如果不存在：

明确：

```text
Conversation persistence = NOT IMPLEMENTED
```

不要新增。

---

# 十一、检查 AIOrchestrationResult

确认当前：

```text
AIOrchestrationResult
```

的真实结构。

记录：

```text
route
content
data
metadata
```

以及：

```text
frozen / immutable
```

是否成立。

重点判断：

> 当前 Result 是否已经足够作为 Chat API 的统一内部结果？

如果不够：

只记录缺口。

不要修改 DTO。

---

# 十二、检查错误模型

整理当前各 API 的错误语义：

```text
400
422
403
404
502
503
500
```

重点看：

```text
Router error
RAG error
Tool error
Text-to-SQL error
LLM error
DB error
Configuration error
```

判断：

> AI Assistant 是否已经有统一的 API error contract？

如果没有：

记录：

```text
Unified Chat Error Contract = NOT YET DEFINED
```

不要实现。

---

# 十三、检查 Observability 是否已经覆盖 Chat

验证：

### LLM Usage

是否可以通过：

```text
request_id
```

关联一次 Chat 请求。

### Tool Execution

是否可以通过：

```text
request_id
tool_call_id
```

关联 Tool Execution。

### RAG

检查是否已有：

```text
request_id
latency
retrieval information
```

不要新增。

只记录当前事实。

---

# 十四、检查当前 API 是否已经能够支持第一版 Assistant

不要讨论前端。

只判断后端。

定义最小 Assistant Backend Contract：

```text
Question
Project
Request ID
        ↓
AIOrchestrator
        ↓
Route
        ↓
Result
```

判断当前项目：

```text
READY
PARTIALLY READY
NOT READY
```

并说明原因。

---

# 十五、识别重复能力

特别检查：

```text
Chat
AI Chat
Tool Chat
Orchestrator Chat
RAG Answer
```

是否存在：

```text
多个入口
多个 Router
多个 Tool execution loop
多个 Result DTO
多个 Error Contract
```

输出：

```text
Duplicate / Legacy / Canonical Candidate
```

注意：

不要删除任何东西。

---

# 十六、提出 Phase 3.12 Step 35

根据真实代码审计结果，提出：

> **最小的 Step 35 实现范围。**

必须非常小。

优先考虑：

```text
统一一个 AI Assistant Chat API
        ↓
AIOrchestrator
        ↓
AIOrchestrationResult
```

但：

**只有当代码审计证明这是正确方向时才能提出。**

不要预设一定需要新 API。

如果现有：

```text
/api/ai/chat
```

已经足够：

那么 Step 35 可能只需要：

```text
完善 contract
```

而不是重新创建：

```text
/api/assistant/chat
```

禁止重复 API。

---

# 十七、Architecture 输出

最终必须画出：

```text
Current:

User
 ├── /api/chat
 ├── /api/chat/with-tools
 ├── /api/ai/chat
 └── /api/rag/answer
          ↓
      multiple paths
```

然后根据真实代码画：

```text
Candidate:

User
  ↓
AI Assistant API
  ↓
AIOrchestrator
  ↓
AI Router
  ├── RAG
  ├── Tool
  └── Text-to-SQL
  ↓
AIOrchestrationResult
```

如果实际情况不同，以真实代码为准。

---

# 十八、文档

新增：

```text
docs/evaluation/Phase 3.12 Step 34 — AI Assistant Chat Architecture Audit.md
```

文档必须包含：

```text
1. Scope
2. Current API Matrix
3. /api/ai/chat
4. /api/chat
5. /api/chat/with-tools
6. AIOrchestrator
7. Conversation / Memory
8. Result Contract
9. Error Contract
10. Observability
11. Duplicate / Legacy Paths
12. Current Readiness
13. Proposed Step 35
14. Architecture
15. Limitations
```

---

# 十九、测试

本 Step 不要求新增大量测试。

只运行现有：

```powershell
python -m pytest -q
```

以及：

```powershell
python -m compileall -q backend tests
```

如果已有 API architecture tests：

运行相关测试。

不要为了本 Step 大规模增加测试。

---

# 二十、最终报告格式

完成后严格报告：

```text
【Phase 3.12 Step 34 COMPLETE】

1. 当前 API Matrix
2. /api/ai/chat
3. /api/chat
4. /api/chat/with-tools
5. AIOrchestrator
6. Conversation / Memory
7. AIOrchestrationResult
8. Error Contract
9. LLM / Tool Observability
10. 重复 / Legacy API
11. Current Readiness
12. Step 35 建议
13. 修改文件
14. 新增文件
15. Tests
16. compileall
17. 当前限制
```

最后必须写：

```text
Phase 3.12 Step 34 到此停止。

不要：
- 新增 Chat API
- 修改 Orchestrator
- 修改 Router
- 新增 Conversation
- 新增 Memory
- 新增 Agent
- 新增 MCP

等待下一步指令。
```

**这是审计步骤，不是编码步骤。**
