你现在继续实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.11 Step 4：Multi-Parameter Tool Argument Contract

## 一、目标

Phase 3.11 Step 3 已完成：

```text
Tool Selection Unification = IMPLEMENTED
```

当前真实链路：

```text
Question
    ↓
AIOrchestrator
    ↓
AIRouterService
    ↓
RouteDecision(tool_name)
    ↓
ToolExecutionService
    ↓
ToolRegistry
    ↓
Schema Validation
    ↓
Handler
```

当前唯一真实业务 Tool：

```text
get_inventory
```

但参数解析仍然是：

```text
Question
    ↓
正则
    ↓
material_code
```

只能支持单字段。

本 Step 的唯一目标：

**让 `get_inventory` 支持第二个真实业务参数 `warehouse_code`，并建立最小、确定性的多参数 Argument Contract。**

---

# 二、严格范围

允许：

```text
修改 AIOrchestrator 的参数提取边界
修改 get_inventory Tool 的参数 Schema（如果真实 Schema 当前确实只有 material_code）
修改 get_inventory Handler（仅支持 warehouse_code 参数传递）
新增 Argument Parser / Extractor 测试
新增 get_inventory 多参数测试
新增 evaluation 文档
更新 architecture.md
```

禁止：

```text
修改 AIRouter 核心分类算法
重新设计 RouteDecision
修改 ToolExecutionService 职责
修改 ToolRegistry 核心执行机制
修改 SQL Validator
修改 SQL Executor
修改 RAG
修改 Text-to-SQL
修改 ToolChatService
新增第二个业务 Tool
```

禁止：

```text
Agent
LangGraph
MCP
Memory
Planning
Multi-Agent
自主 Loop
```

---

# 三、第一步：先阅读真实实现

开始编码前必须阅读：

```text
backend/app/services/ai_orchestrator_service.py
backend/app/services/ai_router_service.py
backend/app/services/tool_execution_service.py
backend/app/tools/
```

重点找到：

```text
_extract_tool_arguments_from_question()
```

以及：

```text
get_inventory
```

真实 Tool Definition / Schema / Handler。

不要假设字段名称。

如果当前真实 Tool 已经存在：

```text
warehouse_code
```

则不要重复添加。

---

# 四、当前问题

Step 1 已经确认：

```text
_extract_tool_arguments_from_question()
```

当前只支持：

```text
material_code
```

例如：

```text
查询物料 MAT-001 当前库存
```

得到：

```json
{
  "material_code": "MAT-001"
}
```

本 Step 增加：

```text
warehouse_code
```

例如：

```text
查询仓库 A01 中物料 MAT-001 的库存
```

得到：

```json
{
  "material_code": "MAT-001",
  "warehouse_code": "A01"
}
```

---

# 五、不要使用 LLM 做参数提取

本 Step：

**禁止增加 LLM 参数解析。**

不要：

```text
Question
 ↓
LLM
 ↓
JSON Arguments
```

保持确定性：

```text
Question
 ↓
Deterministic Argument Extraction
 ↓
arguments
```

原因：

本 Step 的目标是验证 Tool Schema + Execution Boundary，而不是进入 Agent / Function Calling 自动参数生成。

---

# 六、不要做通用 NLP Parser

不要创建：

```text
UniversalArgumentParser
NaturalLanguageParser
SemanticArgumentResolver
AIParameterExtractor
ToolParameterAgent
```

本阶段只支持：

```text
get_inventory
```

需要的两个字段：

```text
material_code
warehouse_code
```

---

# 七、建议建立最小 Argument Extractor

如果当前 `_extract_tool_arguments_from_question()` 已经开始变复杂：

可以把：

```text
Question → arguments
```

抽成一个非常小的纯函数 / Service。

例如：

```text
backend/app/services/tool_argument_extractor.py
```

但：

**只有当现有 Orchestrator 内部逻辑已经明显超过一个简单函数时才创建。**

不要为了抽象而抽象。

---

# 八、Argument Contract

明确：

```text
get_inventory
```

支持：

```text
material_code: str
warehouse_code: str | None
```

其中：

```text
material_code
```

保持现有必填语义。

```text
warehouse_code
```

保持可选语义，如果真实 Tool 当前业务允许。

最终：

```json
{
  "material_code": "MAT-001",
  "warehouse_code": "A01"
}
```

以及：

```json
{
  "material_code": "MAT-001"
}
```

都必须经过：

```text
ToolRegistry.validate_arguments()
```

