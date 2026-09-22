现在开始实施 **Phase 3.5.4：最小 RAG（Retrieval-Augmented Generation）**。

项目：
`D:\coding\ai\wms-ai-assistant`

## 一、目标

本阶段只实现一个最小、清晰、可测试的 RAG Pipeline：

```text
用户问题
  ↓
VectorSearchService
  ↓
Top-K KnowledgeChunk
  ↓
Context Builder
  ↓
DeepSeek / LLMClient
  ↓
最终回答
```

核心目标：

让系统能够基于已经导入 PostgreSQL + pgvector 的 WMS 知识回答问题。

例如：

```text
用户：
采购入库怎么操作？

系统：
根据知识库中的采购入库文档生成回答。
```

---

# 二、开始前必须阅读

先阅读：

* `AGENTS.md`
* `docs/requirements.md`
* `docs/architecture.md`
* `docs/decisions/embedding-dimension.md`

以及当前实现：

* `backend/app/services/knowledge_ingestion_service.py`
* `backend/app/services/vector_search_service.py`
* `backend/app/embedding/`
* `backend/app/llm/`
* `backend/app/models/`
* `backend/app/db/`
* `tests/`

必须复用已有：

* `VectorSearchService`
* `EmbeddingClient`
* `LLMClient`
* `KnowledgeChunk`
* 现有 DB Session
* 现有配置体系

不要重复实现这些组件。

---

# 三、严格限制范围

本阶段只做：

1. RAG Service
2. Context Builder
3. RAG Prompt
4. LLM 调用
5. 返回 RAG Response DTO
6. 单元测试
7. DB + 真实 LLM 的可选 smoke test
8. 必要文档

暂时不要做：

* Agent
* LangGraph
* Tool Calling
* MCP
* WMS/ERP API
* WMS 实时库存查询
* 写操作
* 权限系统
* 对话历史
* Memory
* Streaming
* 前端页面
* 新 API Endpoint
* Reranker
* BM25
* Hybrid Search
* Query Rewrite
* 多 Agent
* Function Calling
* Redis
* Elasticsearch

保持：

**Simple First, Evolve Later**

---

# 四、RAG Service

新增：

`backend/app/services/rag_service.py`

建议提供：

```python
class RagService:

    async def answer(
        self,
        query: str,
        *,
        top_k: int = 5,
    ) -> RagResponse:
        ...
```

具体命名可以根据当前项目代码风格调整。

Pipeline：

```text
validate query
      ↓
VectorSearchService.search()
      ↓
VectorSearchResult[]
      ↓
ContextBuilder
      ↓
LLMClient.chat()
      ↓
RagResponse
```

---

# 五、RAG Response

建议定义：

```python
@dataclass(frozen=True)
class RagResponse:
    answer: str
    sources: list[RagSource]
```

Source 至少包含：

```python
@dataclass(frozen=True)
class RagSource:
    chunk_id: int
    document_id: int
    chunk_index: int
    content: str
    similarity: float
    metadata: dict
```

不要直接向上层暴露 ORM Model。

RAG Response 应该是纯 DTO。

---

# 六、Context Builder

新增：

`backend/app/services/context_builder.py`

负责：

```text
VectorSearchResult[]
        ↓
结构化 Context
```

建议生成类似：

```text
[知识片段 1]
来源：采购入库操作说明
章节：采购入库 > 操作步骤
内容：
......

[知识片段 2]
来源：采购入库操作说明
章节：采购入库 > 异常处理
内容：
......
```

注意：

Context 必须包含来源信息。

这样后续模型可以知道不同内容来自哪个知识片段。

---

# 七、Context 长度

本阶段不要实现复杂 token counting。

可以采用简单的字符长度限制。

例如：

```text
RAG_MAX_CONTEXT_CHARS=12000
```

如果项目配置体系适合，可以增加：

```text
RAG_TOP_K=5
RAG_MAX_CONTEXT_CHARS=12000
```

