# Phase 3.12 Step 96：本地 CI Gate CLI 最小接入

## 一、阶段目标

在 Step 95 已完成：

```text
MatrixBaselineGateResult
        ↓
adapt_gate_result_to_exit_code()
        ↓
0 / 1
```

的基础上，本阶段只增加一个**本地可执行入口**：

```text
Regression Matrix
        ↓
Matrix Execution
        ↓
Baseline Gate
        ↓
MatrixBaselineGateResult
        ↓
CI Adapter
        ↓
SystemExit(0 / 1)
```

目标：

> 在本地能够通过一条命令执行 Matrix Gate，并让 shell 获得正确 exit code。

本阶段**不接 GitHub Actions**。

---

# 二、严格范围

## 允许新增

```text
scripts/run_matrix_gate.py
tests/test_matrix_gate_cli.py
docs/evaluation/phase-3.12-step-96-ci-gate-cli.md
```

允许最小修改：

```text
tests/test_assistant_trace_timeline_regression.py
```

仅用于：

```text
Matrix Contract registration
```

---

# 三、禁止

本阶段禁止：

```text
.github/
GitHub Actions workflow
GitHub Actions YAML
GitHub CLI
PR comment
GitHub annotation
Dashboard
Telemetry
OpenTelemetry
Prometheus
Langfuse
```

禁止：

```text
Baseline Refresh
Baseline Manager
自动更新 baseline
```

禁止修改：

```text
MatrixExecutionSummary
MatrixExecutionBaseline
MatrixBaselineGateResult
evaluate_matrix_baseline_gate()
compare_matrix_execution_baseline()
adapt_gate_result_to_exit_code()
```

禁止修改：

```text
RAG
Tool
T2SQL
Validator
Executor
Router
Orchestrator
LLM
```

---

# 四、CLI 唯一职责

创建：

```text
scripts/run_matrix_gate.py
```

入口负责：

```text
执行已有 Regression Matrix
        ↓
获得 MatrixExecutionSummary
        ↓
构造 MatrixExecutionBaseline
        ↓
evaluate_matrix_baseline_gate()
        ↓
adapt_gate_result_to_exit_code()
        ↓
raise SystemExit(exit_code)
```

注意：

CLI 是 orchestration layer。

它可以调用现有 Matrix/Gate/Adapter。

但：

**不能重新实现这些逻辑。**

---

# 五、禁止复制业务逻辑

CLI 中禁止重新实现：

```text
offline PASS 判断
DB PASS 判断
matrix_total 计算
baseline comparison
drift classification
status 判断
exit code mapping
```

必须复用：

```text
MatrixExecutionSummary
MatrixExecutionBaseline
evaluate_matrix_baseline_gate()
adapt_gate_result_to_exit_code()
```

---

# 六、CLI API

最小形式：

```python
def main() -> int:
    ...
```

然后：

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

允许 CLI 层使用：

```text
SystemExit
```

但：

```text
adapt_gate_result_to_exit_code()
```

本身仍然只能返回：

```text
0 / 1
```

---

# 七、命令

最终支持：

```powershell
python scripts/run_matrix_gate.py
```

正常情况：

```text
exit code = 0
```

Baseline Drift：

```text
exit code = 1
```

---

# 八、输出

本阶段只输出最小文本。

建议：

```text
Matrix Gate
Status: PASS
Exit code: 0
```

或者：

```text
Matrix Gate
Status: DRIFT
Exit code: 1
```

如果 DRIFT：

可以额外输出：

```text
Drifts:
- DB_EXECUTION_DRIFT
```

但：

**不得输出：**

```text
current DTO
baseline DTO
API key
password
database_url
prompt
SQL
RAG chunks
tool args
LLM response
```

---

# 九、CLI 不负责重新判断

禁止：

```python
if current != baseline:
```

禁止：

```python
if summary.failed > 0:
```

禁止：

```python
if residue != 0:
```

这些全部属于现有 Gate。

CLI 只负责：

```text
Matrix execution
→ Gate
→ Adapter
→ process exit
```

---

# 十、Execution Failure

如果 Matrix Execution 本身失败：

不要在 CLI 中创建新的：

```text
ERROR
INFRA_FAILURE
UNKNOWN
```

状态。

复用现有：

```text
MatrixExecutionSummary
```

和：

```text
MatrixBaselineGateResult
```

现有 Gate Contract 决定最终：

```text
PASS / DRIFT
```

---

# 十一、Baseline

Step 96：

**不刷新 Baseline。**

使用现有：

```text
MATRIX_EXECUTION_BASELINE
```

Gate 仍然拥有 baseline。

CLI 不允许：

```text
write baseline
overwrite baseline
generate baseline
```

