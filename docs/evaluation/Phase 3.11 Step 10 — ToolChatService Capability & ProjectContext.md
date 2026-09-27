# Phase 3.11 Step 10 — ToolChatService Capability & ProjectContext

> 目标：让链路 B（ToolChatService / Multi-Step Function Calling）也受**项目级
> Tool 能力（capability）**约束，并明确 `project_id` 的传递边界。
>
> 执行边界仍是唯一授权点；ToolChatService 自身**不做**任何 capability 判断。

---

## 1. Before（Step 9 之后）

```text
ToolChatService
    ↓
ToolExecutionService(capabilities=None)      ← 不限制（无项目语义）
    ↓
ToolRegistry.execute
    ↓
Mock Tool（LLM 可请求任意已注册 Tool）
```

Step 8 遗留的两个缺口：

```text
1. ToolChatService 没有 Capability
2. ToolChatService 没有 ProjectContext（无 project scope）
```

---

## 2. After（Step 10）

```text
POST /api/chat/with-tools（可选 project_id）
    ↓
ToolChatService（LLM Function Calling + Multi-Step Loop）
    ↓
ToolExecutionService（唯一 Capability Enforcement Point）
    ├── Capability 校验（ProjectCapabilities.allows_tool）
    ├── project_id（执行上下文；capability 拒绝错误中携带）
    ↓
ToolRegistry.execute（Schema 校验 → Handler → 异常归一化）
    ↓
Mock Tool（get_inventory / get_work_order；0 DB）

授权来源（服务器端，HTTP 不可注入）：
    project_id → ProjectRegistry.get(project_id) → ProjectRegistration.capabilities
```

```text
未提供 project_id → capabilities=None（不限制）= 旧行为完全不变（不查注册表）
```

### 关键接口（最小改动）

```text
ToolChatService.chat(message, *, registry, execution_service=None)
    * 新增逐次覆盖参数（供 API 层传入项目级边界）；
    * 仅做「边界选择」，不做 capability 判断；
    * 未传 → 构造期注入的边界（Step 9）→ 否则按 registry 构造（capabilities=None）。

POST /api/chat/with-tools
    * 新增可选 project_id（≤128，与 /api/ai/chat 同语义）；
    * 提供时：ProjectRegistry 解析 → 项目级执行边界 → 逐次传入；
    * 未注册 → 404；能力未启用 → 403（Handler 0 次调用）。
```

**未新增 DTO / 未新增第二种 CapabilityError**：
复用既有 `ProjectCapabilities` / `ProjectRegistration`（= 项目上下文来源）与
既有统一异常 `AIOrchestratorCapabilityError`（与 `/api/ai/chat` 相同的 403 语义）。

---

## 3. Capability（验收）

```text
Allowed Tool = PASS
    LLM → ToolChatService → ToolExecutionService（允许）→ ToolRegistry → Handler
    （tests/test_tool_chat_capability.py::Test1AllowedToolExecutes）

Denied Tool = PASS
    LLM 请求 get_work_order（项目白名单只含 get_inventory）
        → ToolExecutionService 前置拒绝
        → AIOrchestratorCapabilityError（capability=get_work_order, project_id=…）
        → Handler 0 次 / Registry.execute 0 次 / LLM 不再继续下一轮
    （tests/test_tool_chat_capability.py::Test2DeniedToolRejected）

Handler bypass = PASS
    拒绝发生在 Registry 之前；Handler 永远拿不到请求（0 次调用）

授权失败 ≠ Tool 失败（§十三）
    capability 拒绝**不**降级为 ToolResult(success=False)，
    也不让 LLM 继续（异常穿透 chat() → API 403）

未知 Tool（§十六 Test 3）= PASS
    未列入白名单 → capability 拒绝（不猜 / 不 fallback / 不 retry）
    已列入白名单但未注册 → Registry 返回 ToolResult(False) → LLM 继续（既有语义）

ToolChatService 不含 Capability 判断（§十六 Test 4）= PASS
    AST / 代码层断言：无 capabilit* / allows_tool / 白名单 / Tool 名分支
```

---

## 4. ProjectContext（验收）

