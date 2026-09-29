# Phase 3.12 Step 62 — Assistant Outcome Contract Design Audit

## 一、阶段目标

基于 Phase 3.12 Step 61 的结论：

```text
OUTCOME_BOUNDARY_MISSING
```

本阶段只完成：

> **设计并审计 Assistant-level Outcome Contract。**

不要实现生产代码。

不要修改：

```text
AIOrchestrationResult
/api/ai/chat
Assistant Trace
LLM Usage
Tool Execution
RAG Execution
```

本阶段需要回答：

```text
一次 AI 请求最终是什么结果？
```

统一定义：

```text
SUCCESS
EMPTY
REFUSED
FAILED
```

并明确：

```text
Business Outcome
        ≠
HTTP Transport Status
        ≠
LLM Execution Status
        ≠
Tool Execution Status
        ≠
RAG Execution Status
```

---

# 二、严格范围

允许：

```text
新增 audit tests
新增 evaluation 文档
新增纯设计文档
读取并分析现有 DTO / Service / API
构造离线 projection cases
```

禁止：

```text
修改生产代码
修改 API contract
新增 Outcome DTO
新增数据库字段
新增数据库表
新增 Index
修改 Prompt
修改 Router
修改 Orchestrator
修改 RAG
修改 Tool
修改 Text-to-SQL
修改 Validator
修改 Executor
修改 LLM Provider
```

禁止：

```text
DeepSeek
真实网络
生产数据库
OpenTelemetry
Metrics
Tracing
Dashboard
```

---

# 三、开始前必须阅读

阅读：

```text
backend/app/services/ai_orchestrator_service.py
backend/app/api/orchestrator_chat.py

backend/app/services/assistant_trace_service.py

backend/app/services/rag_service.py
backend/app/services/tool_chat_service.py
backend/app/services/text_to_sql_service.py

backend/app/dto/
backend/app/api/
```

继续检查：

```text
tests/
docs/evaluation/
docs/architecture.md
```

重点搜索：

```text
AIOrchestrationResult
route
content
data
metadata
refused
tool_success
rag_used_chunks
HTTPException
status_code
```

---

# 四、定义四种 Business Outcome

本阶段提出候选 Contract：

```text
SUCCESS
EMPTY
REFUSED
FAILED
```

但不要立即写代码。

先根据现有真实行为验证定义。

---

## 4.1 SUCCESS

定义：

> 请求已经完成，并产生有效的业务响应。

例如：

### RAG

```text
HTTP 200
route=rag
content=实际答案
```

### Tool

```text
HTTP 200
route=tool
tool_success=true
```

### Text-to-SQL

```text
HTTP 200
SQL 执行成功
返回业务数据/摘要
```

注意：

```text
LLM success
```

不能单独定义为 Assistant SUCCESS。

必须是：

```text
Assistant-level execution completed
```

---

# 五、EMPTY

定义：

> 请求正常完成，但没有可提供的业务结果。

典型：

```text
RAG 检索 0 chunks
```

当前实际行为：

```text
HTTP 200
固定提示语
rag_used_chunks=0
LLM calls=0
```

审计：

```text
EMPTY
```

是否应该与：

```text
SUCCESS
```

区分。

必须明确：

```text
EMPTY 不是 FAILED
```

也不是：

```text
HTTP 404
```

---

# 六、REFUSED

定义：

> 系统明确拒绝执行用户请求，但这是预期的业务安全行为。

当前已有：

```text
HTTP 200
route=text_to_sql
metadata.refused=true
data=None
Validator=0
Executor=0
LLM calls=1
```

因此：

```text
REFUSED
```

必须明确：

```text
REFUSED ≠ FAILED
```

并且：

```text
REFUSED ≠ EMPTY
```

---

# 七、FAILED

定义：

> 请求无法正常完成，且不是预期的安全拒绝。

例如：

```text
LLM provider failure
RAG runtime failure
Tool failure
Text-to-SQL retry exhausted
SQL execution failure
```

注意当前 Tool 存在特殊行为：

```text
Tool handler failure
→ HTTP 200
→ metadata.tool_success=false
```

因此：

> HTTP 200 不能直接定义 Business SUCCESS。

必须以 Assistant-level business outcome 为准。

---

# 八、建立 Outcome 优先级

设计一个明确的判定优先级。

建议审计：

```text
REFUSED
   ↓
FAILED
   ↓
EMPTY
   ↓
SUCCESS
```

但必须验证这个优先级是否适用于所有现有场景。

特别检查：

```text
refusal + HTTP 200
failure + partial execution
empty + HTTP 200
tool_success=false + HTTP 200
```

不要直接接受建议顺序。

最终文档必须给出：

```text
Outcome precedence:
...
```

以及每一条的理由。

---

# 九、Outcome 与 HTTP Status

