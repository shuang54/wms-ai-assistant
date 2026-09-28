# Phase 3.11 — Tool Execution Boundary（Step 1：现状勘察）

> 本 Step 只阅读和分析现有 Tool 执行实现，**Python implementation = 0 changes**。
> 本文档只记录**真实代码**中存在的行为，不记录建议实现、不假设未来结构。
>
> 历史文档提示（Phase 3.11 Step 6 / Step 7 起）：本文中的
> `_resolve_tool_name` / `_extract_tool_arguments_from_question` 实现均已删除——
> Tool 选择统一于 Router（Step 3），参数提取迁至 `ToolArgumentExtractor`（Step 6）。
> 当前架构：`docs/architecture.md` §8.23 / §8.24 / §8.25。
>
> Historical note（Phase 3.11 Step 23 Audit 补充）：本文 §5 记录的
> "链路 B 绕开 Orchestrator：ToolChatService 直接 `registry.execute()`"
> 是 **Step 1 勘察时点**的现状；该直连已在 **Step 9** 迁移为
> `ToolChatService → ToolExecutionService → ToolRegistry`（见
> `docs/evaluation/Phase 3.11 Step 9 — ToolChatService Execution Boundary Migration.md`）。
> 本文其余"现状"描述同理按各自 Step 时点理解；Step 23 的架构审计结论见
> `docs/evaluation/Phase 3.11 Step 23 — Tool Observability Architecture Hardening & Contract Audit.md`。

---

## 1. Current Architecture（真实调用链）

### 1.1 主链路 A：Orchestrator Tool 路径（`POST /api/ai/chat`）

```text
HTTP POST /api/ai/chat                                 api/orchestrator_chat.py
    ↓
_default_orchestrator.execute(question)                （模块级单例；tool_registry 预注册）
    ↓
AIRouterService.route()                                services/ai_router_service.py
    │  Rule-first：TOOL capability alias / description 关键词匹配（tool_match）
    │  LLM fallback（可选，settings.ai_router.llm_fallback_enabled）
    │  兜底：RAG
    ↓ RouteDecision(route=TOOL, source="tool_match")
AIOrchestratorService._run_tool(decision, question)    services/ai_orchestrator_service.py
    ↓
_resolve_tool_name(question, registry)     ← 第二套 Tool 选择（别名=参数名命中 + description 2-gram 命中）
    ↓
_check_capability(tool_name)               ← 项目能力硬校验（Phase 3.8.2；无 capabilities → 跳过）
    ↓
_extract_tool_arguments_from_question()    ← 正则字面量提取（目前仅 material_code）
    ↓
ToolRegistry.execute(tool_name, arguments)             tools/registry.py
    ↓
validate_arguments(definition.parameters, arguments)   ← 极简 JSON Schema 子集校验
    ↓
Handler.__call__(arguments)                            tools/get_inventory.py 等
    ↓
ToolResult（异常已归一化）
    ↓
AIOrchestrationResult(route=TOOL, content, data=ToolResult, metadata)
    ↓
ChatResponse（api 层 DTO 映射 _tool_data）
```

### 1.2 主链路 B：ToolChatService（`POST /api/chat/with-tools`，Mock Tools）

```text
HTTP POST /api/chat/with-tools                         api/tool_chat.py
    ↓
_tool_chat_service.chat(message, registry=_tool_registry)   （模块级单例；注册 mock tools）
    ↓
LLM #1（tools=registry.list_definitions → OpenAI function schema）
    ↓ LLM 返回 tool_call（name + JSON arguments）      ← **LLM 决定 Tool 与参数**
ToolRegistry.execute(name, arguments)
    ↓ ToolResult → JSON 序列化为 role=tool message
LLM #2（携带完整消息历史 + tools）→ ... 循环 ...
    ↓（max_rounds=5 硬上限，每轮最多 1 个 tool call）
最终 answer
```

### 1.3 两条链路的关系（真实）

* **两条链路并行存在，各自独立**：
  * `/api/ai/chat`（Orchestrator）→ 真实 `get_inventory` Tool（真实 DB 只读），参数由**正则提取**；
  * `/api/chat/with-tools`（ToolChatService）→ **mock tools**（无 IO 硬编码），参数由 **LLM function calling** 提供；
