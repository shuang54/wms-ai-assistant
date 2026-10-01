# Phase 3.12 Step 95：CI Adapter 最小实现

## 一、阶段目标

基于 Step 94 已冻结的 Contract，现在只实现：

```text
MatrixBaselineGateResult
        ↓
CI Adapter
        ↓
process exit code
```

本阶段第一次允许出现真实 Adapter 实现。

目标只有一个：

> 将已经完成的 `MatrixBaselineGateResult` 转换成进程 exit code。

GitHub Actions 后续可以直接利用这个 exit code 判断 step success/failure；GitHub 官方文档明确规定 exit code `0` 表示成功，非 `0` 表示失败。

---

# 二、严格范围

## 允许修改

只允许：

```text
tests/test_assistant_trace_timeline_regression.py
```

允许新增：

```text
backend/app/services/matrix_ci_adapter.py
tests/test_matrix_ci_adapter.py
docs/evaluation/phase-3.12-step-95-ci-adapter.md
```

如果当前项目结构已经有更自然的 CI / evaluation utility 位置，可以选择等价位置。

---

# 三、禁止

本阶段禁止：

```text
.github/
GitHub Actions workflow
scripts/run_matrix_gate.py
scripts/check_baseline.py
argparse
click
typer
CLI framework
```

禁止：

```text
Baseline Refresh
Baseline Manager
Dashboard
Telemetry
OpenTelemetry
Prometheus
Langfuse
```

禁止修改：

```text
MatrixExecutionSummary
MatrixExecutionBaseline
MatrixBaselineGateResult
evaluate_matrix_baseline_gate()
compare_matrix_execution_baseline()
```

禁止修改：

```text
Regression Matrix Runner
OFFLINE_EXECUTION_CONTRACT
Step 85 Snapshot Drift
Step 90 Baseline Contract
Step 92 Gate Contract
Step 94 CI Adapter Contract
```

禁止：

```text
DB
PostgreSQL
SQLAlchemy
psycopg
Redis
Kafka
HTTP
LLM
DeepSeek
```

---

# 四、Adapter 唯一职责

实现：

```text
MatrixBaselineGateResult
        ↓
exit code
```

唯一规则：

```text
PASS  → 0
DRIFT → 1
```

不要重新判断：

```text
current == baseline
```

不要重新计算：

```text
drifts
```

不要读取：

```text
MATRIX_EXECUTION_BASELINE
```

不要执行：

```text
Regression Matrix
```

不要查询：

```text
PostgreSQL
```

---

# 五、建议 API

优先使用最小纯函数：

```python
def adapt_gate_result_to_exit_code(
    result: MatrixBaselineGateResult,
) -> int:
    ...
```

如果当前项目命名风格已有等价形式，可以遵循现有风格。

要求：

```text
input:
MatrixBaselineGateResult

output:
int
```

只允许：

```text
0
1
```

---

# 六、Input Contract

Adapter 必须严格接受：

```text
MatrixBaselineGateResult
```

不接受：

```text
MatrixExecutionSummary
MatrixExecutionBaseline
dict
str
tuple
pytest result
subprocess result
stdout
stderr
database connection
HTTP response
LLM response
```

对于错误类型输入：

```text
TypeError
```

即可。

不要自动转换。

不要：

```python
dict → MatrixBaselineGateResult
```

不要：

```python
str → status
```

---

# 七、Status Contract

合法：

```text
PASS
DRIFT
```

映射：

```text
PASS  → 0
DRIFT → 1
```

由于 `MatrixBaselineGateResult` 已经在构造期保证：

```text
status ∈ {PASS, DRIFT}
```

Adapter 不需要重新建立第三种状态。

不要增加：

```text
WARNING
UNKNOWN
PARTIAL
SKIPPED
STALE
ERROR
```

---

# 八、Drift Contract

Adapter：

**完全不关心具体 Drift 类型。**

例如：

