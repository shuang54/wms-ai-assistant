你现在开始执行：

# Phase 3.11 Step 14 — Tool Execution Record / Event Contract

项目：

```text
D:\coding\ai\wms-ai-assistant
```

## 一、阶段目标

在 Step 13 `ToolExecutionContext` 已完成的基础上，为一次 Tool 执行建立一个**不可变、内部使用的 Execution Record 数据契约**。

目标：

```text
ToolExecutionContext
        +
ToolResult
        +
Execution Timing
        ↓
ToolExecutionRecord
```

这个 Record 为后续：

```text
Audit
Observability
Metrics
Tracing
```

提供稳定的数据模型。

**本阶段只建立 Contract，不实现持久化。**

---

# 二、严格范围

## 允许

* 新增 `ToolExecutionRecord` frozen DTO
* 新增纯 Record 构造/转换逻辑
* 必要时在 `ToolExecutionService` 增加最小 Record 生成能力
* 新增单元测试
* 新增 Architecture Contract
* 更新 Architecture / Evaluation 文档

## 禁止

绝对不要新增：

```text
数据库表
数据库 Repository
Audit Service
Dashboard
Metrics Service
Tracing Service
日志持久化
API 字段
HTTP Response 字段
ToolResult 字段
LLM Message 字段
```

不要修改：

```text
Router
AIOrchestrator
ToolRegistry
Tool Handler
Tool Argument Extractor
ToolChatService 的多轮语义
```

不要：

```text
Agent
MCP
LangGraph
Memory
Planning
Retry
Fallback
Parallel Tool
```

---

# 三、Step 1：先阅读真实代码

先不要修改。

阅读：

```text
backend/app/services/tool_execution_context.py
backend/app/services/tool_execution_service.py
backend/app/services/tool_chat_service.py
backend/app/tools/base.py
backend/app/tools/registry.py
```

以及：

```text
tests/test_tool_execution_context.py
tests/test_tool_execution_service.py
tests/test_tool_runtime_failure_contract.py
tests/test_tool_chat_execution_boundary.py
```

重点确认：

1. 当前 `ToolResult` 的真实字段。
2. 当前 ToolExecutionService 的真实执行边界。
3. 当前错误字段：

   * error
   * error_code
   * cause_type
   * 其他现有字段
4. 项目现有 datetime / timezone convention。
5. 项目是否已经有 duration / timing 工具。
6. 是否已经存在类似 Event / Record DTO。

**不要假设字段。**

如果已有等价 DTO：

优先复用。

---

# 四、ToolExecutionRecord

新增：

```text
backend/app/services/tool_execution_record.py
```

如果项目已有更自然的位置，遵循现有架构。

建议：

```python
@dataclass(frozen=True)
class ToolExecutionRecord:
    request_id: str
    project_id: str | None
    tool_call_id: str | None
    round: int

    tool_name: str

    started_at: datetime
    finished_at: datetime
    duration_ms: float

    success: bool
    error_code: str | None
    cause_type: str | None
```

最终字段必须以现有项目实际设计为准。

---

# 五、Record 字段规则

## 1. request_id

来自：

```text
ToolExecutionContext.request_id
```

不得重新生成。

---

## 2. project_id

来自：

```text
ToolExecutionContext.project_id
```

不能从 Tool arguments 获取。

不能由 LLM 提供。

---

## 3. tool_call_id

来自：

```text
ToolExecutionContext.tool_call_id
```

保持原值。

不要重新生成。

---

## 4. round

来自：

```text
ToolExecutionContext.round
```

保持原值。

不能修改。

---

## 5. tool_name

来自：

```text
ToolExecutionService.execute(tool_name)
```

必须是实际执行的 Tool 名称。

不能从：

```text
question
LLM message
arguments
```

重新推断。

---

# 六、Timing Contract

必须区分：

```text
started_at
finished_at
duration_ms
```

要求：

### started_at

记录实际执行开始时间。

### finished_at

记录实际执行结束时间。

### duration_ms

表示：

```text
finished - started
```

对应的实际执行耗时。

要求：

```text
duration_ms >= 0
```

不要使用：

```text
datetime.now() - datetime.now()
```

