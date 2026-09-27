你现在开始实现：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.11 Step 6：Tool Argument Extraction Boundary

## 一、阶段目标

Phase 3.11 Step 1～5 已经完成：

```text
Tool Selection
    ↓
RouteDecision.tool_name
    ↓
Tool Execution Boundary
    ↓
Multi-Parameter Argument Contract
    ↓
Multiple Real Tools
```

当前架构中已经存在：

```text
Router
    ↓
tool_name
    ↓
AIOrchestrator
    ↓
Tool Argument Extraction
    ↓
ToolExecutionService
```

但目前：

**Tool Argument Extraction 仍然直接写在 `AIOrchestratorService` 内部。**

本阶段只解决一个问题：

> 将 Tool 参数提取职责从 Orchestrator 中独立出来，同时保持现有行为完全不变。

目标：

```text
Question
    ↓
Router
    ↓
RouteDecision(tool_name)
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

---

# 二、严格范围

## 允许

新增：

```text
ToolArgumentExtractor
```

以及：

```text
ToolArgumentExtractionResult
```

如现有架构确实需要，可以增加极少量内部 DTO。

允许：

* 将现有确定性参数提取逻辑迁移到新组件
* 新增 Extractor 单元测试
* 新增 Orchestrator 集成测试
* 更新 architecture 文档
* 更新 evaluation 文档
* 删除 Orchestrator 中已经迁移的重复代码

---

## 禁止

不要修改：

```text
AI Router
RouteDecision
ToolExecutionService
ToolRegistry
get_inventory
get_work_order
ToolChatService
RAG
Text-to-SQL
SQL Validator
SQL Executor
```

不要新增：

```text
LLM Argument Extraction
Generic NLP Parser
Agent
LangGraph
MCP
Memory
Planning
Multi-step Tool Calling
```

不要改变：

```text
Tool Selection Contract
Tool Execution Contract
Tool Schema Contract
```

---

# 三、第一步：先阅读真实代码

不要假设当前实现。

重点阅读：

```text
backend/app/services/ai_orchestrator_service.py
backend/app/services/tool_execution_service.py
backend/app/tools/registry.py
backend/app/tools/get_inventory.py
backend/app/tools/get_work_order.py
tests/test_ai_orchestrator.py
tests/test_tool_argument_contract.py
```

找到当前：

```text
material_code extraction
warehouse_code extraction
work_order_no extraction
spans_overlap
pattern definitions
```

完整理解现有行为后再移动。

---

# 四、新组件位置

优先：

```text
backend/app/services/tool_argument_extractor.py
```

如果当前项目存在更合理的 `tools/` 或 domain/service 结构，可以遵循现有项目结构。

不要为了这个组件创建新的复杂目录体系。

---

# 五、ToolArgumentExtractor 职责

新组件只负责：

```text
Question
+
tool_name
    ↓
arguments
```

例如：

```python
extractor.extract(
    tool_name="get_inventory",
    question="查询物料 MAT-001 在 A01 仓库的库存"
)
```

返回：

```python
{
    "material_code": "MAT-001",
    "warehouse_code": "A01",
}
```

例如：

```python
extractor.extract(
    tool_name="get_work_order",
    question="查询工单 WO-202609-001"
)
```

返回：

```python
{
    "work_order_no": "WO-202609-001"
}
```

---

# 六、职责边界

## ToolArgumentExtractor 可以：

```text
字符串匹配
正则表达式
字符区间计算
Tool-specific deterministic patterns
```

## ToolArgumentExtractor 不可以：

```text
ToolRegistry
ToolExecutionService
Database
SQLAlchemy
Session
Router
LLM
HTTP
Filesystem
```

尤其不能：

```python
registry.execute(...)
```

或者：

```python
db.execute(...)
```

---

# 七、非常重要：不要设计成 Generic NLP Parser

不要实现：

```text
parse_anything(question)
```

不要尝试自动理解所有自然语言。

当前阶段只支持：

```text
get_inventory
get_work_order
```

可以采用：

```python
extract(tool_name, question)
```

然后：

```text
get_inventory
    ↓
material_code
warehouse_code

get_work_order
    ↓
