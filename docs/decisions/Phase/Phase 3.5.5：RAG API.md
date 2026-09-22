现在开始实施 **Phase 3.5.5：RAG API**。

项目：
`D:\coding\ai\wms-ai-assistant`

## 一、目标

将已经完成的：

`RagService`

通过 FastAPI 暴露为一个 HTTP API。

最终链路：

```text
POST /api/rag/answer
        ↓
Request DTO
        ↓
RagService.answer()
        ↓
Vector Search
        ↓
Context Builder
        ↓
LLMClient / DeepSeek
        ↓
Response DTO
        ↓
JSON
```

本阶段只做 API 层，不增加新的 AI 能力。

---

# 二、开始前必须阅读

先阅读：

* `AGENTS.md`
* `docs/requirements.md`
* `docs/architecture.md`

以及：

* `backend/app/services/rag_service.py`
* `backend/app/services/vector_search_service.py`
* `backend/app/services/context_builder.py`
* `backend/app/main.py`
* 当前已有 API Router
* 当前已有 `/api/chat`
* `tests/`

必须遵循当前项目的 FastAPI Router / Schema / Dependency / Exception 风格。

---

# 三、严格范围

本阶段只实现：

1. RAG API Request Schema
2. RAG API Response Schema
3. FastAPI Router
4. RagService Dependency
5. API 层异常映射
6. API 测试
7. 文档更新（如果项目已有 API 文档组织方式）

不要实现：

* Chat + RAG
* Agent
* Tool Calling
* MCP
* LangGraph
* WMS/ERP API
* 实时库存
* 写操作
* 权限系统
* Streaming
* WebSocket
* 前端
* Redis
* 新数据库
* 新 AI 模型

---

# 四、Endpoint

新增：

```text
POST /api/rag/answer
```

Request：

```json
{
  "query": "采购入库怎么操作？",
  "top_k": 5
}
```

`top_k` 可以省略，使用 RagSettings 默认值。

---

# 五、Request Schema

建议使用 Pydantic：

```python
class RagAnswerRequest(BaseModel):
    query: str
    top_k: int | None = None
```

具体写法遵循当前项目 Pydantic 版本和代码风格。

校验：

### query

不能：

```text
""
"   "
```

API 层可以做基础格式校验。

但不要删除 RagService 自己的业务校验。

也就是说：

```text
API validation
        ↓
RagService validation
```

两层职责可以同时存在。

---

### top_k

如果传入：

```text
0
-1
51
```

应该返回 HTTP 422。

如果省略：

```json
{
  "query": "采购入库怎么操作？"
}
```

则由 RagService / RagSettings 使用默认值。

不要在 Router 中复制默认配置逻辑。

---

# 六、Response Schema

不要直接返回：

```python
RagResponse
```

如果项目 API 层已经有 Schema 层，请创建 API Response DTO。

例如：

```python
class RagSourceResponse(BaseModel):
    chunk_id: int
    document_id: int
    chunk_index: int
    content: str
    similarity: float
    metadata: dict

class RagAnswerResponse(BaseModel):
    answer: str
    sources: list[RagSourceResponse]
    used_chunks_count: int
```

如果当前 RagResponse 已经适合 Pydantic/FastAPI serialization，也可以采用合理转换方式。

核心要求：

**API 层不能暴露 ORM Model。**

---

# 七、Router

建议新增：

```text
backend/app/api/rag.py
```

例如：

```python
router = APIRouter(
    prefix="/api/rag",
    tags=["rag"],
)
```

然后：

```text
POST /answer
```

最终：

```text
POST /api/rag/answer
```

Router 只负责：

```text
HTTP
 ↓
Schema
 ↓
Service
 ↓
Schema
```

不要把以下逻辑放进 Router：

* Embedding
* pgvector
* Prompt
* LLM
* Context 拼接
* 数据库查询

---

# 八、RagService Dependency

根据项目现有依赖注入方式实现。

优先：

```text
Router
 ↓
RagService
 ↓
VectorSearchService
 ↓
EmbeddingClient
LLMClient
```

如果当前项目还没有统一 DI：

可以采用最简单、符合当前架构的方式。

不要为了这一阶段引入复杂 DI Framework。

---

# 九、HTTP 状态码

正常：

```text
200 OK
```

输入错误：

```text
422 Unprocessable Entity
```

例如：

* query 为空
* top_k 非法

Vector Search / Embedding / LLM 错误：

