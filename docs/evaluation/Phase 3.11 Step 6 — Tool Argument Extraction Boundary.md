# Phase 3.11 Step 6 — Tool Argument Extraction Boundary（Tool 参数提取边界）

> 目标：把 **Tool 参数提取** 从 `AIOrchestratorService` 内部独立出来，
> 形成唯一定义位置的提取边界，并保持现有行为**逐字不变**。
>
> 本 Step 只做「搬移 + 接线 + 测试 + 文档」，**不**新增任何 AI 能力。

---

## 1. Why extraction moved out of Orchestrator（为什么迁出）

迁移前（Phase 3.7.12 → 3.11 Step 5）：

```text
AIOrchestratorService._run_tool 同时承担 5 件事：
    1. Tool 选择读取（decision.tool_name）
    2. ToolDefinition 查找（取 declared properties）
    3. 参数提取（_extract_tool_arguments_from_question + 正则 + 字符区间）
    4. 执行边界调用（ToolExecutionService）
    5. 结果转换（ToolResult → content / metadata）
```

问题：

```text
* 单一模块同时持有「编排」与「自然语言 → 参数」两类职责 → 职责混合；
* 正则 / 字符区间 / material interval exclusion 无法被独立测试与复用；
* 第三个 Tool 出现时，参数提取会继续在 Orchestrator 内膨胀；
* 提取属于「候选值识别」，与「Schema 校验」（ToolRegistry）必须分离，
  但物理上却与编排逻辑绑在同一文件。
```

迁移后：

```text
Question
   ↓
Router（Tool Selection Authority）
   ↓
RouteDecision(tool_name)
   ↓
AIOrchestratorService._run_tool（只管编排）
   ↓
ToolArgumentExtractor.extract（Argument Extraction Boundary）
   ↓
arguments
   ↓
ToolExecutionService.execute（Execution Boundary）
   ↓
ToolRegistry（Schema Validation Authority）
   ↓
Tool（Business Implementation）
   ↓
ToolResult
```

结果：

```text
提取实现位置        = 1（backend/app/services/tool_argument_extractor.py）
Orchestrator 中提取实现 = 0（正则 / 区间 / 匹配函数全部删除）
提取逻辑重复        = 0（不存在第二份实现）
行为变化            = 0（断言全部沿用，仅 import 路径变化）
```

---

## 2. Supported Tools（本阶段支持范围）

```text
get_inventory      —— material_code（必填）+ warehouse_code（可选）
get_work_order     —— work_order_no（必填）
```

* **不**支持第三个 Tool：`_TOOL_FIELD_RULES` 只有上述两条真实规则，
  且由测试锁定（新增 Tool 时测试会失败以提醒显式扩展）；
* 未登记的 Tool 名称 → **不猜**、**不 fallback** 到上述两个 Tool；
* 不实现通用语义参数解析框架 / Schema-driven NLP / LLM Function Calling。

---

## 3. Supported arguments（支持参数与表达）

| Tool | Argument | 声明 | 现有确定性表达（逐字保持） |
|---|---|---|---|
| `get_inventory` | `material_code` | 必填 | question 中第一个**不落在**锚定上下文区间内的合法字面量（`[A-Za-z0-9][A-Za-z0-9._\-]{0,63}`） |
| `get_inventory` | `warehouse_code` | 可选 | `A01 仓库` / `A01仓库` / `仓库 A01` / `仓库A01` / `仓库: A01` / `仓库=A01` / `warehouse A01` / `warehouse_code=A01`（英文大小写不敏感） |
| `get_work_order` | `work_order_no` | 必填 | `工单 WO-001` / `工单号 WO-001` / `工单: WO-001` / `工单号=WO-001` / `work_order WO-001` / `work_order_no=WO-001` |

**本 Step 不新增任何自然语言表达**（无裸字面量工单、无新别名、无语义推断）：

```text
"查询工单 WO-001"        → {"work_order_no": "WO-001"}
"查询 WO-001"            → {}            （无工单上下文 → 不猜；Registry 拒绝）
"查询 A01 仓库库存"      → {"warehouse_code": "A01"}  （不伪造 material_code）
"查询 MAT-001 当前库存"  → {"material_code": "MAT-001"}
```

字段来源（优先级，代码中显式）：

