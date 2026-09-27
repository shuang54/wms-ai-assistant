# Phase 3.11 Step 9 — ToolChatService Execution Boundary Migration

> 目标：让 ToolChatService 复用现有 `ToolExecutionService`，统一 **Tool 执行入口**。
> 多步循环 / Function Calling / 预算语义**全部保持不变**；
> Capability / ProjectContext / 真实 Tool 接入**本阶段不解决**（Deferred）。

---

## 1. Migration Before

```text
ToolChatService
    │
    ├── definitions_to_openai_tools()      （Tool Schema）
    ├── LLM（tools）→ ToolCall(name, arguments)
    └── registry.execute(call.name, call.arguments)     ← 直接执行
            ↓
        ToolRegistry.execute → validate_arguments → Handler → ToolResult
```

问题（Step 8 勘察结论）：链路 B 绕过 Phase 3.11 Step 2 引入的应用层执行边界
（`CURRENT VIOLATION`）。

---

## 2. Migration After

```text
ToolChatService                                 （仍是多步编排的唯一承担者）
    │
    ├── definitions_to_openai_tools()      （Tool Schema，仍来自传入 registry）
    ├── LLM（tools）→ ToolCall(name, arguments)
    └── execution.execute(name, arguments=...)          ← 统一执行边界
            ↓
        ToolExecutionService.execute
            ├── capability 校验（capabilities=None → 不限制；Step 9 Deferred）
            ↓
        ToolRegistry.execute → validate_arguments → Handler → ToolResult
```

```text
Before：ToolChatService → ToolRegistry.execute
After ：ToolChatService → ToolExecutionService → ToolRegistry.execute
```

### 装配（module-level singleton，保持项目既有风格）

```text
backend/app/api/tool_chat.py
    _tool_registry            = ToolRegistry() + register_mock_tools(...)
    _tool_execution_service   = ToolExecutionService(registry=_tool_registry)
    _tool_chat_service        = ToolChatService(execution_service=_tool_execution_service)
    ↓
    POST /api/chat/with-tools → _tool_chat_service.chat(message, registry=_tool_registry)
```

### ToolChatService 接口（向后兼容 + 依赖注入）

```text
ToolChatService(
    llm_client=None,                # 不变
    *, max_tool_rounds=None,        # 不变
    execution_service=None,         # 新增（Phase 3.11 Step 9，可注入）
)

chat(message, *, registry)          # 签名不变（registry 仍逐次传入）

执行边界解析：
    注入 execution_service → 复用之（其 registry 为执行权威；
                              传入 registry 必须是同一实例，否则 ValueError）
    未注入               → 本次按 registry 构造 ToolExecutionService(registry=registry)
                          （capabilities=None → 与迁移前 registry.execute 行为等价）
```

---

## 3. Behavior Preserved

```text
Function Calling                  = PASS（LLM 仍决定 Tool 名称；无 Router 介入）
Argument forwarding               = PASS（ToolCall.arguments 原样 → 边界 → Registry；
                                          不 rename / 不 drop / 不 add / 不 normalize）
Schema Validation                 = PASS（仍由 ToolRegistry.validate_arguments 裁决）
Multi-Step                        = PASS（while + 历史消息 + 顺序执行）
Budget（TOOL_MAX_ROUNDS）          = PASS（先于执行拒绝；max_rounds 次执行 + 1 次拒绝）
Multiple Tool Call rejection      = PASS（>1 → MultipleToolCallsError，0 次执行）
Tool failure continuation         = PASS（ToolResult(success=False) → role=tool → LLM 继续）
Malformed ToolCall                = PASS（LLMToolCallFormatError 原样传播，0 次执行 / 0 重试）
API contract（path/DTO/status/错误结构） = PASS（未改动）
Tool message 序列化                = PASS（success/data/error 三字段，无敏感信息）
```

回归证据：

```text
* tests/test_tool_chat_service.py（30+）与 tests/test_tool_chat_api.py（20+）
  **未修改一行**即全部通过；
* tests/test_tool_chat_service_characterization.py（Step 8 行为基线，8 tests）
  **未修改**即全部通过；
* 新增 tests/test_tool_chat_execution_boundary.py（19 tests）覆盖 §十二 Test 1–8。
```

### 执行边界仍为 ONE Tool（未被改造成 Runtime）

```text
ToolExecutionService.execute(tool_name, *, arguments)     ← 单次执行，签名未变
    ❌ 无 while / for（loop 留在 ToolChatService）
    ❌ 不 import LLM / ToolChatService
    ❌ 无 retry / replanning / parallel / memory
```