* 两条链路都最终调用 `ToolRegistry.execute()`——这是当前**唯一真实的 Tool 执行入口**；
* 不存在统一的 "ToolExecutionService / Tool Execution Boundary" 应用层（见 §13）。

---

## 2. Existing Tool Registry

| 项 | 真实实现 |
|---|---|
| 位置 | `backend/app/tools/registry.py` → `ToolRegistry` |
| 类型 | **内存注册中心（普通类，非单例）**；每个实例独立 `_definitions` / `_handlers`；无隐式全局状态 |
| 方法 | `register(definition, handler)` / `unregister(name)` / `get_definition(name)` / `list_definitions()` / `execute(name, arguments)` / `__contains__` / `__len__` |
| Handler 隔离 | `list_definitions()` **只返回 ToolDefinition**，不暴露 Handler；Handler 仅 `execute()` 内部使用 |
| 重复注册 | `ToolAlreadyRegisteredError`；未知 Tool → `ToolNotFoundError`（`get_definition`）/ `ToolResult(success=False)`（`execute`） |

### Registry 生命周期与注册点（真实）

| 注册点 | 生命周期 | 内容 |
|---|---|---|
| `api/tool_chat.py` → `_tool_registry` | **模块级单例**（import 时构造） | `register_mock_tools()`：mock `get_inventory` + `get_work_order` |
| `api/orchestrator_chat.py` → `_TOOL_REGISTRY` | **模块级单例**（import 时构造） | `build_default_tool_registry()`：真实 `get_inventory`（`GetInventoryHandler`，懒加载全局 engine） |
| `services/project_orchestrator_factory._build_project_tool_registry()` | **每次请求构造**（`project_id` 请求路径：`_build_orchestrator_for_project_id` 每次新建 Orchestrator + Registry） | 按 `ProjectCapabilities.tool_names` 白名单注册；`get_inventory` Handler 绑定该项目 engine / schema / project_id |
| `services/ai_router_service.get_default_router()` | 工厂内临时构造 | 仅用于 capability 元数据（Router 不执行 Tool）；生产路径未使用（`orchestrator_chat` 显式构造 Router） |

* **不存在依赖注入框架 / Depends**；模式 = 模块级单例 + monkeypatch（测试）；
* `main.py` 不做 Tool 注册；注册发生在模块 import 或 Service 工厂调用时。

---

## 3. Existing Tools（当前项目真实存在的全部 Tool）

### 3.1 `get_inventory`（真实 Tool，Phase 3.7.12）

| 项 | 真实值 |
|---|---|
| 文件 | `backend/app/tools/get_inventory.py` |
| Name | `get_inventory` |
| Description | "查询指定物料的当前库存数量（只读）…仅支持 material_code 单参数" |
| Aliases（Router 规则命中用） | `当前库存` / `库存数量` / `查库存` / `物料库存` / `库存查询` |
| 输入 Schema | `{type: object, properties: {material_code: {type: string}}, required: [material_code]}` |
| 输出 | `{"material_code": <str>, "qty": <float|int>, "project_id": <str|None>}`；不存在记录 qty=0 |
| 只读 | **是**（`SET TRANSACTION READ ONLY` + `SET LOCAL statement_timeout` + 绑定参数） |
| DB Access | **是**（真实 PostgreSQL SELECT SUM(qty)，经注入 Engine 或全局 `get_engine()`） |
| HTTP Access | 否 |
| 写操作 | **无**（无 INSERT/UPDATE/DELETE 入口） |
| ProjectContext | 依赖（`GetInventoryProjectContextProvider`；engine / schema / project_id 由工厂注入） |
| 额外校验 | `material_code`：strip 后非空、≤ max_len(默认64)、字符集 `[A-Za-z0-9._-]`；schema/table 配置做安全标识符白名单校验 |

### 3.2 `get_inventory`（Mock，Phase 3.6.1）

| 项 | 真实值 |
|---|---|
| 文件 | `backend/app/tools/mock_tools.py` |
| Name | `get_inventory`（与真实 Tool **同名、不同模块**） |
| 输入 Schema | `material_code`（required）+ `warehouse_code`（可选） |
| 输出 | `{"material_code", "warehouse_code", "quantity": 1000, "unit": "PCS"}`（硬编码） |
| 只读 / DB / HTTP / 写 | 只读；0 DB；0 HTTP；0 写（无任何 I/O） |

### 3.3 `get_work_order`（Mock，Phase 3.6.1）