```text
1) parameters（ToolDefinition.parameters，由 Orchestrator 注入）
       → 只提取其中声明且属于本模块支持集合的字段（声明门槛）
2) parameters 缺失（Tool 未注册 / 独立调用）
       → Tool 名称规则表 _TOOL_FIELD_RULES（仅 get_inventory / get_work_order）
3) 两者都不适用（未知 Tool）
       → {}（不猜 Tool、不 fallback）
```

---

## 4. Deterministic rules（确定性规则，100% 保持）

```text
① 锚定字段（先扫描，并记录字符区间）：
   warehouse_code ← _TOOL_WAREHOUSE_PATTERNS（3 条，顺序敏感）
   work_order_no  ← _TOOL_WORK_ORDER_PATTERNS（2 条）

② 字面量字段（后扫描，排除锚定区间）：
   material_code ← _TOOL_ARG_LITERAL_PATTERN 的第一个命中，
                   且 span 与任意锚定 span **不重叠**（_spans_overlap：半开区间）

③ 顺序敏感（Step 4 修复的行为，原样保留）：
   "A01 仓库 MAT-001"
       → [CODE] 仓库 必须先于 仓库 [CODE] 匹配 → warehouse=A01、material=MAT-001
   "warehouse A01 物料 10001 库存"
       → 模式词 "warehouse" 与 A01 都在锚定区间内 → material=10001
```

* 实现方式：字符串匹配 / 正则 / 字符区间计算，**无** NLP、**无** LLM；
* 模式字符串与 Phase 3.11 Step 4 / Step 5 完全一致（逐字符迁移，
  仅移动文件位置，未改写任何正则）；
* 未命中字段 → 该字段**不出现**在 arguments 中（不补空值 / 不补默认值）。

---

## 5. Schema validation boundary（Schema 校验仍属 ToolRegistry）

```text
ToolArgumentExtractor      = 从自然语言找候选值（不校验合法性）
ToolRegistry.validate_arguments = 唯一 Schema Authority
    ├── required（缺失必填 → ToolResult(success=False)）
    ├── type（string / integer / ...）
    └── unknown field（拒绝，不静默忽略）
Tool（Handler）            = 业务第二层校验（字符集 / 维度 / 业务规则）
```

Extractor **不**实现 required / type / additionalProperties / maxLength
（这些规则不存在于提取模块；提取模块只按"字段是否被声明"过滤，
不判断值是否合法）：

```text
"查询 A01 仓库库存"  → Extractor: {"warehouse_code": "A01"}
                     → Registry: 参数校验失败（missing material_code），Handler 0 次调用
```

---

## 6. Security boundary（安全边界）

```text
允许（AST 静态断言锁定）：re / typing / collections.abc / __future__
禁止：ToolRegistry / ToolExecutionService / Handler / DB / SQLAlchemy /
      Session / Engine / SQL / Router / LLM / HTTP / Socket / 文件系统 /
      subprocess / eval / exec / open / __import__
```

* 组件**零项目内 import**（无 `backend.app.*`），因此结构上不可能触达
  Registry / DB / LLM；
* 提取结果仅作为 Tool 入参传递，**不**进入 SQL 拼接（Tool 内使用绑定参数）；
* 无状态（`vars(extractor) == {}`）：无缓存 / 无重试 / 无副作用，可安全复用；
* 未知 Tool 不产生任何字段（不猜 Tool、不 fallback），避免"猜错 Tool 拿到数据"。

---

## 7. Dependency direction（单向依赖）

```text
Router
   ↓
AIOrchestrator
   ↓
ToolArgumentExtractor
   ↓
arguments
   ↓
ToolExecutionService
   ↓
ToolRegistry
   ↓
Tool
```

```text
AIOrchestrator → ToolArgumentExtractor        ✓（本 Step 建立的唯一方向）
ToolArgumentExtractor → AIOrchestrator        ✗（禁止，且无 import）
Tool → ToolArgumentExtractor → ToolRegistry   ✗（禁止：Tool 不接触 Registry/提取器）
```

Orchestrator 只做两件事：从 `RouteDecision` 取 `tool_name`、从 Registry
读取该 Tool 的**声明字段**（`ToolDefinition.parameters`），然后调用
`extractor.extract(tool_name, question, parameters=...)`；
Extractor 只接收普通 Mapping，不接收 Registry / Tool 对象。

---

## 8. Limitations（如实记录）

