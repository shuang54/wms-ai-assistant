# Phase 3.11 Step 5 — Multiple Real Tools（第二个真实只读 Tool）

> 目标：验证 `ToolExecutionService` 可以成为**多个真实 Tool** 的统一执行边界。
> 新增第二个真实、只读、业务明确的 Tool（`get_work_order` 真实化），
> 证明第二个真实 Tool **不需要**在 Orchestrator 中重新实现一套执行逻辑。

---

## 1. 第二个 Tool

| 项 | 值 |
|---|---|
| Tool Name | `get_work_order`（真实化自 Phase 3.6.1 Mock 版） |
| Purpose | 查询指定工单的状态（只读） |
| Parameters | `work_order_no: str`（必填，1-64 字符，`[A-Za-z0-9][A-Za-z0-9._\-]{0,63}`）；单参数（遵循"不为多参数而多参数"） |
| 输出 | `{"work_order_no": <str>, "status": <str\|None>, "project_id": <str\|None>}`；工单不存在 → `status=None`（不伪造状态） |
| 是否真实 DB | **代码路径真实**（绑定参数 SELECT + READ ONLY 事务 + statement_timeout + 标识符白名单）；**database-level 集成 = NOT SUPPORTED**（见 §6） |
| Project Scope | `GetWorkOrderProjectContextProvider`（本地最小实现，不硬编码项目；engine / schema / project_id 由工厂注入） |
| Read-only | 是（`SET TRANSACTION READ ONLY`；无 INSERT/UPDATE/DELETE 入口；无 shell / HTTP / 文件访问） |
| 配置 | `settings.work_order_tool`（`WMS_WORK_ORDER_SCHEMA` / `WMS_WORK_ORDER_TABLE` / `WMS_WORK_ORDER_NO_MAX_LEN` / `WMS_WORK_ORDER_QUERY_TIMEOUT_SECONDS`） |

文件：`backend/app/tools/get_work_order.py`（与 `get_inventory.py` 完全同构的安全模式）。

---

## 2. Selection（Router 是唯一来源）

```text
"查询物料 10001 当前库存"     → RouteDecision(TOOL, tool_name="get_inventory")
"查询工单 WO-202609-001"      → RouteDecision(TOOL, tool_name="get_work_order")
"统计本月工单数量"             → TEXT_TO_SQL（不被新 Tool 抢占）
```

* Router **零核心修改**：第二个 Tool 仅通过 `ToolRegistryCapabilityAdapter`
  的 capability 元数据（name / description / aliases）进入匹配；
* aliases 遵循既有纪律（无裸词"工单"，防止抢占聚合问题）；
* 多 Tool 回归测试：库存 ↔ 工单互不误选；`get_work_order` 的
  description/aliases 不命中库存问题，反之亦然。

---

## 3. Execution（统一执行边界）

```text
tool_name（来自 RouteDecision）
    ↓
AIOrchestrator._run_tool（参数提取：work_order_no 确定性规则）
    ↓
ToolExecutionService.execute（**同一实例、同一条代码路径**）
    ↓ capability 校验
ToolRegistry.execute（Schema 校验 → Handler → 异常归一化）
    ↓
get_inventory  /  get_work_order（各自的 Handler）
    ↓
ToolResult（统一契约，未新建 WorkOrderResult）
```

* `ToolExecutionService` / `ToolRegistry` / `RouteDecision` **零修改**；
* 集成测试证明：同一个 `ToolExecutionService` 实例分别执行两个 Tool
  （各自 handler 收到精确 arguments，互不误调）；
* capability 白名单对第二个 Tool 同样生效（Handler 0 次调用即拒绝）；
* Orchestrator 中**无 Tool-specific 分支**（无 `if tool == ...`）。

---

## 4. Security

```text
unknown field（password 等）→ ToolRegistry 拒绝（Handler 0 次调用）
invalid type（123 等）      → ToolRegistry 拒绝
missing required            → ToolRegistry 拒绝
SQL injection（"WO-001' OR '1'='1"）
    → Handler 字符集校验拒绝（ToolValidationError，未触达任何 SQL）
    → 且 SQL 构造经 Fake Engine 实测：使用 :work_order_no 绑定参数，
      SQL 文本内不出现输入值；READ ONLY + statement_timeout + rollback
write operation → unavailable（源码静态检查：无 INSERT/UPDATE/DELETE/
                  DROP/ALTER/TRUNCATE 字符串；无写路径）
schema/table 注入（配置被污染）→ 标识符白名单拒绝（ToolExecutionError）
```

* 参数校验分层与既有契约一致：Registry 负责类型 / required / 未知字段；
  Handler 负责字符集（第二层）；
