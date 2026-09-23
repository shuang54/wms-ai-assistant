# WMS AI Assistant — API 设计

> 当前文档对应 MVP Phase 3.5.8 实现（Chat + RAG + Retrieval Evaluation + 真实知识库加载）。
> Phase 3.5.7 起增加 RAG 检索质量评估（见 § 5）。
> Phase 3.5.8 起增加真实知识库加载 CLI（见 § 6）。
> Phase 3.6.2 起增加 Tool Calling 对话接口 `POST /api/chat/with-tools`（见 § 2.4）。
> 后续阶段按 `docs/requirements.md` 演进。

---

## 1. 文档信息

| 项目     | 内容                                              |
| -------- | ------------------------------------------------- |
| 项目名称 | WMS AI Assistant                                  |
| 当前阶段 | MVP — Phase 3.5.8（Chat + RAG + Retrieval Evaluation + 真实知识库加载） |
| Base URL | `/api`                                            |
| 数据格式 | JSON                                              |
| 鉴权     | 当前无；Phase 6+ 引入 JWT/RBAC                    |
| LLM 协议 | OpenAI Chat Completions（兼容 OpenAI / DeepSeek / Qwen / Ollama / 自部署） |
| RAG 存储 | PostgreSQL + pgvector                            |

---

## 2. 当前已实现的接口

### 2.1 GET /api/health

服务健康检查，**不依赖** LLM、数据库或外部系统。

#### 请求

无请求体，无 Query 参数。

#### 响应 200

```json
{
  "status": "ok",
  "service": "wms-ai-assistant"
}
```

| 字段     | 类型   | 说明               |
| -------- | ------ | ------------------ |
| status   | string | 固定值 `"ok"`       |
| service  | string | 机器标识符         |

---

### 2.2 POST /api/chat

对话接口。Phase 3.5.6 起基于 RAG 知识库回答：

```text
ChatService → RagService → Vector Search → Context Builder → LLMClient
```

知识库无命中时返回固定提示语（`sources=[]`），**不**回退闲聊 LLM。

#### 请求

```json
{
  "message": "采购入库怎么操作？"
}
```

| 字段     | 类型   | 必填 | 说明                              |
| -------- | ------ | ---- | --------------------------------- |
| message  | string | ✅   | 用户自然语言消息（不可为空字符串） |

#### 响应 200

```json
{
  "answer": "采购入库主要包括收货、核对、质检和上架……",
  "sources": [
    {
      "chunk_id": 3,
      "document_id": 1,
      "chunk_index": 0,
      "similarity": 0.91,
      "metadata": {
        "heading_path": ["采购入库", "操作步骤"]
      }
    }
  ],
  "used_chunks_count": 1
}
```

| 字段              | 类型   | 说明                                                       |
| ----------------- | ------ | ---------------------------------------------------------- |
| answer            | string | AI 回答（经 RAG 链路生成；空知识库时为固定提示语）         |
| sources           | array  | RAG 命中来源（**仅元数据**，不含 chunk content；可扩展）   |
| used_chunks_count | int    | 实际纳入 Context 的片段数                                  |

> `sources` / `used_chunks_count` 为 Phase 3.5.6 向后兼容新增字段；
> 旧客户端可忽略。`message` / `answer` 协议与 Phase 2 保持一致。

---

### 2.3 POST /api/rag/answer

独立 RAG 问答接口（Phase 3.5.5），与 `/api/chat` 共用 RagService，
可显式指定召回片段数。

#### 请求

```json
{
  "query": "采购入库怎么操作？",
  "top_k": 5
}
```

| 字段   | 类型    | 必填 | 说明                                            |
| ------ | ------- | ---- | ----------------------------------------------- |
| query  | string  | ✅   | 用户问题（不可为空或纯空白）                    |
| top_k  | integer | ❌   | 召回片段数，范围 [1, 50]；省略用服务端默认值    |

#### 响应 200

```json
{
  "answer": "采购入库主要包括……",
  "sources": [
    {
      "chunk_id": 3,
      "document_id": 1,
      "chunk_index": 0,
      "content": "采购入库操作步骤……",
      "similarity": 0.91,
      "metadata": {"heading_path": ["采购入库", "操作步骤"]}
    }
  ],
  "used_chunks_count": 1
}
```

