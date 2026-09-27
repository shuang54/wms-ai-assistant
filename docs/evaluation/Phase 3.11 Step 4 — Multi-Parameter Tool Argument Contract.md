# Phase 3.11 Step 4 — Multi-Parameter Tool Argument Contract

> 目标：让 `get_inventory` 支持第二个真实业务参数 `warehouse_code`，
> 建立**最小、确定性**的多参数 Argument Contract。
> 不引入 LLM 参数解析；不做通用 NLP Parser；不新增第二个 Tool。

---

## 1. Current Problem（Step 3 之后）

```text
Question → 正则 → material_code        （只支持单字段）
```

* `get_inventory` Schema 只有 `material_code`（必填、单参数）；
* 提取器只识别第一个合法字面量（`[A-Za-z0-9][A-Za-z0-9._\-]{0,63}`）；
* 无法表达"某仓库中的某物料"这类双参数查询。

---

## 2. Argument Contract

```text
get_inventory
├── material_code: str          （必填；语义不变）
└── warehouse_code: str | None  （可选；Phase 3.11 Step 4 新增）
```

合法输入示例（均经过 Registry Schema 校验）：

```json
{"material_code": "MAT-001"}
{"material_code": "MAT-001", "warehouse_code": "A01"}
```

字段级规则（与既有安全约束一致）：

* `warehouse_code` 字符集与长度规则**复用 material_code 白名单**
  （`[A-Za-z0-9][A-Za-z0-9._\-]{0,63}`；自动 strip；空串 / 纯空白 /
  非字符串 / 超长 → `ToolValidationError`，`field_path="warehouse_code"`）；
* Schema 仍拒绝 SQL / table / where_clause 等注入面字段（既有测试锁定）。

---

## 3. Supported Expressions（最小确定性集合）

`warehouse_code` 仅识别**显式仓库上下文**（无上下文绝不猜测）：

| 表达 | 示例 |
|---|---|
| `[CODE] 仓库` | "查询 A01 仓库 MAT-001 的库存" |
| `仓库 [CODE]` | "查询仓库 A01 中物料 MAT-001 的库存" |
| `仓库: [CODE]` / `仓库=[CODE]` | "查询仓库: A01 的物料 MAT-001 库存" |
| `warehouse [CODE]` | "warehouse A01 物料 10001 库存"（大小写不敏感） |
| `warehouse_code=[CODE]` | "warehouse_code=A01 中物料 10001 库存" |

`material_code` 保持既有语义：question 中第一个**不落在仓库表达
区间内**的合法字面量。

---

## 4. Extraction Rules

```text
1. warehouse_code：按模式顺序（[CODE] 仓库 → 仓库 [CODE] →
   warehouse[_code] [CODE]）取第一个命中，记录其字符区间；
2. material_code：字面量逐个扫描，跳过落入仓库区间的候选，
   取第一个剩余的合法字面量；
3. 无命中 → 字段不出现在 arguments 中（不补默认值、不猜测）。
```

模式顺序敏感的原因（误提取防护）：

```text
"A01 仓库 MAT-001 的库存"
    [CODE] 仓库 模式先命中 "A01 仓库" → warehouse=A01 ✓
    （若 仓库 [CODE] 先跑，会把 "仓库 MAT-001" 的 MAT-001 当仓库编码 ✗）
```

防护结论（测试锁定）：

```text
"查询 A01 仓库 MAT-001 的库存" → material = MAT-001（不是 A01）
"查询 A01 当前库存"            → material = A01（无仓库上下文，保持既有语义）
"查询物料 10001 当前库存 A01"   → warehouse 不提取（"库存" ≠ "仓库"）
```

---

## 5. Validation Boundary

**Schema 权威仍完全属于 `ToolRegistry.validate_arguments`**：

```text
提取器（Orchestrator）           Registry（唯一 Schema 权威）
────────────────────             ─────────────────────────────
Question → arguments              required / 类型 / 未知字段拒绝
（不复制 required / 类型规则）      → ToolResult(success=False)
```

* "查询仓库 A01 的库存" → 提取 `{"warehouse_code": "A01"}`（material 缺失）
  → **Registry 拒绝**（missing required field），Handler 0 次调用
  —— 绝不假装成功；
* 未知字段（如 batch）：提取器**只生成 Schema 声明字段**，不偷偷扩展
  Tool Schema；若上游硬塞未知字段，Registry 仍会拒绝（测试锁定）。

---

## 6. get_inventory（真实 Tool 变更）

```text
GET_INVENTORY_DEFINITION
    parameters.properties += warehouse_code（string，可选）
    required 仍为 ["material_code"]
    description 更新（说明 warehouse 维度限制）

GetInventoryHandler.__call__
    1.1  material_code 校验（不变）
    1.1b warehouse_code 校验（可选；非法 → ToolValidationError）
    1.2  warehouse 维度 guard：
         非 None → ToolExecutionError(
             "库存表未提供 warehouse 维度，warehouse_code 过滤当前不受支持
              （显式拒绝而非静默忽略参数）")
         —— 位于 Engine 解析 / SQL 执行**之前**（0 DB、0 SQL 变更）
    2+   Engine 解析 → 只读查询（SQL 未修改）
```

