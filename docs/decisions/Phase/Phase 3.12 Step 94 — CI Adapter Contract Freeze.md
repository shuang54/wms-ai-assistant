# Phase 3.12 Step 94 — CI Adapter Contract Freeze

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
Step 92
  ↓
Gate Contract Frozen
  ↓
Step 93
  ↓
CI-Ready Audit PASS
```

当前已确认：

```text
CI integration = NOT DONE
CLI = NOT DONE
GitHub Actions = NOT DONE
Baseline refresh = NOT DONE
```

现在只执行：

# Step 94：CI Adapter Contract Freeze

---

# 一、目标

冻结未来 CI Adapter 的最小责任边界：

```text
Regression Matrix Execution
        ↓
MatrixExecutionSummary
        ↓
evaluate_matrix_baseline_gate()
        ↓
MatrixBaselineGateResult
        ↓
CI Adapter
        ↓
exit code
```

本阶段只冻结最后两层之间的 Contract：

```text
MatrixBaselineGateResult
        ↓
CI Adapter
        ↓
exit code
```

**不实现 Adapter。**

**不实现 CI。**

---

# 二、严格范围

允许修改：

```text
tests/test_assistant_trace_timeline_regression.py
docs/evaluation/
```

允许新增：

```text
docs/evaluation/phase-3.12-step-94-ci-adapter-contract.md
```

禁止：

```text
backend/
.github/
GitHub Actions
CI workflow
CLI
scripts/run_matrix_gate.py
scripts/check_baseline.py
```

禁止：

```text
数据库
Redis
Kafka
HTTP
DeepSeek
OpenAI client
```

禁止修改：

```text
MATRIX_EXECUTION_BASELINE
MatrixExecutionSummary
MatrixBaselineGateResult
Step 85 Snapshot Drift Contract
Step 90 Baseline Contract
Step 92 Gate Contract
```

除非发现明确的 Contract 冲突。

---

# 三、Adapter 唯一职责

未来 Adapter 只负责：

```text
MatrixBaselineGateResult
        ↓
CI process result
```

不得负责：

```text
执行 Regression Matrix
读取 PostgreSQL
重新计算 Baseline
重新判断 Drift
刷新 Baseline
调用 LLM
网络请求
```

即：

```text
Adapter ≠ Runner
Adapter ≠ Gate
Adapter ≠ Baseline Manager
```

---

# 四、Exit Code Contract

冻结：

```text
PASS  → exit code 0
DRIFT → exit code 1
```

只允许：

```text
0
1
```

不要引入：

```text
2
3
4
10
99
```

等额外业务退出码。

未来如果发生：

```text
execution infrastructure failure
```

应该由：

```text
Regression Matrix Execution
```

本身产生失败结果，而不是在 Adapter 中创造新的业务状态。

本阶段不改变 `MatrixBaselineGateResult.status`。

---

# 五、Adapter Input Contract

未来 Adapter 只接受：

```python
MatrixBaselineGateResult
```

不接受：

```text
MatrixExecutionSummary
MatrixExecutionBaseline
pytest stdout
pytest stderr
raw subprocess result
database connection
HTTP response
```

原因：

```text
Gate 已经完成所有业务判断。
```

Adapter 不应该重复判断。

---

# 六、Adapter Output Contract

未来 Adapter 的唯一外部结果：

```text
process exit code
```

对应：

```text
PASS  → 0
DRIFT → 1
```

Adapter 不负责输出：

```text
JSON
HTML
DB record
HTTP response
Dashboard event
Telemetry event
```

本阶段不实现这些能力。

---

# 七、Adapter 不允许重新计算 Drift

特别冻结：

未来 Adapter 不得出现：

```python
if current != baseline:
    ...
```

也不得检查：

```text
offline
db
matrix_total
matrix_status
db_residue
```

这些已经属于：

```text
evaluate_matrix_baseline_gate()
```

职责。

Adapter 只读取：

```python
result.status
```

---

# 八、Adapter 不允许读取 Baseline

未来 Adapter 不应该：

```text
MATRIX_EXECUTION_BASELINE
```

它只接受：

```text
MatrixBaselineGateResult
```

因为 Baseline 已经被 Gate 消费。

这样保证：

```text
Baseline ownership
    ↓
Gate
```

而不是：

```text
Baseline
 ├── Gate
 └── Adapter
```

避免双重判断。

---

# 九、Adapter 不允许修改 Result

未来 Adapter：

```text
result
    ↓
read-only
```

不得：

```text
result.status = ...
result.drifts = ...
result.current = ...
result.baseline = ...
```

不得创建“修正后的” Gate Result。

---

# 十、DRIFT 的 CI 语义

冻结：

```text
DRIFT
```

表示：

> Regression Matrix 与 Frozen Baseline 不一致，CI 应视为失败。

包括：

```text
OFFLINE_EXECUTION_DRIFT
DB_EXECUTION_DRIFT
MATRIX_TOTAL_DRIFT
MATRIX_STATUS_DRIFT
DB_RESIDUE_DRIFT
```

这些都：

```text
exit code = 1
```

不允许 Adapter 根据 drift 类型选择不同退出码。

---

# 十一、PASS 的 CI 语义

只有：

```text
status == PASS
```

才：

```text
exit code = 0
```

即：

```text
PASS
drifts = ("NO_BASELINE_DRIFT",)
```

才是成功。

---

# 十二、Future Adapter Pseudocode

文档中只允许记录类似：

```python
def adapt_gate_to_exit_code(result):
    if result.status == "PASS":
        return 0
    if result.status == "DRIFT":
        return 1
    raise ValueError("Unsupported gate status")
