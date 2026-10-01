# Phase 3.12 Step 74 — Trace / Timeline E2E Contract Regression 汇总

## 一、阶段目标

基于 Phase 3.12 Step 64～73 已完成的：

```text
Outcome
+
Trace
+
Grouped Timeline
+
HTTP API
+
DB E2E
+
Concurrency
+
Trace ↔ Timeline Consistency
+
Read During Write
+
Contract Baseline
```

本阶段只完成：

> 建立一个长期可重复运行的 Trace / Timeline Contract Regression 入口。

目标：

```text
历史已验证测试
        ↓
统一 Regression Suite
        ↓
Trace Contract
Timeline Contract
Isolation
Partial State
Outcome
Security
```

**本阶段不新增任何生产能力。**

---

# 二、严格范围

允许修改：

```text
tests/
docs/evaluation/
```

允许新增：

```text
tests/test_assistant_trace_timeline_regression.py

docs/evaluation/phase-3.12-trace-timeline-regression.md
```

如果现有测试结构已经有更合适的 regression 文件：

**优先复用，不重复创建。**

---

## 禁止修改

```text
backend/
DB schema
migration
API implementation
Trace DTO
Timeline DTO
Trace QueryService
Timeline QueryService
LLM persistence
Tool persistence
RAG persistence
Outcome persistence
```

禁止：

```text
event_id
sequence
span_id
parent_event_id
global ordering
pagination
transaction snapshot
```

禁止进入：

```text
Conversation
Memory
Agent
MCP
OpenTelemetry
```

---

# 三、开始前阅读

先阅读：

```text
tests/test_assistant_trace_db_e2e.py
tests/test_assistant_timeline_db_e2e.py
tests/test_assistant_trace_timeline_consistency.py
tests/test_assistant_timeline_concurrency.py
tests/test_assistant_trace_timeline_read_during_write.py
tests/test_assistant_trace_timeline_contract.py
```

以及：

```text
docs/evaluation/phase-3.12-trace-timeline-contract-baseline.md
docs/architecture.md
```

搜索：

```text
assistant-trace
assistant-timeline
Trace
Timeline
Outcome
source_id
assistant_request_id
```

---

# 四、Regression Suite 原则

本阶段不要复制已有测试逻辑。

目标只是：

```text
统一收口
```

优先使用 pytest：

```python
pytest
```

的：

```text
pytestmark
pytest.importorskip
```

或者项目当前已有的测试组织方式。

不要修改原有测试行为。

---

# 五、建立测试分类

Regression Suite 至少明确以下类别：

```text
1. API Contract
2. Trace Contract
3. Timeline Contract
4. Outcome
5. Partial State
6. Read During Write
7. Cross-request Isolation
8. Concurrency
9. Trace ↔ Timeline Consistency
10. Security
11. Source ID
12. Ordering
```

每类必须能够追溯到已有测试文件。

---

# 六、建立 Regression Matrix

新增文档：

```text
docs/evaluation/phase-3.12-trace-timeline-regression.md
```

建立：

| Area              | Existing Test              |          DB | Network | Coverage       |
| ----------------- | -------------------------- | ----------: | ------: | -------------- |
| Trace API         | existing trace E2E         |         yes |      no | API            |
| Timeline API      | timeline DB E2E            |         yes |      no | API            |
| Consistency       | Trace/Timeline consistency |         yes |      no | Cross-view     |
| Concurrency       | timeline concurrency       |         yes |      no | Isolation      |
| Read During Write | Step 72                    |         yes |      no | Partial        |
| Contract          | Step 73                    |          no |      no | DTO/API        |
| Security          | existing security cases    | yes/offline |      no | Sensitive data |

具体文件名以仓库实际情况为准。

---

# 七、Offline Contract Gate

Regression 入口必须始终包含：

```text
tests/test_assistant_trace_timeline_contract.py
```

它：

```text
DB = 0
Network = 0
```

必须保持离线。

---

# 八、DB Regression Gate