建立矩阵：

| Business Outcome | HTTP Status | 是否合理 |
| ---------------- | ----------: | ---- |
| SUCCESS          |         200 | ?    |
| EMPTY            |         200 | ?    |
| REFUSED          |         200 | ?    |
| FAILED           |     4xx/5xx | ?    |

重点：

### HTTP 200 + Tool failure

当前已经存在：

```text
tool_success=false
```

所以必须确认：

```text
Business Outcome = FAILED
```

还是当前系统有其他语义。

不要因为 HTTP 200 就判定 SUCCESS。

---

# 十、Outcome 与 Route

必须明确：

```text
route = capability selection
```

而：

```text
outcome = request result
```

因此合法组合包括：

```text
RAG + SUCCESS
RAG + EMPTY
RAG + FAILED

TOOL + SUCCESS
TOOL + FAILED

TEXT_TO_SQL + SUCCESS
TEXT_TO_SQL + REFUSED
TEXT_TO_SQL + FAILED
TEXT_TO_SQL + EMPTY
```

审计当前是否存在：

```text
route + outcome
```

的完整覆盖。

---

# 十一、Outcome 与 LLM Calls

建立矩阵：

| Outcome | LLM Calls |
| ------- | --------: |
| SUCCESS | 0 / 1 / N |
| EMPTY   |     0 / ? |
| REFUSED |         1 |
| FAILED  | 0 / 1 / N |

重点：

```text
LLM calls ≠ Outcome
```

例如：

```text
Text-to-SQL:
attempt 1 invalid
attempt 2 valid
→ 2 LLM calls
→ SUCCESS
```

因此：

```text
llm_usage count
```

不能作为 outcome 判定依据。

---

# 十二、Outcome 与 Tool/RAG/Executor

同样审计：

```text
Tool success
RAG execution
SQL executor
```

不能单独代表 Assistant outcome。

构造：

```text
LLM SUCCESS
Tool SUCCESS
Assistant FAILED
```

这种 projection case。

如果当前系统无法产生这种真实状态：

记录：

```text
Not observable today
```

但必须明确：

> 未来 Outcome Contract 必须能够容纳这种状态。

---

# 十三、Result Content 与 Outcome

检查：

```text
content
data
```

是否应该参与 outcome 判断。

建议审计：

### content 非空

是否一定：

```text
SUCCESS
```

答案通常：

```text
No
```

因为：

```text
REFUSED
```

也有 content。

### content 为空

是否一定：

```text
EMPTY
```

也不能直接这样判断。

因此：

```text
content/data
```

只能作为辅助信号。

不能作为唯一 Outcome source。

---

# 十四、Metadata 信号分类

把现有 metadata 分成：

### 明确业务语义

例如：

```text
refused
tool_success
```

### 辅助信号

例如：

```text
rag_used_chunks
row_count
truncated
```

### 技术元数据

例如：

```text
request_id
execution_time_ms
provider
model
```

建立表：

```text
field
category
can_determine_outcome
reason
```

原则：

> 技术元数据不能被误当成 Business Outcome。

---

# 十五、Error 分类

Step 61 已发现：

当前失败主要依赖：

```text
HTTP status
exception class name
```

本阶段只审计未来：

```text
error_class
```

是否值得作为 Outcome 的辅助字段。

候选：

```text
LLM_ERROR
RAG_ERROR
TOOL_ERROR
TEXT_TO_SQL_ERROR
SQL_EXECUTION_ERROR
CONFIG_ERROR
INTERNAL_ERROR
```

注意：

**不要实现这些 error code。**

只判断：

```text
是否需要
是否安全
是否足够稳定
```

特别检查：

```text
error class name
```

是否属于稳定 API contract。

---

# 十六、Security Boundary

未来 Outcome Contract 绝对不能包含：

```text
exception message
prompt
messages
SQL
Tool arguments
Tool result raw data
RAG chunks
API key
Authorization
password
DATABASE_URL
stack trace
raw provider response
```

可以考虑：

```text
outcome
error_class
```

但必须：

```text
allowlist
```

而不是把异常原文直接暴露出去。

---

# 十七、Projection Cases

不要调用真实 LLM。

离线构造至少：

### Case A

```text
RAG
content=answer
chunks=1
→ SUCCESS
```

### Case B

```text
RAG
chunks=0
fixed empty response
→ EMPTY
```

### Case C

```text
TEXT_TO_SQL
refused=true
→ REFUSED
```

### Case D

```text
TEXT_TO_SQL
2 LLM calls
executor success
→ SUCCESS
```

### Case E

```text
TEXT_TO_SQL
retry exhausted
→ FAILED
```

### Case F

```text
TOOL
tool_success=true
→ SUCCESS
```

### Case G

```text
TOOL
tool_success=false
HTTP=200
→ ?
```

