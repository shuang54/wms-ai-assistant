现在开始实施 **Phase 3.5.6：Chat + RAG**。

项目：
`D:\coding\ai\wms-ai-assistant`

本阶段目标：

将现有 `/api/chat` 接入已经完成的 RAG 能力，使 Chat API 能够基于 WMS 知识库回答问题。

最终链路：

```text
POST /api/chat
      ↓
Chat Request
      ↓
ChatService
      ↓
RagService
      ↓
Vector Search
      ↓
Context Builder
      ↓
DeepSeek / LLMClient
      ↓
Chat Response
```

核心原则：

**尽量复用现有代码，不重新实现 RAG。**

---

# 一、开始前必须阅读

开始前必须阅读：

* `AGENTS.md`
* `docs/requirements.md`
* `docs/architecture.md`

以及完整检查：

* `backend/app/api/chat.py`
* `backend/app/services/`
* `backend/app/services/rag_service.py`
* `backend/app/services/vector_search_service.py`
* `backend/app/services/context_builder.py`
* `backend/app/llm/`
* `backend/app/embedding/`
* `backend/app/main.py`
* `tests/`
* 当前 `/api/chat` 相关测试

尤其要先确认：

**当前 `/api/chat` 的 Request / Response 协议是什么。**

不要凭猜测修改 API。

---

# 二、本阶段目标

将：

```text
/api/chat
```

从当前的普通 Chat 流程：

```text
Chat Request
    ↓
LLMClient
    ↓
Answer
```

升级为：

```text
Chat Request
    ↓
ChatService
    ↓
RagService
    ↓
Knowledge Retrieval
    ↓
DeepSeek
    ↓
Chat Response
```

---

# 三、非常重要：不要重复实现 RAG

禁止在 ChatService / Chat API 中重新实现：

* Embedding
* pgvector Search
* Context Builder
* RAG Prompt
* DeepSeek HTTP
* KnowledgeChunk 查询

必须复用：

```text
RagService
```

也就是：

```python
await rag_service.answer(query)
```

而不是重新写一套 RAG。

---

# 四、先判断现有 Chat 架构

在修改前先分析当前：

```text
backend/app/api/chat.py
```

以及对应 Service。

如果当前存在：

```text
ChatService
```

优先修改 ChatService。

如果当前 `/api/chat` 直接调用 LLMClient：

则可以增加最小的 ChatService / Application Service。

但：

**不要为了这一阶段进行大规模架构重构。**

---

# 五、保持现有 API 协议

这是本阶段最重要的约束之一。

现有：

```text
POST /api/chat
```

的：

* URL
* HTTP method
* Request JSON
* Response JSON

原则上必须保持兼容。

例如，如果当前请求是：

```json
{
  "message": "你好"
}
```

不要擅自改成：

```json
{
  "query": "你好"
}
```

如果当前响应是：

```json
{
  "answer": "..."
}
```

不要为了 RAG 强制改成：

```json
{
  "answer": "...",
  "sources": [...]
}
```

除非现有 API 已经设计支持扩展字段。

---

# 六、RAG 来源信息怎么处理

RAG 已经有：

```text
RagResponse
    answer
    sources
    used_chunks_count
```

本阶段需要评估现有 Chat Response 是否能够安全地增加：

```json
{
  "answer": "...",
  "sources": [...]
}
```

如果当前 Chat API Response 本身已经允许扩展，可以增加 sources。

如果现有协议非常严格，**优先保持兼容，不强行改变 Response Schema。**

这种情况下：

```text
Chat API
```

只返回原来的 answer。

RAG source 信息继续保留在内部。

不要为了 sources 破坏已有客户端。

---

# 七、ChatService

如果项目已有：

```text
backend/app/services/chat_service.py
```

优先在现有 ChatService 中接入 RagService。

建议逻辑：

```python
async def chat(message: str) -> ChatResponse:
    rag_response = await rag_service.answer(message)

    return ChatResponse(
        answer=rag_response.answer,
        ...
    )
```

如果当前 ChatService 已经有自己的业务逻辑：

保留它。

不要简单覆盖。

---

# 八、关于普通闲聊

这里需要特别设计。

本阶段**不要实现复杂 Intent Classifier**。

也不要引入 Agent。

建议：

所有 `/api/chat` 用户问题都先走：

```text
RAG
```

例如：

```text
“采购入库怎么操作？”
```

会检索 WMS 知识。

而：

