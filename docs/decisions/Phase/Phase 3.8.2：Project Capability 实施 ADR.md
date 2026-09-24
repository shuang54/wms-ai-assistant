# Phase 3.8.2：Project Capability / Business Configuration — 实施 ADR

> **实际实施结果与技术决策记录**（任务书副本见
> `Phase 3.8.2：Project Capability  Business Configuration.md`）。
>
> 在 Phase 3.8.1（project_id → DataSource → Engine 数据源切换）基础上，
> 解决"不同 Project 可用业务能力仍然固定"的问题：
> 项目除 DataSource / Semantic 外，还能声明允许的
> **Tools / Knowledge / Text-to-SQL** 能力集合。

---

## 1. 为什么需要 Project Capability

Phase 3.8.1 之后，project_id 能切换数据源，但每个项目的**能力面**
完全相同：Router 永远看到全部 Tool、RAG 与 Text-to-SQL 恒可用。
无法表达"项目 B 只允许知识问答，不允许查询业务数据"这类业务约束。

本阶段引入项目级能力模型：

```text
Project
 ├── DataSource      （Phase 3.8.1）
 ├── Semantic        （Phase 3.7.3）
 └── Capabilities    （本阶段）
      ├── tool_names            允许的 Tool 白名单
      ├── knowledge_enabled     是否允许 RAG
      └── text_to_sql_enabled   是否允许 Text-to-SQL
```

## 2. ProjectCapabilities 设计

新增 `backend/app/projects/capabilities.py`：

- frozen dataclass，**只描述不执行**：字段白名单
  `{tool_names, knowledge_enabled, text_to_sql_enabled}`（单元测试
  `assert_no_sensitive_fields` 锁定，杜绝 handler / engine / llm /
  password / api_key / connection_string 混入）；
- `tool_names: tuple[str, ...]`（空元组 = 无 Tool；严格 snake_case
  白名单校验，拒绝重复 / 空项 / 裸字符串）；
- 布尔开关严格 `isinstance(bool)`（拒绝 `1` / `"true"` 等 truthy 值）；
- `DEFAULT_PROJECT_CAPABILITIES = get_inventory + knowledge on +
  text_to_sql on` —— 等价于 Phase 3.8.2 之前的系统行为。

`ProjectRegistration` 新增 `capabilities` 字段（**带默认值**）：
`ProjectRegistration(context, schema_name)` 旧式位置构造零改动兼容
（Phase 3.8.1 的全部测试无需修改即通过）。

## 3. Tool Capability（§六 / §七）

**不创建第二套 Tool Registry**。过滤发生在工厂组装层
（`project_orchestrator_factory._build_project_tool_registry`）：

```text
Global Tool 构建器映射（{get_inventory: builder}）
        ↓ 按 capabilities.tool_names 白名单
per-project ToolRegistry（只注册允许的 Tool；空 = 空 Registry）
        ↓ ToolRegistryCapabilityAdapter（只读元数据）
Router 的 capability 元数据 → 天然看不到被禁用的 Tool
```

- Router 的 Tool 过滤**不需要 Router 自身改动**：它看到的
  capability 元数据来自已过滤的 Registry；
- capabilities 引用全局不存在的 Tool 名 → warning + 跳过
  （静态服务器端配置，运行期不中断其它能力）。

## 4. Knowledge Capability（§八）

`knowledge_enabled=False` → Orchestrator 在 `_run_rag` 入口
（调用 `self._rag.answer` **之前**）抛 `AIOrchestratorCapabilityError`
（RagService 0 次调用）。**不实现项目独立 Knowledge DB**：
全局 Knowledge Store 保持现状（Phase 3.8.1 约定不变）。

## 5. Text-to-SQL Capability（§九）

`text_to_sql_enabled=False` → Orchestrator 在 `_run_text_to_sql`
**第一行**拦截：不解析 ProjectContext、不 inspect Schema、不生成、
不执行 SQL（`SchemaExplorer 0 次 / Generator 0 次 / Executor 0 次`，
DB 集成测试以计数包装实证）。

## 6. Router 过滤（§十 / §十六）

`AIRouterService` 新增构造参数（默认 True，旧行为不变）：

- `knowledge_enabled`：知识规则（`_looks_like_knowledge`）不再选择 RAG；
- `text_to_sql_enabled`：分析规则（`_looks_like_analytics`）不再选择
  TEXT_TO_SQL，问题落到后续知识 / 兜底规则；
