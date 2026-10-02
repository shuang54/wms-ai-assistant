# Phase 3.12 Step 100：CI Gate Contract Freeze & Repository Readiness Audit

## 一、阶段目标

Step 97～99 已经证明：

```text
GitHub Actions
    ↓
PostgreSQL / pgvector
    ↓
init_db
    ↓
Observability Matrix
    ↓
Baseline Gate
    ↓
CI Adapter
    ↓
exit 0
    ↓
GitHub Actions SUCCESS
```

本阶段只完成：

> **冻结 CI Gate Contract，并审计仓库是否已经具备作为稳定 CI Check 使用的条件。**

不要增加新的 CI 功能。

不要修改 Matrix。

不要修改 Gate。

不要修改 Adapter。

不要修改 Workflow。

---

# 二、严格范围

允许修改：

```text
tests/
docs/evaluation/
docs/architecture.md
```

必要时允许新增：

```text
tests/test_phase_3_12_ci_gate_contract.py
docs/evaluation/phase-3.12-step-100-ci-gate-contract.md
```

优先复用现有：

```text
tests/test_github_actions_matrix_gate.py
tests/test_matrix_ci_adapter.py
tests/test_matrix_gate_cli.py
tests/test_assistant_trace_timeline_regression.py
```

如果现有测试已经足够：

**不要新增测试文件。**

---

# 三、绝对禁止

禁止修改：

```text
.github/workflows/observability-matrix-gate.yml
backend/app/
scripts/run_matrix_gate.py
backend/app/services/matrix_ci_adapter.py
MATRIX_EXECUTION_BASELINE
MatrixExecutionBaseline
MatrixBaselineGateResult
evaluate_matrix_baseline_gate()
compare_matrix_execution_baseline()
```

禁止：

```text
Baseline Refresh
PR Comment
Dashboard
Telemetry
OpenTelemetry
Prometheus
Langfuse
Sentry
自动修复
自动重跑
Retry
```

不要设置 GitHub Branch Protection。

本阶段只做：

```text
Contract / Readiness Audit
```

不执行仓库管理设置。

---

# 四、冻结 CI Gate Contract

正式明确：

```text
MatrixExecutionBaseline
        ↓
evaluate_matrix_baseline_gate()
        ↓
MatrixBaselineGateResult
        ↓
adapt_gate_result_to_exit_code()
        ↓
0 / 1
```

---

# 五、Gate PASS Contract

必须冻结：

```text
status = PASS
```

当且仅当：

```text
offline == baseline.offline
db == baseline.db
matrix_total == baseline.matrix_total
matrix_status == baseline.matrix_status
db_residue == baseline.db_residue
db_residue == 0
```

同时：

```text
drifts == ("NO_BASELINE_DRIFT",)
```

---

# 六、Gate DRIFT Contract

任何真实 Drift：

```text
OFFLINE_EXECUTION_DRIFT
DB_EXECUTION_DRIFT
MATRIX_TOTAL_DRIFT
MATRIX_STATUS_DRIFT
DB_RESIDUE_DRIFT
```

都必须：

```text
status = DRIFT
```

并最终：

```text
CI exit code = 1
```

不能：

```text
warning
continue
skip
success
```

---

# 七、CI Adapter Contract

继续保持：

```text
PASS  → 0
DRIFT → 1
```

Adapter：

```text
只读取 Gate Result.status
```

不得重新计算：

```text
baseline
drift
matrix
residue
```

不得：

```text
sys.exit()
```

由 CLI / GitHub Actions process boundary 负责退出。

---

# 八、Workflow Contract

Step 97 Workflow 已经真实运行成功。

冻结：

```text
workflow:
.github/workflows/observability-matrix-gate.yml
```

唯一 job：

```text
observability-matrix-gate
```

触发：

```text
push
pull_request
```

执行：

```text
python -m backend.app.db.init_db
python scripts/run_matrix_gate.py
```

数据库：

```text
pgvector/pgvector:pg16
```

Python：

```text
3.13
```

---

# 九、真实 CI Evidence

将 Step 99 的真实结果作为 Evidence：

```text
Run #2
run_id = 36949287263
commit = 73b3c71
result = success
```

以及：

```text
Run #3
run_id = 36949724371
commit = 510ab81
result = success
```

说明：

```text
Run #2
= Step 99 code fix

Run #3
= documentation-only follow-up
```

不要把 Run ID 写入生产代码。

只写 Evaluation 文档。

---

# 十、Baseline Contract

冻结：

```text
offline:
375 total
356 passed
19 skipped
0 failed
0 errors
exit_code=0
status=PASS

db:
180 total
180 passed
0 skipped
0 failed
0 errors
exit_code=0
status=PASS

matrix_total:
555

matrix_status:
PASS

db_residue:
0
```

强调：

> 这些是当前冻结的 Matrix Execution Baseline，不是永远不能变化的测试数量。

未来如果测试结构发生有意变化：

必须进入新的明确 Phase 更新 baseline。

---