| 项 | 真实值 |
|---|---|
| 文件 | `backend/app/tools/mock_tools.py` |
| Name | `get_work_order` |
| 输入 Schema | `work_order_no`（string，required） |
| 输出 | `{"work_order_no": <str>, "status": "RELEASED"}`（硬编码） |
| 只读 / DB / HTTP / 写 | 只读；0 DB；0 HTTP；0 写 |

### 3.4 全项目副作用扫描（重点）

对 `backend/app/tools/` 全部实现扫描：**不存在任何** `INSERT` / `UPDATE` / `DELETE` / DDL / shell / 任意文件 / 任意 URL 调用。
`get_inventory`（真实）是唯一接触外部系统的 Tool，且为 `BEGIN READ ONLY` 事务内的参数化 `SELECT`。

---

## 4. Tool Input Validation（非法参数在哪里被拒绝）

真实链路的分层：

```text
LLM（链路 B：function calling arguments）
（链路 A：正则提取 _TOOL_ARG_LITERAL_PATTERN，64 字内 [A-Za-z0-9._-] 字面量）
        ↓
ToolRegistry.execute(tool_name, arguments)
        ↓
validate_arguments(definition.parameters, arguments)     ← 第 1 层：Schema 校验
        │   * 顶层 type==object
        │   * required 缺失 → ValueError → ToolResult(success=False)
        │   * primitive 类型不符 → 拒绝
        │   * **未知字段拒绝**（不静默忽略）
        ↓
Handler.__call__(arguments)                              ← 第 2 层：业务校验（Handler 自行负责）
        │   get_inventory：_validate_material_code（strip/长度/字符集）
        ↓
ToolResult
```

以任务书示例为例：`{"warehouse_id": 123}` 且 Tool 要求 `warehouse_code: str`：

* 若 Schema 声明 `warehouse_code` → `warehouse_id` 是**未知字段** → `validate_arguments` 拒绝 → `ToolResult(success=False, error="参数校验失败: unknown field(s): ['warehouse_id']")`；
* 若参数在 Schema 中存在且类型不符（如 `material_code: 123`）→ **ToolRegistry 的 Schema 校验**（integer ≠ string）拒绝；
* Handler 内部的深度校验（字符集等）由 **Handler 自己**执行（`ToolValidationError` → Registry 归一为 `ToolResult(success=False)`）。

校验的**权威层 = ToolRegistry.execute → validate_arguments（Schema）+ Handler（业务）**；LLM 与 Router **不做参数校验**。

---

## 5. Tool Execution

| 问题（任务书 §九） | 真实答案 |
|---|---|
| 1. 谁选择 Tool（链路 A） | **两个角色**：Router `_match_tool` 先判"该走 TOOL"；`AIOrchestratorService._resolve_tool_name` 再从 registry **重新选一次具体 Tool**（别名=参数名命中 → description 2-gram 命中）。两套匹配逻辑并存、语义不完全一致 |
| 1. 谁选择 Tool（链路 B） | **LLM**（function calling，从 `list_definitions` 转换的 schema 中选择） |
| 2. 谁构造 Tool 参数（链路 A） | **Orchestrator** `_extract_tool_arguments_from_question`——正则从问题文本提取字面量（当前仅 `material_code`）；非 LLM、非 Router |
| 2. 谁构造 Tool 参数（链路 B） | **LLM**（tool_call JSON arguments） |
| 3. 谁验证参数 | `ToolRegistry.execute → validate_arguments`（第 1 层）+ Handler 内部（第 2 层）——**两条链路共用同一层** |
| 4. 谁执行 Tool | `ToolRegistry.execute()` → `handler(arguments)`（**全项目唯一执行入口**） |
| 5. 谁处理异常 | `ToolRegistry.execute`：`ToolError` → `ToolResult(success=False)`；其它 `Exception` → 归一化 + logger.error（traceback 不写结果）；`BaseException` 原样上抛。Orchestrator/ToolChatService **不重复捕获** Tool 内部异常（Registry 已归一） |
| 6. 谁生成 Tool Result | `ToolRegistry.execute` 构造 `ToolResult`；Handler 只返回裸 data |
| 7. 谁把结果返回给上层 | Orchestrator：`_tool_result_to_content()` 自然化 + `AIOrchestrationResult.data=ToolResult`；ToolChatService：`_serialize_tool_result()` → role=tool message 回传 LLM |
| 8. Tool 能否调用其它 Tool | **不能**（无此机制；Handler 只收到自己的 arguments） |
| 9. Tool 能否调用 LLM | 机制上**可以**（无静态限制），但当前**没有任何 Tool 这么做** |
| 10. Tool 能否直接访问数据库 | **是**（`get_inventory` 真实 Tool 直接持 Engine 查询）——经 `DatabaseEngineProvider` 受控 Engine 注入，非自建连接 |

