你现在开始实现：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.11 Step 7：Tool Boundary Contract Cleanup & Architecture Lock

## 一、阶段目标

Phase 3.11 Step 1～6 已经完成：

```text
Tool Selection
      ↓
Tool Execution Boundary
      ↓
Multi-Parameter Contract
      ↓
Multiple Real Tools
      ↓
Tool Argument Extraction Boundary
```

当前已经形成：

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
ToolExecutionService
   ↓
ToolRegistry
   ↓
Tool
   ↓
ToolResult
```

Step 6 发现了一个真实但非功能性问题：

```text
部分历史 docstring / 注释
仍然引用已经删除的
_extract_tool_arguments_from_question
```

本阶段只做：

> **架构契约清理 + 静态边界锁定。**

不增加任何业务能力。

---

# 二、严格范围

## 允许

* 修正已经失效的 docstring
* 修正已经失效的注释
* 更新 architecture 文档
* 更新 evaluation 文档
* 新增静态边界测试
* 新增少量 contract regression tests
* 清理已经不存在的函数名引用
* 锁定当前 Tool 架构边界

## 禁止

不要修改：

```text
AI Router
RouteDecision
AIOrchestrator 行为
ToolArgumentExtractor 行为
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

不要：

```text
新增 Tool
新增参数
修改 Tool Schema
修改数据库
修改 SQL
修改 Prompt
增加 LLM Argument Extraction
增加 Function Calling
增加 Generic NLP Parser
增加 Agent
增加 MCP
增加 LangGraph
增加 Memory
增加 Planning
增加 Multi-step Tool Calling
```

---

# 三、Step 1：搜索所有历史引用

首先全项目搜索：

```text
_extract_tool_arguments_from_question
```

以及：

```text
_match_warehouse_code
_match_work_order_no
_spans_overlap
_TOOL_ARG_LITERAL_PATTERN
_TOOL_WAREHOUSE_PATTERNS
_TOOL_WORK_ORDER_PATTERNS
```

这些已经从 Orchestrator 删除的实现，不应该继续作为当前架构说明存在。

重点检查：

```text
backend/
tests/
docs/
```

---

# 四、只清理“过时说明”

如果发现：

```text
docstring
comment
architecture documentation
evaluation documentation
test comment
```

引用旧实现：

修改为当前架构。

例如旧：

```text
Orchestrator
  ↓
_extract_tool_arguments_from_question
```

改成：

```text
Orchestrator
  ↓
ToolArgumentExtractor
```

---

# 五、不要修改测试行为

特别注意：

如果测试中的旧函数名只是：

```text
历史测试名称
```

或者：

```text
测试说明
```

可以更新名称/注释。

但是：

**不要为了“清理”而改变测试逻辑。**

所有既有测试必须继续通过。

---

# 六、建立 Tool Architecture Contract Tests

新增：

```text
tests/test_tool_architecture_contract.py
```

这是本 Step 的核心。

不要测试业务结果。

测试：

**架构边界。**

---

# 七、Contract 1：Router 不执行 Tool

验证：

```text
AI Router
```

不能依赖：

```text
ToolRegistry
ToolExecutionService
Database
SQLAlchemy
Session
```

可以使用：

```text
AST import inspection
```

或者当前项目已有的静态检查方式。

目标：

```text
Router = Selection Only
```

---

# 八、Contract 2：ArgumentExtractor 不执行 Tool

验证：

```text
ToolArgumentExtractor
```

不能 import：

```text
ToolRegistry
ToolExecutionService
AIOrchestrator
SQLAlchemy
Database
Session
HTTP client
LLM client
```

目标：

```text
ArgumentExtractor = Pure Extraction
```

已经存在的：

```text
re
typing
dataclasses
```

等基础依赖可以正常使用。

---

# 九、Contract 3：ToolExecutionService 不解析业务参数

验证：

```text
ToolExecutionService
```

不包含：

```text
material_code
warehouse_code
work_order_no
```

等业务参数提取逻辑。

也不能 import：

```text
AI Router
ToolArgumentExtractor
RAG
Text-to-SQL
```

目标：

```text
ToolExecutionService
=
Capability Check
+
Registry Delegation
```

不要把它变成新的 Orchestrator。

---

# 十、Contract 4：Tool 不调用上层

验证：

```text
get_inventory
get_work_order
```

不能依赖：

