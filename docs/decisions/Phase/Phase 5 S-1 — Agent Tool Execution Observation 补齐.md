你现在开始执行：

# Phase 5 S-1 — Agent Tool Execution Observation 补齐

## 一、阶段目标

解决 Phase 5 Agent State / Resume Boundary Decision 中确认的：

```text
D-1：
Agent 路径 Tool 执行不产生 ToolExecutionRecord。
```

当前：

```text
AgentRuntime
  ↓
ToolExecutionService.execute(...)
  ↓
ToolExecutionContext = None
  ↓
ToolExecutionRecord 不产生
```

本阶段唯一目标：

> **让 Agent Tool Calling 复用现有 ToolExecutionRecord 观测链路。**

本阶段不是 AgentStep Persistence。

本阶段不是 Resume。

本阶段不是 Checkpoint。

本阶段不是 HITL。

---

# 二、严格范围

## 允许修改

只允许围绕 Agent Tool Execution Context 做最小修改：

```text
backend/app/agent/
backend/app/services/tool_execution_service.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/project_orchestrator_factory.py
tests/test_agent*
tests/test_tool_execution*
```

如果实际实现只需要其中少数文件，只修改必要文件。

允许新增：

```text
tests/test_agent_tool_observation.py
```

或者按照当前测试结构选择已有测试文件。

---

# 三、明确禁止

禁止修改：

```text
AgentRun Model
AgentRun Repository
AgentRun Recorder
AgentStep
Checkpoint
Resume
HITL
Cancellation
Conversation
ConversationTurn
Idempotency
Evidence
LLMUsageRecord
AssistantOutcomeRecord
TextToSQL
RAG
Router decision semantics
Prompt
Provider contract
Tool Registry contract
API contract
Database schema
```

禁止新增：

```text
agent_step
checkpoint
approval
resume
timeline
```

禁止：

```text
DB migration
新表
新 Tool
新 Tool Handler
真实业务数据
DeepSeek evaluation
```

---

# 四、开始前必须阅读

先阅读真实代码：

```text
backend/app/services/tool_execution_service.py

backend/app/agent/runtime.py
backend/app/agent/models.py
backend/app/agent/llm.py

backend/app/services/ai_orchestrator_service.py
backend/app/services/project_orchestrator_factory.py

backend/app/tools/
backend/app/llm/

tests/test_tool_execution*
tests/test_agent*
tests/test_agent_orchestrator_integration.py
tests/test_agent_run_persistence.py
tests/test_agent_run_persistence_db.py
```

重点确认：

```text
ToolExecutionContext
ToolExecutionObserver
ToolExecutionRecord
request_id
tool_call_id
round
tool_name
project_id
success
duration
```

不要根据文件名猜接口。

---

# 五、先确认现有 Tool Observation Contract

不要重新设计 ToolExecutionRecord。

找到当前真实：

```text
ToolExecutionContext
```

确认它是否已经包含：

```text
request_id
tool_call_id
round
project_id
```

如果已有：

**直接复用。**

不要创建：

```text
AgentToolExecutionContext
```

除非现有 Context 无法表达 Agent 所需 correlation，并且经过最小设计确认。

---

# 六、Correlation 规则

Agent Tool Execution 必须能够形成：

```text
request_id
    ↓
AgentRun
    ↓
ToolExecutionRecord
```

同时：

```text
tool_call_id
    ↓
ToolExecutionRecord
```

必须保留真实 Provider 返回的：

```text
tool_call_id
```

禁止生成新的：

```text
uuid
```

替代真实 tool_call_id。

---

# 七、round 语义

当前 Agent Runtime 有：

```text
step_index
```

ToolExecutionRecord 有：

```text
round
```

必须明确映射。

优先：

```text
Agent step_index
    ↓
ToolExecutionContext.round
```

如果当前 ToolExecutionService 的 round 有不同语义：

**不要直接强行映射。**

先检查现有 Tool Calling / Chat Tool Calling 的 round 定义。

如果确实存在语义差异：

记录：

```text
Architecture Drift
```

并停止，不修改生产逻辑。

---

# 八、project_id

Agent Tool Execution Context 必须能够得到：

