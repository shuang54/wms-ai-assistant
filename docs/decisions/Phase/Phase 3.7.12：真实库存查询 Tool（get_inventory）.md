# Phase 3.7.12：真实库存查询 Tool（get_inventory）

> 状态：**已完成**（2026-09-24）
> 范围：单个真实只读 Tool；不引入 Agent / LangGraph / MCP / 权限 / 写操作。
> 后续阶段：全栈单点回归 1111 / 含 DB 1222，全绿。

---

## 1. 为什么选择 `get_inventory` 作为第一个真实 Tool

| 候选 | 取舍 |
|---|---|
| `get_inventory` | ✅ 选取：单参数（`material_code`）、只读 SUM、SQL 完全可枚举，不依赖业务规则回放 |
| `get_purchase_order` | 多参数 + 多表 JOIN + 状态机，对 Tool Framework 早期阶段不友好 |
| `get_sales_order` | 同上，且涉及脱敏与权限边界 |
| `get_inbound_order` | 同上 |

阶段目标就是"让真实链路第一次跑通"。`get_inventory` 业务字段最少（物料编码 → 库存数），最适合作为打通 `AIRouter → TOOL → ToolRegistry → Handler → 真实 DB → ToolResult` 的最小可验证节点。

---

## 2. Tool 与 Text-to-SQL 的职责区别

| 维度 | 固定 Tool（`get_inventory`） | Text-to-SQL（分析） |
|---|---|---|
| 触发 | 自然语言固定表达（"查询物料 X 当前库存"） | 自然语言任意分析（"库存最多的前 10 个物料"） |
| 参数 | 业务字段（`material_code`） | 不限字段 / 多表 / 聚合 / 排序 |
| SQL | 模板化 + 绑定参数 | LLM 生成 + SQLValidator + SQLExecutor |
| 范围 | 一个固定能力 | 任意业务问题 |

**本阶段不修改 Text-to-SQL 任何核心逻辑**，§十九 的"不要污染 Text-to-SQL"被严格遵守：见 `TestRouterDiscovery.test_aggregation_question_is_not_hijacked_by_tool`（聚合问题仍归 `TEXT_TO_SQL`）。

---

## 3. Tool 参数设计

```json
{
  "material_code": "10001"
}
```

- 必填、必 strip、不允许空字符串、不允许纯空白
- 字符集 `[A-Za-z0-9._-]{1,64}`（覆盖 `10001` / `MAT-001` / `SKU.001` / `mat_001`）
- **不接受**：`table` / `sql` / `where_clause` / `query` / `connection` 等任意输入
- Tool 层参数白名单 + Handler 二次校验（纵深防御）

---

## 4. Tool Capability（Router 可见）

`GET_INVENTORY_DEFINITION`：

- `name`: `get_inventory`
- `description`: "查询指定物料的当前库存数量（只读）。返回 material_code 与 qty。仅支持 material_code 单参数；不接受 SQL / table / where_clause 等。"
- `aliases`: **有区分度短语**（`当前库存` / `库存数量` / `查库存` / `物料库存` / `库存查询`），**不放裸词"库存"**（§十九，否则聚合问题会被抢占）
- `parameters`: JSON Schema，仅 `material_code` 一个必填字段

`ToolRegistryCapabilityAdapter` 只暴露 `name` / `description` / `aliases`，**不暴露** `handler` / `parameters` / SQL（最小特权原则）。`test_capability_metadata_exposes_aliases_only` 显式断言。

---

## 5. Project Context 如何进入 Tool

链路：

```text
API /api/ai/chat  (project_id)
   ↓
AIOrchestratorService.execute (project_id 由 _build_orchestrator_for_project_id 注入)
   ↓
ToolRegistry.execute (project_id 不进 arguments；保持 Handler 干净)
   ↓
GetInventoryHandler.__call__
   ↓
GetInventoryProjectContextProvider.resolve()  →  ProjectContext
   ↓
返回 payload 包含 project_id（仅 metadata，不进 SQL 拼接）
```

**Tool 内不写死 `project_id == "vietnam-wms"`**。`GetInventoryProjectContextProvider` 是 Tool 层的本地最小 Provider，避免 `orchestrator → tools → orchestrator` 循环依赖；project_id 通过 `settings.project.project_id` 解析，API 层可透传覆盖。

---

## 6. 数据库查询方式

### 6.1 数据来源（重要前置）

| 来源 | 取舍 |
|---|---|
| 现有 public 业务表 | ❌ 项目当前 PostgreSQL **未提供真实库存业务表**（经 SchemaExplorer 确认）；不伪造生产表 |
| 新建业务表 | ❌ 任务书 §三 明确禁止 |
| **隔离测试 schema** | ✅ `inventory_tool_test` schema + `inventory` 表，仅由 `TestRealDatabaseInventory.setup_class` / `teardown_class` 自动建/拆；**不影响 public 与生产数据** |