**为什么不静默忽略参数**：忽略后按全仓汇总返回会给出**错误的业务答案**
（用户问的是特定仓库的量）。显式拒绝是唯一的诚实行为；未来库存表提供
warehouse 维度时，把 `warehouse_code` 作为可选绑定参数传入
`_run_inventory_query` 即可（独立阶段）。

---

## 7. ToolExecutionService Boundary

**零修改**（Step 2 边界保持）：

```text
tool_name + arguments（多参数字典）
    ↓ capability
    ↓
ToolRegistry.execute → validate_arguments → Handler
arguments 原样透传（测试锁定：双参数字典到达 Handler 与提取结果一致）
```

它仍然**不知道** question / material_code / warehouse_code 的语义。

---

## 8. Registry Boundary

**零修改**：`validate_arguments` / Handler 调用 / 异常归一化全部不变；
本 Step 未复制任何 Schema 规则到提取器（单一权威）。

---

## 9. Security

```text
SQL 未修改（仍为绑定参数 SELECT；schema/table 白名单校验不变）
提取器：0 SQL / 0 DB / 0 LLM / 0 HTTP（纯字符串规则匹配）
warehouse_code 值：不进入 SQL（guard 先拒绝）；同名白名单字符集校验
Registry 未知字段拒绝：不变（batch 等注入面不扩大）
SQL injection 防护回归：TestRealDatabaseInventory 既有注入用例全部通过
```

---

## 10. Tests

### 新增 `tests/test_tool_argument_contract.py`（26 用例）

单参数兼容（3）/ 双参数表达（7 + span）/ 缺失 material → Registry 拒绝
（Handler 0 次）/ 缺失 warehouse → 可选通过 / batch 不生成 + Registry
拒绝未知字段 / Schema 权威（definition 声明 + 无注入面）/
ExecutionService 双参数透传 / Orchestrator E2E（真实 Router，双参数与
单参数各一，+ 真实 Handler guard 经 Registry 归一）/
误提取防护（4）。

### 扩展 `tests/test_get_inventory_tool.py`（13 用例）

definition 双参数声明 / `_validate_warehouse_code`（None→None、strip、
空串 / 非法字符 / 超长 / 非字符串拒绝）/ Handler guard（ToolExecutionError，
0 DB）/ 非法 warehouse → `ToolValidationError(field_path="warehouse_code")`。

### 结果

* 定向（契约 + orchestrator + get_inventory）：**117 passed, 19 skipped**
* 第二组（execution_service / router / capabilities / e2e / tool_chat）：
  **154 passed, 5 skipped**
* 全量（未开 DB）：**2459 passed, 317 skipped, 0 failed**
* 全量（`RUN_DB_TESTS=1`）：**2735 passed, 41 skipped, 0 failed**
* compileall：OK；LSP diagnostics：0

真实提取行为（工具链实测）：

```text
"查询 MAT-001 当前库存"                → {"material_code": "MAT-001"}
"查询 A01 仓库 MAT-001 的库存"          → {"warehouse_code": "A01", "material_code": "MAT-001"}
"查询 MAT-001 在 A01 仓库的库存"        → {"warehouse_code": "A01", "material_code": "MAT-001"}
"查询仓库 A01 的库存"                  → {"warehouse_code": "A01"}（material 缺失 → Registry 拒绝）
"查询 MAT-001 在 A01 仓库的库存，并按批次查询" → 两字段（不含 batch）
```

---

## 11. DB Integration

**database-level warehouse filtering is not currently supported**（本 Step
如实报告，未擅自扩展 SQL）：

* 配置指向 `public.inventory` —— 当前真实 DB 中**不存在**任何 `inventory`
  表（information_schema 实测为空）；
* 测试隔离表 `inventory_tool_test.inventory`（既有 DB 测试创建）只有
  `material_code` + `qty` 两列，**无 warehouse 列**；
* 按 Step 4 约束：不修改数据库结构、不创建新表、不修改 fixture DDL、
  不扩展 SQL → **未新增真实 DB 双参数查询测试**；
* warehouse guard 位于 DB 访问之前，因此"携带 warehouse_code → 拒绝"
  可在 0 DB 条件下完整验证（已在契约测试与 get_inventory 测试覆盖）；
* 既有真实 DB 测试（单参数查询 / 注入防护 / 写入保护）全部通过。

---

## 12. Known Limitations（如实记录，本 Step 未处理）

```text
Database-level warehouse filtering = NOT SUPPORTED
（库存表无 warehouse 维度；guard 显式拒绝；未来表提供该列时再接入）
LLM Argument Extraction            = NOT IMPLEMENTED（保持确定性规则）
Generic NLP Argument Parsing       = NOT IMPLEMENTED（仅最小表达集合）
warehouse 多值 / 区间表达             = NOT IMPLEMENTED（取第一个命中）
其它 Tool 的参数扩展                 = NOT IMPLEMENTED（无第二个业务 Tool）
ToolChatService Migration          = NOT IMPLEMENTED
Agent / LangGraph / MCP / Memory / Planning / Multi-step = NOT IMPLEMENTED
```