---

# 九、字段来源必须明确

对于：

```text
material_code
```

继续支持当前已经存在的提取方式。

对于：

```text
warehouse_code
```

只增加明确、可预测的表达。

例如：

```text
仓库 A01
A01 仓库
warehouse A01
warehouse_code=A01
```

具体支持哪些表达：

**根据当前项目语言 / 测试习惯选择最小集合。**

不要无限扩展自然语言表达。

---

# 十、避免误提取

增加测试：

```text
MAT-001
```

不能把：

```text
A01
```

误认为：

```text
material_code
```

同样：

```text
A01
```

不能因为出现：

```text
仓库
```

以外的上下文而被错误识别。

必须明确：

```text
material_code → material context
warehouse_code → warehouse context
```

---

# 十一、缺失参数

测试：

```text
查询仓库 A01 的库存
```

如果：

```text
material_code
```

是必填：

不能生成：

```json
{
  "warehouse_code": "A01"
}
```

然后假装成功。

应该进入当前项目已有的参数错误语义。

优先：

```text
ToolRegistry Schema Validation
```

而不是在 Argument Extractor 中复制一套 Schema Validation。

---

# 十二、未知参数

例如用户：

```text
查询 MAT-001 在 A01 仓库的库存，并按批次查询
```

如果当前 Tool Schema 没有：

```text
batch
```

不要生成：

```json
{
  "material_code": "MAT-001",
  "warehouse_code": "A01",
  "batch": "..."
}
```

必须保持：

```text
未知字段
    ↓
ToolRegistry
    ↓
reject
```

Argument Extractor 不应该偷偷扩展 Tool Schema。

---

# 十三、ToolExecutionService 不负责参数解析

保持：

```text
ToolExecutionService
```

当前职责：

```text
Capability
    ↓
Registry.execute
```

不要把：

```text
question → arguments
```

放进去。

也就是说：

```text
AIOrchestrator
    ↓
Question → arguments
    ↓
ToolExecutionService
    ↓
ToolRegistry
```

保持这个边界。

---

# 十四、ToolRegistry 仍然是 Schema Authority

不要复制：

```text
required
type
additionalProperties
```

到 Argument Extractor。

最终仍然：

```text
arguments
    ↓
ToolRegistry
    ↓
validate_arguments
```

这样未来修改 Tool Schema 时：

```text
Argument Extractor
```

不会成为第二套 Schema Validator。

---

# 十五、get_inventory Handler

如果当前 Handler 只接受：

```text
material_code
```

最小扩展为：

```text
material_code
warehouse_code
```

但必须确认真实 DB 查询逻辑。

如果真实数据库查询已经支持 warehouse：

复用现有查询条件。

如果数据库查询目前不支持 warehouse：

**不要为了本 Step 修改复杂 SQL。**

先报告：

```text
warehouse_code argument contract can be introduced,
but database-level warehouse filtering is not currently supported.
```

不要擅自扩展 SQL。

---

# 十六、数据库安全

如果 Handler 修改 SQL：

必须保持现有安全方式：

```text
Parameterized Query
READ ONLY transaction
statement_timeout
identifier whitelist
```

禁止：

```text
SQL string concatenation
dynamic SQL injection
INSERT
UPDATE
DELETE
DDL
```

---

# 十七、测试矩阵

新增 / 修改测试至少覆盖：

## 1. 单参数兼容

```text
查询 MAT-001 当前库存
```

Expected：

```json
{
  "material_code": "MAT-001"
}
```

---

## 2. 双参数

```text
查询 A01 仓库 MAT-001 的库存
```

Expected：

```json
{
  "material_code": "MAT-001",
  "warehouse_code": "A01"
}
```

---

## 3. 参数顺序变化

例如：

```text
查询 MAT-001 在 A01 仓库的库存
```

仍然：

```json
{
  "material_code": "MAT-001",
  "warehouse_code": "A01"
}
```

---

## 4. 缺失 material_code

确认不会绕过 Schema Validation。

---

## 5. 缺失 warehouse_code

如果 warehouse_code 可选：

必须成功。

如果真实 Schema 要求：

必须被 Registry 拒绝。

以真实 Schema 为准。

---

## 6. Unknown field

确认：

```text
batch
```

不会被偷偷传入 Tool。

---

## 7. Tool Schema Validation

最终必须经过：

```text
ToolRegistry.validate_arguments
```

---

## 8. ToolExecutionService

确认：

