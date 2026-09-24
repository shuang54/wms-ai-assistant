# Phase 3.7.14：RAG Reranker 正式接入 — ADR

> **实际实施结果与技术决策记录**。
>
> 将 Phase 3.5.12 完成离线评测的 `bge-reranker-v2-m3` 正式接入 RAG 在线链路，
> 复用现有 `BGERerankerClient`，最小改动、默认关闭（灰度可控）。

---

## 1. 背景

Phase 3.5.12/3.5.13 完成了 `BGERerankerClient`（Cross-Encoder
bge-reranker-v2-m3）及其离线评估（`RerankerEvaluationService`），但该能力
**未接入生产 RAG**：`RagService` 的链路为
`VectorSearch → ContextBuilder → LLM`，Reranker 仅作为离线实验工具。

Phase 3.7.13 的真实 RAG E2E 已验证无 Reranker 基线链路可用，
本阶段把 Reranker 插入该链路。

## 2. 当前 RAG 链路（接入后）

```text
POST /api/ai/chat → Orchestrator → RagService
    ↓
VectorSearchService.search(top_k=candidate_top_k)     # 开启时扩大召回
    ↓ VectorSearchResult[]（similarity 降序）
BGERerankerClient.rerank(query, contents)             # 本地 Cross-Encoder
    ↓ list[float]（sigmoid 归一化 relevance score）
_rerank_chunks：score 降序稳定排序 → 截断至 top_k
    ↓
ContextBuilder.build(results)
    ↓
LLMClient（DeepSeek deepseek-chat）
    ↓
RagResponse(answer, sources)
```

`RERANKER_ENABLED=false`（默认）时，链路与 Phase 3.5.4 / 3.7.13 **完全一致**
（Reranker 代码路径整体旁路，vector search 的 top_k 直通）。

## 3. 为什么 Reranker 放在 Vector Search 之后

- **Embedding 是双塔召回**：query 与 doc 独立编码，速度快但粗排；
  Cross-Encoder 把 (query, doc) 拼接过同一个 transformer，精度高但代价
  与候选数线性相关。因此"先用 Embedding 从全库捞出 candidate_top_k，
  再用 Cross-Encoder 精排"是标准两段式检索架构。
- 放在 Embedding 之前不可能：Reranker 需要先有候选集合。
- 放在 ContextBuilder 之后无意义：重排的目的就是决定哪些片段进入
  有限长度的 Context。
- 顺序由测试 `TestPipelineOrder::test_strict_order_search_rerank_context_llm`
  显式锁定：`vector_search → reranker → context_builder → llm_chat`。

## 4. Protocol / 抽象设计

**复用现有抽象，未新建 Protocol**：Phase 3.5.12 已有
`backend/app/reranker/client.py::RerankerClient`（抽象基类，
接口 `rerank(query, documents: list[str]) -> list[float]`），
`BGERerankerClient` 是其实现。

- `RagService` 依赖 `RerankerClient` 抽象（构造参数 `reranker_client`），
  **不**直接依赖 `BGERerankerClient` → 未来可替换其他 Reranker 实现。
- `BGERerankerClient` 公共行为**零修改**。
- 新增适配函数 `rag_service._rerank_chunks()`：负责
  "调 client 打分 → 校验分数数量 → 稳定降序排序 → 截断 top_k"，
  Reranker 的排序逻辑只存在这一处（不复制到多个地方）。

设计约束：

- Reranker 不执行数据库操作、不调用 LLM、不修改知识库数据。
- 分数数量与候选数不一致 → `RagError`（防御 Fake / 自定义实现返回脏数据）。
- `RerankerError` 家族异常**原样透传**（与 Embedding / LLM 阶段同一纪律），
  绝不吞掉、绝不降级为未排序结果继续执行。

## 5. 配置项

`backend/app/config.py::RerankerSettings`（新增 2 项，复用既有 `enabled`）：

| 环境变量 | 默认 | 钳制 | 含义 |
|---|---|---|---|
| `RERANKER_ENABLED` | `false` | — | 总开关（**默认关闭**，保证既有行为不变、可灰度） |
| `RERANKER_CANDIDATE_TOP_K` | `10` | [1, 50] | 开启后 Vector Search 的召回条数 |
| `RERANKER_TOP_K` | `3` | [1, 50] | 开启后最终送入 ContextBuilder 的条数（调用方未显式传 top_k 时） |