---

## 4. Capability

```text
Capability = NOT IMPLEMENTED（intentionally deferred）
```

原因：Step 9 只统一执行边界；链路 B 当前使用 **Mock Tools**，且没有项目语义。

```text
ToolChatService 构造边界时不传 capabilities / project_id
    → ToolExecutionService(capabilities=None) = **不限制**
    → 这不是权限控制：任何注册进该 Registry 的 Tool 仍可被 LLM 请求执行。
参考：主链路仍由 ToolExecutionService._check_capability + ProjectCapabilities 硬校验。
```

---

## 5. Project Context

```text
ProjectContext = NOT IMPLEMENTED
```

链路 B 仍不下发 project context（Handler 只收到 arguments）；
Step 9 未引入 project_id / ProjectContextProvider / 项目级 Registry。

---

## 6. Real Tools

```text
Real Tool integration = NOT IMPLEMENTED
```

链路 B 的 registry 仍只注册 Phase 3.6.1 Mock Tools
（`get_inventory` / `get_work_order`，硬编码，0 DB）；
真实只读 Tool（Phase 3.7.12 / Step 5）仍在链路 A，未接入本链路。

---

## 7. 依赖方向（迁移后）

```text
ToolChatService
    ├── LLMProvider（Function Calling）
    ├── ToolExecutionService（执行边界；两条链路共享）
    │        ↓
    │   ToolRegistry（Schema + 执行权威）
    │        ↓
    │   Tool Handler
    ├── llm.tool_schema（ToolDefinition → OpenAI tools）
    └── tools.base.ToolResult（统一结果 DTO）

禁止（保持不变）：
    ToolChatService → Handler / SQL / DB / HTTP / Shell / File（0 依赖）
    ToolChatService → AIOrchestrator / AIRouter / ToolArgumentExtractor（0 依赖）
    ToolChatService → Router + Function Calling 混合选择（不存在）
```

---

## 8. 两条链路（保持独立，共享执行边界）

```text
【链路 A】AI Orchestrator（ONE Tool）
Question → AIOrchestrator → Router → RouteDecision.tool_name
         → ToolArgumentExtractor → ToolExecutionService → ToolRegistry → Tool

【链路 B】ToolChatService（Multi-Step）
User Message → ToolChatService → LLM Function Calling → ToolCall
             → ToolExecutionService → ToolRegistry → Tool → ToolResult
             → LLM（下一轮）... → 最终回答

共享：ToolExecutionService（单 Tool 执行边界）+ ToolRegistry + ToolResult
不共享：Orchestration / Selection / Argument（语义不同，未合并）
```

---

## 9. 测试结果

```text
python -m pytest -q tests/test_tool_chat_execution_boundary.py ... （定向 5 文件）→ 132 passed
python -m pytest -q                                  → 2696 passed / 317 skipped（Step 8：2675）
$env:RUN_DB_TESTS="1"; python -m pytest -q           → 2972 passed / 41 skipped（Step 8：2951）
python -m compileall -q backend                      → clean
LSP diagnostics                                      → 0 error / 0 warning
lint（ruff / flake8）                                 → lint unavailable（未安装新工具）
```

DB writes = 0；Network = 0（Mock Tools + Scripted LLM + Fake 边界，0 真实 LLM）。

---

## 10. Limitations（如实记录）

```text
* Capability / ProjectContext / 真实 Tool 接入 = Deferred（见 §4–§6）
* registry 与 execution_service 的一致性：注入边界时要求传入 registry 与
  边界 registry 为同一实例（否则 ValueError）；未注入时每次 chat 构造一个
  边界实例（无缓存，ToolChatService 无状态）
* fail-fast 语义变化（唯一内部行为变化，无测试/调用方受影响）：
  chat() 开始时即解析执行边界 → 传入不具备 execute() 的 registry 会
  立即 TypeError（迁移前只在真正调用 Tool 时才失败）。
  合法输入（真实 ToolRegistry / 具备 execute 的替身）行为完全不变。
* 未引入 Handler 级重试 / 超时 / 审计（与迁移前一致）
* 多步链路的可观测性仍限于 service logger（无 request_id / tool 审计）
* lint unavailable（环境限制）
```

---

## 11. Final State

```text
AIOrchestrator
    ↓
ToolExecutionService
    ↓
ToolRegistry

ToolChatService
    ↓
ToolExecutionService
    ↓
ToolRegistry

Capability      = Deferred
ProjectContext  = Deferred
Real Tool       = Deferred
Agent / MCP / LangGraph / Memory / Planning = NOT IMPLEMENTED
```