```text
OFFLINE_EXECUTION_DRIFT
DB_EXECUTION_DRIFT
MATRIX_TOTAL_DRIFT
MATRIX_STATUS_DRIFT
DB_RESIDUE_DRIFT
```

全部：

```text
DRIFT → 1
```

不要：

```text
DB_RESIDUE_DRIFT → 2
```

不要：

```text
OFFLINE_EXECUTION_DRIFT → 10
```

不要根据 drift 类型改变 exit code。

---

# 九、纯函数要求

Adapter 必须：

```text
pure
stateless
deterministic
```

禁止：

```text
DB
network
filesystem
environment variables
sleep
retry
subprocess
sys.exit()
```

尤其注意：

不要让函数内部直接：

```python
sys.exit(...)
```

Adapter 只返回：

```python
0
```

或者：

```python
1
```

未来 CLI 再决定是否调用：

```python
raise SystemExit(exit_code)
```

但本阶段不实现 CLI。

---

# 十、Baseline Ownership

Adapter 不得 import：

```text
MATRIX_EXECUTION_BASELINE
```

也不得：

```text
MatrixExecutionBaseline
```

作为运行时依赖。

Adapter 只依赖：

```text
MatrixBaselineGateResult
```

也就是说：

```text
Gate
    ↓
拥有 current / baseline / drift 判断

Adapter
    ↓
只消费 Gate Result
```

---

# 十一、Gate Ownership

Adapter 不得调用：

```text
evaluate_matrix_baseline_gate()
```

也不得调用：

```text
compare_matrix_execution_baseline()
```

完整边界：

```text
Regression Matrix
       ↓
MatrixExecutionSummary
       ↓
MatrixExecutionBaseline
       ↓
evaluate_matrix_baseline_gate()
       ↓
MatrixBaselineGateResult
       ↓
CI Adapter
       ↓
exit code
```

Adapter 是最后一层。

---

# 十二、Security Boundary

Adapter 不得访问：

```text
api_key
password
database_url
authorization
prompt
messages
SQL
RAG chunks
tool args
tool results
raw LLM response
HTTP headers
```

Adapter 只需要：

```text
result.status
```

实际上：

```python
result.status
```

即可完成全部工作。

---

# 十三、Dependency Boundary

生产 Adapter 最终只允许依赖：

```text
MatrixBaselineGateResult
Python stdlib
```

不要依赖：

```text
sqlalchemy
psycopg
postgresql
httpx
openai
deepseek
redis
kafka
pytest
```

不要引入第三方依赖。

---

# 十四、测试

新增：

```text
tests/test_matrix_ci_adapter.py
```

至少覆盖：

### Test 1

```text
PASS → 0
```

### Test 2

```text
DRIFT → 1
```

### Test 3

所有 Drift：

```text
OFFLINE_EXECUTION_DRIFT → 1
DB_EXECUTION_DRIFT      → 1
MATRIX_TOTAL_DRIFT      → 1
MATRIX_STATUS_DRIFT     → 1
DB_RESIDUE_DRIFT        → 1
```

---

### Test 4

非法 input：

```text
None
dict
str
tuple
MatrixExecutionSummary
MatrixExecutionBaseline
```

全部拒绝。

---

### Test 5

Identity：

Adapter 不修改：

```text
MatrixBaselineGateResult
```

调用前后：

```text
result == before
```

---

### Test 6

Immutability：

确认：

```text
MatrixBaselineGateResult
```

仍然 frozen。

---

### Test 7

Determinism：

同一个：

```text
PASS
```

连续调用 100 次：

```text
0
```

同一个：

```text
DRIFT
```

连续调用 100 次：

```text
1
```

---

### Test 8

Security：

静态 AST 检查 Adapter 方法体不得出现：

```text
sys.exit
subprocess
sqlalchemy
psycopg
httpx
openai
deepseek
get_engine
```

排除 docstring。

---

### Test 9

Baseline isolation：

静态检查 Adapter 不引用：

