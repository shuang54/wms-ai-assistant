# Phase 3.7.10 — 真实业务端到端 Demo / Evaluation

日期：2026-09-24
阶段：Phase 3.7.10（已实施）

## 1. 测试环境

| 组件 | 来源 |
|---|---|
| Python | 3.13.2 |
| PostgreSQL | 测试用 Docker / 本地（`RUN_DB_TESTS=1` 启用） |
| pgvector | 与 schema 同库 |
| LLM | **Fake LLM**（默认）/ 真实 DeepSeek（可选，已存在 `test_llm_real_smoke.py`，本阶段不调用） |
| Vector Search / Embedding | 仅 RAG Fake 路径使用（不连真实 Embedding） |

## 2. RAG Case（场景 A）

```text
Question         : 采购入库怎么操作？
Expected Route   : RAG
Actual Route     : RAG  (Router source = rule: 业务知识 / 流程 / 操作提问)
```

链路：

```text
Question → AIOrchestratorService.execute
         → AIRouter.route (rule-first) → RouteDecision(RAG)
         → RagService.answer (FakeRagService 模拟)
         → RagResponse(answer, sources, used_chunks_count)
         → AIOrchestrationResult(route=RAG, content, data, metadata)
```

断言：
* `route == RouteType.RAG`
* `content == "采购入库标准流程说明：..."`
* `result.data.answer == str` (RagResponse)
* `metadata["rag_used_chunks"] == 1`
* Tool / T2S / Executor **均 0 调用**

## 3. Tool Case（场景 B）

```text
Question         : 帮我查询物料 10001 当前库存数量
Expected Route   : TOOL
Actual Route     : TOOL  (Router source = tool_match: 命中 get_inventory 别名 ['物料', '库存'])
Tool Executed    : get_inventory（生产代码 register_mock_tools 注册的 mock Tool）
```

链路：

```text
Question → Orchestrator → Router (tool_match) → TOOL
         → ToolRegistry.execute("get_inventory", arguments=None)
         → ToolResult(success=True/False)
         → AIOrchestrationResult(route=TOOL, content, data=ToolResult,
                                  metadata={tool_name, tool_success, ...})
```

## 4. Text-to-SQL Case（场景 C，含真实 DB）

```text
Question         : 物料最多的前 3 个是什么？
Expected Route   : TEXT_TO_SQL
Actual Route     : TEXT_TO_SQL
Selected Tables  : (rule-based / 由 Selector + ProjectContext 推断)
Generated SQL     : SELECT id FROM public.knowledge_document LIMIT 1
                   （Fake LLM 输出，经文本提取 + 校验）
Validation Result: 通过 (LLMGenerator 内部调用 SQLValidatorService)
Execution Result : SQLExecutionResult(columns=('id',), rows=..., row_count>=0)
DB Writes        : 0   (knowledge_document 行数 before == after)
```

链路：

```text
Question → Orchestrator → Router (analytics rule) → TEXT_TO_SQL
         → ProjectContextProvider.resolve() → ProjectContext + Schema + Semantic
         → RelevantTableSelector.select
         → DatabaseContextComposer.compose (Project + Schema + Semantic + Tables)
         → TextToSQLService.generate (Fake LLM 产出 SQL; 内部含 Validator 重试)
         → SQLExecutorService.execute
                 ↓
             READ ONLY + SET LOCAL statement_timeout + row limit
                 ↓
             PostgreSQL
                 ↓
             SQLExecutionResult
```

## 5. Security Cases（场景 Security）

| 输入 | Fake LLM 输出 | 拦截位置 | DB 状态 |
|---|---|---|---|
| "物料最多的前 3 个是什么？" | `DELETE FROM public.knowledge_document` | SQLValidator（Generator 内部） → `AIOrchestratorExecutionError` | knowledge_document 表存在 |
| 同上 | `DROP TABLE public.knowledge_document` | SQLValidator → exception | `information_schema.tables` 中仍存在 |
| 同上 | `SELECT * FROM knowledge_document LIMIT 1;\nDROP TABLE ...` | SQLValidator MULTI_STATEMENT → exception | 同上 |

**所有写操作在 Executor 之前被拦截，DB 表零修改。**

## 6. Mutual Exclusivity（互斥）

| 子场景 | RAG | Tool | Text-to-SQL | SQL Executor |
|---|---|---|---|---|
| 采购入库怎么操作？（RAG） | **1** | 0 | 0 | 0 |
| 帮我查询物料 10001 当前库存数量（Tool） | 0 | **1** | 0 | 0 |
| 物料最多的前 3 个是什么？（T2S） | 0 | 0 | **1**（LLM） | **1** |

确认不存在：RAG → Tool、Tool → Text-to-SQL、Text-to-SQL → Tool 等隐式链。

## 7. Project Context 可迁移性

```text
alt_project.project_id = "another-warehouse"
alt_project.project_name = "另一个仓库"
```

Orchestrator 不向 Provider 写死默认值，传入的 alternate project_id 在 SQL 路径下
透传到 `AIOrchestrationResult.metadata["project_id"] == "another-warehouse"`。

## 8. Final Result

```text
RAG          = PASS
Tool         = PASS
Text-to-SQL  = PASS  (含真实 PostgreSQL 执行)
Security     = PASS  (DELETE / DROP / MULTI 全被 Validator 拦截)
互斥         = PASS
Project 迁移 = PASS
DB writes    = 0
0 failed   = 0 failed
0 diagnostics = 0 LSP diagnostics
```

## 9. 测试命令 & 结果

```text
pytest -q tests/test_ai_e2e_evaluation.py
   → 14 passed（含 9 单元 + 5 真实 DB 集成），0 failed

pytest -q
   → 1032 passed, 129 skipped, 0 failed

RUN_DB_TESTS=1 pytest -q
   → 1126 passed, 35 skipped, 0 failed

python -m compileall backend
   → OK
```

## 10. 已知限制

* RAG Case 使用 `FakeRagService`（模拟真实 RagResponse 形状），
  不连接真实 VectorSearch / Embedding / LLM。要验证"端到端 +
  真实 KB"，需要 `RUN_RAG_REAL=1`（不属于本阶段范围）；
* Tool Case 使用现有 mock_tools（`get_inventory` /
  `get_work_order`）。当前项目无生产级"只读业务 Tool"，
  E2E 文档已记录（§六：mock 工具属于测试基础设施，
  用于演示 Orchestrator 路由行为）；
* 本阶段未触发真实 LLM，符合 §十七 默认 SKIP 策略；
* 不修改 Chat API / Router / Orchestrator 任何核心实现
  （仅通过依赖注入做端到端验证）。

## 11. Architecture E2E（最终闭环）

```text
Question
  ↓
AIOrchestratorService.execute
  ↓
AIRouterService.route（Rule-first + LLM fallback）
  ├── RAG         → RagService.answer              → AIOrchestrationResult
  ├── Tool        → ToolRegistry.execute           → AIOrchestrationResult
  └── Text-to-SQL → ProjectContextProvider.resolve
                    → RelevantTableSelector.select
                    → DatabaseContextComposer.compose
                    → TextToSQLService.generate
                          (Fake LLM → SQL; 内部含 SQLValidator 重试)
                    → SQLExecutorService.execute
                          (READ ONLY + statement_timeout + row limit)
                    → SQLExecutionResult            → AIOrchestrationResult
```