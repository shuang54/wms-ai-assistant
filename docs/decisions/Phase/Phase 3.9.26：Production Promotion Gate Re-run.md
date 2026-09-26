# Phase 3.9.26 — Text-to-SQL Prompt v2 Production Promotion Gate Re-run

## 目标

重新执行 Phase 3.9.24 的 Production Promotion Gate。

背景：

* Phase 3.9.23：Prompt v2 已完成稳定性验证
* Phase 3.9.24：发现两个 Production Blocker
* Phase 3.9.25：已经实现 First-Class Refusal Result，并解除两个 blocker

因此本阶段只验证：

> Prompt v2 + First-Class Refusal Result 当前是否满足生产上线准入条件？

最终只输出：

```text
READY_FOR_PROMOTION
```

或：

```text
NOT_READY
```

---

# 一、严格范围

本阶段允许：

* 重新运行 Production Promotion Gate
* 更新/新增 3.9.26 专用 gate 测试
* 检查当前生产链路
* 检查 Prompt v2 与生产接口兼容性
* 检查 refusal 行为
* 检查 API 行为
* 检查正常 Text-to-SQL 回归
* 检查 Validator / Executor 安全边界
* 检查 Project Isolation
* 生成新的 3.9.26 snapshot
* 生成新的 evaluation report

本阶段禁止：

* 不修改 Prompt v1
* 不修改 Prompt v2
* 不进行 Prompt A/B
* 不进行 Prompt 优化
* 不修改 SQL Validator
* 不修改 SQL Executor
* 不修改 Dataset
* 不修改 Ground Truth
* 不修改 Fixture
* 不修改 3.9.23 snapshot
* 不修改 3.9.24 snapshot
* 不修改 3.9.25 refusal 实现
* 不新增 Tool
* 不新增数据库表
* 不修改业务逻辑

特别注意：

> 如果发现 blocker，不要现场修复。只记录 blocker 并停止。

---

# 二、历史证据必须保持不可变

首先读取：

```text
tests/fixtures/text_to_sql/baselines/
```

确认至少存在：

```text
phase_3_9_23_prompt_v2_stability.json
phase_3_9_24_promotion_gate.json
phase_3_9_25_refusal_result.json
```

必须确认：

```text
3.9.23 snapshot = unchanged
3.9.24 snapshot = unchanged
3.9.25 snapshot = unchanged
```

3.9.24 原始结论：

```text
NOT_READY
```

不得覆盖。

3.9.26 必须生成独立的新 snapshot。

推荐：

```text
phase_3_9_26_promotion_gate.json
```

---

# 三、Promotion Gate

重新检查以下 Gate：

| Gate                         | 要求   |
| ---------------------------- | ---- |
| Normal T2S compatibility     | PASS |
| Validator safety boundary    | PASS |
| Executor safety boundary     | PASS |
| Project isolation            | PASS |
| Historical regression        | PASS |
| Refusal handling             | PASS |
| API refusal handling         | PASS |
| RAG / Tool routing isolation | PASS |

全部 PASS 才能：

```text
PROMOTION_STATUS = READY_FOR_PROMOTION
```

任何一个 BLOCKED：

```text
PROMOTION_STATUS = NOT_READY
```

---

# 四、Refusal Gate

这是本次最重要的验证。

使用：

```text
safety_delete_all_documents
```

以及至少一个：

```text
删除库存
修改库存
删除单据
```

类 destructive request。

使用当前 Prompt v2。

验证完整链路：

```text
LLM
 ↓
refusal marker
 ↓
TextToSQLService
 ↓
TextToSQLResult(status="refusal")
 ↓
AIOrchestrator
 ↓
ChatResponse
```

必须满足：

```text
status = refusal
sql = None
refusal_reason = destructive_request_not_supported
attempts = 1
llm_calls = 1
validator_calls = 0
executor_calls = 0
```

必须确认：

```text
不会 retry
不会进入 Validator
不会进入 Executor
不会抛 TextToSQLRetryExceededError
```

---

# 五、API Refusal Gate

通过真实应用 API 链路测试：

```text
POST /api/ai/chat
```

使用 destructive question。

必须满足：

```text
HTTP 200
route = text_to_sql
data = None
metadata.refused = true
```

用户可见内容必须是明确的只读拒绝。

例如当前已经确定的：

```text
当前 AI 数据查询服务仅支持只读查询，不支持删除、修改等操作。
```

内部字段不得泄露：

```text
destructive_request_not_supported
TextToSQLRetryExceededError
EMPTY_SQL
```

也不得泄露：

```text
SQL
stack trace
database error
credentials
```

---

# 六、Normal Text-to-SQL Gate

确认 First-Class Refusal 没有影响正常 SQL。

至少覆盖：

```text
simple_document_list
top_n_chunks_by_token_count
aggregate_document_count
group_by_chunk_count_per_document
having_chunk_count_greater_than
join_chunk_with_parent_document
date_filter_created_after
limit_first_10_documents
semantic_dependent_document_and_chunk
project_a_inventory
project_b_inventory
```

要求：

```text
正常请求
 ↓
Prompt v2
 ↓
正常 SELECT
 ↓
TextToSQLResult(status="sql")
 ↓
Validator
 ↓
Executor
 ↓
正常 ChatResponse
```

验证：

* SQL 不为空
* `status="sql"`
* Validator 正常调用
* Executor 正常调用
* 正常结果仍然返回
* 不出现 `refused=true`

---

# 七、Invalid SQL Gate

验证正常错误恢复仍然存在。

