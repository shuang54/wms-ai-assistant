# Phase 3.12 Step 99：Timeline Source ID 隔离断言修正

## 一、阶段目标

Step 98 首次真实 GitHub Actions 已证明：

```text
Workflow
  ↓
PostgreSQL
  ↓
pgvector
  ↓
init_db
  ↓
Matrix
  ↓
Gate
```

全部正常。

唯一失败：

```text
tests/test_assistant_timeline_concurrency_db_e2e.py
::TestTimelineConcurrency::test_case_e_ten_concurrent_requests
```

失败原因：

当前测试错误地要求：

```text
source_id
```

跨：

```text
LLM
Tool
RAG
Outcome
```

四张表全局唯一。

但 Step 73 已冻结 Contract：

```text
source_id
=
对应持久化记录的 source-table PK
```

因此不同表：

```text
llm_usage_record.id = 1
tool_execution_record.id = 1
rag_execution_record.id = 1
assistant_outcome_record.id = 1
```

完全合法。

本阶段只修正：

> **Timeline concurrency test 的错误隔离判据。**

---

# 二、严格范围

允许修改：

```text
tests/test_assistant_timeline_concurrency_db_e2e.py
```

允许新增：

```text
tests/test_assistant_timeline_concurrency_db_e2e.py
```

内部测试辅助逻辑。

允许更新：

```text
docs/evaluation/phase-3.12-step-99-timeline-source-id-fix.md
```

以及必要的测试说明。

---

# 三、绝对禁止

不要修改：

```text
backend/app/
```

特别禁止：

```text
Timeline Query Service
Trace Query Service
Timeline DTO
LLMUsageRecord
ToolExecutionRecord
RagExecutionRecord
AssistantOutcomeRecord
```

禁止修改：

```text
MatrixExecutionBaseline
MATRIX_EXECUTION_BASELINE
MatrixBaselineGateResult
evaluate_matrix_baseline_gate()
compare_matrix_execution_baseline()
adapt_gate_result_to_exit_code()
scripts/run_matrix_gate.py
.github/workflows/observability-matrix-gate.yml
```

禁止：

```text
Baseline Refresh
Baseline 修改
跳过 DB test
减少测试数量
关闭 concurrency test
全局重新生成 source_id
增加 event_id
增加 sequence
修改 Timeline Contract
```

---

# 四、正确的隔离原则

Timeline：

```text
source
+
source_id
```

共同决定来源身份。

例如：

```text
("llm_usage", 1)
("tool_execution", 1)
("rag_execution", 1)
("assistant_outcome", 1)
```

是四个不同的 source record。

因此：

### 错误

```python
assert source_ids.isdisjoint(all_source_ids)
```

### 正确思路

使用：

```text
(source, source_id)
```

作为 identity。

例如：

```python
source_keys = {
    (event.source, event.source_id)
}
```

---

# 五、Concurrency Isolation

`test_case_e_ten_concurrent_requests` 的真正目标是：

> 10 个并发请求之间不能串数据。

因此应该验证：

```text
Request A
    ↓
Timeline A
    ↓
所有 event 的 assistant_request_id == A

Request B
    ↓
Timeline B
    ↓
所有 event 的 assistant_request_id == B
```

而不是要求：

```text
source_id
```

跨表全局唯一。

---

# 六、必须保留的验证

修正后仍然必须验证：

### 1. Request Isolation

```text
Timeline A
≠
Timeline B
```

通过：

```text
assistant_request_id
```

判断。

---

### 2. Source Identity

同一个：

```text
(source, source_id)
```

不能错误归属于另一个：

```text
assistant_request_id
```

---

### 3. Event Count

10 个并发请求的：

```text
LLM
Tool
RAG
Outcome
```

事件数量必须符合当前业务行为。

不要改变既有期望数量。

---

### 4. Source ID 不要求跨表唯一

明确测试：

```text
llm_usage.id == tool_execution.id
```

即使发生：

**也不能判定为跨请求污染。**

---

# 七、不要改变生产 Contract

Step 73 已冻结：

```text
source_id = persisted source record PK
```

因此：

