# Phase 3.11 Step 3 — Tool Selection Contract Unification

> 目标：消除 Orchestrator 中第二套 Tool Selection 算法
> （`_resolve_tool_name`），让 **Router 成为 Tool 选择的唯一来源**。
> 只解决选择重复；不解决自然语言参数解析、不迁移 ToolChatService、
> 不新增 Tool。
>
> 历史文档提示（Phase 3.11 Step 6 / Step 7 起）：本文中"参数构造
> （`_extract_tool_arguments_from_question`）保持不变"的描述已过时——
> 该实现已删除，参数提取现由 `ToolArgumentExtractor` 负责（Step 6）。
> 当前架构：`docs/architecture.md` §8.24 / §8.25。

---

## 1. Previous Architecture（Step 2 之后的真实状态）

```text
Question
    ↓
AIRouterService.route
    ├── _match_tool（capability 元数据匹配 → 只判"是否 TOOL"，不给出名字）
    ↓ RouteDecision(route=TOOL)          ← 无 tool_name 字段
AIOrchestratorService._run_tool
    ├── _resolve_tool_name（**第二套选择**：
    │     ① definition.properties 字段名 in question
    │     ② description 中文 2-gram in question）
    ├── _extract_tool_arguments_from_question
    ↓
ToolExecutionService.execute（capability + Registry）
    ↓
ToolResult
```

两套选择算法并存：Router 用 capability（description 按标点分词 + aliases），
Orchestrator 用 definition（字段名 + 2-gram），输入与算法均不同。

---

## 2. Duplicate Selection Problem（真实影响）

1. Router 判 TOOL 后，具体 Tool 由 Orchestrator 再选一次——Router 的
   匹配结果不可见、不可测（decision 不含名字）；
2. 两套算法对同一问题可能得出不同结论（例如 "查 库存 数量"：
   Orchestrator 2-gram 命中 get_inventory，Router 分词不命中）；
3. Orchestrator 选不出任何 Tool 时抛 502（RouteError），而 Router
   的规则判定无从解释该失败。

---

## 3. New Selection Contract

```text
RouteDecision
    route:       RouteType
    confidence:  float | None
    reason:      str | None
    source:      "rule" / "tool_match" / "llm" / "fallback"
    tool_name:   str | None = None      ← 新增（Phase 3.11 Step 3）
```

契约：

| 路径 | tool_name |
|---|---|
| TOOL（`_match_tool` 规则命中） | 命中的 Tool 名称（capability.name） |
| RAG / TEXT_TO_SQL | None |
| LLM fallback 判 TOOL | None（不猜测具体 Tool） |
| 兜底 RAG | None |

DTO 其它字段与既有路径完全不变（默认值 None，向后兼容）。

---

## 4. Router Responsibility

* `_match_tool` 在 TOOL 决策中写入 `tool_name=cap.name`（元数据来源
  仍仅 name / description / aliases，**不含 handler**）；
* Router 仍是分类器 + Tool 选择器：不执行 Tool、不拼参数、不接触
  Registry / Handler / DB / Engine / Session / SQL（既有静态安全测试
  继续通过）；
* LLM fallback 判 TOOL 时**不**猜测具体 Tool（保持 `tool_name=None`），
  由上层显式拒绝执行——绝不二次调用 LLM 或回退其它 Tool。

---

## 5. Orchestrator Responsibility

`_run_tool` 变更：

```python
tool_name = decision.tool_name
if not tool_name:
    raise AIOrchestratorRouteError(
        "TOOL 路由未携带 Tool 名称（Router 未选择具体 Tool）"
    )
```

* `_resolve_tool_name` 与配套 `_tokenize` **已删除**（Orchestrator 不再
  持有任何 Tool 选择算法）；
* 参数构造（`_extract_tool_arguments_from_question`）**保持不变**
  （Question → arguments 仍属 Orchestrator，Step 3 不处理）；
* capability 拒绝异常显式重抛（403 语义不变，不被包装为 500）；
* Tool 失败（Registry 归一）仍不抛，`metadata.tool_success=False`。

---

## 6. ToolExecutionService Responsibility

**零修改**（Step 2 引入的边界保持原样）：

```text
tool_name（来自 decision，不再由本层或 Orchestrator 选择）
    ↓ capability 校验
ToolRegistry.execute
    ↓
ToolResult
```

---

## 7. Compatibility（真实行为核对）

