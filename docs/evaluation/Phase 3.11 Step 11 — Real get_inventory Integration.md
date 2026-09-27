# Phase 3.11 Step 11 — Real `get_inventory` Integration

> 目标：把**现有真实只读** `get_inventory`（Phase 3.7.12）接入 ToolChatService
> 的 Function Calling 链路；Mock Tools 保留给单元 / characterization 测试。
>
> 本阶段未新增任何 Tool、未修改任何核心 Tool / 执行边界 / Registry 逻辑、
> 未接入真实 `get_work_order`、未使用真实 LLM。

---

## 1. Architecture

```text
POST /api/chat/with-tools
    ↓
ToolChatService                       （LLM Function Calling + Multi-Step Loop）
    ↓
ToolExecutionService                  （capability + project scope；ONE Tool execution）
    ↓
Capability                             （ProjectCapabilities.allows_tool）
    ↓
ToolRegistry                          （Schema 校验 + Handler 派发 + 异常归一化）
    ↓
Real get_inventory Handler             （Phase 3.7.12；未修改）
    ↓
PostgreSQL                             （SELECT …；BEGIN READ ONLY +
                                        statement_timeout + 绑定参数 + rollback）
    ↓
ToolResult → role=tool 消息 → LLM → Final Answer
```

```text
Before：ToolChatService → ToolExecutionService → Mock get_inventory（硬编码，0 DB）
After ：ToolChatService → ToolExecutionService → Capability → ToolRegistry
                     → Real get_inventory → PostgreSQL
```

### 装配（本阶段唯一生产代码修改）

```text
backend/app/api/tool_chat.py
    _tool_registry = ToolRegistry()
    register_get_inventory_tool(_tool_registry)      # Step 11（原 register_mock_tools）
    _tool_execution_service = ToolExecutionService(registry=_tool_registry)
    _tool_chat_service = ToolChatService(execution_service=_tool_execution_service)
```

`ToolChatService` **零修改**：它对 Tool 实现保持无感知（只用
`registry.list_definitions()` 生成 schema + 经执行边界 `execute()`），
因此"换真实 Tool"只是装配点的一行改动 —— 这正是 Step 9/10 边界的验证。

---

## 2. Tool

```text
Tool                = get_inventory（唯一）
Real Handler        = YES（GetInventoryHandler，Phase 3.7.12）
Read Only           = YES（SET TRANSACTION READ ONLY + statement_timeout + rollback）
Definition          = GET_INVENTORY_DEFINITION（material_code 必填 / warehouse_code 可选）
SQL                 = SELECT COALESCE(SUM(qty),0) FROM "<schema>"."<table>"
                      WHERE material_code = :material_code（绑定参数）
Registry Contract   = 生产 Registry 只有 1 个 Definition；
                      同名 Mock Definition 无法覆盖（ToolAlreadyRegisteredError，C13 锁定）
Mock Tool           = 保留（仅测试注入：Function Calling / Multi-Step / Budget / Error）
Real get_work_order = DEFERRED（本阶段明确不接入）
```

---

## 3. LLM

```text
Fake LLM   = YES（tests/test_tool_chat_service.py::ScriptedLLMClient）
Real LLM   = NO（未新增 RUN_REAL_LLM / 未启用真实 DeepSeek）
RUN_REAL_TOOL_CALLING_TEST = 保持 SKIP（未加入默认测试）
```

---

## 4. DB

```text
PostgreSQL = YES（真实只读查询；隔离 fixture schema inventory_tool_test）
DB writes  = 0
    * public.knowledge_document / knowledge_chunk 行数不变；
    * fixture 表（inventory_tool_test.inventory）行数不变；
    * 语句审计：仅 SELECT / SET / ROLLBACK（无 INSERT / UPDATE / DELETE / DDL）。
测试数据 = 复用既有 fixture（seed：10001=120 / 10002=80 / MAT-001=250 / SKU.001=5）
DB 门控   = RUN_DB_TESTS=1；默认 pytest -q **不访问** PostgreSQL。
```

---

## 5. Security

