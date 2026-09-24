# Phase 3.7.13：真实 RAG E2E — ADR

> **实际实施结果与技术决策记录**。
>
> 本阶段把 Phase 3.7.10 E2E 中的 `FakeRagService` 替换为真实 RAG 链路，
> 通过 `POST /api/ai/chat` 端到端验证：
> `Router → Orchestrator → RagService → Embedding(BGE-M3) → pgvector → LLM(DeepSeek) → Response`。

---

## 1. 实际 RAG 依赖链（已验证）

```text
POST /api/ai/chat
    ↓
Chat API (backend/app/api/orchestrator_chat.py)
    ↓
AIOrchestratorService (Phase 3.7.9 + 3.7.13 默认值修复)
    ↓
AIRouterService (Phase 3.7.8, rule-first, route="rag")
    ↓
RagService (Phase 3.5.4，Phase 3.7.13 由 Orchestrator 默认注入)
    ↓
    ├─ VectorSearchService (Phase 3.5.3)
    │     ├─ EmbeddingClient → OpenAICompatibleEmbeddingClient
    │     │     → SiliconFlow BAAI/bge-m3, dim=1024
    │     └─ pgvector cosine distance: knowledge_chunk.embedding <=> :q
    ├─ ContextBuilder (Phase 3.5.4, max_context_chars=12000)
    └─ LLMClient → OpenAICompatibleClient → DeepSeek deepseek-chat
```

Reranker **未**接入 RAG 链路：项目当前仅有 Phase 3.5.12 的 BGERerankerClient
（Cross-Encoder bge-reranker-v2-m3），作为离线实验评估使用；
`RagService / ChatService / VectorSearchService / API` 均不读取
`RERANKER_*` 配置（见 `backend/app/config.py` RerankerSettings 的注释）。
本阶段**未**对 Reranker 接线做修改，仅记录此事实。

---

## 2. Embedding 实现（真实调用）

| 字段 | 值 |
|---|---|
| Provider | SiliconFlow |
| Model | `BAAI/bge-m3` |
| Dimension | 1024 |
| API | `POST https://api.siliconflow.cn/v1/embeddings` |
| 凭证来源 | 环境变量 `EMBEDDING_API_KEY` / `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` |

实现：`OpenAICompatibleEmbeddingClient`（Phase 3.5.1）。
本阶段**未**修改 `EmbeddingClient`，直接复用既有实现。

---

## 3. Vector Search（真实 PostgreSQL + pgvector）

| 字段 | 值 |
|---|---|
| 数据库 | PostgreSQL（docker-compose 本地开发） |
| 扩展 | pgvector |
| 表 | `knowledge_chunk`（Phase 3.2） |
| 向量列 | `embedding vector(1024)` |
| 算子 | `<=>` cosine distance |
| 排序 | `ORDER BY embedding <=> :q`（最小化重复计算） |

实现：`VectorSearchService`（Phase 3.5.3）。
本阶段**未**修改 `VectorSearchService`。

---

## 4. Reranker 实际参与状态

**结论：不参与 RAG E2E。**

`RagService.answer` 的流水线为
`VectorSearch → ContextBuilder → LLMClient`，不含 Reranker。
该设计与 Phase 3.5.12 的离线评估架构保持一致：
Reranker 评估使用本地 Cross-Encoder（不联网、不污染生产 DB）；
生产 RAG 链路暂未把 Reranker 纳入。

后续 Phase（如 3.7.14+）若要把 Reranker 嵌入生产链路，
应作为独立决策单独处理（参考 `docs/requirements.md` / `docs/architecture.md`）。

---

## 5. LLM（真实 DeepSeek 调用）

| 字段 | 值 |
|---|---|
| Provider | DeepSeek |
| Model | `deepseek-chat` |
| API | `POST https://api.deepseek.com/chat/completions` |
| 凭证来源 | 环境变量 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` |

实现：`OpenAICompatibleClient`（Phase 2）。
本阶段**未**修改 `LLMClient`，直接复用既有实现。

---

## 6. 关键接线修复（Phase 3.7.13 唯一代码改动）

### 问题

Phase 3.7.11 实现的 `AIOrchestratorService._default_orchestrator`
（`backend/app/api/orchestrator_chat.py`）未注入 `RagService`：

```python
_default_orchestrator: AIOrchestratorService = AIOrchestratorService(
    router=AIRouterService(...),
    tool_registry=_TOOL_REGISTRY,
    # rag_service 默认 None → RAG 路径不可达
)
```

当 Router 规则命中 `RouteType.RAG` 时，
`Orchestrator._run_rag` 进入 `if self._rag is None: raise AIOrchestratorExecutionError("RAG service 未配置")`，
RAG 路径在生产环境**完全不可用**。

### 修复（最小改动）

将 `AIOrchestratorService.__init__` 的 `rag_service` 默认值改为懒加载
真实 `RagService()`（Phase 3.5.4 实现，未修改）：

```python
def __init__(self, *, ..., rag_service: Any | None = None, ...):
    if rag_service is None:
        from backend.app.services.rag_service import RagService
        rag_service = RagService()
    self._rag = rag_service
