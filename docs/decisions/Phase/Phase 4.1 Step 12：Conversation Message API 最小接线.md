继续 Phase 4.1。

当前状态：

```text
Step 10 COMPLETE
Step 11 COMPLETE
```

已存在：

```text
backend/app/services/chat_application_service.py
backend/app/services/conversation_service.py
backend/app/api/conversations.py
backend/app/dto/conversation_api.py
```

现在只执行：

# Phase 4.1 Step 12：Conversation Message API 最小接线

## 一、唯一目标

把 Step 11 已完成的：

```text
ChatApplicationService.execute_message()
```

接入 HTTP：

```text
POST /api/conversations/{conversation_id}/messages
```

最终链路：

```text
HTTP
 ↓
Conversation API
 ↓
ChatApplicationService
 ↓
ConversationService
 ↓
AIOrchestrator
 ↓
AIOrchestrationResult
 ↓
HTTP Response
```

本阶段只做 API Adapter。

---

# 二、严格禁止

禁止修改：

```text
ChatApplicationService
ConversationService
ConversationRepository
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
Context Builder
Memory
Summary
Regenerate
Trace
Timeline
Outcome persistence
```

禁止：

```text
DeepSeek
SiliconFlow
真实 DB 测试
数据库 Schema 修改
Migration
Auth
Pagination
Streaming
```

不要修改 Step 11 的 workflow。

---

# 三、开始前阅读

必须先阅读：

```text
backend/app/services/chat_application_service.py
backend/app/api/conversations.py
backend/app/dto/conversation_api.py
backend/app/services/conversation_service.py
```

以及：

```text
tests/test_conversation_api.py
tests/test_chat_application_service.py
```

确认当前真实：

```text
ChatApplicationService
Conversation API
Conversation DTO
```

不要假设接口。

---

# 四、新增 Request DTO

新增一个最小 Request DTO，例如：

```text
ConversationMessageRequest
```

只允许：

```text
content: str
```

规则：

```text
1 <= len(content) <= 10000
```

或者根据当前项目已有 content 限制复用真实约束。

禁止客户端传：

```text
project_id
assistant_request_id
request_id
route
metadata
context
model
provider
```

项目必须来自：

```text
conversation.project_id
```

request ID 必须来自：

```text
AIOrchestrationResult.metadata["request_id"]
```

---

# 五、新增 Response DTO

不要重新设计 AI Response。

优先复用：

```text
AIOrchestrationResult
```

如果 HTTP 层已有统一 ChatResponse：

可以复用当前：

```text
route
content
data
metadata
```

原则：

> Conversation Message API 返回的 AI Result 结构与 `/api/ai/chat` 保持一致。

不要新增：

```text
ConversationChatResponse
```

除非当前 Pydantic 序列化确实要求一个 HTTP DTO。

---

# 六、API Endpoint

新增：

```text
POST /api/conversations/{conversation_id}/messages
```

请求：

```json
{
  "content": "查询当前库存"
}
```

API 只负责：

```text
Path validation
↓
Request DTO validation
↓
调用 ChatApplicationService
↓
Result DTO conversion
↓
HTTP exception mapping
```

API 不允许：

```text
SQLAlchemy
Repository
DB Session
AIOrchestrator 直接调用
```

---

# 七、Dependency Injection

保持当前项目 API 风格。

如果当前：

```text
conversations.py
```

使用：

```text
get_conversation_service()
```

则为：

```text
ChatApplicationService
```

增加同风格的 accessor / dependency。

但：

**不要为了这一条 API 引入复杂 DI Framework。**

可以采用当前项目已经使用的简单 accessor。

FastAPI 官方支持通过 dependency injection 注入 service/dependency，适合保持 API 层只做适配。

---

# 八、异常映射

严格复用 Step 11 已存在异常。

至少确认：

```text
ConversationNotFoundError → 404
ConversationArchivedError → 409
ValueError → 422
ConversationRepositoryError → 500
AIOrchestratorExecutionError → 500
```

不要新增：

```text
ChatApplicationError
ConversationMessageError
AIChatError
```

如果 Step 11 当前实际异常类型不同，以真实代码为准。

---

# 九、Archived Conversation

测试：

```text
POST /api/conversations/{archived_id}/messages
```

必须：

```text
409
```

并且：

```text
AI calls = 0
turn append = 0
```

---

# 十、Not Found

测试：

```text
POST /api/conversations/{missing_id}/messages
```

必须：

```text
404
```

并且：

```text
AI calls = 0
```

---

# 十一、Empty Content

测试：

```json
{
  "content": ""
}
```

以及：

```json
{
  "content": "   "
}
```

必须：

```text
422
```

并且：

```text
AI calls = 0
USER turn = 0
ASSISTANT turn = 0
```

不要把校验逻辑复制到 Application Service。

HTTP DTO 层负责格式校验；Step 11 的业务校验继续保留。

---

# 十二、Normal Success

使用 Fake ChatApplicationService。

验证：

```text
POST
 ↓
Service.execute_message()
 ↓
AI result
 ↓
200
```