钳制上界 50 与 `VectorSearchService.MAX_TOP_K` 对齐，
异常值（如 9999 / -5）被钳到边界（有测试锁定），不会导致无限资源消耗。

`top_k` 语义说明：`RagService.answer(top_k=N)` 的 `top_k` 始终表示
**最终**纳入回答的片段数；开启 Reranker 时召回窗口自动扩大为
`max(N, RERANKER_CANDIDATE_TOP_K)`。

## 6. 开启 / 关闭行为

| | `RERANKER_ENABLED=false`（默认） | `RERANKER_ENABLED=true` |
|---|---|---|
| Vector Search top_k | `RAG_TOP_K`（默认 5） | `max(top_k, 10)` |
| Reranker | 不参与（即使注入了 client） | 参与（注入优先，否则懒加载默认单例） |
| 最终 top_k 默认 | `RAG_TOP_K=5` | `RERANKER_TOP_K=3` |
| 空检索 | canned answer（不调 LLM） | canned answer（不调 Reranker、不调 LLM） |
| Reranker 失败 | —（无此阶段） | 异常透传（HTTP 映射见下） |

配置开关是链路形态的**唯一权威**：即使注入了 `reranker_client`，
`enabled=false` 时也不参与——保证"关闭 = 完全旁路"可被测试验证。

错误映射（`backend/app/api/_rag_error_mapping.py` 新增）：
`RerankerConfigurationError → 503`，`RerankerModelError → 502`，
`RerankerInputError / RerankerError → 500`（RAG 直连 API `/api/rag/answer`
路径；经 `/api/ai/chat` Orchestrator 路径统一包装为
`AIOrchestratorExecutionError → 500`，与 Phase 3.7.13 行为一致）。

## 7. 测试结果

### Unit / Integration Test（`tests/test_rag_reranker_integration.py`，新增 26 项）

- 适配层 `_rerank_chunks`：正常排序、top_k 截断、空候选、单候选、
  同分稳定性、异常透传、分数数量防御、输入错误透传
- RagService 开关行为：关闭（Reranker=0）、开启（Reranker=1，召回窗口扩大）、
  显式 top_k、候选不足、空检索短路、异常透传（LLM 不被调用）、懒加载默认单例
- 顺序测试：`vector_search → reranker → context_builder → llm_chat`（严格锁定）
- 重排效果：3 候选 A/B/C 被 Fake Reranker 反转 → ContextBuilder 收到 C/B/A，
  且 LLM user prompt 中顺序一致（CCC 位置 < BBB < AAA）
- API E2E（Fake Reranker 注入）：`POST /api/ai/chat` 200 + route=rag +
  reranker 参与验证（开启 / 关闭两种）
- 配置默认值与钳制（9999 → 50，-5 → 1）

### 全量回归

| 命令 | 结果 |
|---|---|
| `pytest -q` | ✅ **1141 passed, 148 skipped**（默认不加载模型、不调外部 API） |
| `RUN_DB_TESTS=1 pytest -q` | ✅ **1252 passed, 37 skipped** |
| `python -m compileall backend` | ✅ 通过 |
| LSP / lint | ✅ 0 错误 |

### 真实 RAG + Reranker E2E（opt-in，已执行）

```
RUN_REAL_RAG_E2E=1 RUN_DB_TESTS=1 RERANKER_ENABLED=true
pytest tests/test_rag_real_e2e.py -v
```

结果：**6 passed**（baseline E2E + Reranker E2E + 4 项失败情况单测）。

Reranker E2E 实测数据：

| 指标 | 值 |
|---|---|
| Question | 采购入库怎么操作？ |
| Route | rag |
| Candidates（vector search 召回） | 3 |
| Kept（rerank 后保留） | 3 |
| Rerank 耗时（含首次模型懒加载） | 81011.7 ms |
| 总 RAG 耗时 | 83970.5 ms |
| Top similarity | 0.7398 |
| Content | DeepSeek 生成的采购入库五步流程回答 |

E2E 同时验证：`BGERerankerClient._model` 被真实加载、
`"RAG rerank applied"` / `"RAG answered" (reranker_used=True)` 日志存在、
sources 命中本次插入文档、RAG 链路对 DB 只读（前后 doc 数一致）。

