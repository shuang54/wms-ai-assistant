# Phase 3.12 Step 91 — Regression Matrix Actual Baseline Regression Gate

继续 Phase 3.12。

当前状态：

* Step 89：Regression Matrix 已有实际执行结果
* Step 90：`MatrixExecutionBaseline` 已冻结
* 当前 Baseline：

  * Offline：375 total / 356 passed / 19 skipped / 0 failed / 0 errors / exit 0 / PASS
  * DB：180 total / 180 passed / 0 skipped / 0 failed / 0 errors / exit 0 / PASS
  * Matrix total：555
  * DB residue：0

现在只执行：

# Step 91：建立 Regression Matrix Actual Baseline Gate

---

## 一、目标

建立：

```text
Step 89 Actual Execution
        ↓
Step 90 Frozen Baseline
        ↓
Step 91 Regression Gate
        ↓
PASS / DRIFT
```

本阶段不重新执行 pytest。

不重新运行：

```text
18 Offline files
15 DB files
```

不创建新的 Runner。

不创建新的 Regression Framework。

只对已经存在的：

```text
MatrixExecutionSummary
MatrixExecutionBaseline
```

进行比较。

---

# 二、严格范围

允许修改：

```text
tests/test_assistant_trace_timeline_regression.py
docs/evaluation/
```

允许新增：

```text
docs/evaluation/phase-3.12-step-91-matrix-baseline-gate.md
```

如果现有 regression 文档适合直接追加，可以不新增文件。

禁止：

```text
backend/
生产代码
DB schema
API
Observability API
Trace
Timeline
RAG
Tool
T2SQL
Router
Orchestrator
```

禁止新增：

```text
新的 pytest runner
新的 regression framework
新的 baseline 文件
新的数据库表
新的 persistence
新的 network call
DeepSeek
```

---

# 三、不要重新执行 Matrix

本阶段必须明确：

```text
pytest execution = 0
DB suite execution = 0
LLM = 0
Network = 0
```

只使用 Step 89 已有的实际结果对象/辅助函数。

如果现有实现已经有：

```text
_matrix_execution_summary()
```

直接复用。

不要重新实现执行逻辑。

---

# 四、Regression Gate

新增一个纯函数，例如：

```python
evaluate_matrix_baseline_gate(
    current: MatrixExecutionSummary,
    baseline: MatrixExecutionBaseline,
) -> ...
```

具体名称根据现有代码风格决定。

返回值可以是简单 immutable DTO，例如：

```text
MatrixBaselineGateResult
```

建议字段：

```text
status
drifts
current
baseline
```

其中：

```text
status = PASS | DRIFT
```

---

# 五、Gate 必须检查

## 1. Offline Execution

比较：

```text
current.offline
baseline.offline
```

至少检查：

```text
total
passed
skipped
failed
errors
exit_code
status
```

如果任意字段发生变化：

```text
OFFLINE_EXECUTION_DRIFT
```

---

## 2. DB Execution

比较：

```text
current.db
baseline.db
```

至少检查：

```text
total
passed
skipped
failed
errors
exit_code
status
```

如果任意字段发生变化：

```text
DB_EXECUTION_DRIFT
```

---

## 3. Matrix Total

比较：

```text
current.matrix_total
baseline.matrix_total
```

不同：

```text
MATRIX_TOTAL_DRIFT
```

---

## 4. Matrix Status

比较：

```text
current.matrix_status
baseline.matrix_status
```

不同：

```text
MATRIX_STATUS_DRIFT
```

---

## 5. DB Residue

比较：

```text
current.db_residue
baseline.db_residue
```

不同：

```text
DB_RESIDUE_DRIFT
```

并且：

```text
current.db_residue != 0
```

必须导致：

```text
DRIFT
```

---

# 六、Gate PASS 条件

只有全部满足：

```text
offline = baseline.offline
db = baseline.db
matrix_total = baseline.matrix_total
matrix_status = baseline.matrix_status
db_residue = baseline.db_residue
db_residue == 0
```

才：

```text
PASS
```

否则：

```text
DRIFT
```

不要使用：

```text
“基本一致”
“允许少量变化”
“通过率差不多”
```

这是 Frozen Regression Gate。

---

# 七、Duration 不参与 Gate

明确：

```text
duration_seconds
```

不参与：

```text
baseline equality
drift detection
gate status
```

例如：

```text
9.15s
```

变成：

```text
12.50s
```

不能产生：

```text
DRIFT
```

---

# 八、Node-hosted Contract 不参与执行 Gate

保持 Step 86～90 的设计：

```text
OFFLINE_EXECUTION_CONTRACT
```

仍然：

```text
node-hosted
```

不进入：

```text
FILES
CATEGORIES
offline suite
DB suite
MatrixExecutionSummary
MatrixExecutionBaseline
```

但是其 Contract Audit 仍然属于 Regression Matrix 的结构完整性范围。