---

## 6. Tool Result

`ToolResult`（frozen dataclass，`tools/base.py`）：

```text
tool_name: str
success:   bool
data:      Any | None   （success=True 时；结构由 Tool 自行决定）
error:     str | None   （success=False 时；不含 traceback / API Key / DB URL / SQL）
```

契约（`__post_init__` 强制）：`success=True → error is None`；`success=False → data is None 且 error 非空`。

---

## 7. Error Handling（全景）

```text
Handler 抛 ToolError 家族         → ToolResult(success=False, error="<类名>: <msg>")   （不上抛）
Handler 抛其它 Exception         → ToolResult(success=False, error=ToolExecutionError 形态,
                                     cause_type=<类名>)；traceback 仅进 logger.error
Handler 抛 BaseException         → 原样上抛（KeyboardInterrupt / SystemExit）
Tool 未注册                       → ToolResult(success=False, error="Tool 未注册: ...")
Schema 校验失败                   → ToolResult(success=False, error="参数校验失败: ...")
```

Orchestrator 侧（链路 A）：

```text
TOOL 路由但 _resolve_tool_name 无命中   → AIOrchestratorRouteError（API 层 502）
Tool 返回 success=False                 → **不抛**；正常返回 AIOrchestrationResult
                                          （metadata.tool_success=False；content 带失败说明）
Check capability 被禁用                 → AIOrchestratorCapabilityError（API 层 403）
execute 意外异常                        → AIOrchestratorExecutionError（API 层 500）
```

ToolChatService 侧（链路 B）：

```text
Tool 执行失败（含参数错误）→ 不打断链路：ToolResult(success=False) 序列化回传 LLM，
                              允许 LLM 基于错误继续（或最终回答）
Tool 预算耗尽             → ToolCallingBudgetExceededError（API 层 502）
LLM 返回多个 tool call    → MultipleToolCallsError（API 层 502）
```

---

## 8. Project Context

* Tool 层有**本地最小 Provider**：`GetInventoryProjectContextProvider`（`tools/get_inventory.py`）——只用 `settings.project.project_id` / description 组装 `ProjectContext`，**不访问 DB Schema / Semantic**（避免 orchestrator ↔ tools 循环依赖）；
* 项目级请求路径（`project_id` 非空）：`project_orchestrator_factory` 注入项目 engine + `project_id` override，Handler 的 `GetInventoryHandler(engine=..., inv_settings=schema_name 替换, project_id=...)` 全部由工厂解析；
* **无 `project_id == "vietnam-wms"` 硬编码**：`get_inventory.py` 明确"project_id 仅通过 ProjectContextProvider 解析；Tool 不关心具体 project_id 值"；`ProjectContext.resolve()` 失败仅记 warning，project_id 留空，不阻断查询；
* `ProjectContext` 仅用于结果的 metadata 回显（`project_id` 字段），**不进入 SQL**。

---

## 9. Security Boundary（基于真实实现，非名称）

| 能力 | 真实结论 |
|---|---|
| 写数据库 / 删除 / 修改 | **不允许**（全部 Tool 无写路径；真实 Tool 为 READ ONLY 事务） |
| 执行任意 SQL | **不允许**（`material_code` 绑定参数；无 SQL/table/where 参数入口；Schema 拒绝未知字段） |
| 调用任意 HTTP URL | **不允许**（无 HTTP 调用；无 requests/httpx 使用） |
| 读取任意文件 / 执行 Shell | **不允许**（无 os/subprocess/open 使用） |
| 敏感信息泄露 | error/detail 不含 DATABASE_URL / password / SQL 原文 / traceback（代码显式保证 + 测试覆盖） |
| 纵深防御 | ① Router 是分类器非边界；② Orchestrator capability 硬校验（403 前置于执行）；③ Registry 未知 Tool 拒绝；④ Schema 未知字段拒绝；⑤ Handler 业务校验；⑥ DB 层 READ ONLY 事务 + statement_timeout + 标识符白名单 |