作为唯一精度来源。

如果项目没有现成 timing utility：

可以使用：

```text
datetime.now(timezone.utc)
```

记录 wall-clock timestamp，

同时使用：

```text
time.perf_counter()
```

计算 duration。

不要为了计时修改其他 Service。

---

# 七、Success Contract

```text
record.success == tool_result.success
```

不得自行推断。

例如：

```text
ToolResult(success=False)
→ record.success=False
```

即使 Handler 已经正常返回一个 ToolResult，也不能认为 execution success。

---

# 八、Error Contract

优先复用当前 `ToolResult` 已有错误字段。

例如当前存在：

```text
error_code
cause_type
```

就直接映射。

如果不存在：

**不要为了本阶段强行设计新的 ErrorCode 系统。**

可以：

```text
error_code = None
```

或者使用项目已有错误分类。

`cause_type`：

* 只能记录安全的异常类型名称
* 不允许记录完整 traceback
* 不允许记录 exception message 中可能包含的 secrets

禁止把以下内容写入 Record：

```text
API Key
password
DATABASE_URL
connection string
authorization header
完整 traceback
SQL
Tool arguments
Tool result data
```

---

# 九、不要把 arguments/result 放进 Record

这是本阶段的重要安全边界。

Record 只记录：

```text
谁
哪个 request
哪个 project
哪个 tool call
第几轮
执行哪个 tool
什么时候执行
执行多久
成功还是失败
失败类型
```

不要记录：

```text
arguments
ToolResult.data
SQL
数据库连接
LLM prompt
LLM response
```

原因：

Execution Record 是未来 Observability/Audit 的基础事件。

不应该在基础事件中直接携带业务数据。

---

# 十、不要修改 ToolResult

保持：

```text
ToolResult
```

现有结构完全不变。

不要新增：

```text
request_id
duration_ms
started_at
finished_at
```

到 ToolResult。

原因：

```text
ToolResult = Tool execution result
ToolExecutionRecord = execution metadata
```

两个 Contract 必须分离。

---

# 十一、Record 创建方式

优先实现一个纯构造方式，例如：

```python
ToolExecutionRecord.from_execution(
    context=context,
    tool_name=tool_name,
    result=result,
    started_at=started_at,
    finished_at=finished_at,
    duration_ms=duration_ms,
)
```

要求：

```text
Pure
Deterministic
No DB
No IO
No logging
No LLM
```

如果项目现有 DTO 风格不支持 classmethod：

遵循现有项目风格。

不要为了这个 DTO 引入新 framework。

---

# 十二、是否修改 ToolExecutionService

先根据真实代码判断。

原则：

### 不改变现有 execute() contract

当前：

```python
execute(...) -> ToolResult
```

必须继续保持。

不要改成：

```python
execute(...) -> ToolExecutionRecord
```

也不要改成：

```python
execute(...) -> tuple[ToolResult, ToolExecutionRecord]
```

因为这会影响：

```text
AIOrchestrator
ToolChatService
tests
```

---

如果要让 Service 创建 Record：

只能采用：

**最小、向后兼容的方式。**

例如内部创建 Record，或者使用已有 observer/sink 扩展点。

但是：

**不要为了本阶段新增复杂 Observer Framework。**

如果当前架构没有合适的扩展点：

那么本阶段只实现：

```text
ToolExecutionRecord DTO
+
from_execution()
+
unit tests
+
architecture contract
```

不要硬改 ToolExecutionService。

---

# 十三、Architecture Contract

新增：

```text
C16 Tool Execution Record Contract
```

至少验证：

### C16.1

Record 是：

```text
frozen / immutable
```

### C16.2

字段只有定义好的 execution metadata。

### C16.3

Record 不包含：

```text
arguments
result data
SQL
credentials
connection
```

### C16.4

Record 的：

```text
request_id
project_id
tool_call_id
round
```

来自 ToolExecutionContext。

### C16.5

`tool_name` 来自实际 execute 参数。

### C16.6

`success` 来自 ToolResult。

### C16.7

错误信息不会泄露：

```text
secret
password
connection string
traceback
```

### C16.8

Record 本身不执行：

```text
DB
LLM
Tool
IO
```

---

