# Phase 3.7.9 — ADR：AI Orchestrator

日期：2026-09-24
状态：已实施（Phase 3.7.9）

## Context

前 8 个阶段沉淀了完整的 AI 能力栈：

* AIRouter（3.7.8）→ 决定走 RAG / Tool / Text-to-SQL
* RAG（3.5）/ Tool（3.6）/ Text-to-SQL（3.7.4-3.7.7）→ 三类能力

但这些能力**互相独立**，没有形成端到端可执行的链路。
需要一个组合层把它们串起来，由 Application / API 层消费。

## Decision

新增 `backend/app/services/ai_orchestrator_service.py`：

```text
User Question
    ↓
AIRouter.route（3.7.8，单次决策）
    ↓
AIOrchestratorService.execute
    ├── route == RAG          → RagService.answer
    ├── route == TOOL         → ToolRegistry.execute（按 Capability 选 Tool）
    └── route == TEXT_TO_SQL  → RelevantTableSelector
                                  ↓
                                DatabaseContextComposer
                                  ↓
                                TextToSQLService.generate
                                  ↓
                                SQLExecutor.execute
    ↓
AIOrchestrationResult（frozen）
```

执行纪律：

* **一次性**（no Agent / no Loop / no Replanning）；
* **单一 route**（不根据执行结果再路由）；
* **不重写能力**（全部依赖注入，仅组合）；
* **路径全复用**（SQL 必须经过 Generator → Executor，绕过 Vertex 双重保护零代码）。

## Tool Routing 策略

Router 只决定 `route == TOOL`，不指定具体 Tool。
Orchestrator 用 `_resolve_tool_name(question, registry)` 从
`ToolDefinition.parameters.properties` 的字段名（alias）+ 中文
2-gram description 关键词两个层次匹配，挑出 1 个 Tool。
**args 提取留给 Tool 自身 schema 校验**（Phase 3.7.9 不实现
从自然语言抽取 JSON 参数；Tool 参数不足时 registry 已归一为
`ToolResult(success=False)`，不抛、不掩盖）。

## ProjectContextProvider

Orchestrator **不**直接创建 Engine / Session / LLM Client。
通过 ``ProjectContextProvider`` Protocol 获得
`(ProjectContext, DatabaseSchema, ProjectSemantic)`：

* 默认实现 = ``DefaultProjectContextProvider``，包装
  ``SchemaExplorerService`` + ``ProjectSemanticLoader``（不连
  Orchestrator 基础设施层职责）；
* 测试 = ``FakeProjectProvider``，直接返回预置对象。

## 安全边界（纵深防御四层不变）

```text
Prompt / Router           = 软约束（路由决策）
TextToSQLService          = 静态 SQL 安全边界（3.7.6）
SQLExecutor               = 运行时 READ ONLY + timeout + limits（3.7.7）
Orchestrator              = 组合层；不绕过上述任何层
只读数据库账号（部署层）  = 部署时配置
```

Orchestrator 单元 / 静态测试已断言：

* 源码不含 `engine.execute` / `.execute(text(` / `create_engine` /
  `sqlglot` / `SQLValidatorService` / `create_llm_client`；
* 源码不含 `eval` / `exec` / `subprocess` / `os.system`；
* 源码不 `from backend.app.api ...`（API 解耦）；
* Router 源码不含 `ai_orchestrator` / `Orchestrator`（无循环依赖）。

## Responsibilities

| 层 | 职责 |
|---|---|
| `AIRouter` | 决定走 RAG / Tool / Text-to-SQL |
| `RagService` | 知识库检索 + 回答生成（3.5） |
| `ToolRegistry` | 固定业务能力执行（3.6） |
| `RelevantTableSelector` | 问题 → 相关表（3.7.4） |
| `DatabaseContextComposer` | Schema + Semantic + Project → Context（3.7.3） |
| `TextToSQLService` | 生成 + Validator 重试（3.7.6） |
| `SQLExecutor` | READ ONLY + timeout + limits（3.7.7） |
| **`AIOrchestratorService`** | **组合上述 7 个组件为单一执行链（本阶段）** |
| AI Application API | 未来 Application 层入口（未实现） |

## Known Limitations（后续优化点）

* TOOL 路径暂不支持从自然语言抽取 JSON 参数；Tool 缺参数时
  registry 已返回 `success=False`，但用户体验可改进；
* Orchestrator 异步主路径里 `ProjectContextProvider.resolve()`
  仍可能阻塞事件循环；下一步把它包到 `asyncio.to_thread`；
* 一次性执行：未来若需要“分析 → 工具 → 再分析”链式，需要
  在明确安全边界下单独设计（不属本阶段范围）。

## Future

* Phase 3.7.10+（未开始）：Chat API 接入 Orchestrator、多轮
  Session 上下文、API 限流 / 监控 / 审计；
* Tool 参数自动抽取（LLM Function Calling 风格的简化版）。