---

## 10. Multi-step Tool Calling

真实结论（分链路）：

```text
链路 A（Orchestrator / /api/ai/chat）：
    Question → Router → 1 个 Tool → 结束
    Tool → Tool        = NOT IMPLEMENTED
    Tool → LLM         = NOT IMPLEMENTED（Tool 结果不回流 LLM 再生成）
    LLM → Tool → LLM   = NOT IMPLEMENTED 在此链路
    （Router 的 LLM fallback 是分类调用，不接触 Tool 结果）

链路 B（ToolChatService / /api/chat/with-tools）：
    LLM → Tool → LLM → Tool → ... → LLM   = **IMPLEMENTED**（Phase 3.6.3）
    * 顺序执行、每轮最多 1 个 tool call、max_rounds 硬上限（默认 5，钳制 [1,20]）
    * 但注册的是 **mock tools**（无真实 IO）；真实 Tool 未接入此链路
```

**不存在** Agent / 规划 / 自主重规划 / 无限循环。

---

## 11. Existing Tests

| 文件 | 覆盖 | 可复用资产 |
|---|---|---|
| `test_tool_framework.py` | ToolDefinition / ToolResult 契约、validate_arguments、Registry 注册/执行/异常/安全、Module surface | `_EchoHandler` / `_AlwaysFailHandler` / `_ToolErrorHandler` / `_simple_definition()` |
| `test_get_inventory_tool.py` | 定义与校验、注册、Router 发现、**真实 DB fixture**（`_setup_inventory_fixture`/`_teardown`）、Orchestrator 集成、API 集成、DB write protection | `FakeRouter`、`_OrchDeps`、真实 engine fixture |
| `test_tool_chat_service.py` | 多步循环全场景（0/1/2/3 步、预算耗尽、失败继续、LLM 异常） | `ScriptedLLMClient`（可脚本化 Fake LLM）、`_TestNoteHandler`、registry fixture |
| `test_tool_chat_api.py` | `/api/chat/with-tools` HTTP 契约 | `FakeToolChatService` |
| `test_ai_orchestrator.py` | 三条路由 + Tool 路径（成功/未命中/失败透传/意外异常）+ 输入校验 + 依赖注入 | `FakeRouter` / `FakeRAG` / `FakeTextToSQL` / `FakeSQLExecutor` / `FakeTableSelector` / `FakeContextComposer` / `FakeProjectProvider`；内联 BoomHandler；DB e2e（`RUN_DB_TESTS`） |
| `test_ai_router.py` | 规则路由 / LLM fallback / DTO / 静态安全 | `FakeLLM` |
| `test_project_capability_isolation.py` 等 | per-project Tool 过滤 / capability 拒绝 | — |

**结论：没有独立的通用 `FakeToolRegistry` / `RecordingTool` / `ExplodingTool` 类**；等价能力以私有 handler（`_EchoHandler` / `BoomHandler` / `_TestNoteHandler`）+ 真实 `ToolRegistry` 组合实现——**后续开发应复用此模式，不重新创建测试框架**。

---

## 12. Usage Observability Relationship（真实）

```text
Tool 本身产生 LLM Usage？        → 否（Tool 不调用 LLM；无 usage 事实）
链路 A 的 LLM 调用点             → Router 的 LLM fallback（可选；规则未命中才发生）
链路 B 的 LLM 调用点             → 每轮 ToolChatService.chat 的 LLM 调用（真实 client）
其它 LLM 调用点                  → RAG answer / Text-to-SQL generate
```

* 全部 LLM 调用经 `LLMClient.chat()` → `_observe_call()` → observation sink + accounting sink（`llm/client.py`，Phase 3.10.7 / 3.10.11）；
* sink 为 **per-client 注入**；`get_default_llm_client()` → `create_llm_client(settings.llm)` **不注入 sink** → 默认 `NoopObservationSink` / `NoopAccountingSink`；
* `DatabaseLLMAccountingSink` 在**生产代码中没有任何默认接线点**（docstring 明确"显式接入，默认不启用"）；
* 因此当前真实默认行为：**LLM usage 不落库**，除非某个组装点显式注入 sink（当前 backend/app 内无此调用者）。

即：当前不存在

```text
User → LLM → Tool → LLM（Tool 结果回流 LLM 再回答，且产生 usage）
```