### 6.2 查询 SQL（模板）

```sql
SELECT COALESCE(SUM(qty), 0)
FROM "<schema>"."<table>"
WHERE material_code = :material_code
```

- `material_code` → **绑定参数**（不拼字符串）
- `schema` / `table` → 启动期由 `_assert_safe_identifier`（`^[A-Za-z_][A-Za-z0-9_]*$`）白名单校验后安全拼接；二者来自配置 `INVENTORY_TOOL_SCHEMA_NAME` / `INVENTORY_TOOL_TABLE_NAME`，可通过环境变量指向任何已有库存表
- 配置由 `InventoryToolSettings` 持有（`backend/app/config.py`）：`schema_name` / `table_name` / `material_code_max_len` / `query_timeout_seconds`

### 6.3 事务级纵深防御

```sql
SET TRANSACTION READ ONLY;
SET LOCAL statement_timeout = <ms>;
-- SELECT ...
-- 自动 ROLLBACK
```

- `BEGIN READ ONLY`：数据库端拒绝任何写入
- `SET LOCAL statement_timeout`：避免长事务占用
- 自动 `ROLLBACK` 归还连接
- Python 端 `asyncio.to_thread` 包裹同步驱动

---

## 7. 只读安全设计（任务书 §九 / §十二 / §十三）

| 威胁 | 防御层 |
|---|---|
| 任意 SQL 输入 | Tool 参数白名单（JSON Schema）+ Handler 二次校验（字符集） |
| `'; DELETE FROM ...` | material_code 不进入 SQL 拼接，仅绑定参数；事务 `READ ONLY` 兜底 |
| SQL 关键字 | `_MATERIAL_CODE_PATTERN` 拒绝空白符、引号、分号、注释符 |
| 越权访问其他表 | schema/table 来自配置且白名单校验，不接受 Tool 入参 |
| DB 凭据泄露 | ToolResult.error 仅含 `cause_type=Exception 类名`，不含 SQL / 连接串 / traceback |
| 配置被注入 | `_assert_safe_identifier` 防御 schema/table 配置错误或环境变量注入 |

---

## 8. 错误处理（任务书 §十三）

| 情况 | Tool 行为 |
|---|---|
| `material_code=""` 或 `"   "` | `ToolValidationError` → `ToolResult(success=False, error="material_code 不能为空或纯空白")`；不回显原始输入 |
| `material_code` 含非法字符 | 同上（"包含非法字符..."） |
| `material_code` 超长 | 同上（"长度不能超过 ..."） |
| DATABASE_URL 未配置 | `ToolExecutionError("Database engine 未配置")` |
| schema/table 配置非法 | `ToolExecutionError("schema_name 配置非法: ...")` |
| DB 异常 | `ToolExecutionError("数据库查询失败", cause_type=...)`；**不暴露 SQL / host / port / password** |
| 物料不存在 | `success=True, data={"qty": 0.0, ...}`（业务语义：零库存） |

---

## 9. 测试结果（任务书 §十七 / §二十）

### 9.1 测试覆盖矩阵（12 项 → 13 类 48 用例）

| §十七 要求 | 测试类 | 用例数 |
|---|---|---|
| 1. 正常查询 | `TestRealDatabaseInventory.test_real_query_returns_seed_qty` | 1 |
| 2. 不存在物料 → qty=0 | `TestRealDatabaseInventory.test_real_query_returns_zero_for_missing` | 1 |
| 3. 空参数 / 4. 空白参数 | `TestDefinitionAndValidation` (5) + `TestRegistryRegistration.test_registry_execute_validation_failure_*` | 7 |
| 5. SQL Injection 不越权 | `TestRealDatabaseInventory.test_sql_injection_does_not_exfiltrate` | 1 |
| 6. SQL Injection + DELETE | `TestRealDatabaseInventory.test_sql_injection_delete_attempt_rejected` | 1 |
| 7. Tool Registry | `TestRegistryRegistration` (6) | 6 |
| 8. Router | `TestRouterDiscovery` (8) | 8 |
| 9. Orchestrator | `TestOrchestratorIntegration` (2) | 2 |
| 10. API | `TestApiIntegration` (3) | 3 |
| 11. Project Context | `TestProjectContext` (2) | 2 |
| 12. DB 无写入 | `TestRealDatabaseInventory.test_no_db_write_to_public_knowledge` + `TestDbWriteProtection.test_knowledge_tables_unchanged` | 2 |

### 9.2 实际命令与结果

