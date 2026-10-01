# Phase 3.12 Step 93 — Regression Matrix Gate CI-Ready Audit

继续 Phase 3.12。

当前状态：

```text
Step 89
  ↓
MatrixExecutionSummary
  ↓
Step 90
  ↓
Frozen MatrixExecutionBaseline
  ↓
Step 91
  ↓
MatrixBaselineGateResult
  ↓
Step 92
  ↓
Gate Contract Frozen
```

当前 Gate：

```text
PASS
```

Baseline：

```text
Offline 375 / 356 / 19 / 0 / 0 / exit 0 / PASS
DB      180 / 180 /  0 / 0 / 0 / exit 0 / PASS
Matrix  555 / PASS
Residue 0
```

现在只执行：

# Step 93：CI-Ready Audit

---

## 一、目标

本阶段不接 CI。

只回答：

> 当前 Regression Matrix Baseline Gate 是否已经具备未来被 CI 调用的清晰边界？

建立：

```text
Gate Contract
    ↓
CI Readiness Audit
    ↓
READY / NOT READY
```

本阶段不创建 GitHub Actions。

---

# 二、严格范围

允许修改：

```text
tests/test_assistant_trace_timeline_regression.py
docs/evaluation/
```

允许新增：

```text
docs/evaluation/phase-3.12-step-93-ci-ready-audit.md
```

禁止：

```text
.github/
GitHub Actions
CI workflow
backend/
DB schema
API
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
CI runner
shell wrapper
新的 baseline
新的 regression framework
```

---

# 三、CI-Ready 不等于 CI

本阶段必须明确：

```text
CI-ready audit
≠
CI integration
```

只验证：

```text
Gate 是否有明确输入
Gate 是否有明确输出
Gate 是否 deterministic
Gate 是否有稳定 exit semantics
Gate 是否无隐式副作用
```

不要真正接 CI。

---

# 四、Gate Input Contract

审计：

```text
evaluate_matrix_baseline_gate(
    current,
    baseline
)
```

输入必须明确：

```text
MatrixExecutionSummary
MatrixExecutionBaseline
```

禁止：

```text
None
raw dict
pytest output
filesystem path
DB connection
LLM response
```

如果当前类型系统已经限制输入，则测试其真实行为。

不要重新设计 DTO。

---

# 五、Gate Output Contract

当前：

```text
MatrixBaselineGateResult
```

审计：

```text
status
drifts
current
baseline
```

必须保持 immutable。

未来 CI 可以只读取：

```text
status
```

但本阶段不要创建 CLI。

---

# 六、Exit Semantics Audit

未来 CI 最终只需要：

```text
PASS
DRIFT
```

审计：

```text
PASS
→ 可映射为成功

DRIFT
→ 可映射为失败
```

但是：

**本阶段不要真的 `sys.exit()`。**

不要修改现有 Gate API。

不要把：

```text
PASS / DRIFT
```

转换成 shell exit code。

只建立测试证明：

```text
status == PASS
status == DRIFT
```

具有稳定语义。

---

# 七、No Hidden Execution

Gate 本身必须：

```text
pytest execution = 0
DB execution = 0
network = 0
LLM = 0
```

新增测试只能使用 synthetic：

```text
MatrixExecutionSummary
MatrixExecutionBaseline
```

禁止：

```text
_matrix_execution_summary()
_current_matrix_baseline()
_db_residue_total()
subprocess
```

本阶段不要重复 Step 89/90/91 的真实执行。

---

# 八、No Baseline Mutation

审计：

```text
MATRIX_EXECUTION_BASELINE
```

Gate 前后完全一致。

禁止：

```text
refresh
update
overwrite
replace
```

未来 CI 只能：

```text
read baseline
```

不能：

```text
write baseline
```

---

# 九、Determinism

至少验证：

同一：

```text
current
baseline
```

调用：

```text
evaluate_matrix_baseline_gate()
```

连续 10 次。

要求：

```text
result[0]
==
result[1]
==
...
==
result[9]
```

不依赖：

```text
time
random
DB
network
pytest execution
```

---

# 十、Input Immutability

验证：

```text
current
baseline
```

在：

```text
Gate before
Gate after
```

完全一致。

包括：

```text
offline
db
matrix_total
matrix_status
db_residue
```

---

# 十一、Output Immutability

验证：

```text
MatrixBaselineGateResult
```

不能修改：

```text
status
drifts
current
baseline
```

如果当前 nested DTO 已 frozen：

继续复用。

不要重新定义 immutable framework。

---

# 十二、Drift Completeness

审计：

```text
offline drift
db drift
matrix total drift
matrix status drift
db residue drift
```

都能够进入：

```text
drifts
```

不能：

```text
只返回第一个 drift
```

组合 drift：

```text
offline
+
db
+
residue
```