不要修改这一边界。

---

# 九、Baseline 不允许自动更新

非常重要。

如果：

```text
current != baseline
```

只能：

```text
DRIFT
```

不能：

```text
自动更新 baseline
```

不能：

```text
current → baseline
```

不能：

```text
覆盖 MATRIX_EXECUTION_BASELINE
```

本阶段没有 baseline refresh。

---

# 十、Synthetic Drift Tests

不重新执行真实测试。

使用 synthetic DTO 验证 Gate：

### Case A

完全一致：

```text
→ PASS
```

### Case B

Offline passed：

```text
356 → 355
```

预期：

```text
OFFLINE_EXECUTION_DRIFT
DRIFT
```

### Case C

DB failed：

```text
0 → 1
```

预期：

```text
DB_EXECUTION_DRIFT
DRIFT
```

### Case D

Matrix total：

```text
555 → 556
```

预期：

```text
MATRIX_TOTAL_DRIFT
DRIFT
```

### Case E

Matrix status：

```text
PASS → DRIFT
```

预期：

```text
MATRIX_STATUS_DRIFT
DRIFT
```

### Case F

DB residue：

```text
0 → 1
```

预期：

```text
DB_RESIDUE_DRIFT
DRIFT
```

### Case G

仅 duration 改变：

```text
9.15 → 20.00
```

预期：

```text
PASS
```

### Case H

多个 drift 同时发生：

例如：

```text
offline changed
db changed
residue changed
```

要求：

```text
status = DRIFT
```

并且：

```text
drifts
```

包含所有实际 drift 类型。

---

# 十一、Determinism

相同：

```text
current
baseline
```

连续执行：

```text
evaluate_matrix_baseline_gate()
```

结果必须完全一致。

禁止：

```text
time
random
network
DB
pytest execution
```

---

# 十二、Immutability

如果 Gate Result 是 DTO：

要求：

```text
frozen=True
```

或者复用现有 immutable DTO 风格。

Gate 不得修改：

```text
current
baseline
```

特别测试：

```text
baseline remains unchanged
```

---

# 十三、Security

Gate Result 不得包含：

```text
API key
password
database URL
authorization
prompt
SQL
RAG chunks
tool args
raw response
```

这里只允许：

```text
execution summary
baseline
drift types
status
```

---

# 十四、测试数量

新增约：

```text
8～12 tests
```

重点是 Contract coverage。

不要为了数量重复测试。

---

# 十五、不要重新运行 Matrix

完成代码后只运行：

```powershell
python -m pytest -q tests/test_assistant_trace_timeline_regression.py
```

然后：

```powershell
python -m compileall backend tests scripts
```

不要运行：

```powershell
python -m pytest -q
```

不要：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

本阶段只验证 Gate 逻辑。

---

# 十六、Documentation

新增或更新：

```text
docs/evaluation/phase-3.12-step-91-matrix-baseline-gate.md
```

记录：

```text
Step 91 — Regression Matrix Actual Baseline Gate
```

至少说明：

```text
Current Actual:
Offline 375 / 356 / 19 / 0 / 0 / exit 0 / PASS
DB      180 / 180 / 0 / 0 / 0 / exit 0 / PASS
Matrix  555 / PASS
Residue 0
```

Frozen Baseline：

```text
same values
```

Gate：

```text
PASS
```

明确：

```text
No test execution in Step 91
No DB execution in Step 91
No LLM
No network
No baseline update
```

---

# 十七、Regression Matrix 不重新设计

继续复用：

```text
FILES
CATEGORIES
NODE_HOSTED_CONTRACT_CATEGORIES
NODE_HOSTED_REPRESENTATIVE_NODES
_offline_suite
_db_suite
MatrixExecutionSummary
MatrixExecutionBaseline
```

不要创建第二套：

```text
RegressionMatrix
RegressionRunner
ExecutionMatrix
BaselineMatrix
```

---

# 十八、完成报告

完成后只报告：

```text
Phase 3.12 Step 91 COMPLETE

1. 修改文件
2. 新增文件
3. Gate Contract
4. Current Actual
5. Frozen Baseline
6. Drift Types
7. PASS / DRIFT
8. Synthetic Drift Tests
9. Immutability
10. Security
11. Test Result
12. compileall
13. Matrix 是否重新执行
14. DB / Network / LLM
15. Git Diff
16. 当前限制

Phase 3.12 Step 91 READY
Phase 3.12 Step 91 STOP
```

如果发现：

```text
Step 89 Actual
≠
Step 90 Baseline
```

不要修改 Baseline。

只报告：

```text
DRIFT
```

---

# 十九、硬停止

完成 Step 91 后：

**立即 STOP。**

不要进入 Step 92。

不要重新跑 Matrix。

不要刷新 Baseline。

不要做性能优化。

不要做 Dashboard。

不要接 Prometheus / OpenTelemetry。

等待下一步指令。