外部 API 成本（单次 Reranker E2E）：
Embedding 2 次（ingestion + query）+ DeepSeek 1 次；
Reranker 为本地 CPU 推理（无 API 费用）。

## 8. 性能结果

- 首次调用含模型懒加载（CPU 上 bge-reranker-v2-m3 约 80s，
  含 568M 参数加载 + 3 候选推理）；模型进程内只加载一次
  （`BGERerankerClient._load_lock` 线程锁保证）。
- 未重新设计推理框架（任务书 §十）：未引入 Redis / Celery / 多进程 /
  GPU 服务 / 微服务 / 异步推理框架。
- 耗时记录复用现有 logging 机制：`RAG rerank applied`
  （candidate_count / kept_count）、`RAG answered`
  （新增 `reranker_used` / `rerank_elapsed_ms` 字段）。

## 9. 数据清理验证

- 集成测试（Fake Reranker）：不触 DB。
- 真实 Reranker E2E：测试文档经既有 ingestion pipeline 插入，
  teardown 按 `document_id` 精确删除（FK CASCADE 清 chunks）。
- E2E 全部跑完后实测：`knowledge_document = 0`，`knowledge_chunk = 0`
  （长期数据库无污染）。

## 10. 已知限制

1. **CPU 推理延迟高**：本机无 CUDA，首次调用（含模型加载）约 80s；
   稳态下 3 候选推理仍在秒级。生产启用建议 GPU 或调小
   `RERANKER_CANDIDATE_TOP_K`。
2. **默认关闭**：`RERANKER_ENABLED=false`，生产灰度需显式开启。
3. **Reranker 分数未落库 / 未返回**：sources 中的 `similarity` 仍是
   Embedding cosine similarity（保持既有 API 协议不变）；rerank score
   仅用于内部排序（日志记录统计量）。
4. **同步阻塞**：Cross-Encoder 推理在事件循环线程内同步执行
   （`torch.inference_mode`）；候选数被钳制在 ≤50，MVP 阶段可接受，
   后续如需优化可移入线程池（不在本阶段范围）。
5. **`/api/rag/answer` 与 `/api/ai/chat` 两条 RAG 路径均生效**，
   但 Orchestrator 路径的错误统一包装为 `AIOrchestratorExecutionError(500)`，
   不区分 502/503 细类（Phase 3.7.13 既有行为，未改动）。

## 11. 后续方向（仅记录，不在本阶段实施）

- Reranker 推理移入线程池 / 进程池，避免阻塞事件循环。
- 评估把 rerank score 纳入 `RagSource`（需 API 协议变更评审）。
- 基于真实查询日志做 A/B 评估，决定是否默认开启。
- GPU 部署与批处理调优（复用 Phase 3.5.13 benchmark 结论）。

## 12. 修改 / 新增文件清单

| 操作 | 路径 | 说明 |
|---|---|---|
| 修改 | `backend/app/config.py` | `RerankerSettings` 新增 `candidate_top_k` / `top_k`（钳制 [1,50]）；文档更新为"已接入 RAG" |
| 修改 | `backend/app/services/rag_service.py` | 构造参数 `reranker_client`；`_get_reranker()`；`answer()` 插入 rerank 阶段；`_rerank_chunks()` 适配函数；日志新增 reranker 字段 |
| 修改 | `backend/app/api/_rag_error_mapping.py` | 新增 Reranker 异常家族 → HTTP 映射（503/502/500） |
| 修改 | `.env.example` | Reranker 段更新 + 两个新变量 |
| 新增 | `tests/test_rag_reranker_integration.py` | 26 项测试（§9.1–9.5 全覆盖） |
| 修改 | `tests/test_rag_real_e2e.py` | baseline E2E 固定禁用态；新增 `TestRealRagRerankerEndToEnd`（三层 opt-in 门控） |
| 新增 | 本 ADR | — |

**未修改**（任务书 §二禁令）：
`BGERerankerClient` 公共行为、`VectorSearchService`、`EmbeddingClient`、
`LLMClient`、AI Router、AI Orchestrator 核心执行逻辑、Text-to-SQL、
SQL Validator / Executor、Tool Framework、`/api/chat`、数据库结构。