必须返回全部实际 drift 类型。

---

# 十三、No False NO_BASELINE_DRIFT

冻结 Step 92 的修正。

规则：

```text
如果存在真实 drift
→ 不应额外返回 NO_BASELINE_DRIFT
```

例如：

```text
current == baseline
residue != 0
```

必须：

```text
("DB_RESIDUE_DRIFT",)
```

而不是：

```text
("NO_BASELINE_DRIFT", "DB_RESIDUE_DRIFT")
```

同时：

```text
完全一致 + residue == 0
```

才：

```text
("NO_BASELINE_DRIFT",)
```

---

# 十四、Duration Isolation

继续保持：

```text
duration_seconds
```

不影响：

```text
status
drifts
```

synthetic：

```text
duration = 1
duration = 100
duration = 9999
```

只改变 duration：

```text
→ PASS
```

---

# 十五、Node-hosted Isolation

继续保持：

```text
OFFLINE_EXECUTION_CONTRACT
```

不进入：

```text
MatrixExecutionSummary
MatrixExecutionBaseline
Gate Result
```

CI-Ready Gate 不得增加：

```text
node_hosted
```

等字段。

---

# 十六、Security Audit

未来 CI 输出中不允许泄露：

```text
api_key
password
database_url
authorization
prompt
messages
sql
rag chunks
tool args
raw response
```

Gate Result / repr 中继续验证不存在这些内容。

不要创建新的 security scanner。

复用已有测试风格。

---

# 十七、Architecture Boundary

测试/静态审计：

```text
Gate
```

只能依赖：

```text
MatrixExecutionSummary
MatrixExecutionBaseline
```

以及纯比较逻辑。

禁止依赖：

```text
PostgreSQL
SQLAlchemy
psycopg
HTTP client
OpenAI client
DeepSeek
Redis
Kafka
```

如果当前模块已有这些依赖：

只报告，不为了通过本阶段而重构。

---

# 十八、未来 CI Adapter 边界

只在文档中说明未来可以存在：

```text
CI Adapter
```

但：

**不要实现。**

未来形态可以是：

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

本阶段只冻结前 3 层。

---

# 十九、不要创建 CLI

禁止新增：

```text
scripts/run_matrix_gate.py
scripts/check_baseline.py
```

不要现在引入：

```text
argparse
click
typer
```

Gate 目前保持：

```text
Python API
```

即可。

---

# 二十、测试数量

建议：

```text
10～15 tests
```

覆盖：

```text
input
output
PASS
DRIFT
determinism
immutability
drift completeness
NO_BASELINE_DRIFT
duration isolation
node-hosted isolation
security
dependency boundary
no execution
```

不要重复 Step 91/92 已有测试。

重点是：

> CI Readiness，而不是再次测试 Gate 本身。

---

# 二十一、Documentation

新增：

```text
docs/evaluation/phase-3.12-step-93-ci-ready-audit.md
```

至少记录：

```text
Step 93
CI-Ready Audit
```

Current Gate：

```text
PASS / DRIFT
```

明确：

```text
CI integration = NOT DONE
CI workflow = NOT DONE
CLI = NOT DONE
Baseline refresh = NOT DONE
```

并说明：

```text
Gate input
Gate output
Determinism
Immutability
Security
No hidden execution
No DB
No network
No LLM
```

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

不要重新执行：

```text
18 offline files
15 DB files
```

---

# 二十三、Git Diff

完成后检查：

```powershell
git status --short
git diff --stat
git diff -- backend
```

必须确认：

```text
backend diff = empty
```

并且：

```text
无 CI workflow
无 GitHub Action
无 CLI
无 baseline 修改
无 DB migration
无 API 修改
```

---

# 二十四、完成报告

严格输出：

```text
Phase 3.12 Step 93 COMPLETE

1. 修改文件
2. 新增文件
3. CI-Ready Boundary
4. Input Contract
5. Output Contract
6. Exit Semantics
7. Determinism
8. Immutability
9. Drift Completeness
10. NO_BASELINE_DRIFT
11. Duration Isolation
12. Node-hosted Isolation
13. Security
14. Dependency Boundary
15. Hidden Execution
16. Tests
17. compileall
18. CI 是否接入
19. DB / Network / LLM
20. Git Diff
21. 当前限制

Phase 3.12 Step 93 READY
Phase 3.12 Step 93 STOP
```

如果发现 Gate 本身存在新的语义冲突：

不要继续实现。

报告：

```text
Phase 3.12 Step 93 BLOCKED
```

说明冲突。

---

# 二十五、硬停止

完成 Step 93 后：

**立即 STOP。**

不要：

```text
Step 94
CI integration
GitHub Actions
CLI
Baseline refresh
Dashboard
OpenTelemetry
Prometheus
```

等待下一步指令。