如果不需要增加配置，也可以使用合理默认值。

重要：

**不要把所有数据库内容无限塞进 Prompt。**

Context 超过限制时：

* 按检索排序保留最相关的 chunk
* 从后往前截断 chunk
* 不要随机截断

并且保留完整的来源标记。

---

# 八、Prompt

新增一个清晰的 RAG System Prompt。

核心原则：

```text
你是企业 WMS 知识助手。

只能根据提供的知识库内容回答问题。

如果知识库没有足够信息：
明确告诉用户“知识库中没有找到足够的信息”，
不要自行编造。

回答应该：
1. 准确
2. 简洁
3. 基于知识库
4. 必要时使用步骤列表
5. 不要声称自己查询了实时 WMS 数据
```

重要：

**不要把“模型自己的知识”当成 WMS 知识。**

例如知识库没有：

```text
“当前库存是多少？”
```

不能让 DeepSeek 自己猜。

应该回答知识库没有提供实时库存信息。

---

# 九、Source Citation

回答本身先不要强制 DeepSeek 生成复杂引用格式。

RagResponse 单独返回：

```text
answer
sources
```

例如：

```json
{
  "answer": "采购入库主要包括收货、核对、质检和上架等步骤。",
  "sources": [
    {
      "chunk_id": 123,
      "document_id": 10,
      "chunk_index": 0,
      "similarity": 0.91,
      "metadata": {
        "heading_path": [
          "采购入库",
          "操作步骤"
        ]
      }
    }
  ]
}
```

这样以后前端可以自己展示：

```text
参考来源：
采购入库操作说明
  └─ 操作步骤
```

---

# 十、LLMClient

必须复用现有：

```text
LLMClient
```

不要在 RagService 中直接：

```text
requests.post(...)
httpx.post(...)
```

不要自己管理 DeepSeek API。

RagService 只负责：

```text
messages
 ↓
LLMClient.chat(messages)
```

---

# 十一、LLM Prompt 结构

建议：

System：

```text
你是企业 WMS 知识助手。
只能依据 CONTEXT 回答。
如果 CONTEXT 没有足够信息，就明确说明。
不要编造 WMS 业务规则。
```

User：

```text
请根据下面的知识库内容回答问题。

CONTEXT:
{context}

QUESTION:
{query}
```

不要把 query embedding、distance、数据库 SQL 等内部信息发送给 LLM。

---

# 十二、没有检索结果

如果：

```python
VectorSearchService.search()
```

返回：

```python
[]
```

本阶段不要调用 LLM。

直接返回：

```text
answer =
“知识库中没有找到与该问题相关的信息。”
```

并：

```python
sources = []
```

原因：

没有 Context 时调用 LLM 很容易产生幻觉。

---

# 十三、低相似度结果

本阶段可以先不增加复杂 threshold。

也就是说：

```text
top_k
```

控制召回数量。

如果搜索到了结果，就进入 RAG。

但可以设计一个未来扩展点，例如：

```text
similarity_threshold
```

**不要现在实现复杂的动态阈值策略。**

---

# 十四、错误处理

如果 VectorSearchService 报错：

向上层传播，不要吞异常。

如果 LLMClient 报错：

向上层传播。

不要：

```python
except Exception:
    return "系统暂时正常"
```

掩盖真实错误。

---

# 十五、测试

新增：

`tests/test_context_builder.py`

至少测试：

### 1. 单 chunk

确认：

* content 存在
* source 信息存在
* heading_path 存在时正确显示

### 2. 多 chunk

确认顺序与 Vector Search 返回顺序一致。

### 3. Context 长度

超过：

```text
RAG_MAX_CONTEXT_CHARS
```

时不会无限增长。

### 4. Empty

输入：

```python
[]
```

返回空 Context。

---

新增：

`tests/test_rag_service.py`

至少测试：

### 1. 正常 RAG

Mock：