| 字段              | 类型   | 说明                                             |
| ----------------- | ------ | ------------------------------------------------ |
| answer            | string | LLM 生成的回答；空检索时为固定提示语             |
| sources           | array  | 命中来源（RAG 专用，含 chunk content）           |
| used_chunks_count | int    | 实际纳入 Context 的片段数                         |

> `/api/rag/answer` 面向调试 / 前端需要完整片段的场景，返回 `content`；
> `/api/chat` 面向对话，sources 仅返回元数据。

---

### 2.4 POST /api/chat/with-tools

对话接口（Phase 3.6.2：LLM Function Calling + Tool Framework）。
**不经过 RAG**，是独立的 Tool Calling 链路；`/api/chat` 行为不变。

```text
ToolChatService → LLM #1（携带 tools）
              → 判断是否需要 Tool
              → 需要：ToolRegistry.execute() → ToolResult → role=tool 消息
              → LLM #2 → 最终回答
```

当前阶段约束（Phase 3.6.2）：

- 单轮最多 **1 次** Tool Call、最多 **2 轮** LLM；
- LLM 返回多个 tool call → 502（明确拒绝，不并行执行）；
- Tool 参数错误 / 未注册 Tool 不打断请求：错误以 tool message 回传 LLM，
  由 LLM 生成自然语言错误说明；
- 注册的 Tool 为两个 **Mock**（`get_inventory` / `get_work_order`），不接真实 WMS / ERP。

#### 请求

```json
{
  "message": "查询 MAT001 的库存"
}
```

| 字段    | 类型   | 必填 | 说明                     |
| ------- | ------ | ---- | ------------------------ |
| message | string | ✅   | 用户自然语言消息（非空） |

#### 响应 200

```json
{
  "answer": "MAT001 当前库存为 1000 PCS。",
  "tool_calls": [
    {
      "tool_name": "get_inventory"
    }
  ]
}
```

| 字段       | 类型  | 说明                                                        |
| ---------- | ----- | ----------------------------------------------------------- |
| answer     | string | AI 最终回答（无需 Tool 时直接来自 LLM #1）                 |
| tool_calls | array  | 实际发生的 Tool 调用（**仅 tool_name**，最多 1 个；无需 Tool 时为 `[]`） |

#### 错误映射（在 §3.2 基础上新增）

| HTTP | 触发条件                                      | detail 示例                              |
| ---- | --------------------------------------------- | ---------------------------------------- |
| 502  | LLM 返回多个 tool call（MultipleToolCallsError） | `"LLM 返回多个 Tool Call（当前不支持）"` |
| 500  | ToolChatError（编排内部错误）                  | `"Tool Chat 服务内部错误: ..."`           |

LLM 家族异常（LLMConfigError 503 / LLMRequestError、LLMResponseError 502）
沿用 §3.2 映射。

---

## 3. 错误响应

### 3.1 统一错误格式

```json
{
  "detail": "可理解的错误描述"
}
```

FastAPI 默认 `HTTPException` 走 `{"detail": ...}` 形式。
后续阶段会扩展为 `{code, message, details}` 结构。

### 3.2 HTTP 状态码映射（Phase 3.5.6）

`/api/chat` 与 `/api/rag/answer` 共享同一异常映射
（`backend/app/api/_rag_error_mapping.py`）：

| HTTP | 触发条件                                                         | detail 示例                          |
| ---- | ---------------------------------------------------------------- | ------------------------------------ |
| 200  | 正常（含空知识库）                                                | —                                    |
| 400  | Pydantic 外的空消息（服务层校验）/ VectorSearchInputError        | `"message 不能为空"`                |
| 422  | Pydantic 校验失败：空 / 缺字段 / 类型错误 / top_k 越界           | FastAPI 默认错误列表                  |
| 502  | 上游错误：Embedding / pgvector / LLM（连接失败、超时、非 2xx）    | `"LLM 请求失败: 无法连接 ..."`       |
| 503  | 配置错误（`LLM_API_KEY` / `EMBEDDING_API_KEY` 等缺失）            | `"LLM 配置错误: LLM_API_KEY 未配置"` |
| 500  | 兜底：未预期的 RAG / Embedding / LLM 异常                        | `"RAG 服务内部错误: ..."`            |