```text
AIOrchestrator
AI Router
ToolArgumentExtractor
ToolExecutionService
```

目标：

```text
Tool
=
Business Implementation
```

Tool 只能接收：

```text
arguments
project context
existing DB abstraction
```

以及当前项目已经允许的底层依赖。

不要扩大依赖范围。

---

# 十一、Contract 5：Tool 之间不能互调

继续锁定 Step 5 的结果：

```text
get_inventory
    X→ get_work_order

get_work_order
    X→ get_inventory
```

静态检查：

```text
Tool A import Tool B
Tool B import Tool A
```

都必须不存在。

同时检查实例属性 / 构造参数是否携带：

```text
ToolRegistry
ToolExecutionService
AIOrchestrator
```

如果当前 Step 5 已经有相关测试：

**优先复用，不要重复实现两套完全相同的测试。**

---

# 十二、Contract 6：Orchestrator 只负责编排

验证：

```text
AIOrchestrator
```

可以依赖：

```text
Router
ToolArgumentExtractor
ToolExecutionService
```

但不应该直接依赖：

```text
Tool handler
Database
SQLAlchemy Session
ToolRegistry.execute
```

注意：

如果当前 Orchestrator 构造函数中存在：

```text
ToolRegistry
```

但只是为了构造：

```text
ToolExecutionService
```

必须根据真实代码判断。

不要为了测试强行改变已有设计。

本测试重点是：

**Orchestrator 不绕过 ToolExecutionService 直接执行 Tool。**

---

# 十三、Contract 7：Registry 是 Schema Authority

增加测试：

```text
ToolRegistry
```

仍然是唯一参数 Schema Validation 入口。

至少验证：

```text
missing required
unknown field
invalid type
```

继续保证：

```text
Extractor
    ↓
candidate arguments

Registry
    ↓
schema validation
```

不要把：

```text
required
additionalProperties
type
```

复制到 Extractor。

---

# 十四、Contract 8：ToolExecutionService 原样透传

锁定 Step 4：

```text
arguments
```

经过：

```text
ToolExecutionService
```

必须：

```text
unchanged
```

例如：

```python
{
    "material_code": "MAT-001",
    "warehouse_code": "A01"
}
```

进入 Tool 时仍然是：

```python
{
    "material_code": "MAT-001",
    "warehouse_code": "A01"
}
```

ToolExecutionService 不得：

```text
rename
filter
fill default
parse
normalize
```

---

# 十五、Contract 9：Tool Selection 唯一来源

继续锁定 Step 3：

```text
RouteDecision.tool_name
```

是 Tool Selection 唯一来源。

测试：

```text
Router
  ↓
RouteDecision(tool_name)
  ↓
Orchestrator
```

Orchestrator 不得重新：

```text
猜 Tool
tokenize question
根据关键词重新选择 Tool
fallback 到另一个 Tool
```

特别检查：

```text
get_inventory
get_work_order
```

都只能由：

```text
RouteDecision.tool_name
```

决定。

---

# 十六、Contract 10：Unknown Tool 不 fallback

如果：

```python
RouteDecision(
    route=TOOL,
    tool_name=None
)
```

必须保持当前既有错误语义。

不能：

```text
guess
get_inventory
get_work_order
RAG fallback
Text-to-SQL fallback
```

不要修改现有异常类型。

只增加 regression test。

---

# 十七、Contract 11：单 Tool 执行

当前架构仍然是：

```text
Question
 ↓
Router
 ↓
ONE Tool
```

增加/保留测试：

```text
ToolExecutionService.execute()
```

每个 Orchestrator TOOL 请求：

```text
Tool execution count == 1
```

禁止：

```text
Tool A
 ↓
Tool B
```

也禁止：

```text
Tool
 ↓
Router
 ↓
Tool
```

---

# 十八、Contract 12：禁止 LLM 参数提取

本阶段不要增加任何：

```text
LLM
Function Calling
structured output
JSON mode
```

ArgumentExtractor 必须继续：

```text
deterministic
```

验证：

```text
ToolArgumentExtractor
```

源码中没有：

```text
OpenAI
DeepSeek
LLM client
HTTP
```

---

# 十九、文档清理

更新：

```text
docs/architecture.md
```

建议增加：

```text
§8.25 Tool Boundary Contracts
```

明确写：