- LLM fallback 选中被禁用 route → 视为分类失败（AIRouterClassificationError）
  → 保守兜底 RAG（由 Orchestrator 硬校验拒绝）；
- Router **不知道任何具体项目名**，只接收能力开关 —— 保持通用
  （项目名 → 能力的映射只存在于服务器端 Registry）。

> 注：当所有 route 都被禁用且规则均未命中时，Router 的保守兜底仍会
> 返回 RAG 决策（Router 无 "拒绝" 语义）——这正是"Router 是分类器，
> 不是安全边界"的原因，最终拦截由 §7 的硬校验完成。

## 7. Orchestrator 二次校验（§十一）

`AIOrchestratorService` 新增 `capabilities: ProjectCapabilities | None`
构造参数（`None` = 不限制，默认 Orchestrator 旧行为）。执行前硬校验
（`_check_capability`）：

| 路径 | 校验时机 | 拒绝时下游调用 |
|---|---|---|
| RAG | `_run_rag` 入口 | RagService 0 次 |
| TOOL | `_resolve_tool_name` 之后、`registry.execute` 之前 | Handler 0 次 |
| TEXT_TO_SQL | `_run_text_to_sql` 第一行 | Schema/Generator/Executor 全部 0 次 |

纵深防御两层：Router 过滤（干净路由）+ Orchestrator 硬校验（安全边界）。
即使注入 stub Router 强制返回 TOOL/TEXT_TO_SQL 决策，Orchestrator 仍拒绝
（单元测试覆盖）。

新异常 `AIOrchestratorCapabilityError(capability, project_id)`。

## 8. API 行为（§十七）

- `ChatRequest` **不变**：仍只有 `question + project_id`（无任何能力字段，
  用户无法通过 HTTP 开启能力）；
- `AIOrchestratorCapabilityError` → **HTTP 403**（`项目能力未启用: ...`），
  responses 文档新增 403 描述；
- 未知 project_id → 404（3.8.1 行为不变）；不带 project_id →
  默认 Orchestrator（不查注册表，旧行为不变）；
- 请求体携带 `tool_names` / `text_to_sql_enabled` / `database_url` 等
  额外字段 → Pydantic 默认丢弃，不产生任何效果（集成测试实证）。

## 9. 工厂集成

`build_orchestrator_for_project` 读取 `registration.capabilities`：

- Tool Registry：按白名单注册（§3）；
- Router：`AIRouterService(tool_capabilities=过滤后 adapter,
  knowledge_enabled=..., text_to_sql_enabled=...)`；
- Orchestrator：`capabilities=...`（硬校验）；
- RAG / T2S Generator / Selector / Composer / Executor：与 3.8.1 相同
  （RAG 全局共享；Executor 绑定项目 Engine）。

## 10. 测试结果

### 单元（tests/test_project_capabilities.py，27 项，无 DB / LLM）

DTO 校验（默认值 / frozen / tool_names 规则 / 严格 bool / 字段白名单）·
Registration 集成（默认能力 / 显式能力 / 非法类型拒绝 / unknown → 404）·
Router 门控（t2s 禁用 → 分析问题落 RAG / knowledge 禁用 → 兜底 /
空 Registry → 无 TOOL 命中 / LLM 选禁用 route → 兜底 / 回归）·
Orchestrator 硬校验（三种能力 denied + 下游 0 次调用 + capabilities=None
不限制）。

### DB 集成（tests/test_project_capability_isolation.py，21 项，RUN_DB_TESTS=1）

测试项目：project-a（全能力，schema project_a）、project-b（全禁用）、
project-c（knowledge only，§四 sales-demo 型）。结果 **21/21 passed**：

| 验证点 | 结果 |
|---|---|
| 14.2 工厂过滤：a 含 get_inventory / b 空 Registry / Router 开关 | ✅ |
| 14.3 a + get_inventory → qty 100；b → denied + **Executor/Explorer 0 次** | ✅ |
| 14.5 a T2S 正常（qty 100）；b T2S denied（Generator/Executor/inspect 全 0） | ✅ |
| §十六 c 分析问题 → route=rag（T2S 未被 Router 选择） | ✅ |
| API a：tool / rag / text_to_sql 三路回归成立 | ✅ |
| API b：403 + 业务 DB 0 查询；RAG denied + FakeRag 0 次 | ✅ |
| API c：库存问题落 RAG（不走 TOOL/T2S） | ✅ |
| §十七 安全：额外字段注入 tool_names/database_url → 无效，仍 403 | ✅ |
| 兼容：无 project_id / vietnam-wms 默认 → 旧行为 | ✅ |