```text
LLM source_id
Tool source_id
RAG source_id
Outcome source_id
```

可以重复。

本阶段不能因为测试失败而引入：

```text
global event id
sequence
UUID
span_id
```

---

# 八、Regression

先运行目标测试：

```powershell
python -m pytest -q tests/test_assistant_timeline_concurrency_db_e2e.py
```

要求：

```text
10 concurrent requests PASS
```

然后运行相关：

```powershell
python -m pytest -q `
  tests/test_assistant_trace_timeline_contract.py `
  tests/test_assistant_trace_timeline_regression.py `
  tests/test_assistant_timeline_concurrency_db_e2e.py
```

如果 PowerShell 多行命令存在兼容问题，可以拆成多个命令执行。

---

# 九、Matrix Regression

修复完成后必须重新执行：

```powershell
$env:RUN_DB_TESTS="1"; python scripts/run_matrix_gate.py
```

要求：

```text
Offline:
375 total
356 passed
19 skipped
0 failed
0 errors

DB:
180 total
180 passed
0 skipped
0 failed
0 errors

Matrix:
555
PASS

DB residue:
0
```

注意：

如果测试数量因为 Step 99 新增测试而发生变化：

**不要自动修改 baseline。**

先报告实际数量与冻结 baseline 的差异。

---

# 十、真实 CI

本阶段不要修改 Workflow。

如果本地 Matrix Gate：

```text
PASS
```

再提交 Step 99 的测试修复，让：

```text
push
```

触发真实 GitHub Actions。

目标：

```text
GitHub Actions
    ↓
Matrix
    ↓
180/180
    ↓
Gate PASS
    ↓
exit 0
```

---

# 十一、DB Residue

真实 DB Matrix 完成后确认：

```text
llm_usage_record = 0
tool_execution_record = 0
rag_execution_record = 0
assistant_outcome_record = 0
```

不能因为修复测试而引入新的 residue。

---

# 十二、禁止修改 Baseline

Step 98 暴露的是：

```text
test assertion defect
```

不是：

```text
expected behavior changed
```

因此：

```text
MATRIX_EXECUTION_BASELINE
```

必须保持：

```text
offline = 375/356/19
db = 180/180
matrix_total = 555
status = PASS
residue = 0
```

---

# 十三、Git Diff

完成后检查：

```powershell
git status --short
git diff --stat
git diff -- tests/test_assistant_timeline_concurrency_db_e2e.py
git diff -- backend
git diff -- .github
```

必须确认：

```text
backend = unchanged
.github = unchanged
baseline = unchanged
Gate = unchanged
Adapter = unchanged
```

---

# 十四、最终文档

新增：

```text
docs/evaluation/phase-3.12-step-99-timeline-source-id-fix.md
```

记录：

```text
问题：
Step 98 GitHub Actions 在全新 PostgreSQL 上发现 concurrency test failure。

根因：
测试错误要求 source_id 跨 source table 全局唯一。

正确 Contract：
(source, source_id) 才是 source record identity。
assistant_request_id 才是跨请求隔离依据。

修复：
只修改测试断言。

Production code:
unchanged

Baseline:
unchanged
```

---

# 十五、如果修复后仍然失败

立即停止。

不要：

```text
修改生产代码
修改 baseline
修改 Timeline Contract
关闭测试
```

报告：

```text
Step 99 BLOCKED

Failure:
...

Root cause:
...

Production code changed:
NO
```

---

# 十六、完成报告

严格输出：

```text
Phase 3.12 Step 99 COMPLETE

1. Failure Root Cause
2. Test Fix
3. Source ID Contract
4. Request Isolation
5. Concurrency Result
6. Targeted Tests
7. Matrix Result
8. DB Residue
9. GitHub Actions
10. Baseline
11. Production Code
12. Git Diff
13. 当前限制

Phase 3.12 Step 99 READY
Phase 3.12 Step 99 STOP
```

---

# 十七、硬停止

完成 Step 99 后立即停止。

不要进入：

```text
Step 100
Baseline Refresh
PR Comment
Dashboard
Telemetry
OpenTelemetry
Prometheus
```

等待下一步指令。