```text
Router
    = Selection Authority

AIOrchestrator
    = Orchestration

ToolArgumentExtractor
    = Candidate Argument Extraction

ToolRegistry
    = Schema Validation + Tool Dispatch Authority

ToolExecutionService
    = Capability + Execution Boundary

Tool
    = Business Implementation

ToolResult
    = Unified Tool Output Contract
```

---

# 二十、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.11 Step 7 — Tool Boundary Contract.md
```

记录：

```text
1. Boundary definitions
2. Dependency direction
3. Selection authority
4. Argument extraction authority
5. Schema authority
6. Execution authority
7. Tool isolation
8. Security boundaries
9. Contract tests
10. Known limitations
```

---

# 二十一、历史 Docstring 清理

至少检查 Step 6 已经明确发现的：

```text
backend/app/services/tool_execution_service.py
backend/app/tools/get_inventory.py
backend/app/tools/get_work_order.py
tests/test_get_inventory_tool.py
```

将：

```text
_extract_tool_arguments_from_question
```

等旧描述改成：

```text
ToolArgumentExtractor
```

但：

**不要修改实际执行逻辑。**

---

# 二十二、不要过度清理

不要进行：

```text
全项目 docstring 重写
大规模注释重构
文件重命名
模块重新组织
import 全面重排
```

只修正：

**与当前 Tool Boundary 直接相关的过时内容。**

---

# 二十三、测试命令

Windows PowerShell：

先：

```powershell
python -m pytest -q tests/test_tool_architecture_contract.py
```

然后：

```powershell
python -m pytest -q tests/test_tool_argument_extractor.py
```

再：

```powershell
python -m pytest -q tests/test_ai_orchestrator.py
```

然后完整：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

最后：

```powershell
python -m compileall -q backend
```

并检查：

```text
LSP diagnostics
```

如果环境没有：

```text
ruff
flake8
```

不要为了本 Step 安装新工具。

如实记录：

```text
lint unavailable
```

---

# 二十四、禁止修改核心行为

本 Step 理想状态：

```text
AI Router             SHA256 unchanged
RouteDecision         unchanged
ToolExecutionService  SHA256 unchanged
ToolRegistry          unchanged
get_inventory         behavior unchanged
get_work_order        behavior unchanged
ToolChatService       unchanged
```

允许修改：

```text
docstring
comment
architecture docs
evaluation docs
tests
```

如果发现某个核心模块必须修改才能通过 Contract：

**停止并报告，不要直接修改。**

---

# 二十五、完成标准

最终应该得到：

```text
                    Question
                       │
                       ▼
                    Router
                       │
                RouteDecision
                  tool_name
                       │
                       ▼
                AIOrchestrator
                       │
                       ▼
             ToolArgumentExtractor
                       │
                    arguments
                       │
                       ▼
             ToolExecutionService
                       │
                       ▼
                 ToolRegistry
                       │
               Schema Validation
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
       get_inventory       get_work_order
             │                   │
             └─────────┬─────────┘
                       ▼
                   ToolResult
```

并且所有边界都满足：

```text
Selection        = Router
Extraction       = ToolArgumentExtractor
Schema           = ToolRegistry
Execution        = ToolExecutionService
Business Logic   = Tool
Output           = ToolResult
```

---

# 二十六、最终报告

完成后严格按照：

```text
【Phase 3.11 Step 7 COMPLETE】

1. 历史引用清理
- 清理文件：
- 旧函数名残留：
- 是否还有失效引用：

2. Contract Tests
- Router Selection：
- Argument Extraction：
- Registry Schema：
- Execution Boundary：
- Tool Isolation：
- Tool-to-Tool：
- Single Tool：
- Unknown Tool：

3. 新增文件

4. 修改文件

5. 核心代码是否变化
- Router：
- RouteDecision：
- ToolExecutionService：
- ToolRegistry：
- get_inventory：
- get_work_order：
- ToolChatService：

6. Tests
- directed：
- full no DB：
- full DB：

7. compile / LSP / lint

8. DB writes
- result：

9. Network
- result：

10. 发现的问题

11. 当前限制

12. 最终架构

最后：

**Step 7 到此停止。**

不要进入 Step 8。
不要迁移 ToolChatService。
不要增加第三个 Tool。
不要引入 LLM Argument Extraction。
不要引入 Agent / MCP / LangGraph。
不要实现 Multi-step Tool Calling。
```

**完成 Step 7 后立即停止，等待下一步指令。**