```text
“你好”
```

如果知识库没有相关结果：

```text
RagService
 ↓
empty result
 ↓
“知识库中没有找到与该问题相关的信息。”
```

这是符合当前 RAG-only 阶段的。

**不要为了让“你好”更智能而偷偷调用普通 LLM。**

后续如果需要：

```text
闲聊 + RAG
```

再单独设计 Intent / Router。

---

# 九、保持 RAG 的防幻觉规则

必须继续使用已有：

```text
backend/app/prompts/rag_system.txt
backend/app/prompts/rag_user.txt
```

不要在 ChatService 重新写 Prompt。

ChatService 不负责 Prompt。

---

# 十、top_k

当前 RagService 支持：

```python
answer(query, top_k=None)
```

本阶段 `/api/chat`：

**默认使用 RagSettings 的默认 top_k。**

不要在 ChatService 写：

```python
top_k=5
```

因为这会复制配置。

优先：

```python
await rag_service.answer(message)
```

让 RagService 决定默认值。

---

# 十一、Dependency Injection

检查当前 Chat API 的依赖模式。

如果：

```text
api/chat.py
```

已经使用：

```python
_chat_service = ChatService()
```

则保持类似风格。

不要突然引入：

* dependency injection framework
* container
* singleton framework

测试中继续允许：

```text
monkeypatch
```

替换 Service。

---

# 十二、异常处理

当前 `/api/chat` 已有的错误行为必须尽量保持兼容。

RAG 接入后：

```text
RagService
 ↓
VectorSearchError
EmbeddingError
LLMError
```

不要吞掉异常。

不要返回：

```text
“你好，我暂时无法回答”
```

来隐藏系统错误。

如果现有 Chat API 有统一异常映射：

复用它。

如果没有：

按照当前项目已有 API 错误处理方式处理。

**不要重新设计一套全新的错误体系。**

---

# 十三、空知识库

如果：

```text
RagService.answer()
```

返回：

```text
answer =
“知识库中没有找到与该问题相关的信息。”

sources = []
used_chunks_count = 0
```

Chat API 应该正常返回。

不要把：

```text
知识库没有相关信息
```

当成 HTTP 500。

---

# 十四、Chat Response 扩展策略

请先查看现有 Response Schema，再决定。

如果可以向后兼容地增加：

```text
sources
used_chunks_count
```

可以增加。

推荐最终：

```json
{
  "answer": "采购入库主要包括……",
  "sources": [
    {
      "document_id": 1,
      "chunk_id": 3,
      "chunk_index": 0,
      "similarity": 0.91,
      "metadata": {
        "heading_path": [
          "采购入库",
          "操作步骤"
        ]
      }
    }
  ],
  "used_chunks_count": 1
}
```

但：

**不要把 source.content 强制返回给前端，除非当前 API 已经有这种需求。**

优先返回必要来源元数据。

---

# 十五、测试

重点修改/新增：

```text
tests/test_chat_api.py
tests/test_chat_service.py
```

具体根据当前项目已有测试结构决定。

必须保护原有 Chat 行为。

---

## Test 1：Chat → RAG

Mock：

```text
RagService
```

返回：

```text
RagResponse(
    answer="采购入库包括收货、核对、质检和上架。",
    sources=[...],
    used_chunks_count=2,
)
```

调用：

```text
POST /api/chat
```

验证：

* HTTP 200
* answer 正确
* RAG 被调用
* query/message 正确传递

---

## Test 2：验证不直接调用 LLM

Chat API / ChatService 接入 RAG 后：

测试：

```text
Chat
 ↓
RagService
```

而不是：

```text
Chat
 ↓
LLMClient
```

如果 ChatService 已经有 LLMClient：

必须确认本阶段不会发生：

```text
RagService → LLMClient
ChatService → LLMClient
```

导致一次请求调用两次 LLM。

这是非常重要的测试。

---

# 十六、Single LLM Call

正常 RAG Chat 请求：

```text
User
 ↓
Embedding API
 ↓
Vector Search
 ↓
DeepSeek
```

应该只有：

**1 次 Embedding Query**

和：

**1 次 LLM 调用**

不要：

```text
RAG → DeepSeek
Chat → DeepSeek
```

导致两次生成。

---

# 十七、Empty Search

Mock：

```python
RagResponse(
    answer="知识库中没有找到与该问题相关的信息。",
    sources=[],
    used_chunks_count=0,
)
```