work_order_no
```

未来增加第三个 Tool 时再扩展。

不要提前实现：

```text
通用语义参数解析框架
Schema-driven NLP
LLM Function Calling
```

---

# 八、保持现有规则 100% 不变

这是本阶段最重要的要求。

从 Orchestrator 迁移到 Extractor：

### get_inventory

必须保持：

```text
material_code
warehouse_code
```

现有所有表达形式继续工作：

```text
MAT-001
A01 仓库
仓库 A01
仓库: A01
warehouse A01
warehouse_code=A01
```

以及当前：

```text
spans_overlap
```

和：

```text
material interval exclusion
```

逻辑。

### get_work_order

必须保持：

```text
WO-202609-001
```

当前支持的工单表达。

不要在 Step 6 顺便增加新的自然语言表达。

---

# 九、Unknown Tool

如果：

```python
extract(
    tool_name="unknown_tool",
    question="..."
)
```

必须有明确行为。

优先：

```text
返回 {}
```

或者当前架构已经定义的明确异常。

不要猜测 Tool。

不要 fallback 到：

```text
get_inventory
get_work_order
```

---

# 十、Missing Arguments

Extractor 不负责 Schema Validation。

例如：

```text
查询 A01 仓库库存
```

Extractor 可以返回：

```python
{
    "warehouse_code": "A01"
}
```

而不是在 Extractor 中复制：

```text
material_code required
```

随后：

```text
ToolExecutionService
    ↓
ToolRegistry
    ↓
Schema Validation
    ↓
missing required material_code
```

继续保持：

**ToolRegistry 是唯一 Schema Authority。**

同理：

```text
查询工单
```

Extractor 可以返回：

```python
{}
```

然后由 Registry 判断：

```text
work_order_no required
```

---

# 十一、不得复制 Schema Validation

不要在 Extractor 中实现：

```text
required
type
additionalProperties
maxLength
```

除非这些规则本来就是为了**识别候选字符串**而存在。

职责必须保持：

```text
Extractor
    ↓
从自然语言找候选值

Registry
    ↓
验证参数是否合法

Handler
    ↓
业务级第二层校验
```

---

# 十二、Orchestrator 修改

完成 Extractor 后：

`AIOrchestratorService` 应该变成：

```text
RouteDecision
    ↓
tool_name
    ↓
ToolArgumentExtractor
    ↓
arguments
    ↓
ToolExecutionService
```

删除 Orchestrator 内部已经迁移的：

```text
regex patterns
_match_warehouse_code
_match_work_order_no
_spans_overlap
material extraction implementation
```

具体删除哪些：

**以真实代码为准。**

不要误删其他非 Tool 逻辑。

---

# 十三、依赖方向

最终必须：

```text
AIOrchestrator
    ↓
ToolArgumentExtractor
```

而不是：

```text
ToolArgumentExtractor
    ↓
AIOrchestrator
```

更不能：

```text
Tool
    ↓
ToolArgumentExtractor
    ↓
ToolRegistry
```

保持：

```text
Router
    ↓
Orchestrator
    ↓
ArgumentExtractor
    ↓
ExecutionService
    ↓
Registry
    ↓
Tool
```

单向依赖。

---

# 十四、是否需要 DTO

优先保持简单。

如果现有代码：

```python
dict[str, object]
```

已经足够：

**不要为了形式而新增 DTO。**

只有当当前接口明显需要一个不可变、稳定的 extraction result 时才新增 DTO。

如果新增：

```python
@dataclass(frozen=True)
class ToolArgumentExtractionResult:
    tool_name: str
    arguments: Mapping[str, object]
```

必须保持：

```text
immutable
```

并且不要让它携带：

```text
ToolResult
DB Session
Router state
LLM response
```

Python 官方文档说明 `frozen=True` 可以模拟 dataclass 的只读实例语义，因此如果当前项目确实采用 frozen DTO，可以继续遵循这一既有模式。

---

# 十五、测试

新增：

```text
tests/test_tool_argument_extractor.py
```

至少覆盖：

## get_inventory

```text
MAT-001
MAT-001 + A01
A01 仓库 + MAT-001
仓库 A01 + MAT-001
warehouse A01 + MAT-001
warehouse_code=A01
```

验证：

```text
arguments 完全正确
```

---

## get_work_order

```text
WO-001
工单 WO-001
查询工单号 WO-001
work_order WO-001
work_order_no=WO-001
```

以当前 Step 5 实际支持表达为准。

---

## Regression

验证：

```text
warehouse expression
    ↓
不会被 material_code extraction 误识别
```

继续覆盖 Step 4 已经修复的：

```text
A01 仓库 MAT-001
```

---

## Missing

```text
查询 A01 仓库库存
```

结果：

```python
{
    "warehouse_code": "A01"
}
```

不得自动添加：

```text
material_code
```

---

## Unknown Tool

验证：

```text
unknown_tool
```

不会：

```text
get_inventory
get_work_order
```

---

## Security

Extractor 不应该：

```text
SQL
DB
HTTP
LLM
filesystem
```

可以增加 AST/import 静态测试确认。

---

# 十六、Orchestrator Regression

原来的：

```text
tests/test_ai_orchestrator.py
```

全部继续通过。

至少增加：

### Inventory

```text
Question
 ↓