```text
Capability              = PASS（project-a 允许 → 真实执行；project-b → 403 语义 +
                          Handler 0 次 + DB 0 次）
Schema validation       = PASS（missing required / unknown field → Registry 拒绝 →
                          0 条 SQL、Handler 0 次）
Unknown arguments       = REJECT（"fake_field" → unknown field；未进入真实 DB）
Injection input         = REJECT（"' OR 1=1 --" → Handler 字符集校验拒绝；
                          0 条 SQL；不回显注入值）
Handler bypass          = PASS（ToolChatService 无 registry.execute / 无 SQL / 无 DB；
                          唯一执行调用点 = execution.execute → C2/C13 静态锁定）
Read-only guarantee     = PASS（语句审计 + READ ONLY 事务 + 绑定参数 %(material_code)s）
warehouse_code          = 显式拒绝（库存表无该维度；不静默全仓汇总；0 条 SQL）
```

---

## 6. Multi-Step

```text
ToolCall(get_inventory, MAT-001) → Real Tool → ToolResult → Final Answer  = PASS
ToolCall ×2（MAT-001 / 10002）   → 2 次真实 SELECT（每次 ONE Tool execution）= PASS
LLM 调用次数 = Tool 执行次数 + 1（预算 TOOL_MAX_ROUNDS 钳制 [1,20] 不变）   = PASS
```

---

## 7. Tests

```text
新增：tests/test_tool_chat_real_get_inventory.py
    14 tests（10 DB-gated + 4 非 DB）；
    DB 部分：真实 E2E / arguments 原样 / missing required / unknown field /
            注入输入 / warehouse_code 拒绝 / Multi-Step 双查询 / DB writes=0 /
            project scope 允许 / HTTP API E2E
契约：tests/test_tool_chat_architecture_contract.py +C13（6 tests）
    真实 Definition 身份 / get_work_order 未注册 / Mock 无法覆盖 /
    Service 无 SQL-DB / API 无 Engine-SQL / 执行边界唯一
适配（生产 Registry 换真实 Tool 后，避免默认运行触达 DB）：
    tests/test_tool_chat_api.py              TestRealServiceWiring → monkeypatch Mock Registry
    tests/test_tool_chat_execution_boundary.py  HTTP 用例 → monkeypatch Mock Registry
    tests/test_tool_chat_project_context.py     client fixture → monkeypatch Mock Registry
    tests/test_tool_chat_api.py              test_registry_passed_to_service → 断言 {get_inventory}

full no DB：2743 passed / 327 skipped（Step 10：2733 / 317 → +10 passed / +10 skipped）
full DB    ：3029 passed / 41 skipped （Step 10：3009 / 41 → +20：14 新增 + 6 C13）
```

---

## 8. 当前限制（如实记录）

```text
* 真实 Tool 只有 get_inventory；get_work_order = DEFERRED；
* project scope 只决定**授权**；提供 project_id 时 Tool 仍使用全局
  inventory 设置（engine + settings.inventory_tool.schema/table），
  不像链路 A 那样按项目绑定 Engine / schema（per-project Tool Registry
  绑定 = 后续独立阶段）；
* warehouse_code 仍为"契约保留"（真实库存表无 warehouse 维度 → 显式拒绝）；
* 未新增审计 / request_id / Tool 调用日志字段；
* 未测试真实 DeepSeek（Real LLM = NO）；
* lint unavailable（环境未安装 ruff / flake8；未安装新工具）。
```

---

## 9. Final State

```text
ToolChatService
    ↓ LLM Function Calling（Fake LLM in tests）
    ↓ Multi-Step Loop（预算 TOOL_MAX_ROUNDS，[1,20]）
ToolExecutionService
    ↓ Capability（ProjectCapabilities.allows_tool；拒绝 → 403 语义）
    ↓ ONE Tool execution
ToolRegistry
    ↓ Schema 校验（required / type / unknown field）
Real get_inventory Handler
    ↓ PostgreSQL（READ ONLY SELECT）
ToolResult
    ↓ role=tool → LLM
Final Answer

Real get_inventory = PASS
Real get_work_order = DEFERRED
Capability = PASS
ProjectContext = PASS（授权作用域；Engine 绑定 Deferred）
DB writes = 0
Real LLM = NO
Agent / MCP / LangGraph / Memory / Planning = NOT IMPLEMENTED
```