---

# 十二、测试

新增：

```text
tests/test_matrix_gate_cli.py
```

至少覆盖：

### 1. PASS

Fake：

```text
MatrixBaselineGateResult(status="PASS")
```

确认：

```text
main() == 0
```

---

### 2. DRIFT

Fake：

```text
MatrixBaselineGateResult(status="DRIFT")
```

确认：

```text
main() == 1
```

---

### 3. SystemExit

真实入口：

```text
__main__
```

最终：

```text
PASS → SystemExit(0)
DRIFT → SystemExit(1)
```

---

### 4. Adapter reuse

AST / monkeypatch 验证 CLI 调用了：

```text
adapt_gate_result_to_exit_code()
```

而不是重新实现：

```text
PASS → 0
DRIFT → 1
```

---

### 5. Gate reuse

验证 CLI 调用：

```text
evaluate_matrix_baseline_gate()
```

不自行比较 baseline。

---

### 6. No baseline refresh

静态检查：

```text
MATRIX_EXECUTION_BASELINE
```

只读。

不得出现：

```text
=
overwrite
write
save
dump
json.dump
```

等 baseline 写入行为。

---

### 7. No hidden dependency

CLI 不得依赖：

```text
OpenAI
DeepSeek
SQLAlchemy
psycopg
Redis
Kafka
HTTP client
```

---

### 8. No secrets

CLI 源码不得出现：

```text
api_key
authorization
password
database_url
```

排除 docstring。

---

### 9. Determinism

相同 Gate Result：

```text
PASS → 0
DRIFT → 1
```

连续运行结果一致。

---

### 10. CLI argument contract

本阶段：

```text
不需要任何参数
```

因此：

```text
--refresh
--update-baseline
--db
--project
--environment
```

全部不存在。

---

# 十三、Matrix Regression 集成

继续使用：

```text
tests/test_assistant_trace_timeline_regression.py
```

不要创建第二套 Matrix。

如果 Step 95 已登记：

```text
CI_ADAPTER_IMPLEMENTATION
```

Step 96 可以增加：

```text
CI_GATE_CLI
```

但必须继续使用现有 Matrix Contract Registry。

---

# 十四、测试命令

先：

```powershell
python -m pytest -q tests/test_matrix_ci_adapter.py
```

然后：

```powershell
python -m pytest -q tests/test_matrix_gate_cli.py
```

然后：

```powershell
python -m pytest -q tests/test_assistant_trace_timeline_regression.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

最后执行一次：

```powershell
python scripts/run_matrix_gate.py
```

确认：

```text
Exit code = 0
```

如果当前环境 DB 不可用：

记录为：

```text
CLI execution skipped
```

不要修改代码绕过。

---

# 十五、Git Diff

检查：

```powershell
git status --short
git diff --stat
git diff -- backend
```

确认：

```text
生产业务逻辑 = 0
Adapter contract = unchanged
Gate contract = unchanged
Baseline = unchanged
DB migration = 0
API = unchanged
Prompt = unchanged
LLM = 0
Network = 0
GitHub Actions = 0
```

---

# 十六、Documentation

新增：

```text
docs/evaluation/phase-3.12-step-96-ci-gate-cli.md
```

记录：

```text
Step 96

Regression Matrix
      ↓
Matrix Baseline Gate
      ↓
CI Adapter
      ↓
Local CLI
      ↓
exit code
```

明确：

```text
PASS  → 0
DRIFT → 1
```

并说明：

```text
本阶段：
✓ 本地 CLI
✓ 真实 exit code

未实现：
✗ GitHub Actions
✗ Baseline Refresh
✗ Dashboard
✗ Telemetry
✗ PR Annotation
```

---

# 十七、不要接 GitHub Actions

虽然 GitHub Actions 会根据 step 的 exit code 判断 success/failure，`0` 为成功、非 `0` 为失败，但本阶段只验证本地 CLI，不创建 workflow。

不要新增：

```text
.github/workflows/
```

---

# 十八、完成报告

严格按照：

```text
Phase 3.12 Step 96 COMPLETE

1. 新增/修改文件
2. CLI API
3. Matrix Execution
4. Gate
5. Adapter
6. Exit Code
7. Baseline
8. Security
9. Dependency Boundary
10. Tests
11. CLI 实测
12. compileall
13. DB / Network / LLM
14. Git Diff
15. 当前限制

Phase 3.12 Step 96 READY
Phase 3.12 Step 96 STOP
```

---

# 十九、强制 STOP

完成后立即停止。

不要进入：

```text
Step 97
GitHub Actions
Baseline Refresh
Dashboard
Telemetry
OpenTelemetry
Prometheus
```

等待下一步指令。