### 全量回归

| 命令 | 结果 |
|---|---|
| `pytest -q` | ✅ **1198 passed, 186 skipped** |
| `RUN_DB_TESTS=1 pytest -q` | ✅ **1346 passed, 38 skipped** |
| `python -m compileall backend` | ✅ 通过 |
| LSP / lint | ✅ 0 错误 |
| 测试 schema 清理 | ✅ 无 `project_%` 残留 |

## 11. 修改 / 新增文件清单

| 操作 | 路径 | 说明 |
|---|---|---|
| 新增 | `backend/app/projects/capabilities.py` | ProjectCapabilities DTO + 默认能力常量 |
| 修改 | `backend/app/projects/registry.py` | ProjectRegistration.capabilities（带默认值）+ 校验 |
| 修改 | `backend/app/services/ai_router_service.py` | knowledge_enabled / text_to_sql_enabled 门控（默认 True）+ LLM fallback 禁用 route 拒绝 |
| 修改 | `backend/app/services/ai_orchestrator_service.py` | AIOrchestratorCapabilityError + capabilities 硬校验（三路径执行前拦截） |
| 修改 | `backend/app/services/project_orchestrator_factory.py` | 按 capabilities 过滤 Tool Registry + Router 开关 + Orchestrator capabilities |
| 修改 | `backend/app/api/orchestrator_chat.py` | 403 映射 + responses 文档 |
| 修改 | `tests/test_project_registry.py` | 字段白名单测试加入 capabilities（3.8.1 测试最小更新） |
| 新增 | `tests/test_project_capabilities.py` | 单元测试（27 项） |
| 新增 | `tests/test_project_capability_isolation.py` | DB 集成测试（21 项） |
| 新增 | 本 ADR | — |

**未修改**：Router 路由规则本体、RAG / Reranker、Text-to-SQL Generator、
SQL Validator / Executor 安全机制、Tool Framework、Semantic Layer、
数据库结构（0 migration）、`/api/chat`、ToolRegistry 本体。

## 12. 已知限制与设计取舍

1. **§十五 "B + 库存问题 → Tool capability denied" 的解读偏差**：
   任务书 §七（Router 不应看到被禁用 Tool —— "干净"设计）与 §十五
   （B 的库存问题应返回 "Tool capability denied"）存在内在冲突：
   Router 过滤后 B 的库存问题**不会**路由到 TOOL，而是落到 RAG 兜底。
   本实施遵循 §七（干净路由）：B 的拒答以
   `AIOrchestratorCapabilityError`（403）呈现（全禁用项目）或落 RAG
   执行（knowledge-only 项目）；"TOOL 路由被 Orchestrator 硬拒绝 +
   Handler 0 次调用"由单元测试（stub Router 强制 TOOL 决策）覆盖。
2. **tool_names 必须枚举**：无 "全部 Tool" 通配语义（显式优于隐式）；
   全局新增 Tool 时需同步 `DEFAULT_TOOL_NAMES` 与工厂 builder 映射。
3. **InMemoryProjectRegistry**：进程内代码级注册（§十二），重启重置；
   多进程部署需保证注册一致（生产建议配置中心，本阶段只设计接口）。
4. **RAG 知识库仍全局共享**：`knowledge_enabled=False` 只是不允许走
   RAG，不是项目独立知识库（§八明确）。
5. **Router 保守兜底仍可能返回 RAG 决策**：当项目禁用全部能力且规则
   未命中时，Router 无 "拒绝" 语义，返回兜底 RAG → Orchestrator 403。
   错误消息描述的是被拒绝的兜底路径（knowledge），非用户原始意图。
6. **LLM fallback prompt 未按能力裁剪**：LLM 仍可能输出被禁用 route，
   Router 将其视为分类失败（一次浪费的 LLM 调用 + 兜底），不影响安全。

## 13. 后续方向（仅记录，不在本阶段实施）

- 能力配置迁移到 YAML / 数据库注册中心（InMemory → 持久化）；
- per-project Knowledge Store（项目独立知识库）；
- 能力与用户权限（RBAC）联动；
- 更多正式 Tool（get_purchase_order 等）接入工厂 builder 映射。