# 十一、重要：不要把真实 CI Run 当 Baseline

必须明确：

```text
GitHub Run
    ≠
Baseline
```

Run 是：

```text
Evidence
```

Baseline 是：

```text
Expected Contract
```

因此：

```text
Run #2 PASS
```

只是证明：

```text
Current execution == Frozen baseline
```

不能自动：

```text
Run result → overwrite baseline
```

---

# 十二、DB Residue Contract

继续冻结：

```text
llm_usage_record = 0
tool_execution_record = 0
rag_execution_record = 0
assistant_outcome_record = 0
```

CI Matrix 是：

```text
temporary execution environment
```

不是：

```text
production data validation
```

---

# 十三、LLM / Network Contract

CI Gate：

```text
DeepSeek = 0
SiliconFlow = 0
OpenAI = 0
external API = 0
```

Workflow 不需要：

```text
API Key
Secrets
```

Matrix Gate 是：

```text
offline + local PostgreSQL evaluation
```

不是：

```text
LLM benchmark
```

---

# 十四、Concurrency Contract

正式记录 Step 99 修复后的规则：

### Request identity

```text
assistant_request_id
```

### Source identity

```text
(source, source_id)
```

### 禁止

```text
source_id global uniqueness
```

即：

```text
("llm_usage", 1)
("tool_execution", 1)
("rag_execution", 1)
("assistant_outcome", 1)
```

完全合法。

---

# 十五、CI Readiness Audit

检查：

### 1. Workflow

```text
唯一 workflow
唯一 job
无 retry
无 continue-on-error
无 || true
无 exit 0
```

### 2. Gate

```text
纯 baseline comparison
```

### 3. Adapter

```text
纯 status → exit code
```

### 4. CLI

```text
PASS → 0
DRIFT → 1
```

### 5. Database

```text
temporary CI PostgreSQL
```

### 6. Secrets

```text
0
```

### 7. Production

```text
backend diff = 0
```

---

# 十六、GitHub Required Status Check

本阶段**只做审计，不修改 GitHub Settings**。

检查当前仓库是否已经存在：

```text
Observability Matrix Gate
```

对应的 successful check。

如果无法通过当前工具读取 branch protection：

记录：

```text
Branch protection status:
NOT AUDITED
```

不要猜测。

GitHub 要求 required status check 必须在最新 commit 上成功，且 required check 可以通过 branch protection/ruleset 配置。

---

# 十七、不要自动开启 Required Check

本阶段禁止：

```text
Require status check
Protect main
Require PR review
Require branches up-to-date
```

这些属于 Repository Governance，不属于本阶段。

只报告：

```text
Workflow CI readiness = PASS
Repository branch protection = NOT CHANGED
```

---

# 十八、测试

优先运行已有：

```powershell
python -m pytest -q `
  tests/test_github_actions_matrix_gate.py `
  tests/test_matrix_ci_adapter.py `
  tests/test_matrix_gate_cli.py
```

然后：

```powershell
python -m pytest -q `
  tests/test_assistant_trace_timeline_contract.py `
  tests/test_assistant_trace_timeline_regression.py
```

最后：

```powershell
python -m compileall -q backend tests scripts
```

不要重新跑完整 DB Matrix。

Step 99 已经完成真实 DB Matrix + GitHub Actions 验证。

---

# 十九、如果需要新增 Contract Test

只验证：

```text
PASS → 0
DRIFT → 1
baseline unchanged
workflow path unchanged
```

不要重新实现 Gate。

---

# 二十、Documentation

新增：

```text
docs/evaluation/phase-3.12-step-100-ci-gate-contract.md
```

记录：

```text
1. CI Gate Contract
2. Baseline
3. Gate
4. Adapter
5. CLI
6. Workflow
7. PostgreSQL
8. Residue
9. LLM / Network
10. Concurrency Source Identity
11. Real GitHub Evidence
12. Required Status Check
13. Current Limitations
```

---

# 二十一、最终 Git Diff

检查：

```powershell
git status --short
git diff --stat
git diff -- backend
git diff -- .github
```

确认：

```text
backend = unchanged
workflow = unchanged
Gate = unchanged
Adapter = unchanged
CLI = unchanged
baseline = unchanged
DB schema = unchanged
```

允许：

```text
tests/
docs/
```

---

# 二十二、最终报告

严格：

```text
Phase 3.12 Step 100 COMPLETE

1. CI Gate Contract
2. PASS / DRIFT
3. Adapter
4. CLI
5. Workflow
6. Baseline
7. DB Residue
8. LLM / Network
9. Concurrency Contract
10. GitHub Evidence
11. Required Status Check
12. Tests
13. compileall
14. Git Diff
15. 当前限制

Phase 3.12 Step 100 READY
Phase 3.12 Step 100 STOP
```

---

# 二十三、硬停止

完成后立即停止。

不要进入：

```text
Step 101
Branch Protection
PR Automation
PR Comment
Dashboard
Telemetry
OpenTelemetry
Prometheus
Langfuse
```

等待下一步指令。