```text
project-a（允许 get_inventory）           = PASS（Tool 执行成功）
project-b（tools=()）                     = PASS（capability 拒绝）
project-c（只允许 get_work_order）        = PASS（get_inventory 拒绝 / get_work_order 成功）
cross-project capability                  = DENIED（无跨项目白名单混用）
    每个 project_id 只使用自身 registration.capabilities（服务器端解析）

Handler 只收到 arguments（§十八）          = PASS
    * kwargs == {} / 唯一位置参数 = arguments
    * project_id / request_id / tool_call_id 均不进入 Handler
LLM 输入不含 project scope                 = PASS
    * messages / tools schema 中无 project_id
```

---

## 5. Multi-Step（§二十）

```text
PASS：
    Tool A（允许）→ Tool B（允许）→ Final Answer
    每次执行 = ONE Tool（ToolExecutionService 仍是单次执行边界；
    loop / 预算 / 消息历史仍只属于 ToolChatService）
    每次调用各 1 次 Handler（无内部 loop）
```

---

## 6. API（§十九）

```text
API contract = PASS
    * 新增可选字段 project_id（message 语义 / 响应结构 / path 不变）
    * 403（能力未启用）/ 404（项目未注册）与 /api/ai/chat 语义一致
    * 未提供 project_id → 旧行为完全不变
    * OpenAPI：ToolChatRequest.properties = {message, project_id}；
      POST 响应文档含 403 / 404；响应 schema 仍为 {answer, tool_calls}
```

---

## 7. Security

```text
Capability = centralized
    唯一 Enforcement Point = ToolExecutionService._check_capability
    （链路 A 与链路 B 共用；ToolChatService / API 不做授权判断）
Project scope = centralized
    能力只能来自服务器端 ProjectRegistry（HTTP 无法注入 capabilities）
    project_id 仅作为执行上下文进入边界（并出现在 403 错误中）
Tool Handler = no authorization logic
    Handler 只接收 arguments（无 project_id / 权限分支）
Tool 白名单不可由请求覆盖
    DTO 仅有 message / project_id；capabilities 字段无法注入
```

---

## 8. 两条链路（共享执行边界，Orchestration 仍独立）

```text
AIOrchestrator → ToolExecutionService → Capability → ProjectContext → ToolRegistry
ToolChatService → ToolExecutionService → Capability → ProjectContext → ToolRegistry

共享：ToolExecutionService（唯一授权 + 单 Tool 执行边界）+ ToolRegistry + ToolResult
不共享：Router / ToolArgumentExtractor（链路 B 不经它们）
```

---

## 9. 测试证据

```text
tests/test_tool_chat_capability.py          14 passed（Test 1–5 + 静态断言）
tests/test_tool_chat_project_context.py     14 passed（A/B/cross-project + Handler 隔离 + API + OpenAPI）
tests/test_tool_chat_architecture_contract.py  45 passed（新增 C11 Capability / C12 ProjectContext）
tests/test_tool_chat_execution_boundary.py  19 passed（Step 9 迁移回归，未修改）
tests/test_tool_chat_service.py / test_tool_chat_api.py  未修改即全部通过

全量：python -m pytest -q → 2733 passed / 317 skipped
      $env:RUN_DB_TESTS="1"; python -m pytest -q → 3009 passed / 41 skipped
      python -m compileall -q backend → clean
```

---

## 10. Limitations（如实记录）

```text
* 链路 B 仍只挂 Mock Tools（真实 Tool 接入 = NOT IMPLEMENTED，Step 11+）
* capability 只覆盖 Tool 白名单：knowledge_enabled / text_to_sql_enabled
  在链路 B 不适用（该链路不经过 RAG / Text-to-SQL）
* project scope 进入边界的形式 = (project_id, capabilities)；
  未把完整 ProjectContext DTO（含 data_source）注入边界
  （链路 B 不需要 Engine / Schema / Semantic）
* 未提供 project_id 时不做任何限制（旧行为）——调用方需自行选择是否传
* 未新增审计 / request_id / Tool 调用日志字段（与既有行为一致）
* lint unavailable（环境未安装 ruff / flake8；未安装新工具）
```