> **不暴露**：响应中禁止出现 Authorization header、API Key、Python traceback、SQL 语句、embedding 向量、数据库连接串。

### 3.3 异常分层

```
LLMError（基类）                        llm/client.py
    ├── LLMConfigError                  配置缺失
    ├── LLMRequestError                 网络 / 超时 / 非 2xx
    └── LLMResponseError               响应解析失败 / 结构异常

EmbeddingError（基类）                   embedding/exceptions.py
    ├── EmbeddingConfigurationError      配置缺失 / 非法
    ├── EmbeddingInputError              输入非法
    ├── EmbeddingAPIError                网络 / 超时 / HTTP 非 2xx
    ├── EmbeddingResponseError           响应解析失败
    └── EmbeddingDimensionError          维度不一致

VectorSearchError（基类）                services/vector_search_service.py
    ├── VectorSearchInputError           query 非法
    └── VectorSearchParameterError       top_k 越界

RagError                                services/rag_service.py（RAG 自身错误）
```

---

## 4. 后续计划中的接口（**当前未实现**）

按 `docs/requirements.md` Phase 4+ 演进：

| 方法 | 路径                          | 阶段    | 说明                       |
| ---- | ----------------------------- | ------- | -------------------------- |
| GET  | `/api/conversations`          | Phase 2.5 | 会话列表                 |
| GET  | `/api/conversations/{id}`     | Phase 2.5 | 会话详情                 |
| POST | `/api/knowledge/documents`    | Phase 3.6+ | 知识库文档入库（API 化） |
| POST | `/api/knowledge/search`       | Phase 3.6+ | 知识库检索（内部接口）   |
| GET  | `/api/tools`                  | Phase 4 | Tool 注册清单              |
| POST | `/api/tools/{tool_name}`      | Phase 4 | Tool 调用                  |
| POST | `/api/auth/login`             | Phase 6 | 用户登录                   |
| GET  | `/api/audit/logs`             | Phase 6 | 审计日志                   |

---

## 5. RAG 检索质量评估（Phase 3.5.7）

> 本节为 **测试评估层** 描述，不对外暴露 HTTP 接口。

### 5.1 目的

建立可重复运行的 RAG 检索质量 Baseline，作为后续优化（Reranker / Hybrid
Search / Query Rewrite）的对比参照。

### 5.2 范围

```text
test query
  ↓
RagEvaluationService
  ↓
VectorSearchService.search(query, top_k)
  ↓
Top-K Results
  ↓
Keyword / document_id 匹配
  ↓
RetrievalEvaluationSummary
```

**不含**

- 不重新实现 embedding / pgvector / 距离计算（复用 `VectorSearchService`）
- 不调用 LLM（评估的是检索链路，与生成链路解耦）

### 5.3 评估数据集：`tests/fixtures/rag/evaluation_cases.json`

```jsonc
{
  "_schema_version": "1.0",
  "cases": [
    {
      "id": "case_001",
      "query": "采购入库的操作步骤是什么？",
      "expected_keywords": ["采购入库"],
      "expected_document_id": null,   // 可选；不假设 DB 当前 ID
      "expected_source": null,        // 可选；metadata.source 包含
      "expected_title": null           // 可选；metadata.title 包含
    },
    ...
  ]
}
```

不假设具体 document_id，避免依赖 DB 当前状态。

### 5.4 评估 Service：`backend/app/services/rag_evaluation_service.py`

```python
service = RagEvaluationService(vector_search_service=vector_search_service)

summary = await service.evaluate(cases, top_k=5)
# → RetrievalEvaluationSummary(
#     total_cases, matched_cases, failed_cases,
#     hit_rate = matched_cases / total_cases,
#     top_k, results=(RetrievalEvaluationResult, ...))
```

匹配规则：

| 字段                   | 命中条件                                                       |
| ---------------------- | -------------------------------------------------------------- |
| `expected_keywords`   | 全部关键字（任一 Top-K 命中）才匹配；支持中文/`metadata.source` |
| `expected_document_id` | 任一 Top-K result 的 `document_id == expected_document_id`     |

异常策略：

- 单 case 异常 → 该 `RetrievalEvaluationResult.error` 填入，
  `matched=False`，计入 `failed_cases`；**不阻断**其余 case。

DTO（frozen dataclass）：