```text
project_id
```

来源必须是：

```text
当前 Agent / Orchestrator 已有 ProjectContext
```

禁止：

```text
硬编码 vietnam-wms
```

禁止：

```text
重新查询 Project Registry
```

禁止：

```text
AgentRuntime 直接访问 DB
```

如果 AgentRuntime 当前拿不到 project_id：

优先由 Orchestrator / Agent capability 层传递。

保持：

```text
AgentRuntime
    ↓
DB-free
```

---

# 九、AgentRuntime 边界

非常重要：

AgentRuntime 可以产生：

```text
ToolExecutionContext
```

但是：

**AgentRuntime 不允许创建 DB Repository / ORM / Session。**

正确结构：

```text
AgentRuntime
   ↓
ToolExecutionService
   ↓
ToolExecutionObserver
   ↓
ToolExecutionRecord
```

而不是：

```text
AgentRuntime
   ↓
ToolExecutionRepository
```

---

# 十、ToolExecutionService 二次安全检查

继续保持现有：

```text
Agent capability authorization
        ↓
ToolExecutionService authorization
        ↓
Handler
```

不要因为补 Observation 而改变：

```text
authorization
schema validation
read-only enforcement
```

尤其：

```text
Tool Observation
```

不能绕过：

```text
Tool Authorization
```

---

# 十一、Failure Semantics

Tool 执行失败时：

```text
ToolExecutionRecord.success = false
```

必须仍然记录。

Agent Runtime 原有行为保持：

```text
TOOL_EXECUTION_FAILURE
→ Agent FAILED
```

不能因为 Observation 写失败而：

```text
Agent FAILED
```

这是两种不同失败：

```text
Tool failure
≠
Observation persistence failure
```

---

# 十二、Observation Persistence Failure

继续使用当前 ToolExecutionService 的 best-effort 语义。

如果：

```text
ToolExecutionRecord
```

写入失败：

不能改变：

```text
Tool Result
```

不能改变：

```text
Agent Runtime Result
```

不能触发：

```text
retry
```

不能触发：

```text
fallback
```

原则：

> Tool observability failure must never become Tool business failure.

---

# 十三、必须验证三种 Tool 路径

## Case A：Tool Success

```text
Agent
 ↓
LLM TOOL_CALL
 ↓
Authorization
 ↓
ToolExecutionService
 ↓
Handler
 ↓
Tool Result
```

验证：

```text
ToolExecutionRecord = 1
success = true
```

---

## Case B：Tool Failure

验证：

```text
ToolExecutionRecord = 1
success = false
```

同时：

```text
Agent status = FAILED
error_code = TOOL_EXECUTION_FAILURE
```

---

## Case C：Tool Observation Failure

模拟：

```text
ToolExecutionRecord persistence failure
```

验证：

```text
Tool business result unchanged
Agent result unchanged
No retry
```

---

# 十四、Correlation Tests

至少测试：

```text
request_id
tool_call_id
project_id
round
```

都正确进入 ToolExecutionRecord。

特别验证：

```text
AgentRun.request_id
==
ToolExecutionRecord.request_id
```

以及：

```text
Provider tool_call_id
==
ToolExecutionRecord.tool_call_id
```

---

# 十五、One Tool Per Round

保持 Phase 5 已冻结约束：

```text
0 or 1 logical Tool per round
```

本阶段不能改变：

```text
parallel tool calls
multi-tool execution
```

如果 Provider 返回多个 tool calls：

继续沿用当前：

```text
provider rejection
```

不要修改。

---

# 十六、No New Database Schema

绝对不要新增表。

不要新增字段。

不要新增 migration。

只复用现有：

```text
tool_execution_record
```

如果当前表已经满足需求：

不要改表。

---

# 十七、测试数据库

DB 测试允许读取/验证：

```text
tool_execution_record
```

测试必须：

```text
insert
verify
cleanup
```

最终：

```text
tool_execution_record residue = 0
```

不得修改历史 baseline。

如果全量测试仍出现 Phase 4.2 已知的 order-dependent failures：

必须继续标记：

```text
PRE_EXISTING
```

不要为了本阶段全绿而修改 baseline。