Router
 ↓
get_inventory
 ↓
Extractor
 ↓
ToolExecutionService
```

### Work Order

```text
Question
 ↓
Router
 ↓
get_work_order
 ↓
Extractor
 ↓
ToolExecutionService
```

验证：

```text
route == TOOL
tool_name 正确
arguments 正确
ToolExecutionService 调用一次
```

---

# 十七、ToolExecutionService 必须零修改

完成后检查：

```text
git diff
```

确认：

```text
backend/app/services/tool_execution_service.py
```

没有变化。

同样：

```text
ToolRegistry
Router
RouteDecision
```

原则上零变化。

如果出现必须修改的情况：

**先停止并报告，不要为了测试通过而扩大边界。**

---

# 十八、ToolChatService 必须零修改

明确要求：

```text
backend/app/services/tool_chat_service.py
```

不要修改。

Phase 3.11 后续再单独处理。

---

# 十九、文档

新增：

```text
docs/evaluation/Phase 3.11 Step 6 — Tool Argument Extraction Boundary.md
```

记录：

```text
1. Why extraction moved out of Orchestrator
2. Supported Tools
3. Supported arguments
4. Deterministic rules
5. Schema validation boundary
6. Security boundary
7. Dependency direction
8. Limitations
```

最终架构：

```text
Question
   ↓
Router
   ↓
RouteDecision(tool_name)
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
Schema Validation
   ↓
Tool
   ↓
ToolResult
```

---

# 二十、Architecture

更新：

```text
docs/architecture.md
```

增加：

```text
§8.24 Tool Argument Extraction Boundary
```

明确：

```text
Router
= Tool Selection Authority

ToolArgumentExtractor
= Natural-language-to-argument candidate extraction

ToolRegistry
= Schema Validation Authority

ToolExecutionService
= Execution Boundary

Tool
= Business Implementation
```

---

# 二十一、测试命令

Windows PowerShell：

```powershell
python -m pytest -q tests/test_tool_argument_extractor.py
```

然后：

```powershell
python -m pytest -q tests/test_ai_orchestrator.py
```

再：

```powershell
python -m pytest -q
```

如果 DB 测试需要：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

最后：

```powershell
python -m compileall backend
```

并检查：

```text
LSP diagnostics
lint
```

---

# 二十二、完成标准

必须满足：

```text
Tool Selection      = Router
Argument Extraction = ToolArgumentExtractor
Schema Validation   = ToolRegistry
Execution           = ToolExecutionService
Business Logic      = Tool
```

并且：

```text
Router              unchanged
RouteDecision       unchanged
ToolExecutionService unchanged
ToolRegistry        unchanged
ToolChatService     unchanged
```

现有：

```text
get_inventory
get_work_order
```

行为必须完全回归通过。

---

# 二十三、禁止借机扩展

本 Step 完成后：

不要：

```text
迁移 ToolChatService
增加第三个 Tool
增加 LLM 参数解析
增加 Function Calling
增加 Agent
增加 MCP
增加 LangGraph
增加 Memory
增加 Planning
增加 Multi-step Tool Calling
```

---

# 二十四、最终报告

完成后严格按照：

```text
【Phase 3.11 Step 6 COMPLETE】

1. 新增文件

2. 修改文件

3. ToolArgumentExtractor
- 支持 Tool：
- 支持参数：
- 是否纯确定性：

4. Orchestrator
- 是否移除 Tool 参数提取逻辑：
- 是否保持行为一致：

5. Boundary
- Router：
- ArgumentExtractor：
- ToolRegistry：
- ToolExecutionService：
- Tool：

6. Regression
- get_inventory：
- get_work_order：
- missing argument：
- unknown field：

7. Tests
- directed：
- full no DB：
- full DB：

8. compile / LSP / lint

9. DB writes
- result：

10. Network
- result：

11. 发现的问题

12. 当前限制

13. 最终架构

最后：

**Step 6 到此停止。**

不要进入 Step 7。
不要迁移 ToolChatService。
不要增加第三个 Tool。
不要引入 LLM Argument Extraction。
不要引入 Agent / MCP / LangGraph。
不要实现 Multi-step Tool Calling。
```

**完成后立即停止，等待下一步指令。**