DB 回归必须继续使用：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest ...
```

不要使用：

```text
RUN_DB_TESTS=1 pytest
```

DB Regression 必须覆盖：

```text
Trace
Timeline
Outcome
Concurrency
Read During Write
Cross-request isolation
```

---

# 九、Real LLM 禁止

本阶段：

```text
DeepSeek calls = 0
```

所有 LLM 行为继续使用现有 Fake / Stub。

不要：

```text
重新跑真实 LLM
修改 API Key
修改 .env
```

---

# 十、Production DB 禁止

只允许：

```text
项目现有测试 PostgreSQL
```

禁止：

```text
生产数据库
开发数据库
真实 WMS 数据
```

---

# 十一、Regression 不新增测试语义

非常重要。

本阶段：

**不要新增新的 Contract。**

只能验证 Step 73 已经冻结的 Contract：

```text
partial state 合法
outcome 不推断
source_id = persistence PK
group ordering
request isolation
security
```

如果发现已有测试之间的断言不一致：

```text
停止
报告冲突
不要修改 production
不要自行选择一个 Contract
```

---

# 十二、DB Residue

Regression 完成后必须确认：

```text
ai_ops.llm_usage_record = 0
ai_ops.tool_execution_record = 0
ai_ops.rag_execution_record = 0
ai_ops.assistant_outcome_record = 0
```

清理必须：

```text
tracked request_id
step74- prefix
```

定向清理。

禁止：

```text
TRUNCATE
DELETE all
DROP
```

---

# 十三、Cross-request Isolation

Regression 必须覆盖至少：

```text
Request A
Request B
Request C
```

并确认：

```text
A response 不包含 B/C
B response 不包含 A/C
C response 不包含 A/B
```

同时：

```text
Timeline source_id
```

必须属于对应 request 的 persistence records。

---

# 十四、Partial State

Regression 至少覆盖：

```text
LLM-only
LLM+RAG
Tool-only
Outcome-present
```

明确：

```text
HTTP 200
≠
complete final snapshot
```

不得增加：

```text
inferred outcome
```

---

# 十五、Outcome

至少验证：

```text
SUCCESS
FAILED
REFUSED
EMPTY
null
```

其中：

```text
null
```

表示：

```text
assistant_outcome_record 不存在
```

不得根据：

```text
LLM
Tool
RAG
Executor
```

自动推断。

---

# 十六、Trace ↔ Timeline

Regression 必须验证：

### LLM

```text
Trace.llm_usage[].id
==
Timeline.llm_events[].source_id
```

### Tool

由于 Trace Tool 不暴露 DB PK：

只验证：

```text
count
status
content
order
```

### RAG

同理：

```text
count
fields
order
```

### Outcome

```text
Timeline.outcome_event.source_id
==
assistant_outcome_record.id
```

---

# 十七、Security

Regression 必须检查：

```text
prompt
messages
system_prompt
user_prompt
SQL
query
embedding
similarity
arguments
tool_result
raw_response
exception
database_url
api_key
authorization
password
traceback
```

不得出现在：

```text
Trace
Timeline
Outcome
```

响应中。

同时保留：

```text
prompt_tokens
completion_tokens
result_count
```

这些合法字段。

---

# 十八、Source ID

所有 Timeline：

```text
source_id
```

必须：

```text
int
not bool
not str
not UUID
not sequence
not index
```

并且：

```text
属于当前 request 对应 persistence table 的 PK 集合
```

---

# 十九、Ordering

只验证：

```text
LLM:
created_at ASC, id ASC

Tool:
id ASC

RAG:
id ASC

Outcome:
max 1
```

不要验证：

```text
LLM vs Tool vs RAG
```

之间的全局顺序。

不要生成：

```text
sequence
event_id
```

---

# 二十、Concurrency

复用 Step 70 已有测试。

至少保证：

```text
5 concurrent RAG
5 mixed RAG/Tool
3 T2SQL retry
3 refusal
10 mixed requests
```

如果现有测试已经覆盖：

**不要复制实现。**

Regression 只需要把它作为 gate。

---

# 二十一、Read During Write

复用 Step 72。

必须保持：

```text
partial state legal
```

不要求：

```text
Trace == Timeline
```

在写入进行中的每一次读取。

只要求：

```text
isolation
source_id correctness
contract validity
no fake completeness
```

---

# 二十二、Contract Drift Detection

Step 73 的：

```text
tests/test_assistant_trace_timeline_contract.py
```

继续作为：

```text
offline contract gate
```

如果未来有人修改：

```text
DTO
API
route
event fields
```

该测试应该首先失败。

不要为了通过测试而修改 Contract Baseline。

---

# 二十三、测试命令

### Step 1：Contract

```powershell
python -m pytest -q tests/test_assistant_trace_timeline_contract.py
```

### Step 2：Step 72

```powershell
python -m pytest -q tests/test_assistant_trace_timeline_read_during_write.py
```

### Step 3：Step 74 Regression

```powershell
python -m pytest -q tests/test_assistant_trace_timeline_regression.py
```

如果 Regression 文件只是 suite collector：

必须明确：

```text
offline
```

和：

```text
DB-gated
```

两种运行模式。

### Step 4：DB

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_assistant_trace_timeline_regression.py
```

---

# 二十四、Compile

执行：

```powershell
python -m compileall -q backend tests scripts
```

要求：

```text
0 errors
```

---

# 二十五、Git Diff

执行：

```powershell
git status --short
git diff --stat
git diff -- backend
```

要求：

```text
backend = 0
```

---

# 二十六、完成报告

严格：

```text
Phase 3.12 Step 74 完成报告

1. Regression Matrix
2. Offline Contract Gate
3. Trace
4. Timeline
5. Outcome
6. Partial State
7. Read During Write
8. Cross-request Isolation
9. Concurrency
10. Trace ↔ Timeline
11. Security
12. Source ID
13. Ordering
14. Tests
15. Compileall
16. DB Residue
17. Backend Diff
18. 发现的问题
19. 当前限制

Phase 3.12 Step 74 READY
Phase 3.12 Step 74 STOP
```

如果发现：

```text
历史测试之间 Contract 不一致
```

则：

```text
Phase 3.12 Step 74 NOT READY
```

只报告冲突。

**不要修改生产代码。**

---

# 二十七、硬停止

Step 74 完成后：

**立即 STOP。**

不要进入：

```text
Unified Timeline
event_id
sequence
span_id
parent_event_id
pagination
Conversation
Memory
Agent
MCP
OpenTelemetry
```

等待下一步指令。
