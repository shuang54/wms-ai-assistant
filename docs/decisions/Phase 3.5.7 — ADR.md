# Phase 3.5.7 — RAG Evaluation & Retrieval Quality：决策记录

> 状态：Active（Phase 3.5.7 实现已完成）。
> 范围：建立可重复运行的 RAG 检索质量评估 Baseline；不修改 RAG 主链路。

## 决策摘要

| # | 决策点                       | 选择                                                         | 原因                                                                                   |
| - | ---------------------------- | ------------------------------------------------------------ | -------------------------------------------------------------------------------------- |
| 1 | 第一版评估指标               | **Top-K Keyword Hit Rate**（matched_cases / total_cases）    | 最简单、可解释、与检索链路直接相关；MRR / NDCG / Recall@K 留待后续 Phase                |
| 2 | DTO 数据结构                 | **frozen dataclass**，不暴露 ORM / embedding / DB connection | 与现有 `VectorSearchResult` / `RagResponse` 风格一致；类型稳定；序列化友好             |
| 3 | 单 case 异常处理             | **结构化失败记录**（不吞、不阻断其余 case）                  | 评估是离线批处理任务；一个 case 失败不应拖垮整次评估；与现有 Logger 一起记录 cause       |
| 4 | 评估 Service 不持有 LLM      | 仅依赖 `VectorSearchService.search()`                        | 评估只测检索链路，避免误调 DeepSeek 产生费用；通过 `_llm_client` 不存在的属性 + 源码扫描双重断言 |
| 5 | 数据集来源                   | `tests/fixtures/rag/evaluation_cases.json`（≥ 10 WMS cases）  | 与现有 `tests/fixtures/knowledge/` 一致；JSON 便于人工编辑 / 版本控制                     |
| 6 | 真实评估测试                 | 默认 `SKIP`，`RUN_REAL_RAG_EVAL=1` 启用                      | 不依赖 `RUN_DB_TESTS`；独立可切换；只调 Embedding + DB，不调 LLM                         |

## 已避免的反模式

- ❌ 重新实现 embedding / pgvector 距离计算 → ✅ 复用 `VectorSearchService.search`
- ❌ 使用 LLM 判断「是否相关」 → ✅ 简单 substring 匹配 + case-insensitive
- ❌ 评估过程中调用 DeepSeek 生成 → ✅ 源码 + 行为双层断言（`test_no_llm_call_during_real_evaluation`）
- ❌ 假设具体 document_id → ✅ 优先用 `expected_keywords`（且 source / title 可选）
- ❌ 引入 Reranker / Hybrid Search / Query Rewrite 优化 → ✅ 留到后续优化阶段，先建立 Baseline

## 后续 Phase 待做（**本阶段不实现**）

- Recall@K / MRR / NDCG（更精细指标）
- 失败 case 的人工标注与 diff 展示
- 真实知识库注入后的复测对比

## 验收对照

详见任务规范 § 十五：

| 项                                       | 状态 |
| ---------------------------------------- | ---- |
| evaluation dataset ≥ 10 cases            | ✅   |
| evaluation service 完成                  | ✅   |
| VectorSearchService 被复用               | ✅   |
| 不调用 LLM                                | ✅   |
| Top-K Keyword Hit Rate 可计算            | ✅   |
| 单元测试全部通过                          | ✅ 15/15 |
| DB 测试通过                              | ✅ 329/7 |
| 默认不消耗 Embedding API 以外的 LLM 额度 | ✅（默认 SKIP） |
| Real Evaluation 默认 SKIP                | ✅ |
| 不修改 Chat / RAG API 协议                | ✅ |
| 不引入 Agent / Tool / LangGraph / MCP      | ✅ |
| 不修改核心 Vector Search 算法             | ✅ |