必须由审计确定。

### Case H

```text
RAG
HTTP=500
→ FAILED
```

### Case I

```text
LLM failure
HTTP=500
→ FAILED
```

### Case J

```text
LLM success
Tool success
Final assembly failure
→ ?
```

这个 case 用来验证未来 Contract 是否能够表达：

```text
底层成功 ≠ Assistant success
```

---

# 十八、Contract 最小性评估

比较两个候选方案。

## Option A

```text
outcome
```

只有：

```text
SUCCESS
EMPTY
REFUSED
FAILED
```

## Option B

```text
outcome
error_class
```

例如：

```text
FAILED + TOOL_ERROR
FAILED + LLM_ERROR
```

分析：

```text
可表达能力
安全性
客户端复杂度
向后兼容
未来 Trace 使用
```

**不要选择过度复杂方案。**

最终只给出：

```text
Recommended future contract:
...
```

不实现。

---

# 十九、HTTP Contract 是否需要变化

审计：

当前：

```json
{
  "route": "...",
  "content": "...",
  "data": "...",
  "metadata": "..."
}
```

未来是否应该增加：

```json
{
  "outcome": "success"
}
```

还是：

```json
metadata.outcome
```

还是：

```text
只在 Trace 内部提供
```

本阶段只比较方案。

不要修改 HTTP API。

---

# 二十、Trace 是否应该拥有 Outcome

Step 61 已确定：

```text
Trace Outcome Boundary Missing
```

本阶段需要判断未来：

```text
Assistant Trace
```

应该：

### 方案 1

直接复用 Chat Outcome。

### 方案 2

单独产生 Trace Outcome。

### 方案 3

Trace 从统一 Assistant Outcome Read Model 查询。

比较：

```text
一致性
重复存储
查询复杂度
安全边界
未来分页
```

推荐一个方向，但不要实现。

---

# 二十一、测试

新增：

```text
tests/test_assistant_outcome_contract_audit.py
```

至少测试：

```text
SUCCESS projection
EMPTY projection
REFUSED projection
FAILED projection
HTTP 200 + tool failure
retry success
retry exhausted
empty RAG
refusal
LLM failure
```

这些测试只验证：

> Contract 定义是否能够覆盖现有行为。

不要修改生产代码。

---

# 二十二、文档

新增：

```text
docs/evaluation/phase-3.12-step-62-assistant-outcome-contract-audit.md
```

内容：

## 1. Scope

```text
Contract audit only
No production implementation
```

## 2. Current Behavior

```text
SUCCESS
EMPTY
REFUSED
FAILED
```

## 3. Outcome Definitions

明确每个状态。

## 4. Precedence

明确判定优先级。

## 5. HTTP Mapping

明确 Business Outcome 与 HTTP status 的关系。

## 6. Route Mapping

明确 route ≠ outcome。

## 7. Execution Mapping

明确 LLM / Tool / RAG / Executor ≠ Assistant Outcome。

## 8. Security

字段白名单。

## 9. Candidate Contract

Option A / B。

## 10. Recommendation

只给未来设计建议。

## 11. Current Limitations

明确当前系统无法表达什么。

---

# 二十三、测试命令

执行：

```powershell
python -m pytest -q tests/test_assistant_outcome_contract_audit.py
```

然后：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

如果 lint unavailable：

```text
lint = unavailable
```

不要安装新工具。

---

# 二十四、Git Diff

执行：

```powershell
git status --short
git diff --stat
git diff
```

必须确认：

```text
Production code changes = 0
API contract changes = 0
DB schema changes = 0
Index changes = 0
Prompt changes = 0
```

如果出现 production code 修改：

**立即 STOP 并报告。**

---

# 二十五、最终报告

严格按照：

```text
Phase 3.12 Step 62 完成报告

1. Audit Scope
2. Current Outcome Signals
3. SUCCESS Definition
4. EMPTY Definition
5. REFUSED Definition
6. FAILED Definition
7. Outcome Precedence
8. HTTP Mapping
9. Route Mapping
10. LLM / Tool / RAG / Executor Mapping
11. Metadata Classification
12. Error Classification
13. Security Boundary
14. Projection Cases
15. Option A vs Option B
16. Recommended Future Contract
17. Trace Integration Recommendation
18. Tests
19. DB Tests
20. compileall
21. Git Diff
22. Production Code Changes
23. DB Schema Changes
24. Current Limitations

Phase 3.12 Step 62 STOP
```

---

# 二十六、强制 STOP

完成后立即停止。

不要实现：

```text
Outcome DTO
Outcome API
Trace Outcome
Pagination
Unified Timeline
Conversation
Memory
Agent
MCP
OpenTelemetry
Dashboard
Cost Tracking
```

只完成 Contract Audit。

等待下一步指令。