在 Orchestrator 链路中只有 `Router（可能 LLM 分类）→ Tool`；ToolChatService 链路存在 `LLM → Tool → LLM`（mock tools 场景）。

---

## 13. Boundary Assessment（本 Step 核心判断）

**结论：情况 B —— 当前 Tool 执行链存在职责混杂，尚无清晰的 Application Tool Execution Boundary；但并非完全散乱，Registry 层已承担了执行/校验/结果归一职责。**

### 已经清晰的层次

```text
ToolRegistry.execute()   —— 参数 Schema 校验 + Handler 执行 + 异常归一化 + ToolResult 构造
                            （职责单一、契约稳定、两条链路共用）
Handler                  —— 业务校验 + IO（只读）
```

### 混杂点（真实证据）

1. **两套 Tool 选择逻辑并存**：
   * Router `_match_tool`（capability 元数据：description 全词 + aliases）；
   * Orchestrator `_resolve_tool_name`（definition.properties 字段名命中 + description 2-gram 命中）；
   两者输入不同、算法不同，同一问题可能被两条逻辑选出不同结果。
2. **参数构造在 Orchestrator 内**：`_extract_tool_arguments_from_question` 是正则字面量提取器——只支持 `material_code` 单字段；任何多参数 Tool / 自然语言参数表达都无法处理；扩展需改 Orchestrator 核心文件。
3. **结果转换在 Orchestrator 内**：`_tool_result_to_content`（自然化展示）、metadata 组装（decision_source / route_reason / tool_name / tool_success）与执行逻辑耦合在 `_run_tool`。
4. **能力校验耦合在执行入口**：`_check_capability(tool_name)` 在 `_run_tool` 内——语义上属于"执行前置守门"，与 Tool 选择/参数构造/执行混在同一方法。
5. **链路 B 绕开 Orchestrator**：ToolChatService 直接 `registry.execute()`，没有共享的"应用层 Tool 执行服务"；两条链路的错误映射 / 结果序列化 / 日志各自实现。

### 尚未存在的问题（不需要修）

* Registry 层无重复实现；
* 无多步循环泄漏到 Orchestrator；
* 无 Agent / LangGraph / MCP（符合阶段约束，不需要引入）。

---

## 14. Recommended Next Step（等待用户裁决，本 Step 未实施）

若决定建立 Boundary，**最小演进方向**（不需要 Agent / 架构大改）：

```text
现状：
    Orchestrator._run_tool = Tool 选择(第二套) + 参数构造(正则) + 能力校验 + 执行 + 结果转换

选项 1（推荐，最小）：新增 ToolExecutionService（应用层）
    Orchestrator / ToolChatService
        ↓
    ToolExecutionService（统一 Tool 选择结果 → 参数构造策略 → 能力校验 → registry.execute
                          → ToolResult → 上层映射）
    理由：让"谁选 Tool / 谁构造参数 / 谁校验 / 谁执行"只有一个答案；
           Orchestrator 与 ToolChatService 共享同一执行边界；
           未来切换"参数构造 = LLM function calling"只需替换策略。

选项 2：不改结构，仅治理第二套选择逻辑
    把 _resolve_tool_name 与 Router._match_tool 收敛为一套（例如 Registry 侧提供统一匹配），
    保留 _run_tool 其余行为。

选项 3：不改代码（当前无真实故障）
    真实 Tool 只有 1 个（单参数），正则提取侥幸够用；
    等第 2 个真实 Tool（多参数）出现时再建 Boundary，
    避免为假想需求提前抽象（AGENTS.md §27）。
```

**建议等待用户在第 2 个真实 Tool 落地前再裁决选项 1 / 2 / 3；本 Step 不实施。**

---

## 15. 明确记录（Non-Goals / 未实现项）

```text
ToolExecutionService          = NOT IMPLEMENTED
Agent / LangGraph / MCP       = NOT IMPLEMENTED
Memory / Planning             = NOT IMPLEMENTED
多步 Tool Calling（Orch 链路） = NOT IMPLEMENTED
真实 Tool 接入 ToolChatService = NOT IMPLEMENTED（该链路仅 mock tools）
Tool 级权限 / 审计 / 持久化    = NOT IMPLEMENTED
Dashboard / Frontend / Billing / Authentication / Queue / Worker = NOT IMPLEMENTED
```