```text
pytest tests/test_get_inventory_tool.py -q
    → 65 passed in 1.55s
RUN_DB_TESTS=1 pytest tests/test_get_inventory_tool.py -q
    → 65 passed in 1.68s
RUN_DB_TESTS=1 pytest -q
    → 1222 passed, 35 skipped (only RUN_REAL_TOOL_CALLING_TEST opt-in opt-in skipped)
pytest -q
    → 1111 passed, 146 skipped
python -m compileall backend
    → exit 0
```

LSP / lint：4 个相关文件 0 errors。**数据库写入 = 0；真实 LLM 调用 = 0**（`RUN_REAL_TOOL_CALLING_TEST` opt-in 跳过）。

### 9.3 §二十二 验收链路

```text
POST /api/ai/chat {"question": "查询物料 10001 当前库存"}
   ↓
AIOrchestratorService (orchestrator_chat._default_orchestrator)
   ↓
AIRouterService (ToolRegistryCapabilityAdapter 注入)
   ↓ source=tool_match
TOOL → get_inventory
   ↓
Extract material_code via Phase 3.7.12 compatibility layer
   ↓
ToolRegistry.execute("get_inventory", arguments={"material_code": "10001"})
   ↓
GetInventoryHandler.__call__
   ↓
_run_inventory_query → BEGIN READ ONLY → SELECT SUM(qty) WHERE material_code = :material_code
   ↓
ToolResult(success=True, data={"material_code": "10001", "qty": 120.0, "project_id": ...})
   ↓
AIOrchestrationResult
   ↓
ChatResponse(route=tool, content=..., metadata=...)
```

链路每一步有显式测试覆盖（`TestApiIntegration.test_api_returns_tool_route_with_real_db`）。

---

## 10. 当前限制

1. **真实库存业务表不存在**：`get_inventory` 当前由 `inventory_tool_test` 测试 schema 驱动；生产部署需要把 `INVENTORY_TOOL_SCHEMA_NAME` / `INVENTORY_TOOL_TABLE_NAME` 指向真实表（且表必须含 `material_code` / `qty` 两列）。本阶段不创建生产库存表。
2. **参数提取仅 `material_code`**：`_extract_tool_arguments_from_question` 当前只解析单字段；扩展 `warehouse_code` / `lot_no` 等需在该函数中显式添加，**不提前实现**（任务书 §三）。
3. **Project Context 只用于 metadata**：Tool 当前**不**根据 project_id 选择不同 data source；project_id 决定上游 LLM / 文档加载 / 配置注入的能力**本阶段未实现**（需后续 Phase）。
4. **Orchestrator 兼容性 patch**：`_run_tool` 从传入 `arguments=None` 改为从 `ToolDefinition.parameters` 抽取（任务书 §三允许的最小兼容性修改）。**不**重写 Orchestrator 核心 route 逻辑。
5. **没有错误重试 / 缓存 / 限流**：Tool 调用是简单 DB 直查；后续若引入缓存或重试，需在本阶段之外明确需求。

---

## 11. 后续可以扩展哪些 Tool

**不**提前实现，仅列入路线：

```text
get_purchase_order    采购订单查询（多参数 + 状态机）
get_sales_order       销售订单查询（多参数 + 权限边界 + 脱敏）
get_inbound_order     入库单查询（多表 JOIN）
get_outbound_order    出库单查询（多表 JOIN + 批次）
get_inventory_by_lot  按批次库存（需先支持 batch_inventory / expiry 表）
get_warehouse_usage   仓库库位使用率（聚合 → 应优先走 Text-to-SQL）
```

扩展纪律：每个 Tool 沿用 `ToolRegistry` + `ToolDefinition` + `Project Context Provider` + 参数白名单 + `BEGIN READ ONLY` 五件套，避免每个 Tool 各搞一套（任务书 §十三）。

---

## 附：本阶段修改 / 新增文件

### 修改
- `backend/app/config.py`：新增 `InventoryToolSettings`（schema/table/material_code_max_len/query_timeout_seconds）
- `backend/app/services/ai_router_service.py`：导出 `ToolRegistryCapabilityAdapter`
- `backend/app/services/ai_orchestrator_service.py`：`_run_tool` 增加 arguments 抽取；`_load_default_project_context` 用 `DataSource(name=..., type=...)` 新签名
- `backend/app/api/orchestrator_chat.py`：默认 Orchestrator 注入预注册 `get_inventory` 的 Registry + Router 能力元数据
- `backend/app/tools/base.py`：ToolDefinition 增加 `aliases` 字段（任务书 §七 + §十九 要求）

### 新增
- `backend/app/tools/get_inventory.py`：ToolDefinition / ProjectContextProvider / 参数校验 / 只读查询 / Handler / 注册助手
- `tests/test_get_inventory_tool.py`：13 类 48 用例（含真实 DB）
- `docs/decisions/Phase/Phase 3.7.12：真实库存查询 Tool（get_inventory）.md`：本文件