```

注意：

**不要实现这个函数。**

不要把它加入 production code。

这里只作为 Contract 示例。

---

# 十三、Status Exhaustiveness

增加测试证明：

```text
PASS
DRIFT
```

覆盖全部合法 Gate Status。

如果构造：

```text
WARNING
UNKNOWN
PARTIAL
SKIPPED
STALE
```

仍然应该被 Gate Result Contract 拒绝。

Adapter 不需要处理这些状态。

---

# 十四、No Automatic Refresh

冻结：

```text
DRIFT
    ↓
exit 1
```

不能：

```text
DRIFT
    ↓
refresh baseline
    ↓
exit 0
```

不能：

```text
DRIFT
    ↓
overwrite MATRIX_EXECUTION_BASELINE
```

Baseline Refresh 永远属于未来显式授权阶段。

---

# 十五、No Output Formatting Contract

本阶段不冻结：

```text
console output
JSON output
GitHub annotation
Markdown report
PR comment
```

未来 CI Adapter 只需要：

```text
exit code
```

输出格式另行设计。

---

# 十六、Security Boundary

未来 Adapter 不得处理：

```text
API key
password
database_url
authorization
prompt
messages
SQL
RAG chunks
tool args
raw response
```

Adapter 只读取：

```text
status
```

如果未来需要打印 drift：

只允许打印：

```text
drift type
```

不能打印：

```text
current raw DTO
baseline raw DTO
```

本阶段不实现输出。

---

# 十七、No Hidden Dependencies

未来 Adapter 的依赖边界冻结为：

```text
MatrixBaselineGateResult
```

以及：

```text
stdlib
```

不得直接依赖：

```text
SQLAlchemy
psycopg
PostgreSQL
HTTP client
OpenAI
DeepSeek
Redis
Kafka
pytest internals
```

---

# 十八、测试要求

新增约：

```text
8～12 tests
```

重点覆盖：

### 1

```text
PASS → 0
```

### 2

```text
DRIFT → 1
```

### 3

所有 drift type：

```text
→ 1
```

### 4

非法 status：

```text
→ Gate Result construction rejected
```

### 5

Adapter Contract 不读取：

```text
baseline
current
```

### 6

Adapter Contract 不重新计算 drift。

### 7

Baseline immutable。

### 8

Gate Result immutable。

### 9

Adapter input only:

```text
MatrixBaselineGateResult
```

### 10

No DB / network / LLM dependency。

注意：

**这里仍然只测试 Contract，不实现 Adapter。**

---

# 十九、静态审计

可以使用现有 AST 审计方式验证：

未来 Adapter Contract 示例中不得出现：

```text
sys.exit
subprocess
sqlalchemy
psycopg
get_engine
OpenAI
DeepSeek
```

但不要扫描整个项目。

只扫描：

```text
Step 94 Contract / test scope
```

不要新增通用 scanner。

---

# 二十、Documentation

新增：

```text
docs/evaluation/phase-3.12-step-94-ci-adapter-contract.md
```

至少记录：

```text
Phase 3.12 Step 94
CI Adapter Contract Freeze
```

核心：

```text
Gate Result
    ↓
Adapter
    ↓
PASS  → 0
DRIFT → 1
```

以及：

```text
Adapter:
- no execution
- no DB
- no network
- no LLM
- no baseline access
- no drift calculation
- no baseline refresh
- no output persistence
```

明确：

```text
CI integration = NOT DONE
Adapter implementation = NOT DONE
GitHub Actions = NOT DONE
CLI = NOT DONE
```

---

# 二十一、不要创建 Adapter

禁止新增：

```text
backend/app/...
scripts/...
.github/...
```

任何实际 Adapter 文件。

本阶段只冻结 Contract。

---

# 二十二、测试命令

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

不要重新运行：

```text
18 Offline files
15 DB files
```

---

# 二十三、Git Diff

确认：

```text
backend diff = empty
```

并且：

```text
.github/ = untouched
```

没有：

```text
CI workflow
CLI
Adapter implementation
Baseline modification
DB migration
API modification
```

---

# 二十四、完成报告

严格输出：

```text
Phase 3.12 Step 94 COMPLETE

1. 修改文件
2. 新增文件
3. CI Adapter Boundary
4. Input Contract
5. Output Contract
6. Exit Code Contract
7. PASS / DRIFT
8. Drift Mapping
9. Baseline Ownership
10. No Recalculation
11. No Refresh
12. Security
13. Dependency Boundary
14. Tests
15. compileall
16. Adapter 是否实现
17. CI 是否接入
18. DB / Network / LLM
19. Git Diff
20. 当前限制

Phase 3.12 Step 94 READY
Phase 3.12 Step 94 STOP
```

---

# 二十五、硬停止

完成 Step 94 后：

**立即 STOP。**

不要进入：

```text
Step 95
CI implementation
GitHub Actions
CLI
Baseline Refresh
Dashboard
OpenTelemetry
Prometheus
```

等待下一步指令。