验证：

```text
POST /api/chat
```

正常返回。

---

# 十八、RAG Error

Mock：

```text
VectorSearchError
EmbeddingAPIError
LLMRequestError
```

验证：

Chat API 不会：

* 吞异常
* 返回假的成功答案
* 泄露 traceback
* 泄露 API Key

---

# 十九、回归测试

所有现有 `/api/chat` 测试必须继续通过。

特别检查：

* Request schema
* Response schema
* 空 message
* 正常 message
* 错误处理
* OpenAPI

不能因为 RAG 接入破坏已有 API。

---

# 二十、真实 RAG Chat Smoke Test

如果项目已有真实测试机制，可以增加一个可选：

```text
RUN_REAL_LLM_TEST=1
```

Smoke Test：

```text
POST /api/chat
{
    "message": "采购入库怎么操作？"
}
```

验证：

```text
API
 ↓
RagService
 ↓
Vector Search
 ↓
Context
 ↓
DeepSeek
 ↓
Answer
```

不要默认运行。

不要大量调用真实 DeepSeek。

---

# 二十一、手工 API 验证

如果开发环境运行 FastAPI，可以实际测试：

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/chat `
  -H "Content-Type: application/json" `
  -d '{"message":"采购入库怎么操作？"}'
```

如果当前端口或请求字段不同，以项目现有配置为准。

至少验证：

```text
HTTP 200
answer 非空
```

如果知识库已有数据，确认回答与知识库内容相关。

---

# 二十二、安全

确认 `/api/chat` Response 不包含：

* API Key
* Authorization
* Bearer
* SQL
* embedding vector
* PostgreSQL URL
* traceback

不要打印：

* 完整 prompt
* 完整 embedding
* Authorization Header

日志如果已有：

可以保留：

```text
query_length
used_chunks_count
elapsed_ms
```

---

# 二十三、不要修改这些模块

除非发现编译/集成所必需，否则不要修改：

```text
backend/app/services/vector_search_service.py
backend/app/services/rag_service.py
backend/app/services/context_builder.py
backend/app/embedding/
backend/app/llm/
backend/app/services/knowledge_ingestion_service.py
```

尤其：

**不要为了 Chat + RAG 重写 RagService。**

---

# 二十四、不要实现以下内容

本阶段明确禁止：

* Agent
* Tool Calling
* LangGraph
* MCP
* WMS API
* ERP API
* 实时库存
* 数据库业务查询
* Intent Classifier
* Query Rewrite
* Reranker
* Hybrid Search
* Memory
* Conversation History
* Streaming
* WebSocket
* 前端 UI
* 权限

这些留到后续阶段。

---

# 二十五、测试命令

PowerShell：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest tests/test_chat_api.py -v
```

如果存在 ChatService 测试：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest tests/test_chat_service.py -v
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest
```

要求：

```text
0 failed
```

并运行 lint。

---

# 二十六、Git Diff

完成后：

```powershell
git status --short
git diff --stat
git diff
```

重点确认：

* `.env` 没有进入 Git
* API Key 没有进入 Git
* 没有修改 VectorSearchService
* 没有修改 RagService
* 没有修改 EmbeddingClient
* 没有修改 KnowledgeIngestionService
* 没有无关重构
* `/api/rag/answer` 没有被破坏

---

# 二十七、最终报告

完成后只汇报：

```text
Phase 3.5.6 Chat + RAG

1. Chat Pipeline
   /api/chat
   → ChatService
   → RagService
   → Vector Search
   → Context
   → DeepSeek
   → Chat Response

2. API Compatibility
   - Request：保持/变化
   - Response：保持/增加 sources
   - /api/rag/answer：PASS

3. Tests
   - Chat API tests: X passed
   - ChatService tests: X passed
   - Full regression: X passed / X skipped / X failed
   - DB tests: PASS/FAIL
   - Real RAG smoke: PASS/SKIP/FAIL
   - Lint: PASS/FAIL

4. LLM Call Count
   - Embedding query: 1
   - LLM generation: 1
   - Duplicate LLM call: NO

5. Security
   - API Key: 未泄露
   - Authorization: 未泄露
   - SQL: 未泄露
   - embedding: 未泄露
   - traceback: 未泄露

6. Modified Files
   - ...

7. Git Diff
   - ...

如果全部通过：

Phase 3.5.6 COMPLETE

完成后立即停止。

**不要开始 Phase 3.5.7。**
```
