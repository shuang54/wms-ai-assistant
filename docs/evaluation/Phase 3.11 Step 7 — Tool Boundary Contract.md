# Phase 3.11 Step 7 — Tool Boundary Contract（契约清理 + 架构锁定）

> 目标：修正 Step 6 发现的历史 docstring / 注释残留，并把当前 Tool 架构
> 边界固化为**可执行契约**（Contract Tests）。
>
> 本 Step **不**新增任何业务能力：RouteDecision / ToolArgumentExtractor /
> ToolExecutionService / ToolRegistry / Tool 行为全部不变。

---

## 1. Boundary definitions（职责定义）

```text
                    Question
                       │
                       ▼
                    Router                          ← Selection Authority
                       │                               （只读 capability 元数据）
                RouteDecision
                  tool_name
                       │
                       ▼
                AIOrchestrator                      ← Orchestration
                       │                               （decision → extract → execute）
                       ▼
             ToolArgumentExtractor                  ← Candidate Argument Extraction
                       │                               （确定性；不校验、不执行）
                    arguments
                       │
                       ▼
             ToolExecutionService                   ← Capability + Execution Boundary
                       │                               （capability 校验 + 委派）
                       ▼
                 ToolRegistry                       ← Schema Validation + Dispatch Authority
                       │
               Schema Validation
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
       get_inventory       get_work_order            ← Business Implementation
             │                   │
             └─────────┬─────────┘
                       ▼
                   ToolResult                       ← Unified Tool Output Contract
```

| 组件 | 权威 | 明确**不**负责 |
|---|---|---|
| `AIRouterService` | Tool 选择（`decision.tool_name`） | 参数提取 / Tool 执行 / DB |
| `AIOrchestratorService` | 编排（decision → extract → execute） | 参数提取实现 / Schema 校验 / Tool 执行 |
| `ToolArgumentExtractor` | 自然语言 → 参数候选值 | 合法性校验 / 执行 / 选择 Tool |
| `ToolRegistry` | Schema 校验 + Handler 派发 + 异常归一化 | 选择 / 参数提取 / 业务语义 |
| `ToolExecutionService` | capability 校验 + 委派 Registry | 参数提取 / 参数变形 / 业务逻辑 |
| `Tool`（Handler） | 业务实现（只读） | 调用上层 / 调用其它 Tool |

---

## 2. Dependency direction（依赖方向）

```text
Router                  → （无 Tool 执行依赖；无 Registry 模块级 import）
AIOrchestrator          → Router / ToolArgumentExtractor / ToolExecutionService
ToolArgumentExtractor   → 仅 Python 标准库（re / typing / collections.abc）
ToolExecutionService    → ToolRegistry（+ ProjectCapabilities 纯配置 DTO）
ToolRegistry            → Handler（执行权威）
Tool（Handler）          → DB 抽象 / 配置 / 项目上下文（**不**依赖上层）
```

允许的例外（由 Contract Test 精确锁定归属，见 §9）：

```text
1. Router.get_default_router：延迟 import ToolRegistry + mock_tools
   → 仅生成 capability 元数据（list_definitions），不执行 Tool。
2. ToolExecutionService._check_capability：延迟 import
   AIOrchestratorCapabilityError（定义在 Orchestrator 模块，避免循环依赖）
   → 只取异常类型，保持 capability 拒绝语义不变。
3. get_inventory.build_default_tool_registry：延迟 import
   register_get_work_order_tool（装配，非 Tool→Tool 调用）。
4. AIOrchestrator 持有 ToolRegistry：仅构造 ToolExecutionService +
   读取 ToolDefinition.parameters（声明字段）；不调用 registry.execute()。
```

**未修改任何设计**：以上例外均为既有真实代码（Step 2/3/5 已存在），
Step 7 只把它们写成可验证契约，没有为了测试方便去改动架构。

---

## 3. Selection authority（选择权威）

```text
RouteDecision.tool_name = Tool 选择的唯一来源

"查询物料 10001 当前库存"  → RouteDecision(TOOL, tool_name="get_inventory")
"查询工单 WO-202609-001"   → RouteDecision(TOOL, tool_name="get_work_order")
```

Contract 断言：