```text
VectorSearchService
LLMClient
```

验证：

```text
query
 ↓
search
 ↓
context
 ↓
LLM
 ↓
answer
```

全部打通。

### 2. Empty Search Result

验证：

* 不调用 LLM
* answer 为知识库没有相关信息
* sources=[]

### 3. Vector Search Error

验证异常传播。

### 4. LLM Error

验证异常传播。

### 5. Sources

验证：

VectorSearchResult 正确转换为 RagSource。

### 6. Prompt

检查发送给 LLM 的 messages：

必须包含：

```text
CONTEXT
QUESTION
```

并且包含实际知识内容。

### 7. Anti-hallucination

测试 Prompt 包含：

```text
只能根据知识库内容回答
```

以及：

```text
知识库没有足够信息时不要编造
```

---

# 十六、DB Integration Test

使用：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest
```

如果需要 RAG DB 测试：

1. 使用现有 PostgreSQL
2. 插入临时 knowledge_document
3. 插入临时 knowledge_chunk
4. 使用 1024 维 embedding
5. Vector Search 找到 chunk
6. Context Builder 生成 Context
7. LLM 可以 Mock

数据库测试重点验证：

```text
PostgreSQL
 ↓
pgvector
 ↓
VectorSearchService
 ↓
RAG Service
```

不要大量调用真实 DeepSeek。

---

# 十七、真实 LLM Smoke Test

可以增加一个可选 smoke test：

```text
RUN_LLM_TESTS=1
```

只允许调用一次或极少次数。

使用真实 DeepSeek。

验证：

```text
KnowledgeChunk
 ↓
VectorSearch
 ↓
Context
 ↓
DeepSeek
 ↓
Answer
```

不要把 API Key 写进代码。

不要把 API Key 打印到日志。

---

# 十八、关于已有 /api/chat

当前项目 Phase 1 已经存在：

```text
/api/chat
```

本阶段：

**不要直接修改它。**

RAG Service 先作为内部 Application Service 存在。

后面单独做 Chat API / Chat + RAG 阶段。

这样可以避免 API 层、RAG、LLM 同时变化。

---

# 十九、测试目标

至少确保：

```text
Context Builder tests
RAG Service tests
```

全部通过。

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest
```

必须：

```text
0 failed
```

同时执行 lint。

---

# 二十、检查已有测试是否回归

特别确认之前：

```text
Phase 3.5.1
Embedding
Phase 3.5.2
Knowledge Ingestion
Phase 3.5.3
Vector Search
```

全部没有回归。

---

# 二十一、Git Diff

完成后：

```powershell
git status --short
git diff --stat
git diff
```

确认：

* 没有 `.env`
* 没有 API Key
* 没有 Authorization
* 没有完整 embedding vector
* 没有无关文件
* 没有修改前面阶段核心逻辑

---

# 二十二、严格停止条件

本阶段完成后：

**不要开始 Phase 3.5.5。**

不要：

* 创建 Chat API
* 创建 Agent
* 接 WMS API
* 接实时库存
* LangGraph
* Tool Calling

完成后立即停止并报告。

---

# 二十三、最终报告

按照以下格式：

```text
Phase 3.5.4 RAG

1. 实现
   - RagService
   - ContextBuilder
   - RagResponse
   - RagSource
   - RAG Prompt

2. Pipeline
   Query
   → Vector Search
   → Context
   → DeepSeek
   → Answer

3. 测试
   - Context tests: X passed
   - RAG tests: X passed
   - Full regression: X passed / X skipped / X failed
   - DB tests: PASS/FAIL
   - LLM smoke: PASS/SKIP/FAIL
   - Lint: PASS/FAIL

4. 安全
   - API Key: 未泄露
   - Authorization: 未泄露
   - embedding vector: 未输出

5. 修改文件
   - ...

6. Git diff
   - ...

如果全部通过：
Phase 3.5.4 COMPLETE
```

**完成后立即停止。**
