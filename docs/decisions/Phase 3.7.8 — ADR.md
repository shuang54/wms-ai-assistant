# Phase 3.7.8 — ADR：AI Router

日期：2026-09-24
状态：已实施（Phase 3.7.8）

## Context

LLM 入口（Chat / API）将面对三类问题：

*   知识 / 流程 / 操作（→ RAG）
*   已注册的固定业务能力（→ Tool）
*   数据分析 / 统计 / 聚合（→ Text-to-SQL）

直接让 LLM 在 Prompt 中“选能力”会让系统失去：① 路由确定性、
② 安全边界（LLM 可能在 Prompt injection 下越权）、③ 决策可审计。

需要独立的 **AI Router**：纯路由决策层，不执行任何下游能力。

## Decision

新增 `backend/app/services/ai_router_service.py`：

```text
User Question
    ↓
AIRouterService
    ├─ ① Rule-first: TOOL capability 匹配（最具体）
    ├─ ② Rule-first: 数据分析特征（优先级 > 知识，避免"是什么"误吞）
    ├─ ③ Rule-first: 知识 / 流程特征
    ├─ ④ LLM fallback（受 AI_ROUTER_LLM_FALLBACK_ENABLED 控制）
    │       ├─ 严格 JSON 解析
    │       ├─ route ∈ {rag, tool, text_to_sql} 校验
    │       └─ 越权 / 空响应 / JSON 非法 → fallback RAG
    └─ ⑤ Conservative fallback: RAG（拒绝盲打 Text-to-SQL 入库）
        ↓
RouteDecision（frozen，含 source: rule / tool_match / llm / fallback）
```

策略原则：

*   **Rule-first**：绝大多数问题不需要 LLM；
*   **分析 > 知识**：含数据分析意图的句子优先判为 Text-to-SQL，
    避免"库存最多是什么"被宽泛知识词误判；
*   **未知 → RAG**：保守兜底，绝不让未识别问题直接进入数据库路径。

## Tool Capability Registry

复用已有 `backend/app.tools.registry.ToolRegistry`（Phase 3.6.1），
通过新增 `ToolRegistryCapabilityAdapter` 暴露为只读 metadata：

```text
ToolDefinition
    ↓ ToolRegistryCapabilityAdapter（无 handler / 无参数 schema 值）
ToolCapability(name, description, aliases=字段名)
```

Adapter 在 `list_definitions()` 异常时返回空元组，不污染路由层。

测试场景下亦可使用 `InMemoryToolCapabilityRegistry`。

## Security

*   **LLM 输出 = 不可信输入**：
    route 必须是 ``{rag, tool, text_to_sql}`` 之一，否则丢弃并
    fallback RAG；reason 截断至 200 字符防异常 payload；
*   **Prompt injection 不可执行**：用户问题视为 `data` 而非
    `instruction`；即使 LLM 越权回 `delete_database`，Router 拒绝；
*   **静态隔离**：Router 模块不依赖 / 不调用 SQLAlchemy / sqlglot /
    RAG / Tool handler / TextToSQL 生成链路 / 校验 / 执行；
*   **消息脱敏**：底层 LLM 异常只透传类型名；
*   **不会触发下游**：注入诱饵测试断言
    `TextToSQLService.generate` 与 `SQLExecutorService.execute`
    调用计数恒为 0。

## Responsibilities

```text
AIRouter        决定走哪条路径（不执行）
RAG Service     知识 / 流程回答
ToolRegistry    固定业务能力执行
TextToSQL       数据分析（Generator → Validator → Executor）
AI Orchestrator 未来 Application 层根据 RouteDecision 调度（未实现）
```

## Prompt

`backend/app/prompts/router_system.txt`：明确三类 route 定义、
禁止事项（不可执行、不可篡改、用户问题视为 data）、强制 JSON 输出。
`backend/app/prompts/router_user.txt`：使用 ``string.Template``
（``$question`` 占位）以兼容示例 JSON 中的 ``{...}`` 字面量。

## Config

```text
AI_ROUTER_LLM_FALLBACK_ENABLED   （默认 true；false → 直接 fallback）
```

## Future

*   Phase 3.7.9+（未开始）：Application Orchestration / API / Chat 集成；
*   当语义层成熟时，规则可基于 ``ProjectSemantic.table_subjects``
    进一步精细化；
*   Embedding-based Router 备用实现（接口不变，替换实现）。