# Phase 3.11 Step 2 — 最小 ToolExecutionService

> 目标：按 Step 1 勘察结论，执行**最小 Boundary 抽取**——把"执行 +
> capability + 结果"从 Orchestrator 抽出，形成唯一应用层 Tool 执行入口。
> 不统一 Tool 选择、不做自然语言参数解析、不迁移 ToolChatService。
>
> 历史文档提示（Phase 3.11 Step 6 / Step 7 起）：本文中"参数提取仍属
> Orchestrator（`_extract_tool_arguments_from_question`）"的描述已过时——
> 该实现已删除，参数提取现由 `ToolArgumentExtractor` 负责（Step 6）。
> 当前架构：`docs/architecture.md` §8.24 / §8.25。

---

## 1. Goal

```text
从：
    Orchestrator._run_tool
        ├── Tool 选择（_resolve_tool_name）
        ├── capability 检查
        ├── 参数提取
        ├── Registry.execute
        ├── ToolResult → content
        └── metadata

到：
    Orchestrator._run_tool
        ├── Tool 选择（保留）
        ├── 参数提取（保留）
        ├── ToolResult → content（保留）
        ├── metadata（保留）
        └── ToolExecutionService.execute()   ← 新增执行边界
                ├── capability 检查（迁移）
                └── ToolRegistry.execute（委派）
```

---

## 2. Existing Problem（Step 1 结论回顾）

1. `_run_tool` 一方法混合 5 件事（选择 / 参数 / 能力校验 / 执行 / 结果转换）；
2. 两条链路（Orchestrator / ToolChatService）各自直连 `ToolRegistry.execute`，
   没有共享的应用层执行边界；
3. capability 校验嵌入 `_run_tool`，与执行/结果逻辑耦合。

（Step 1 记录的"两套 Tool 选择逻辑"**本 Step 不处理**，见 §14。）

---

## 3. New Boundary

```text
backend/app/services/tool_execution_service.py
    → ToolExecutionService
```

```text
tool_name + arguments（已解析 / 已构造）
    ↓
ToolExecutionService.execute()
    ├── _check_capability()      ← 项目工具白名单（迁移自 Orchestrator）
    ↓
ToolRegistry.execute()           ← Tool 唯一执行权威（不绕过）
    ├── validate_arguments()
    ├── Handler.__call__
    └── 异常归一化 → ToolResult
    ↓
ToolResult（原样返回）
```

---

## 4. ToolExecutionService Responsibility

| 负责 | 不负责 |
|---|---|
| capability 校验（工具白名单；被拒 Handler 0 次调用） | Tool 选择（拒绝 question 输入，不新增第三套算法） |
| 委派 `ToolRegistry.execute()` | 参数构造（仍属 Orchestrator） |
| 原样返回 `ToolResult` | 接触 Handler（执行权力完全属于 Registry） |
| 构造期 duck-typing 校验（registry 必须提供 `execute()`） | 异常包装 / retry / fallback / loop / 缓存 |

接口：

```python
ToolExecutionService(
    registry: ToolRegistry,
    *,
    capabilities: ProjectCapabilities | None = None,   # None = 不限制（旧行为）
    project_id: str | None = None,                     # 仅用于 capability 错误信息
)

async execute(tool_name: str, *, arguments: dict | None = None) -> ToolResult
```

只读属性：`registry` / `capabilities` / `project_id`（便于测试断言注入关系）。

---

## 5. Registry Responsibility（不变）

`ToolRegistry.execute()` 仍是全项目唯一 Tool 执行入口：

* `validate_arguments()` Schema 校验（参数类型 / required / 未知字段拒绝）；
* Handler 调用；
* `ToolError` → `ToolResult(success=False)` 归一化；其它 `Exception` → 归一化 +
  `cause_type` + `logger.error`；`BaseException` 原样上抛；
* **本 Step 零修改**（registry.py / base.py / errors.py 均未变更）。

---

## 6. Orchestrator Responsibility

`AIOrchestratorService`（变更点）：

* `__init__` 新增可选参数 `tool_execution_service: ToolExecutionService | None`：
  * 显式注入优先；
  * `None` 且存在 Tool Registry → 自动构造
    （`capabilities` / `project_id` 同步注入）；
  * `tool_registry=None`（旧行为）→ 保持 `None`；
* `_run_tool`：`resolve` → `extract` → `ToolExecutionService.execute()` → content/metadata；
* `_check_capability`：删除 Tool 名称分支（迁移），保留 knowledge / text_to_sql
  （RAG / T2S 仍由 Orchestrator 校验）。

Orchestrator 仍完整持有：Router 调用、Tool 名称解析、参数构造、
`AIOrchestrationResult` 组装、RAG / T2S 编排。

---

## 7. Capability Validation

```text
迁移前：_run_tool → self._check_capability(tool_name)
迁移后：ToolExecutionService._check_capability(tool_name)（在 Registry.execute 之前）
```

* 语义完全一致：`capabilities=None` → 不限制；白名单不含 → 抛
  `AIOrchestratorCapabilityError(message, capability=tool_name, project_id=...)`；
* **异常类型保持** `AIOrchestratorCapabilityError`（延迟导入避免循环依赖），
  HTTP 层 403 语义不变；