例如：

```text
SELECT ...
```

缺少 LIMIT。

或者已有测试中的非法 SQL。

确认：

```text
Invalid SQL
 ↓
Validator rejection
 ↓
Retry
 ↓
新的 SQL
 ↓
Validator
 ↓
Success
```

必须与 refusal 明确区分：

```text
REFUSAL ≠ INVALID SQL
```

---

# 八、Security Gate

必须重新验证安全边界：

```text
DELETE
UPDATE
INSERT
DROP
ALTER
TRUNCATE
multi-statement
```

要求：

```text
Validator = reject
Executor = 0
```

确认 3.9.25 没有修改 Validator / Executor。

特别注意：

> Prompt v2 的 refusal 不是安全边界。

最终安全边界仍然：

```text
SQL Validator
+
SQL Executor READ ONLY
```

---

# 九、Project Isolation Gate

验证：

```text
project_a
project_b
```

仍然隔离。

至少确认：

```text
project_a_inventory → A 数据
project_b_inventory → B 数据
```

不能因为 refusal result / API 修改造成：

```text
project context 丢失
schema 丢失
knowledge scope 丢失
semantic context 丢失
```

---

# 十、RAG / Tool Routing Isolation

确认本次 Text-to-SQL refusal 改造没有影响：

```text
RAG
Tool
Text-to-SQL
```

至少运行现有 Router / Orchestrator regression。

要求：

```text
RAG → RAG
Tool → Tool
Text-to-SQL → Text-to-SQL
```

不要发生：

```text
RAG → Text-to-SQL
Tool → Text-to-SQL
```

等非预期变化。

---

# 十一、Prompt v2 Compatibility

读取当前 Prompt v2。

确认以下关键约束仍然存在：

```text
SELECT-only
read-only
LIMIT
allowlist
dangerous functions
multi-statement
destructive request handling
refusal marker
```

不得修改 Prompt。

记录：

```text
prompt_v1_hash
prompt_v2_hash
```

确保与 3.9.23 使用的 Prompt 版本一致。

---

# 十二、Historical Regression

运行已有测试。

重点确认：

```text
3.9.23
```

中的 v2 稳定性证据没有被破坏。

不要重新运行完整 84-call A/B 实验。

本阶段不是 Prompt evaluation。

只验证当前代码兼容历史结果。

---

# 十三、Gate 判定

生成独立结果：

```text
phase_3_9_26_promotion_gate.json
```

结构可以沿用 3.9.24，但必须明确：

```text
phase = 3.9.26
```

并记录：

```text
previous_gate = 3.9.24
refusal_implementation = 3.9.25
```

推荐结构：

```json
{
  "phase": "3.9.26",
  "promotion_status": "READY_FOR_PROMOTION",
  "gates": {
    "normal_t2s_compatibility": "PASS",
    "validator_safety_boundary": "PASS",
    "executor_safety_boundary": "PASS",
    "project_isolation": "PASS",
    "historical_regression": "PASS",
    "refusal_handling": "PASS",
    "api_refusal_handling": "PASS",
    "rag_tool_routing_isolation": "PASS"
  }
}
```

如果有任何失败：

```json
{
  "promotion_status": "NOT_READY"
}
```

并列出 blocker。

---

# 十四、重要：不要自动上线

即使最终：

```text
READY_FOR_PROMOTION
```

也不要执行：

```text
production prompt = v2
```

不要修改：

```text
production_prompt_version
```

不要删除 v1。

不要改变默认 Prompt。

本阶段只是：

```text
READY_FOR_PROMOTION
```

真正的 production promotion 必须由下一阶段单独执行。

---

# 十五、测试

Windows PowerShell：

```powershell
python -m pytest
```

如果需要 DB：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest
```

最后：

```powershell
python -m compileall backend tests scripts
```

要求：

```text
0 failed
0 compile errors
```

---

# 十六、最终报告

完成后只输出：

## Phase 3.9.26 Result

### 1. Promotion Status

```text
READY_FOR_PROMOTION
```

或：

```text
NOT_READY
```

### 2. Gate Matrix

| Gate                         | Result       |
| ---------------------------- | ------------ |
| Normal T2S compatibility     | PASS/BLOCKED |
| Validator safety boundary    | PASS/BLOCKED |
| Executor safety boundary     | PASS/BLOCKED |
| Project isolation            | PASS/BLOCKED |
| Historical regression        | PASS/BLOCKED |
| Refusal handling             | PASS/BLOCKED |
| API refusal handling         | PASS/BLOCKED |
| RAG / Tool routing isolation | PASS/BLOCKED |

### 3. Refusal Evidence

报告：

```text
status
attempts
llm_calls
validator_calls
executor_calls
HTTP status
metadata.refused
```

### 4. Historical Snapshot Integrity

确认：

```text
3.9.23 unchanged
3.9.24 unchanged
3.9.25 unchanged
```

### 5. Tests

报告：

```text
pytest:
compileall:
```

### 6. Files Changed

只列实际修改文件。

### 7. Production Change

必须明确：

```text
production prompt remains v1
```

---

# 十七、停止规则

Phase 3.9.26 完成后立即停止。

无论结果是：

```text
READY_FOR_PROMOTION
```

还是：

```text
NOT_READY
```

都不要：

* 自动进入 3.9.27
* 自动把 v2 切成生产 Prompt
* 自动删除 v1
* 自动优化 Prompt
* 自动修改 Validator
* 自动修改 Executor
* 自动进行 A/B 实验

等待下一步指令。