# 十四、测试

新增：

```text
tests/test_tool_execution_record.py
```

建议覆盖 20～30 个高质量测试。

至少包括：

## DTO

```text
正常创建
frozen
字段类型
request_id
project_id
tool_call_id
round
tool_name
```

## Timing

```text
started_at
finished_at
duration_ms
duration_ms >= 0
```

## Result Mapping

```text
ToolResult success=True
→ record.success=True

ToolResult success=False
→ record.success=False
```

## Error Mapping

覆盖当前真实 ToolResult 错误结构。

## Context Mapping

验证：

```text
context.request_id → record.request_id
context.project_id → record.project_id
context.tool_call_id → record.tool_call_id
context.round → record.round
```

## Isolation

验证 Record 不包含：

```text
arguments
result data
SQL
secret
```

## Immutability

尝试修改字段必须失败。

## Determinism

同样输入：

```text
same context
same tool
same result
same timestamps
same duration
```

得到等价 Record。

---

# 十五、Real Tool Regression

如果 Record 已经安全地接入 ToolExecutionService：

增加少量 DB-gated regression：

```text
get_inventory
```

使用现有：

```text
MAT-001
```

验证：

```text
ToolResult.success = True
Record.success = True
Record.tool_name = "get_inventory"
Record.round = 1
Record.project_id = expected scope
Record.request_id != ""
```

但是：

**不要因为这个测试而修改 get_inventory SQL。**

如果没有安全、自然的接入点：

本阶段不要强行接入。

只测试 DTO contract。

---

# 十六、禁止新增持久化

再次强调：

本阶段不允许：

```text
INSERT tool_execution_record
CREATE TABLE
Repository
Migration
Audit database
```

最终：

```text
DB writes = 0
```

---

# 十七、测试命令

先运行：

```powershell
python -m pytest -q tests/test_tool_execution_record.py
```

然后相关测试：

```powershell
python -m pytest -q `
  tests/test_tool_execution_context.py `
  tests/test_tool_execution_service.py `
  tests/test_tool_runtime_failure_contract.py `
  tests/test_tool_chat_execution_boundary.py
```

再运行：

```powershell
python -m pytest -q
```

如果需要 DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

注意 Windows PowerShell。

不要使用：

```text
RUN_DB_TESTS=1 pytest
```

---

# 十八、编译 / 静态检查

运行：

```powershell
python -m compileall backend tests
```

如果项目已有 lint：

继续使用现有 lint。

要求：

```text
0 failed
0 diagnostics
```

---

# 十九、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.11 Step 14 — Tool Execution Record Contract.md
```

记录：

## 1. Purpose

为什么需要 ToolExecutionRecord。

## 2. Contract

记录全部字段。

## 3. Relationship

```text
ToolExecutionContext
        ↓
Tool Execution
        ↓
ToolResult
        +
Timing
        ↓
ToolExecutionRecord
```

## 4. Security Boundary

明确：

```text
No arguments
No result data
No SQL
No secrets
No persistence
```

## 5. Compatibility

明确：

```text
ToolResult unchanged
ToolExecutionService.execute() return type unchanged
ToolRegistry unchanged
Tool Handler unchanged
API unchanged
```

## 6. Current Limitation

明确：

```text
No persistence
No audit
No dashboard
No metrics aggregation
No tracing backend
```

---

# 二十、完成后的最终报告

严格按照：

```text
【Phase 3.11 Step 14 COMPLETE】

1. 新增文件
2. 修改文件
3. ToolExecutionRecord Contract
4. Context → Record
5. ToolResult → Record
6. Timing
7. Security
8. Architecture Contract C16
9. 测试结果
10. compile / lint
11. DB writes
12. API 是否变化
13. ToolResult 是否变化
14. 当前限制
15. Step 14 结论
```

最后明确：

```text
ToolExecutionContext
        +
ToolResult
        +
Timing
        ↓
ToolExecutionRecord
```

然后：

**立即停止。**

不要进入 Step 15。

不要做 Audit。

不要做 Metrics。

不要做 Dashboard。

不要做 Tracing。

不要做数据库持久化。

不要开发 Agent / MCP / LangGraph / Memory / Planning。