| 输入 | Step 3 后真实行为 | 说明 |
|---|---|---|
| "查询物料 MAT-001 当前库存" | TOOL + `get_inventory` → material_code=MAT-001 | 别名"当前库存"规则命中（锁定测试） |
| "查询物料 10001 当前库存数量" | TOOL + `get_inventory` | 同上（e2e 场景 B 通过） |
| "查询 MAT-001 的库存" | TEXT_TO_SQL（rule） | **既有行为**（Phase 3.7.12 起"库存"不是 TOOL 别名，避免抢占聚合问题）；本 Step 未改变 |
| Router 判 TOOL 但无 tool_name | `AIOrchestratorRouteError`（502 语义） | 不猜、不回退、不重调 LLM |
| Router 返回未注册 Tool 名 | ToolResult(success=False)（安全失败，200 语义） | Registry 既有语义；无 fallback |
| capability 拒绝 | `AIOrchestratorCapabilityError`（403） | 类型 / capability / project_id 不变 |
| RAG / TEXT_TO_SQL / 兜底 | 与 Step 2 完全一致 | 对应路由测试全部通过 |

`git diff` 确认：ToolRegistry / Tool 实现 / ToolChatService / RAG /
Text-to-SQL / SQL Validator / SQL Executor **零修改**。

---

## 8. Security

```text
Router：        NO DB / NO Handler / NO Engine / NO Session / NO SQL（不变，静态测试锁定）
Orchestrator：  NO Tool Handler / NO DB / NO SQL（不变，静态测试锁定）
ToolExecutionService：NO DB / NO SQL / NO LLM / NO HTTP / NO Shell（不变）
ToolRegistry：  继续作为唯一 Tool Execution Authority（不变）
```

新增的 tool_name 是纯字符串标识（来自 capability 元数据），不含
handler / 参数 / 敏感信息。

---

## 9. Tests

### 新增 / 更新

* `tests/test_ai_router.py` → 新增 `TestToolSelectionContract`（6 用例）：
  alias 命中携带名字 / description 命中携带名字 / adapter 字段名携带名字 /
  多 Tool 命中确定性 / RAG+T2S+fallback 均为 None / LLM fallback 不猜名字；
* `tests/test_ai_orchestrator.py` → 新增 `TestToolSelectionUnification`（5 用例）：
  Router 选 tool_b 而问题文本指向 get_inventory → 执行 tool_b（证明无二次选择）/
  未知 Tool 安全失败且 Router 仅调用一次 / 真实 Router e2e（0 DB）/
  capability 403 语义 / decision 无 tool_name → RouteError（改造自旧测试）；
  `_tool_decision()` helper 升级为携带 tool_name（默认 get_inventory）；
  删除 `TestResolveToolName`（对应函数已删除，Router 侧契约已覆盖）；
* `tests/test_project_capabilities.py` / `tests/test_get_inventory_tool.py`：
  `_StubRouter` / `_OrchDeps` 的 TOOL 决策补齐 tool_name（测试语义 =
  "Router 已选择"）。

### 结果

* 定向（router / orchestrator / tool_execution_service / capabilities）：**135 passed**
* Tool 相关 + E2E + Observation 集成：**180 passed, 22 skipped**
* 全量（未开 DB）：**2420 passed, 317 skipped, 0 failed**
* 全量（`RUN_DB_TESTS=1`）：**2696 passed, 41 skipped, 0 failed**
* `compileall`：OK；LSP diagnostics：0

真实 Router 验证（工具链实测）：

```text
"查询 MAT-001 的库存"          → text_to_sql | tool_name=None  （既有行为）
"查询物料 MAT-001 当前库存"     → tool | get_inventory | tool_match
"查询物料 10001 当前库存数量"    → tool | get_inventory | tool_match
```

---

## 10. Known Limitations（如实记录，本 Step 未处理）

```text
Natural Language Argument Parsing = NOT IMPLEMENTED
（Question → arguments 仍是 material_code 正则字面量提取）
Multi-parameter Tool              = NOT IMPLEMENTED（当前真实 Tool 仅单参数）
ToolChatService Migration         = NOT IMPLEMENTED（链路 B 仍直接 Registry.execute）
LLM fallback 判 TOOL 且无名字 → 502（有意保守：不猜 / 不重调 LLM）
Agent / LangGraph / MCP / Memory / Planning / Multi-Agent / Loop = NOT IMPLEMENTED
```
