# Phase 3.12 Step 73 — Trace / Timeline Contract Baseline 固化

## 一、阶段目标

基于 Step 64～72 已完成的 Trace / Timeline 能力：

```text
Assistant Trace
+
Grouped Timeline
+
Outcome
+
Read During Write
+
Concurrency
+
Trace ↔ Timeline Consistency
```

本阶段只完成：

> **把当前已经验证过的 Trace / Timeline Contract 固化成一份可长期回归的 Baseline。**

本阶段不是新功能开发。

**禁止修改生产逻辑。**

---

# 二、严格范围

允许：

```text
tests/
docs/evaluation/
docs/architecture.md
docs/decisions/
```

禁止修改：

```text
backend/
```

禁止：

```text
DB schema
migration
API implementation
Trace QueryService
Timeline QueryService
Trace DTO
Timeline DTO
LLM persistence
Tool persistence
RAG persistence
Outcome persistence
```

禁止进入：

```text
Unified Timeline
event_id
sequence
span_id
parent_event_id
global ordering
pagination
Conversation
Memory
Agent
MCP
OpenTelemetry
```

---

# 三、开始前阅读

阅读：

```text
docs/architecture.md

docs/evaluation/
    Phase 3.12 Step 64...
    Phase 3.12 Step 65...
    ...
    Phase 3.12 Step 72...

tests/
    test_assistant_trace_db_e2e.py
    test_assistant_timeline_db_e2e.py
    test_assistant_trace_timeline_consistency.py
    test_assistant_timeline_concurrency.py
    test_assistant_trace_timeline_read_during_write.py
```

如果实际文件名不同：

先搜索：

```text
assistant_trace
assistant_timeline
read_during_write
trace_timeline
```

不要假设文件名。

---

# 四、建立当前 Contract Matrix

新增：

```text
docs/evaluation/phase-3.12-trace-timeline-contract-baseline.md
```

建立一张 Contract Matrix。

至少包含：

| Contract                  | Current Behavior           | Verified By   |
| ------------------------- | -------------------------- | ------------- |
| Unknown Trace request     | 200 + empty + outcome=null | Step 68/69/72 |
| Unknown Timeline request  | 200 + four empty groups    | Step 68/69/72 |
| LLM Trace id              | Provider request ID        | Step 71/72    |
| LLM Timeline source_id    | LLM usage DB PK            | Step 71/72    |
| Tool Timeline source_id   | Tool DB PK                 | Step 69/72    |
| RAG Timeline source_id    | RAG DB PK                  | Step 69/72    |
| Outcome source_id         | Outcome DB PK              | Step 69/71/72 |
| Partial state             | legal                      | Step 72       |
| Missing outcome           | null                       | Step 69/71/72 |
| Outcome SUCCESS           | explicit only              | Step 71/72    |
| Outcome FAILED            | explicit only              | Step 71/72    |
| Outcome REFUSED           | explicit only              | Step 71/72    |
| Outcome EMPTY             | explicit only              | Step 71/72    |
| Cross-request isolation   | required                   | Step 70/71/72 |
| No fake outcome inference | required                   | Step 72       |
| Group ordering            | stable within source group | Step 69/70    |
| Global ordering           | NOT PROVIDED               | Step 65/72    |
| event_id                  | NOT PROVIDED               | Step 65/72    |
| sequence                  | NOT PROVIDED               | Step 65/72    |
| span_id                   | NOT PROVIDED               | Step 65/72    |
| pagination                | NOT PROVIDED               | Step 67/72    |
| transaction snapshot      | NOT PROVIDED               | Step 72       |

---

# 五、明确 Request ID 语义

文档必须单独写清楚：

## LLM

Trace 中：

```text
LLM segment request_id
=
Provider request ID
```

Assistant 关联使用：

```text
assistant_request_id
```

Timeline：

```text
source_id
=
llm_usage_record.id
```

---

## Tool / RAG

Trace 中：

```text
request_id
=
assistant_request_id
```

Timeline：

```text
source_id
=
对应 persistence record DB PK
```

不要把这些概念合并成一个“request_id”。

---

# 六、Partial State Contract

明确记录：

```text
HTTP 200
≠
完整最终快照
```

例如：

```text
LLM 已写入
Outcome 尚未写入
```