```text
* Orchestrator 源码不含 Tool 名称 / 业务字段字面量（无 Tool-specific 分支）；
* 不存在第二套选择函数（_resolve_tool_name 已删除，AST 级 0 引用）；
* decision 与问题文本冲突时，执行 decision 指定的 Tool
  （"查询物料 MAT-001 的工单 WO-202609-001 状态" + decision=get_work_order
   → 执行 get_work_order，get_inventory handler 0 次调用）。
```

---

## 4. Argument extraction authority（参数提取权威）

```text
ToolArgumentExtractor.extract(tool_name, question, *, parameters) → dict

* 确定性：字符串匹配 / 正则 / 字符区间计算；无 NLP、无 LLM、无 IO；
* 候选值语义：命中即给，未命中不给（不补默认值、不伪造）；
* 未知 Tool（无声明字段）→ {}（不猜 Tool、不 fallback）；
* 无状态：vars(instance) == {}；同一输入恒等输出。
```

Contract 断言（C2 / C12）：stdlib-only import；无 Registry / DB / LLM / HTTP；
无 `async` / `await`；无 function calling / JSON mode / prompt 依赖。

---

## 5. Schema authority（Schema 权威）

```text
Extractor（候选值）
    ↓
ToolExecutionService（原样透传）
    ↓
ToolRegistry.validate_arguments（唯一校验入口）
    ├── missing required → ToolResult(success=False)
    ├── unknown field     → ToolResult(success=False)
    └── invalid type      → ToolResult(success=False)
    ↓（通过）
Handler（业务第二层校验）
```

Contract 断言（C7）：三类失败均在 Handler 之前拦截（Handler 0 次调用）；
Extractor 代码层不含 `validate_arguments` / `required` / `additionalProperties` /
`ToolResult`（未复制 Schema 规则）。

---

## 6. Execution authority（执行权威）

```text
ToolRegistry.execute = 唯一执行入口（Schema 校验 → Handler → 异常归一化）
ToolExecutionService = 进入 Registry 的唯一应用层通道
  * 只做 capability 校验 + 委派；
  * 不识别业务参数字段（material_code / warehouse_code / work_order_no 0 出现）；
  * 无正则 / 无提取模式（不演变为第二个 Orchestrator）。
```

Contract 断言（C3 / C6）：执行边界模块级 import 仅 `ToolRegistry` +
`ProjectCapabilities`；`*.execute(...)` 的 receiver 只允许 `self._registry`；
Orchestrator 的 `.execute(...)` receiver 只允许执行边界 / SQL 执行器。

---

## 7. Tool isolation（Tool 隔离）

```text
Tool（get_inventory / get_work_order）
    ├── 不依赖 Orchestrator / Router / Extractor / ExecutionService；
    ├── 不 import 对方模块（模块级 0 交叉 import）；
    ├── Handler 内不引用对方名称（Tool 名 / 定义名 / 对方字段）；
    ├── 不调用 ToolRegistry.execute（只接收 arguments）；
    └── 装配层唯一例外：build_default_tool_registry 的注册助手延迟 import。
```

Contract 断言（C4 / C5）；Handler 实例属性 / 构造参数隔离沿用
`tests/test_get_work_order_tool.py::TestToolToToolIsolation`（不重复实现）。

---

## 8. Security boundaries（安全边界）

```text
* 无 DB 直连：LLM / 参数提取层不具备任何 DB 能力（AST 锁定）；
* 无写操作：Tool 仅 READ ONLY 事务 + 绑定参数（既有测试覆盖，Step 7 未触碰）；
* 无任意 SQL / table / where_clause 参数（Tool Schema 权威，Step 7 未修改）；
* 无未知字段透传：Registry 拒绝（C7）；
* 无 fallback 执行：Unknown Tool / 缺 tool_name 不落到其它 Tool / RAG / T2S（C10）；
* 无 LLM 参数提取：Extractor 保持 deterministic（C12）；
* 无新增依赖 / 无安装新工具（lint 工具缺失时如实记录，不引入）。
```

---

## 9. Contract tests（契约测试）

新增：`tests/test_tool_architecture_contract.py`（53 tests，0 DB / 0 Network / 0 LLM）