* `_run_tool` 新增 `except AIOrchestratorCapabilityError: raise`——防止
  capability 拒绝被下方 `except Exception` 误包装为 `AIOrchestratorExecutionError`（500）；
* Handler 0 次调用（校验发生在 Registry.execute 之前）。

---

## 8. Argument Flow

```text
Question
    ↓ _extract_tool_arguments_from_question（Orchestrator，Phase 3.7.12 正则；未变）
arguments: dict | None
    ↓ ToolExecutionService.execute(tool_name, arguments=...)
ToolRegistry.execute(tool_name, arguments=...)
    ↓（Registry 侧：None → {}；Schema 校验 → Handler）
Handler
```

* 本层**不做任何参数变形**（不新增 / 不删除 / 不 strip 字段）；
* `arguments=None` 原样传给 Registry（Registry 按 `{}` 处理，行为不变）；
* 既有 `material_code` 正则提取结果完全兼容（回归测试锁定）。

---

## 9. Error Flow

```text
capability 拒绝                  → AIOrchestratorCapabilityError（原样，403）
Tool/参数/Handler 失败（Registry 归一） → ToolResult(success=False)（不抛；Orchestrator 透传 metadata.tool_success=False）
Registry 抛未归一化异常           → 原样传播 → Orchestrator 包装 AIOrchestratorExecutionError（500，消息格式不变）
未知 route / 未命中 Tool          → AIOrchestratorRouteError（502，未变）
```

无 retry / 无 fallback / 无 loop（保持不变）。

---

## 10. ToolResult Flow

```text
ToolRegistry.execute() → ToolResult
    ↓ 原样（identity 不变，不重新包装）
ToolExecutionService.execute() → ToolResult
    ↓
Orchestrator：content = _tool_result_to_content(result)
              data = tool_result
              metadata = {decision_source, route_reason, tool_name, tool_success}
```

---

## 11. Tests

新增 `tests/test_tool_execution_service.py`（25 用例）：

| 组 | 覆盖 |
|---|---|
| 构造校验 | registry None / 缺 execute / capabilities 类型 / project_id 类型 → TypeError；只读属性 |
| 成功执行 | capabilities=None 不限制；capability allowed；默认能力允许 get_inventory |
| capability rejected | 异常类型 / capability / project_id 语义；Handler 0 次；Registry 0 次；None=不限制 |
| Registry 归一化 | ToolError → ToolResult(False)；未知 Tool → ToolResult(False)；Schema 失败 → ToolResult(False) 且 Handler 0 次 |
| unexpected exception | Registry 抛 RuntimeError → 同一对象原样传播（不吞 / 不包装） |
| arguments 透传 | dict 原样（对象身份 `is`）；None 原样 |
| Orchestrator 集成 | 默认自动构造（registry 同一对象）；无 tools → None；显式注入被 `_run_tool` 使用（收到正确 tool_name / material_code 参数）；capability 403 回归；Tool 失败透传 |
| 静态安全 | AST：无 DB / HTTP / Shell import；无 session/engine/connection/socket 等标识符；无 SQL 字符串 |

复用既有资产：真实 `ToolRegistry` + 私有 handler（test_tool_framework 模式）、
`FakeRouter` / `_tool_decision`（test_ai_orchestrator 复用），不新建测试框架。

---

## 12. Regression

* `tests/test_tool_execution_service.py`：**25 passed**
* 定向回归（新测试 + orchestrator / router / tool_framework / get_inventory /
  tool_chat_service / project_capabilities / project_configuration）：
  **311 passed, 20 skipped**
* 全量（未开 DB）：**2413 passed, 317 skipped, 0 failed**
* 全量（`RUN_DB_TESTS=1`）：**2689 passed, 41 skipped, 0 failed**
* `python -m compileall backend tests`：OK；LSP diagnostics：0

---

## 13. Security

`ToolExecutionService` 自身（静态检查 + 代码审查）：

```text
NO SQL / NO DB Session / NO Engine / NO Connection / NO HTTP /
NO Shell / NO File IO / NO LLM / NO API Key / NO Password
```

* 唯一依赖：`ToolRegistry`（执行）+ `ProjectCapabilities`（纯配置 DTO）；
* 不接触 Handler、不绕过 `validate_arguments`；
* capability 校验前置（Registry / Handler 0 次调用即拒绝）；
* 错误信息不新增敏感内容（capability 消息与迁移前完全一致）。

---

## 14. Known Limitations（如实记录，本 Step 未处理）

```text
Tool Selection Unification          = NOT IMPLEMENTED
（Router._match_tool 与 _resolve_tool_name 两套选择逻辑仍在，本 Step 未改 Router）
Natural Language Argument Extraction = NOT IMPLEMENTED
（仍是 material_code 正则字面量提取；多参数 Tool 出现前不设计通用解析器）
ToolChatService Migration            = NOT IMPLEMENTED
（链路 B 仍直接调用 ToolRegistry.execute，未接入执行边界）
多参数 Tool 支持                      = NOT IMPLEMENTED
（当前真实 Tool 仅 get_inventory 单参数）
Agent / LangGraph / MCP / Memory / Planning / Multi-Agent = NOT IMPLEMENTED
```