---

# 十八、AgentRun Relationship

本阶段只建立：

```text
AgentRun
    │
    └── request_id
           │
           └── ToolExecutionRecord
```

不要新增：

```text
agent_run_id
```

到 ToolExecutionRecord。

不要 FK。

保持现有：

```text
request_id correlation
```

---

# 十九、LLM Observation Relationship

保持：

```text
AgentRun
    │
    ├── LLMUsageRecord
    │
    └── ToolExecutionRecord
```

但：

```text
AgentRun
```

仍然不复制：

```text
provider
model
prompt_tokens
completion_tokens
tool arguments
tool result
```

本阶段不修改 LLMUsageRecord。

---

# 二十、真实 DeepSeek

默认：

```text
NO REAL LLM
```

所有单元/集成测试：

```text
Fake AgentLLM
Fake Tool Handler
```

如果已有 real smoke：

本阶段不需要重新运行。

不要为了验证 ToolExecutionRecord 而调用 DeepSeek。

---

# 二十一、测试文件

优先复用：

```text
tests/test_agent_orchestrator_integration.py
tests/test_tool_execution_service.py
```

如果现有结构不适合：

新增：

```text
tests/test_agent_tool_observation.py
```

至少覆盖：

```text
success
failure
observation failure
request_id
tool_call_id
project_id
round
authorization
no DB direct dependency
```

---

# 二十二、源码边界审计

继续保留并加强：

```text
AgentRuntime
```

不得 import：

```text
sqlalchemy
db
repository
ToolExecutionRecord
```

AgentRuntime 可以依赖：

```text
ToolExecutionService
ToolExecutionContext
```

但不能依赖：

```text
ToolExecutionRepository
```

如果现有架构为了 observation 必须改变这一点：

停止并报告 Architecture Drift。

---

# 二十三、验证命令

先执行针对性测试：

```powershell
python -m pytest -q tests/test_agent_orchestrator_integration.py
python -m pytest -q tests/test_tool_execution_service.py
```

然后：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

编译：

```powershell
python -m compileall -q backend tests scripts
```

---

# 二十四、Git Diff

执行：

```powershell
git status --short
git diff --check
git diff --stat
git diff
```

确认：

```text
No AgentRun schema change
No AgentStep
No Checkpoint
No Resume
No HITL
No Conversation change
No Evidence change
No LLMUsageRecord change
No Prompt change
No Provider change
No Tool Registry change
No API change
No migration
No secrets
```

---

# 二十五、Commit

只有满足：

```text
Target tests = PASS
Offline full = PASS
DB tests = PASS or only known PRE_EXISTING failures
compileall = PASS
DB residue = 0
git diff --check = PASS
```

才 commit：

```text
feat: Phase 5 agent tool execution observation
```

不要：

```text
push
PR
merge
rebase
```

---

# 二十六、最终报告

完成后严格输出：

```text
【Phase 5 AGENT TOOL OBSERVATION COMPLETE】

1. D-1
2. 修改文件
3. ToolExecutionContext
4. request_id correlation
5. tool_call_id correlation
6. project_id
7. round
8. Tool Success Observation
9. Tool Failure Observation
10. Observation Failure Isolation
11. Authorization Boundary
12. AgentRuntime DB Dependency
13. AgentRun Relationship
14. LLM Observation Relationship
15. DB Tests
16. Offline Regression
17. DB Residue
18. compileall
19. Git Diff
20. Commit
21. Architecture Drift
22. Known Limitations

最终状态：

AgentRun Persistence     = READY
Agent Tool Observation  = READY
AgentStep Persistence    = NOT STARTED
Checkpoint               = NOT STARTED
Resume                   = NOT STARTED
HITL                     = NOT STARTED
Cancellation             = NOT STARTED

STOP
```

---

# 二十七、强制 STOP

完成后立即停止。

不要进入：

```text
AgentStep Persistence
Checkpoint
Resume
HITL
Cancellation
Streaming
Memory
Planning
Multi-Agent
LangGraph
MCP
```

本阶段只解决：

```text
D-1
Agent Tool Execution
        ↓
ToolExecutionRecord
```

不要扩大范围。