```text
C1  Router 不执行 Tool             TestC1RouterIsSelectionOnly（5）
C2  Extractor 纯提取               TestC2ArgumentExtractorIsPure（4）
C3  ExecutionService 无参数逻辑     TestC3ExecutionServiceHasNoArgumentLogic（6）
C4  Tool 不依赖上层                TestC4ToolDoesNotDependOnUpperLayers（6）
C5  Tool 互不调用                  TestC5ToolToToolIsolation（4）
C6  Orchestrator 只编排            TestC6OrchestratorIsOrchestrationOnly（5）
C7  Registry = Schema Authority    TestC7RegistryIsSchemaAuthority（5）
C8  arguments 原样透传             TestC8ArgumentsPassthrough（4）
C9  Selection 唯一来源             TestC9SelectionAuthorityIsRouter（4）
C10 Unknown Tool 不 fallback       TestC10UnknownToolDoesNotFallback（2）
C11 单 Tool 执行                   TestC11SingleToolPerRequest（3）
C12 禁止 LLM 参数提取              TestC12NoLlmArgumentExtraction（4）
```

复用（**不**重复实现相同断言）：

```text
tests/test_ai_orchestrator.py           §J 选择统一 / §L 提取边界
tests/test_tool_execution_service.py    §7 arguments identity / §10 AST
tests/test_tool_argument_extractor.py   §8 迁移锁定 / §9 AST 安全
tests/test_tool_argument_contract.py    §4/6 多参数契约 / §9 E2E
tests/test_get_work_order_tool.py       §8 Tool-to-Tool 隔离（属性/构造参数）
tests/test_ai_router.py                 Router 侧选择契约
```

结果：

```text
python -m pytest -q tests/test_tool_architecture_contract.py   53 passed
（全量与 DB 结果见 §11）
```

---

## 10. Known limitations（如实记录）

```text
* 契约以「静态 AST + Fake 注入」为主，不覆盖运行时动态 import / importlib；
* Tool 隔离检查以模块 + Handler 边界为准；Tool → 第三方库的依赖目前
  仅列出网络/DB 客户端黑名单（sqlalchemy 属既有 DB 抽象，允许）；
* Router 的 LLM fallback 路径（settings 开启时）属于既有行为，本 Step 未验证
  （Contract 只锁定"Router 不执行 Tool"）；
* ToolExecutionService 与 Orchestrator 之间存在一个**必要的异常类型**
  延迟 import（capability 拒绝语义）；该依赖已被精确锁定，但仍是模块耦合点，
  后续如需彻底解耦应把异常类型下沉到独立模块（**本 Step 不做**）；
* lint 工具（ruff / flake8）在本环境未安装 → lint unavailable（不安装新工具）；
* ToolChatService（链路 B）仍为 LLM function calling 多步循环 + mock tools，
  未纳入本次 Contract（Step 7 明确不迁移）。
```

---

## 11. Test / Verification 结果

```text
directed：tests/test_tool_architecture_contract.py   53 passed
          tests/test_tool_argument_extractor.py      67 passed
          tests/test_ai_orchestrator.py              37 passed, 2 skipped(DB)
full no DB：2633 passed, 317 skipped（Step 6 为 2580 → +53 新增契约测试）
full DB（RUN_DB_TESTS=1）：2909 passed, 41 skipped
python -m compileall -q backend                       clean
LSP diagnostics                                      0 error / 0 warning
lint（ruff / flake8）                                  lint unavailable（环境未安装；未安装新工具）
```

DB writes = 0；Network = 0（Contract 测试全为静态 + Fake）。

历史 docstring / 注释清理（仅说明文字，0 行为改动）：

```text
backend/app/services/tool_execution_service.py   docstring（"不构造参数"一段）
backend/app/tools/get_inventory.py              docstring（链路 + 设计纪律）
backend/app/tools/get_work_order.py             docstring（链路）
tests/test_get_inventory_tool.py                注释（2 处旧函数名）
docs/evaluation/Phase 3.11 — …（Step 1/2/3）      顶部"历史文档提示"（不改正文快照）
```

---

## 12. Final Architecture

```text
Selection        = Router
Extraction       = ToolArgumentExtractor
Schema           = ToolRegistry
Execution        = ToolExecutionService
Business Logic   = Tool
Output           = ToolResult
Orchestration    = AIOrchestratorService
```