```text
* 仅 2 个真实 Tool 的字段规则（新增 Tool 必须显式扩展字段规则）；
* 仅确定性表达：裸字面量工单号（"查询 WO-001"）**不**提取（保持 Step 5 行为）；
* material_code 依赖"第一个不在锚定区间内的字面量"启发式：
  句子中出现多个候选字面量时只取第一个（既有行为，未改变）；
* warehouse_code 目前是**契约保留**：真实库存表无 warehouse 维度，
  Handler 收到该值即显式拒绝（Phase 3.11 Step 4 已确立，本 Step 未触碰）；
* 无 LLM 参数提取 / 无 Function Calling / 无通用 NLP（本 Step 明确不做）；
* `tool_execution_service.py` / `get_inventory.py` / `get_work_order.py`
  的 docstring 中仍有"参数提取属 Orchestrator / _extract_tool_arguments"
  的历史表述——这些文件按 Step 6 约束**零修改**，文案在后续阶段统一更新。
```

---

## 9. Boundary（职责分工，均已由测试锁定）

```text
Router                  = Tool Selection Authority（唯一选择来源）
ToolArgumentExtractor   = Candidate Extraction（自然语言 → 参数候选值；确定性）
ToolRegistry            = Schema Validation Authority（+ 唯一执行权威）
ToolExecutionService    = Execution Boundary（capability 校验 + 委派 Registry）
Tool（Handler）          = Business Implementation（只读业务查询）
AIOrchestrator          = Orchestration（decision → extract → execute；无提取实现）
```

零修改清单（SHA256 前后一致）：

```text
backend/app/services/tool_execution_service.py   unchanged
backend/app/services/ai_router_service.py        unchanged（Router / RouteDecision）
backend/app/services/tool_chat_service.py        unchanged
backend/app/tools/registry.py                    unchanged
backend/app/tools/get_inventory.py               unchanged
backend/app/tools/get_work_order.py              unchanged
```

---

## 10. Tests

```text
tests/test_tool_argument_extractor.py   新增（Extractor 单元 + AST 安全 + 迁移锁定）
tests/test_ai_orchestrator.py           新增 §L TestToolArgumentExtractionBoundary
                                        （Question → Router → Extractor →
                                          ToolExecutionService 各调用一次）
tests/test_tool_argument_contract.py    仅 import 改为 ToolArgumentExtractor
                                        （Step 4 断言逐条保留）
```

覆盖（directed）：

```text
get_inventory：MAT-001 / MAT-001 + A01 / A01 仓库 + MAT-001 /
               仓库 A01 + MAT-001 / warehouse A01 + MAT-001 /
               warehouse_code=A01 / 无空格连写 / 大小写
get_work_order：WO-001 / 工单 WO-001 / 查询工单号 WO-001 /
                work_order WO-001 / work_order_no=WO-001
Regression：锚定区间排除（A01 不进入 material_code；
            work_order span 排除；_spans_overlap 半开区间语义）
Missing："查询 A01 仓库库存" → {"warehouse_code": "A01"}（不补 material_code）
Unknown Tool：unknown_tool → {}（不 fallback get_inventory / get_work_order）
Declared gate：声明门槛与真实 ToolDefinition 一致（防漂移）；
               未声明字段不提取（get_work_order 不提取 warehouse_code）
Orchestrator：inventory / work order 端到端（各调用一次 + arguments 精确）；
              missing required → Registry 拒绝（Handler 0 次）；
              unknown tool → {} 透传；注入 Extractor 生效；非法注入被拒
Security：AST 断言（imports / identifiers / forbidden calls / 无 SQL 字符串）
```

结果：

```text
python -m pytest -q tests/test_tool_argument_extractor.py    67 passed
python -m pytest -q tests/test_ai_orchestrator.py            37 passed, 2 skipped(DB)
python -m pytest -q                                          2580 passed, 317 skipped
$env:RUN_DB_TESTS="1"; python -m pytest -q                   2856 passed, 41 skipped
python -m compileall -q backend                              clean
```

DB writes = 0；Network = 0（真实 PG 查询仅属既有 DB 集成测试，本 Step 未新增）。

---

## 11. Final Architecture

```text
Question
   ↓
Router                              （Tool Selection）
   ↓
RouteDecision(tool_name)
   ↓
AIOrchestrator
   ↓
ToolArgumentExtractor               （Argument Extraction）
   ↓
arguments
   ↓
ToolExecutionService                （Execution Boundary）
   ↓
ToolRegistry                        （Schema Validation）
   ↓
Tool                                （Business Implementation）
   ↓
ToolResult
```