```

### 设计纪律遵守

| 约束 | 实际做法 |
|---|---|
| 不修改 RagService / VectorSearchService / EmbeddingClient / LLMClient | ✅ 仅改 Orchestrator 的默认值，未触及下游 |
| 不在 API 层 import 低层服务 | ✅ API 层（`orchestrator_chat.py`）无 `from backend.app.services.rag_service import`；测试 `test_api_module_does_not_import_forbidden_services` 仍然通过 |
| 不修改已有 API 协议 | ✅ ChatResponse 字段不变 |
| 最小修改 | ✅ 单文件 10 行改动 |

### 为什么改 Orchestrator 默认值而非 API 层显式注入

API 层通过 `AIOrchestratorService()` 构造时只显式注入了 `tool_registry`
（因为 Phase 3.7.11 Tool 路由需要 Tool 元数据）。
RAG 与 Tool 不同：API 层**不**应直接 import RagService，否则会破坏
"API 层不依赖低层服务"的纵深防御。

修复方式：把"默认注入"责任上移到 `AIOrchestratorService.__init__`，
让 `AIOrchestratorService()` 无参构造即可获得完整 RAG 链路。
API 层零改动，唯一受益者是默认 Orchestrator 实例。

---

## 7. 测试知识来源

测试专用知识文档：测试代码在 `tmp_path` 动态生成，
不依赖 CLI 加载的 `docs/knowledge/wms-basic-operations.md`，
因此**不污染**长期开发数据库。

```python
unique_doc = tmp_path / f"wms_rag_e2e_{unique_marker}.md"
unique_doc.write_text(
    "# WMS 测试文档 {unique_marker}\n\n"
    "## 入库流程概述\n\n"
    "... 描述入库步骤、异常情况 ...",
    encoding="utf-8",
)
```

**复用既有 Pipeline**（任务书 §五）：

```text
Parser → Chunker → EmbeddingClient → DB 写入
            ↓
        KnowledgeIngestionService.ingest_one()
            ↓
        knowledge_document + knowledge_chunk[]
```

不直接 INSERT 手写 embedding（任务书 §五 明确禁止）。

---

## 8. API E2E（真实链路）

| 项 | 值 |
|---|---|
| 端点 | `POST /api/ai/chat` |
| 请求体 | `{"question": "采购入库怎么操作？", "project_id": "vietnam-wms"}` |
| 预期状态码 | `200` |
| 预期 route | `"rag"` |
| 预期 content | 非空，且 ≠ RagService.canned empty answer |
| 预期 sources | 非空，含 `document_id` / `chunk_id` / `similarity` |
| 真实链路验证 | ✅ retrieval 命中本次插入的 knowledge_chunk（document_id 匹配） |

---

## 9. 外部 API 调用控制（opt-in）

| 开关 | 默认行为 |
|---|---|
| `RUN_REAL_RAG_E2E` 未设置 | 真实 E2E 测试 SKIPPED（不调外部 API） |
| `RUN_DB_TESTS` 未设置 | 真实 E2E 测试 SKIPPED（不连真实 DB） |
| `pytest -q` 默认 | 真实 E2E 测试 SKIPPED（无需任何环境变量） |

三层防护：
1. 模块级 skipif（无任一开关 → skip）
2. fixture 内 `_require_real_dependencies()`（开关齐备但 `.env` 缺 Key → skip）
3. 单元测试（`TestRagFailureUnitTests`）不依赖任何 opt-in flag

不新增互相重叠的开关；遵循项目既有命名风格
（`RUN_REAL_LLM_TEST` / `RUN_REAL_TOOL_CALLING_TEST` / `RUN_DB_TESTS`）。

---

## 10. 测试结果

### Unit Test（默认 `pytest -q`）

| 测试 | 结果 |
|---|---|
| `tests/test_rag_real_e2e.py::TestRagFailureUnitTests::*` | ✅ 4 passed |
| 全部既有测试（1111 项 + 4 项新增失败情况） | ✅ 1115 passed |

### DB Integration Test（`RUN_DB_TESTS=1 pytest -q`）

| 测试 | 结果 |
|---|---|
| 含 `test_real_wms_kb_ingestion` / `test_embedding_db` / `test_vector_search_service` / 等 | ✅ 1226 passed, 36 skipped |

### Real RAG E2E（`RUN_REAL_RAG_E2E=1 RUN_DB_TESTS=1`）

| 端到端验证项（任务书 §十五） | 结果 |
|---|---|
| 1. Router 路由到 RAG | ✅ `route == "rag"` |
| 2. API 返回 200 | ✅ HTTP 200 |
| 3. RAG content 非空（非 canned） | ✅ DeepSeek 生成的 100+ 字符回答 |
| 4. Sources 非空 + 含 document_id/chunk_id/similarity | ✅ 1 source, similarity=0.7082 |
| 5. Retrieval 来自 knowledge_chunk | ✅ document_id 命中本次插入 |
| 6. project_id 透传 | ✅ 请求中携带 `project_id="vietnam-wms"` 不破坏 RAG |

外部 API 调用次数（单次 E2E）：
- Embedding API：1 次（ingestion，1 chunk）
- LLM API（DeepSeek）：1 次（chat）
- 总计：2 次真实 API 调用（符合任务书 §二十 成本控制）

---

## 11. 数据清理（任务书 §十六）

`tests/test_rag_real_e2e.py` 使用 `_tracked_doc_ids` + `_cleanup` fixture：

```python
@pytest.fixture
def _cleanup(engine, _tracked_doc_ids):
    yield
    if not _tracked_doc_ids:
        return
    ids = list(_tracked_doc_ids)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM knowledge_document WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
