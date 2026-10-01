# Phase 3.12 Step 92 — Regression Matrix Baseline Gate Contract Freeze

继续 Phase 3.12。

当前状态：

```text
Step 89
  ↓
MatrixExecutionSummary
  ↓
Step 90
  ↓
MatrixExecutionBaseline
  ↓
Step 91
  ↓
MatrixBaselineGateResult
  ↓
PASS / DRIFT
```

Step 91 已完成：

```text
Gate = PASS
Offline = 375 / 356 / 19 / 0 / 0 / exit 0 / PASS
DB      = 180 / 180 /  0 / 0 / 0 / exit 0 / PASS
Matrix  = 555 / PASS
Residue = 0
```

现在只执行：

# Step 92：冻结 Baseline Gate Contract

---

# 一、目标

把 Step 91 已经验证通过的 Gate 规则正式冻结。

本阶段只做：

```text
Contract
+
Regression Tests
+
Documentation
```

不重新执行 Matrix。

不重新运行 DB suite。

不刷新 Baseline。

不接 CI。

---

# 二、严格范围

允许修改：

```text
tests/test_assistant_trace_timeline_regression.py
docs/evaluation/
```

允许新增：

```text
docs/evaluation/phase-3.12-step-92-matrix-baseline-gate-contract.md
```

如果现有 Step 91 文档适合直接追加，可以不新增文件。

禁止：

```text
backend/
生产代码
API
DB schema
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
新的 pytest framework
新的 Runner
新的 Baseline
新的数据库表
CI pipeline
GitHub Action
网络调用
LLM
```

---

# 三、冻结 Gate DTO

保持：

```python
MatrixBaselineGateResult
```

不重新设计。

Contract 固定为：

```text
status
drifts
current
baseline
```

要求：

```text
frozen / immutable
```

---

# 四、冻结 Status Contract

唯一允许：

```text
PASS
DRIFT
```

禁止新增：

```text
WARNING
UNKNOWN
PARTIAL
SKIPPED
STALE
```

如果未来需要这些状态，必须新阶段明确设计。

---

# 五、冻结 Drift Contract

Step 91 当前 drift 类型固定：

```text
NO_BASELINE_DRIFT
OFFLINE_EXECUTION_DRIFT
DB_EXECUTION_DRIFT
MATRIX_TOTAL_DRIFT
MATRIX_STATUS_DRIFT
DB_RESIDUE_DRIFT
```

本阶段测试：

```text
drift type set
```

必须与上述完全一致。

禁止：

```text
PERFORMANCE_DRIFT
DURATION_DRIFT
COLLECTABILITY_DRIFT
NETWORK_DRIFT
LLM_DRIFT
```

---

# 六、冻结 PASS 条件

Gate PASS 必须严格等价于：

```text
current.offline == baseline.offline
AND
current.db == baseline.db
AND
current.matrix_total == baseline.matrix_total
AND
current.matrix_status == baseline.matrix_status
AND
current.db_residue == baseline.db_residue
AND
current.db_residue == 0
```

不能出现：

```text
>=
<=
容差
百分比
通过率阈值
允许 skipped 差异
```

---

# 七、冻结 DRIFT 条件

以下任一变化：

```text
Offline execution
DB execution
Matrix total
Matrix status
DB residue
```

都必须产生：

```text
DRIFT
```

---

# 八、冻结 Duration 语义

明确：

```text
duration_seconds
```

永远：

```text
NOT PART OF BASELINE
NOT PART OF EQUALITY
NOT PART OF DRIFT
NOT PART OF GATE STATUS
```

增加一个 Contract Test：

```text
duration changes only
→ PASS
```

---

# 九、冻结 Node-hosted Boundary

继续保持：

```text
OFFLINE_EXECUTION_CONTRACT
```

属于：