不要简单返回一个假的成功响应。

根据项目已有异常处理规范决定。

如果项目目前没有统一错误处理机制，建议：

```text
500 Internal Server Error
```

并且：

**响应中不要泄露：**

* API Key
* Authorization
* SQL
* embedding vector
* 内部 traceback
* 数据库连接字符串

不要为了这一阶段创建复杂的错误码系统。

---

# 十、空知识库

当 RagService 返回：

```python
RagResponse(
    answer="知识库中没有找到与该问题相关的信息。",
    sources=[],
    used_chunks_count=0,
)
```

API 应正常：

```text
HTTP 200
```

这不是服务器错误。

---

# 十一、测试

新增：

`tests/test_rag_api.py`

使用 FastAPI TestClient / AsyncClient，遵循项目现有测试方式。

至少覆盖：

### 1. 正常请求

```json
{
  "query": "采购入库怎么操作？"
}
```

Mock RagService：

验证：

* HTTP 200
* answer
* sources
* used_chunks_count

---

### 2. top_k

请求：

```json
{
  "query": "采购入库怎么操作？",
  "top_k": 3
}
```

验证：

RagService 收到：

```text
top_k=3
```

---

### 3. 默认 top_k

请求：

```json
{
  "query": "采购入库怎么操作？"
}
```

验证：

Router 不擅自覆盖 RagService 的默认配置逻辑。

---

### 4. 空 query

测试：

```text
""
"   "
```

验证：

```text
422
```

并且不调用 RagService。

---

### 5. 非法 top_k

测试：

```text
0
-1
51
```

验证：

```text
422
```

---

### 6. 空检索结果

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
200
```

---

### 7. Service Error

Mock RagService 抛出已有 Service / Vector / LLM 异常。

验证 API 不返回假成功。

如果项目当前没有统一异常格式：

验证至少：

```text
HTTP 500
```

并确认响应没有内部敏感信息。

---

### 8. Response Mapping

验证：

```text
RagResponse
 ↓
RagAnswerResponse
```

字段全部正确：

* answer
* sources
* chunk_id
* document_id
* chunk_index
* content
* similarity
* metadata
* used_chunks_count

---

# 十二、不要真实调用 DeepSeek

API 测试全部 Mock：

```text
RagService
```

不要因为测试 API 而真实调用 DeepSeek。

真实 LLM Smoke Test继续保持原来的：

```text
RUN_REAL_LLM_TEST
```

机制。

---

# 十三、API 文档

如果 FastAPI 自动 OpenAPI 已启用：

确认：

```text
/api/rag/answer
```

能够出现在：

```text
/openapi.json
```

以及 Swagger UI。

不要为了这一阶段增加额外 API 文档框架。

---

# 十四、与已有 /api/chat 的关系

非常重要：

**不要修改现有 `/api/chat`。**

当前：

```text
/api/chat
```

保持原状。

本阶段新增：

```text
/api/rag/answer
```

后续 Phase 才考虑：

```text
/api/chat
   ↓
RAG
```

不要现在提前合并。

---

# 十五、安全

确认：

API Response 不包含：

* API Key
* Authorization Header
* SQL
* query embedding
* document embedding
* pgvector distance（除非现有 RagSource 明确要求）
* 数据库连接信息
* traceback

Sources 中只返回 RagResponse 已经定义的公开字段。

---

# 十六、回归测试

执行：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest tests/test_rag_api.py -v
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

# 十七、检查 Git

完成后：

```powershell
git status --short
git diff --stat
git diff
```

重点确认：

* `.env` 没有进入 Git
* API Key 没有进入 Git
* 没有修改 `/api/chat`
* 没有修改 RagService 核心逻辑
* 没有修改 VectorSearchService
* 没有修改 EmbeddingClient
* 没有修改 KnowledgeIngestionService
* 没有无关修改

---

# 十八、完成标准

以下全部满足：

* `POST /api/rag/answer` 可用
* Request Schema 完成
* Response Schema 完成
* RagService 正确调用
* 空 query 返回 422
* 非法 top_k 返回 422
* 空知识库正常返回 200
* Service 错误不会伪装成成功
* OpenAPI 正常
* API tests 全部通过
* 全量测试通过
* DB tests 通过
* lint 通过
* 无敏感信息泄露
* `/api/chat` 未被修改

才算：

`Phase 3.5.5 COMPLETE`

完成后立即停止。

不要开始 Phase 3.5.6。