允许：

```text
Trace:
LLM = 1
RAG = 0
Tool = 0
Outcome = null
```

Timeline：

```text
LLM = 1
RAG = 0
Tool = 0
Outcome = null
```

禁止：

```text
根据 LLM 存在
→ 自动推断 SUCCESS
```

禁止：

```text
根据 Tool success
→ 自动推断 SUCCESS
```

禁止：

```text
根据 RAG result
→ 自动推断 EMPTY / SUCCESS
```

---

# 七、Outcome Contract

固定：

```text
SUCCESS
FAILED
REFUSED
EMPTY
```

只有：

```text
assistant_outcome_record
```

明确存在时才表达 outcome。

不存在：

```text
outcome = null
```

不要推断。

---

# 八、Source ID Contract

明确：

```text
source_id
```

是：

> 当前 persistence record 的数据库主键。

它：

```text
不是 event_id
不是 sequence
不是全局唯一时间线 ID
不是排序号
不是数组下标
不是 UUID
```

不要为当前 baseline 添加新的 ID。

---

# 九、Ordering Contract

当前只承诺：

```text
LLM:
created_at ASC, id ASC

Tool:
id ASC

RAG:
id ASC

Outcome:
最多一个
```

明确：

> 当前系统不提供跨 LLM / Tool / RAG / Outcome 的全局时间排序。

不要在文档中使用：

```text
first
second
third
chronological
```

描述跨组事件。

---

# 十、Read During Write Contract

记录 Step 72 已验证：

```text
写入过程中读取
→ partial state 合法
```

但：

```text
不保证 snapshot consistency
```

不保证：

```text
Trace 与 Timeline 在任意瞬间完全一致
```

不保证：

```text
跨表原子可见
```

只要求：

```text
request isolation
source_id correctness
contract validity
no fake completeness
```

---

# 十一、Security Contract

记录当前 Trace / Timeline JSON：

不得暴露：

```text
prompt
messages
system_prompt
user_prompt
SQL
query
embedding
similarity
tool arguments
tool result
raw response
exception message
database_url
API key
authorization
password
traceback
```

并且：

```text
unknown request
```

不得泄露其他 request 数据。

---

# 十二、增加 Contract Regression Test

新增：

```text
tests/test_assistant_trace_timeline_contract.py
```

这个测试必须：

```text
offline
DB = 0
network = 0
```

不要连接 PostgreSQL。

不要创建真实 request。

只测试：

```text
DTO / documented contract
```

至少覆盖：

1. Timeline top-level fields
2. Timeline event fields
3. Trace top-level fields
4. Outcome enum values
5. source_id type
6. absence of event_id
7. absence of sequence
8. absence of span_id
9. partial outcome nullable semantics
10. no sensitive field names

---

# 十三、Static Dependency Audit

对：

```text
tests/test_assistant_trace_timeline_contract.py
```

使用 AST 检查 executable imports。

不得出现：

```text
sqlalchemy
psycopg
redis
celery
kafka
backend.app.db
```

不要使用全文字符串扫描。

---

# 十四、测试命令

只运行：

```powershell
python -m pytest -q tests/test_assistant_trace_timeline_contract.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

不要运行：

```text
full pytest
RUN_DB_TESTS=1
DeepSeek
```

---

# 十五、Git Diff

检查：

```powershell
git status --short
git diff --stat
git diff -- backend
```

要求：

```text
backend = 0 modified
```

新增/修改只能是：

```text
tests/
docs/
```

---

# 十六、完成报告

完成后严格报告：

```text
Phase 3.12 Step 73 完成报告

1. Contract Matrix
2. Request ID Semantics
3. Partial State Contract
4. Outcome Contract
5. Source ID Contract
6. Ordering Contract
7. Read During Write Contract
8. Security Contract
9. 新增测试
10. 测试结果
11. Compileall
12. Backend Diff
13. DB / Network
14. 发现的问题
15. 当前限制

Phase 3.12 Step 73 READY
Phase 3.12 Step 73 STOP
```

如果发现当前实现与已验证 Contract 不一致：

```text
Phase 3.12 Step 73 NOT READY
```

只报告问题。

**不要修改生产代码。**

```

Step 73 完成后再决定是否进入下一步。
```