```text
MATRIX_EXECUTION_BASELINE
MatrixExecutionBaseline
compare_matrix_execution_baseline
evaluate_matrix_baseline_gate
```

---

### Test 10

Drift recalculation isolation：

确认 Adapter 不检查：

```text
current
baseline
offline
db
matrix_total
matrix_status
db_residue
```

Adapter 只读取：

```text
status
```

---

### Test 11

Mapping completeness：

继续复用 Step 94 的：

```text
CI_ADAPTER_EXIT_CODES
```

不要重新创建第二份：

```text
PASS → 0
DRIFT → 1
```

如果 Step 94 已经存在该映射，则 Adapter 必须复用它。

---

### Test 12

No hidden execution：

调用 Adapter 时：

```text
Regression Matrix = 0
DB = 0
Network = 0
LLM = 0
```

可以通过 monkeypatch / AST 证明。

---

# 十五、不要实现 CLI

本阶段：

**不创建：**

```text
main()
cli()
run_gate()
```

不要：

```text
python -m ...
```

不要：

```text
argparse
```

不要：

```text
sys.exit()
```

只实现：

```text
adapt_gate_result_to_exit_code()
```

---

# 十六、Documentation

新增：

```text
docs/evaluation/phase-3.12-step-95-ci-adapter.md
```

只记录：

```text
Step 95

MatrixBaselineGateResult
        ↓
CI Adapter
        ↓
exit code

PASS  → 0
DRIFT → 1
```

并明确：

```text
Adapter does not:
- execute matrix
- query DB
- calculate baseline
- calculate drift
- refresh baseline
- call LLM
- call network
```

同时记录：

```text
Step 95 does not implement:
- CLI
- GitHub Actions
- Baseline Refresh
- Dashboard
- Telemetry
```

---

# 十七、Regression Matrix

继续复用现有：

```text
tests/test_assistant_trace_timeline_regression.py
```

不要建立第二个 Regression Framework。

如果 Step 94 已经有：

```text
CI_ADAPTER_EXIT_CODES
CI_ADAPTER_INPUT_TYPE
CI_ADAPTER_OUTPUT
CI_ADAPTER_FORBIDDEN_RESPONSIBILITIES
```

继续复用。

可以增加：

```text
CI_ADAPTER_IMPLEMENTATION
```

作为现有 Matrix Contract 的一个节点。

不要创建第二套 Contract Registry。

---

# 十八、测试命令

只运行：

```powershell
python -m pytest -q tests/test_matrix_ci_adapter.py
```

然后：

```powershell
python -m pytest -q tests/test_assistant_trace_timeline_regression.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

**不要运行：**

```text
pytest -q
RUN_DB_TESTS=1 pytest
DeepSeek
真实 PostgreSQL
```

---

# 十九、Git Diff

完成后检查：

```powershell
git status --short
git diff --stat
git diff -- backend
```

必须确认：

```text
生产业务逻辑 = 0
DB migration = 0
API contract = 0
Prompt = 0
Validator = 0
Executor = 0
Router = 0
Orchestrator = 0
LLM = 0
Network = 0
```

---

# 二十、完成报告

完成后严格报告：

```text
Phase 3.12 Step 95 COMPLETE

1. 新增/修改文件
2. Adapter API
3. Input Contract
4. Output Contract
5. PASS / DRIFT
6. Drift Mapping
7. Baseline Ownership
8. Gate Ownership
9. Security
10. Dependency Boundary
11. Tests
12. Regression
13. compileall
14. DB / Network / LLM
15. CLI
16. Git Diff
17. 当前限制

Phase 3.12 Step 95 READY
Phase 3.12 Step 95 STOP
```

---

# 二十一、强制 STOP

完成后立即停止。

不要进入：

```text
Step 96
GitHub Actions
CLI
Baseline Refresh
Dashboard
Telemetry
OpenTelemetry
Prometheus
```

等待下一步指令。