* `_run_work_order_query` 与 inventory 版本同构（绑定参数 + READ ONLY +
  超时 + rollback），错误消息不含 SQL 原文 / connection string。

---

## 5. Boundary（职责分工，均已由测试锁定）

```text
Router                  = selection（Tool 选择唯一来源；不含 handler/DB）
Orchestrator            = orchestration（decision.tool_name → 参数提取 →
                          执行边界；无 Tool-specific 分支 / 无 DB / 无 loop）
ToolExecutionService    = execution boundary（capability + 委派 Registry；
                          零修改即支持第二个 Tool）
ToolRegistry            = schema / execution authority（validate_arguments +
                          Handler 调用 + 异常归一化；唯一执行权威）
Tool（Handler）          = business implementation（只读查询 + 项目上下文）
```

Tool-to-Tool 隔离（测试锁定）：

```text
GetWorkOrderHandler / GetInventoryHandler
    → 无 ToolRegistry / ToolExecutionService / Orchestrator 依赖
      （实例属性 + 构造参数 + 模块级 AST import 三方向检查）
Tool A 不调用 Tool B（E2E 断言两个 handler 调用计数互补）
Question → Router → ONE Tool（无 Tool 链）
```

---

## 6. DB Integration（如实报告）

**Step 5 勘察结论（information_schema 实测）**：当前真实 DB 仅存在

```text
ai_ops.llm_usage_record
public.knowledge_document / public.knowledge_chunk
t2s_eval.*（Text-to-SQL 评估隔离表）
```

**没有任何 work_order 业务表**（也没有其它适合安全接入的第二个只读业务表）。

因此按约束处理：

```text
database-level get_work_order = NOT SUPPORTED
（不修改数据库结构、不创建表、不修改生产数据、不新增 DB fixture）
```

DB 路径正确性通过**非 DB 的完整边界测试**验证（Fake Engine）：

* SQL 使用绑定参数（`:work_order_no`），输入值不进入 SQL 文本；
* `SET TRANSACTION READ ONLY` + `SET LOCAL statement_timeout` + `rollback`
  按序执行；
* status 返回 / 不存在 → None / project_id 回显；
* 注入串在 Handler 层被拒绝（未触达 SQL）。

生产接入时由部署环境提供工单表并通过环境变量指向（Tool 代码不建表）。

---

## 7. Tests

新增 `tests/test_get_work_order_tool.py`（47 用例）：

* Definition（name / description / aliases 无裸词 / schema / 无注入面）
* 参数校验（正常 / 缺失 / unknown field / invalid type / SQL injection /
  Handler 层注入拒绝）
* Handler（Fake Engine，0 DB）：绑定参数 + READ ONLY + timeout + rollback /
  status 语义 / project A/B 轻量隔离（override + Fake provider）/
  engine 未配置 / 标识符白名单
* Registry（注册 / ToolResult 契约 / 双 Tool 默认 Registry）
* ToolExecutionService 共用（同一实例双 Tool / capability 拒绝）
* Orchestrator E2E（真实 Router：工单问题只调 work_order，库存问题只调
  inventory，RAG=0 / T2S=0）
* Multi-Tool Selection Regression（互不误选 + 聚合问题防抢占 +
  API 默认装配发现 get_work_order）
* Tool-to-Tool 隔离（属性 / 构造参数 / AST import / 无写 SQL 检查）

同步更新（既有断言随行为扩展）：

```text
test_get_inventory_tool.py：
    build_default_tool_registry → ["get_inventory", "get_work_order"]
    capability 元数据 → 双 Tool（loop 校验无 handler 泄露 / 无裸词）
```

结果：

* 新文件定向：**47 passed**
* Tool / Router / Orchestrator 定向：**336 passed, 19 skipped**
* 全量（未开 DB）：**2506 passed, 317 skipped, 0 failed**
* 全量（`RUN_DB_TESTS=1`）：**2782 passed, 41 skipped, 0 failed**
* compileall：OK；LSP diagnostics：0

---

## 8. Limitations（如实记录）

```text
database-level get_work_order      = NOT SUPPORTED（真实 DB 无该业务表）
LLM argument extraction            = NOT IMPLEMENTED
Generic NLP parser                 = NOT IMPLEMENTED
ToolChatService migration          = NOT IMPLEMENTED（仍走 Mock Registry）
Multi-step Tool Calling            = NOT IMPLEMENTED（Question → ONE Tool）
项目级默认能力白名单含 get_work_order = NO
（默认项目 capabilities 仍为 ("get_inventory",)；项目级启用需在
  服务器端能力白名单显式添加 "get_work_order"，工厂已支持）
Agent / LangGraph / MCP / Memory / Planning = NOT IMPLEMENTED
```