```text
NODE_HOSTED_CONTRACT_CATEGORIES
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

增加 Contract Test：

```text
node-hosted category
not in execution matrix
```

---

# 十、冻结 Baseline Read-only

增加测试：

```text
evaluate_matrix_baseline_gate()
```

执行前后：

```text
MATRIX_EXECUTION_BASELINE
```

必须完全一致。

Gate：

```text
READ ONLY
```

禁止：

```text
current → baseline
```

---

# 十一、冻结 Current Read-only

Gate 执行前后：

```text
current
```

必须保持完全一致。

不能：

```text
current.status = ...
```

不能：

```text
current.db_residue = ...
```

不能修改 nested DTO。

---

# 十二、冻结 Drift Determinism

相同输入：

```text
current
baseline
```

连续调用：

```text
evaluate_matrix_baseline_gate()
```

要求：

```text
result1 == result2
```

包括：

```text
status
drifts
current
baseline
```

---

# 十三、冻结 Composite Drift

测试：

```text
offline drift
+
db drift
+
residue drift
```

要求：

```text
DRIFT
```

并且所有对应 drift type 都存在。

不能只返回第一个错误。

---

# 十四、冻结 No Drift

完全一致：

```text
current == baseline
db_residue == 0
```

必须：

```text
status = PASS
drifts = ("NO_BASELINE_DRIFT",)
```

---

# 十五、冻结 Residue Safety

测试：

```text
current.db_residue = 1
baseline.db_residue = 1
```

即：

```text
current == baseline
```

但：

```text
db_residue != 0
```

仍然必须：

```text
DRIFT
```

这是安全边界。

---

# 十六、Security Contract

Gate DTO / repr / nested DTO 不允许出现：

```text
api_key
password
database_url
authorization
prompt
messages
sql
rag_chunk
tool_args
raw_response
```

继续复用 Step 91 的 security assertions。

不要重新设计 security scanner。

---

# 十七、禁止测试真实执行

本阶段：

```text
pytest execution = 0
DB execution = 0
Network = 0
LLM = 0
```

所有新增测试使用：

```text
synthetic MatrixExecutionSummary
synthetic MatrixExecutionBaseline
```

不要调用：

```text
_matrix_execution_summary()
```

不要读取真实 PostgreSQL。

不要触发 Step 89 cache。

---

# 十八、测试数量

建议：

```text
10～15 tests
```

重点覆盖：

```text
status
drift types
PASS
DRIFT
duration
node-hosted
immutability
determinism
composite drift
residue safety
security
baseline read-only
```

不要重复 Step 91 已有测试。

Step 92 应该是：

```text
Contract freeze
```

不是再次做 Step 91。

---

# 十九、Documentation

新增或更新：

```text
docs/evaluation/phase-3.12-step-92-matrix-baseline-gate-contract.md
```

明确：

```text
Step 92 Contract Freeze
```

至少记录：

```text
Gate Result:
PASS | DRIFT
```

以及：

```text
Drift Types:
NO_BASELINE_DRIFT
OFFLINE_EXECUTION_DRIFT
DB_EXECUTION_DRIFT
MATRIX_TOTAL_DRIFT
MATRIX_STATUS_DRIFT
DB_RESIDUE_DRIFT
```

并记录：

```text
Duration excluded
Node-hosted excluded
Baseline immutable
Current immutable
No automatic refresh
No execution in Step 92
```

---

# 二十、不要修改 Step 90 Baseline

绝对不要修改：

```text
MATRIX_EXECUTION_BASELINE
```

Step 90 是：

```text
Frozen Baseline
```

Step 92 只冻结：

```text
Gate semantics
```

不是更新 Baseline。

---

# 二十一、测试命令

只运行：

```powershell
python -m pytest -q tests/test_assistant_trace_timeline_regression.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

不要运行：

```powershell
python -m pytest -q
```

不要运行：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

不要重新执行 Matrix。

---

# 二十二、完成报告

严格输出：

```text
Phase 3.12 Step 92 COMPLETE

1. 修改文件
2. 新增文件
3. Gate Status Contract
4. Drift Type Contract
5. PASS Contract
6. Duration Contract
7. Node-hosted Boundary
8. Baseline Immutability
9. Current Immutability
10. Determinism
11. Composite Drift
12. Residue Safety
13. Security
14. Test Result
15. compileall
16. Matrix 是否执行
17. DB / Network / LLM
18. Git Diff
19. 当前限制

Phase 3.12 Step 92 READY
Phase 3.12 Step 92 STOP
```

如果发现 Step 91 的 Gate 行为与上述 Contract 不一致：

**不要修改 Step 91 来强行通过。**

先报告：

```text
Phase 3.12 Step 92 BLOCKED
```

并说明具体冲突。

---

# 二十三、硬停止

完成 Step 92 后：

**立即 STOP。**

不要：

```text
CI
GitHub Actions
Baseline Refresh
Dashboard
Prometheus
OpenTelemetry
Performance Optimization
```

等待下一步指令。