Response 必须保持：

```text
route
content
data
metadata
```

不要额外加入：

```text
conversation_id
turn_id
assistant_request_id
```

除非当前统一 ChatResponse 已经包含这些字段。

---

# 十三、Assistant Turn

API 层不要自己创建 Assistant Turn。

验证：

```text
HTTP
 ↓
ChatApplicationService
 ↓
USER turn
 ↓
AI
 ↓
ASSISTANT turn
```

API 只拿最终：

```text
AIOrchestrationResult
```

返回。

---

# 十四、Refusal

测试真实 Step 11 semantics：

```text
HTTP 200
route=text_to_sql
metadata.refused=true
metadata.outcome=REFUSED
content=可展示拒绝话术
```

确认 API 不修改：

```text
route
content
data
metadata
```

---

# 十五、EMPTY

测试：

```text
route=rag
metadata.outcome=EMPTY
content=None
```

API 必须仍然：

```text
HTTP 200
```

不要把 EMPTY 转成：

```text
404
204
500
```

---

# 十六、Tool Failure

测试：

```text
HTTP 200
metadata.tool_success=false
metadata.outcome=FAILED
```

API 必须保持：

```text
200
```

不要把 Application Result 转成 HTTP 500。

Outcome 与 HTTP status 是不同概念。

---

# 十七、AI Failure

模拟：

```text
ChatApplicationService.execute_message()
→ AIOrchestratorExecutionError
```

必须：

```text
HTTP 500
```

并保持现有：

```json
{
  "detail": "..."
}
```

结构。

不要暴露：

```text
stacktrace
SQL
prompt
API key
raw exception
```

---

# 十八、API Architecture Audit

新增：

```text
tests/test_conversation_message_api_architecture_audit.py
```

AST 检查：

```text
backend/app/api/conversations.py
```

不得 import：

```text
sqlalchemy
psycopg
backend.app.db
ConversationRepository
AIOrchestratorService
RagService
ToolChatService
TextToSQLService
```

API 只能依赖：

```text
ConversationMessageRequest
ChatApplicationService
现有 response DTO
现有异常
```

如果当前 `conversations.py` 已经存在其他合法 imports，不要破坏现有 API。

---

# 十九、API Contract Tests

新增：

```text
tests/test_conversation_message_api_contract.py
```

至少覆盖：

```text
POST route exists
request schema
response schema
404
409
422
500
200
refusal
empty
tool failure
```

---

# 二十、不要做 DB E2E

本阶段：

```text
DB = 0
Network = 0
LLM = 0
```

全部使用：

```text
Fake ChatApplicationService
```

不要设置：

```text
RUN_DB_TESTS
```

不要连接 PostgreSQL。

---

# 二十一、Regression

至少运行：

```powershell
python -m pytest -q tests/test_conversation_message_api_contract.py
python -m pytest -q tests/test_conversation_message_api_architecture_audit.py
python -m pytest -q tests/test_conversation_api_contract.py
python -m pytest -q tests/test_chat_application_service.py
```

然后：

```powershell
python -m compileall -q backend tests
```

不要运行全量 pytest。

不要运行 DB tests。

---

# 二十二、Git Diff

检查：

```powershell
git status --short
git diff --stat
git diff
```

本 Step 允许：

```text
backend/app/api/conversations.py
backend/app/dto/conversation_api.py
tests/
```

以及当前项目实际需要的一个小型 DI/accessor 文件（如果确有必要）。

禁止修改：

```text
ChatApplicationService
ConversationService
AIOrchestrator
```

---

# 二十三、最终验收

必须满足：

```text
□ POST /api/conversations/{id}/messages
□ Request 只有 content
□ project_id 不允许客户端覆盖
□ assistant_request_id 不允许客户端指定
□ ChatApplicationService 是唯一 Application 入口
□ API 不直接调用 AIOrchestrator
□ API 不访问 Repository
□ API 不访问 DB
□ 404 正确
□ 409 正确
□ 422 正确
□ 500 正确
□ SUCCESS 200
□ EMPTY 200
□ REFUSED 200
□ Tool failure 200
□ AI failure 500
□ 原 ChatResponse envelope 不变
□ 无 DB
□ 无 Network
□ 无 LLM
□ compileall OK
```

---

# 二十四、完成报告

严格输出：

```text
Phase 4.1 Step 12 完成报告

1. API
2. Request DTO
3. Response DTO
4. ChatApplicationService 接线
5. Project Binding
6. USER / ASSISTANT Turn
7. SUCCESS
8. EMPTY
9. REFUSED
10. Tool Failure
11. AI Failure
12. HTTP Error Mapping
13. Security
14. Architecture Audit
15. Contract Tests
16. compileall
17. DB / Network / LLM
18. Git Diff
19. Current Limitations

Phase 4.1 Step 12 READY
Phase 4.1 Step 12 STOP
```

完成后立即停止。

不要进入：

```text
Step 13
Context Builder
Memory
Summary
Regenerate
Auth
Pagination
```

等待下一步指令。