| 类型                          | 字段                                                            |
| ----------------------------- | --------------------------------------------------------------- |
| `RetrievalEvaluationCase`     | case_id, query, expected_keywords, expected_document_id, ...    |
| `RetrievalEvaluationResult`   | case_id, query, top_k, matched, results_count, error, ...       |
| `RetrievalEvaluationSummary`  | total_cases, matched_cases, failed_cases, hit_rate, top_k, ... |

### 5.5 Baseline 指标

第一版只计算：

```
hit_rate = matched_cases / total_cases
```

后续如需 MRR / NDCG / Recall@K，独立 Phase。

### 5.6 运行测试

```bash
# 单元（默认）
pytest tests/test_rag_evaluation_service.py -q

# 真实链路（默认 SKIP；不消耗 LLM 额度，只消耗 Embedding）
RUN_REAL_RAG_EVAL=1 pytest tests/test_rag_evaluation_real.py -q -s
```

### 5.7 Baseline 快照（Phase 3.5.8）

导入第一份真实 WMS 知识文档 `docs/knowledge/wms-basic-operations.md`
（10 个 Chunk / BGE-M3 1024 维）后实测：

```
top_k         : 5
total_cases   : 12
matched_cases : 10
failed_cases  : 0
hit_rate      : 83.33%
```

未命中 case（真实反映 KB 覆盖范围，未人工修改 query / keywords）：

- `case_003` "单据归档在哪里处理？" → KB 当前未覆盖「单据归档」
- `case_011` "采购单如何创建？" → KB 覆盖「采购入库」但未涵盖「采购单」

> 不修改 `evaluation_cases.json` / Vector Search / Top-K 以「提升分数」。

---

## 6. 真实知识库加载（Phase 3.5.8）

### 6.1 CLI 导入入口

复用现有 `KnowledgeIngestionService`，**不**直接操作 ORM / Parser /
Chunker / EmbeddingClient：

```bash
python -m backend.app.cli.ingest_knowledge docs/knowledge/wms-basic-operations.md
```

可选 `--json` 输出单行 JSON。

### 6.2 行为契约

| 行为                        | 实现                                                                  |
| --------------------------- | --------------------------------------------------------------------- |
| Markdown 解析               | `MarkdownParser`（Phase 3.3）                                         |
| 标题感知切分                | `MarkdownAwareChunker`（Phase 3.4）                                  |
| Embedding                   | `EmbeddingClient`（BGE-M3 1024 维）                                  |
| 维度校验                    | 双保险（Client 内部 + Service 写库前）                                |
| 重复检测                    | `KnowledgeDocument.content_hash`（UNIQUE INDEX）                       |
| 重复导入                    | `IngestionResult.status == "already_exists"`，不再调 Embedding / DB   |
| 异常                       | `DocumentNotFoundError` / `UnsupportedDocumentTypeError` / `DocumentParseError` / `EmptyDocumentError` / `KnowledgeIngestionDatabaseError` |

### 6.3 不在本阶段实现

- ❌ `POST /api/knowledge/ingest` HTTP 接口（Phase 3.6+）
- ❌ 目录递归批量导入 / 任务队列 / 异步 worker
- ❌ 文档更新 / 删除 / 版本化
- ❌ 文件存储位置（OSS / S3）

### 6.4 CLI 退出码

| 退出码 | 含义                                                       |
| ------ | ---------------------------------------------------------- |
| 0      | 成功（含 `ready` / `already_exists`）                       |
| 1      | 失败（参数错误 / 文件不存在 / Service 异常）                |

---

## 7. 设计原则

- **RESTful**：资源导向，HTTP 语义清晰。
- **OpenAPI 自动生成**：所有接口必须能被 FastAPI 自动文档化。
- **响应统一**：错误响应遵循一致结构。
- **可演进**：接口路径与字段命名考虑后续扩展，避免过早锁定。
- **向后兼容**：新增响应字段（如 `sources`）不破坏既有客户端；不擅自变更既有字段语义。
- **不暴露内部细节**：错误信息对用户友好，敏感信息（API Key、SQL、堆栈、Authorization header、embedding 向量）禁止出现在响应中。
- **单一 LLM 调用**：一次对话请求只允许一次 Embedding + 一次 LLM 调用；Chat 与 RAG 共享同一条链路。