```

只删除本次测试插入的 document_id（FK CASCADE 自动清理 chunks），
**不**动 CLI 手动加载的其它文档。

**实测**：连续运行两次 E2E 后，`knowledge_document = 0`, `knowledge_chunk = 0`
（基线为空表，长期 DB 无残留）。

---

## 12. 当前限制

1. **Reranker 未参与**：当前 RAG 链路不经过 bge-reranker-v2-m3。
   Reranker 仅作为离线评估工具，不在生产 RAG 链路中。
2. **真实 E2E LLM 响应非确定性**：DeepSeek 对同一问题的回答会变化；
   测试 assertion 只验证"内容 ≠ canned empty answer"，不锁死具体文字。
3. **每次 E2E 仅一次真实 LLM 调用**（任务书 §二十 控制成本）。
   不进行大规模参数对比 / 反复 ingestion。
4. **单文档检索范围**：测试文档体量小（~150 字），触发 DeepSeek 的
   "知识库内容不足" anti-hallucination 判断属于 LLM 正常行为。
   真实生产知识库（CLI 加载的 `wms-basic-operations.md`）体量充足，
   不存在此问题。
5. **RagService 的 `context` 参数** 未在 RAG 路径使用
   （`AIOrchestrationResult._run_rag` 当前不读 `context`）；
   project_id 在 TEXT_TO_SQL 路径才会进入 SQL metadata，
   在 RAG 路径仅通过 `_build_orchestrator_for_project_id` 完成
   ProjectContextProvider 注入（不破坏 RAG 链路）。

---

## 13. 修改/新增文件清单

| 操作 | 路径 |
|---|---|
| 修改 | `backend/app/services/ai_orchestrator_service.py`（+10 行：rag_service 默认值改为 RagService()） |
| 修改 | `backend/app/api/orchestrator_chat.py`（注释更新：说明 Phase 3.7.13 修复） |
| 新增 | `tests/test_rag_real_e2e.py`（真实 E2E + 4 个失败情况单元测试） |
| 修改 | `docs/decisions/Phase/Phase 3.7.13：真实 RAG E2E.md`（本 ADR） |

**未修改**（任务书 §十八 明确禁止）：
- `RagService` / `VectorSearchService` / `EmbeddingClient` / `LLMClient`
- `RerankerClient`（Reranker 不在 RAG 链路中）
- `SQL Validator` / `SQL Executor` / `TextTo-SQL`
- `ToolRegistry` / `Tool Definitions`
- 数据库表结构 / Schema

---

## 14. 验证清单（任务书 §二十三）

```
✓ FakeRagService 未参与最终真实 E2E
✓ FakeLLM 未参与最终真实 E2E
✓ FakeEmbedding 未参与最终真实 E2E
✓ PostgreSQL + pgvector 真实参与
✓ 真实知识数据参与检索
✓ 返回真实 LLM Answer
✓ API /api/ai/chat 成功（HTTP 200）
✓ 不污染长期数据库（清理后 doc=0, chunk=0）
✓ 默认 pytest 不调用真实 LLM（RUN_REAL_RAG_E2E 未设置 → skip）
✓ Real RAG E2E opt-in（双开关：RUN_REAL_RAG_E2E + RUN_DB_TESTS）
✓ 不修改 Text-to-SQL
✓ 不修改 Tool
✓ 不引入 Agent
✓ 不引入 LangGraph
✓ 不引入 MCP
```