```text
arguments
```

原样透传。

---

## 9. Orchestrator E2E

完整：

```text
Question
 ↓
Router
 ↓
RouteDecision(tool_name=get_inventory)
 ↓
Argument Extraction
 ↓
ToolExecutionService
 ↓
ToolRegistry
 ↓
get_inventory
 ↓
ToolResult
```

---

# 十八、真实 DB 测试

如果现有：

```text
get_inventory
```

已经有真实 PostgreSQL 测试：

增加最少一个：

```text
material_code + warehouse_code
```

真实 DB 查询测试。

前提：

**测试数据库真实 schema 已存在 warehouse 字段 / 关联关系。**

如果不存在：

不要修改数据库结构。

不要创建新表。

不要修改生产数据。

---

# 十九、Router 不变

本 Step：

```text
ai_router_service.py
```

原则上：

```text
MODIFIED = NO
```

Tool Selection 已经在 Step 3 完成。

不要把参数解析塞进 Router。

---

# 二十、RouteDecision 不变

Step 3 已经完成：

```text
RouteDecision.tool_name
```

本 Step：

```text
MODIFIED = NO
```

---

# 二十一、ToolExecutionService 不变

本 Step：

```text
tool_execution_service.py
```

原则上：

```text
MODIFIED = NO
```

它不应该知道：

```text
question
material_code
warehouse_code
```

---

# 二十二、ToolChatService 不变

明确：

```text
ToolChatService = NOT MODIFIED
```

不要趁这个机会统一 Function Calling。

---

# 二十三、不要新增第二个 Tool

本 Step：

```text
New Tool = 0
```

只增强：

```text
get_inventory
```

---

# 二十四、文档

新增：

```text
docs/evaluation/Phase 3.11 Step 4 — Multi-Parameter Tool Argument Contract.md
```

记录：

```text
1. Current Problem
2. Argument Contract
3. Supported Expressions
4. Extraction Rules
5. Validation Boundary
6. get_inventory
7. ToolExecutionService Boundary
8. Registry Boundary
9. Security
10. Tests
11. DB Integration
12. Known Limitations
```

更新：

```text
docs/architecture.md
```

增加：

```text
Question
    ↓
AI Router
    ↓
RouteDecision(tool_name)
    ↓
AIOrchestrator
    ↓
Argument Extraction
    ↓
ToolExecutionService
    ↓
ToolRegistry
    ↓
Schema Validation
    ↓
get_inventory
```

明确：

```text
LLM Argument Extraction = NOT IMPLEMENTED
Generic NLP Argument Parsing = NOT IMPLEMENTED
Agent = NOT IMPLEMENTED
```

---

# 二十五、测试命令

先：

```powershell
python -m pytest -q tests/test_ai_orchestrator.py tests/test_get_inventory_tool.py
```

然后：

```powershell
python -m pytest -q tests/test_tool_execution_service.py tests/test_ai_router.py
```

然后：

```powershell
python -m pytest -q
```

如果 DB 测试受到影响：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

---

# 二十六、质量检查

执行：

```powershell
python -m compileall backend tests
```

以及现有 LSP / lint。

要求：

```text
0 failed
0 diagnostics
```

---

# 二十七、最终报告

严格输出：

```text
【Phase 3.11 Step 4 COMPLETE】

1. 新增文件
2. 修改文件
3. Argument Contract
4. Argument Extraction
5. get_inventory
6. ToolExecutionService
7. ToolRegistry
8. Router
9. RouteDecision
10. ToolChatService
11. Security
12. Tests
13. DB Tests
14. Full Regression
15. compile / lint
16. DB writes
17. Network
18. 发现的问题
19. 当前限制
```

最终明确：

```text
Tool Selection Unification       = IMPLEMENTED
Tool Execution Boundary          = IMPLEMENTED
Multi-Parameter Tool             = IMPLEMENTED
Deterministic Argument Extraction = IMPLEMENTED
LLM Argument Extraction          = NOT IMPLEMENTED
Generic NLP Parser               = NOT IMPLEMENTED
ToolChatService Migration        = NOT IMPLEMENTED
Agent                            = NOT IMPLEMENTED
MCP                              = NOT IMPLEMENTED
Multi-step Orchestration         = NOT IMPLEMENTED
```

**Step 4 完成后立即停止。**

不要进入 Step 5。

不要新增第二个 Tool。

不要引入 LLM 参数解析。

不要修改 ToolChatService。

等待下一步指令。
