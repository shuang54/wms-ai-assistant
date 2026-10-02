# Phase 3.12 Step 98：GitHub Actions 首次真实运行审计

## 一、阶段目标

Step 97 已完成：

```text
.github/workflows/observability-matrix-gate.yml
        ↓
PostgreSQL pgvector
        ↓
init_db
        ↓
run_matrix_gate.py
        ↓
PASS / DRIFT
```

本阶段**不新增 CI 能力**。

唯一目标：

> 验证 Step 97 Workflow 在真实 GitHub Actions 环境中是否能够完整运行。

---

# 二、严格范围

本阶段允许：

```text
观察真实 GitHub Actions Run
检查 Workflow 日志
定位 CI 环境问题
必要时进行最小 CI 配置修正
新增 CI 审计测试
新增 evaluation 文档
```

允许修改：

```text
.github/workflows/observability-matrix-gate.yml
tests/test_github_actions_matrix_gate.py
docs/evaluation/phase-3.12-step-98-github-actions-run-audit.md
```

---

# 三、禁止

禁止修改：

```text
backend/app/
scripts/run_matrix_gate.py
MatrixExecutionSummary
MatrixExecutionBaseline
MatrixBaselineGateResult
evaluate_matrix_baseline_gate()
compare_matrix_execution_baseline()
adapt_gate_result_to_exit_code()
```

禁止：

```text
Baseline Refresh
PR Comment
GitHub Annotation
Dashboard
Telemetry
OpenTelemetry
Prometheus
Langfuse
```

禁止为了让 CI 通过而：

```text
降低测试数量
修改 baseline
跳过 DB tests
关闭 pgvector
关闭 residue check
吞掉 exit code
continue-on-error
|| true
```

---

# 四、真实 Run

首先提交当前 Step 97 状态并触发：

```text
pull_request
```

或者：

```text
push
```

不要新增新的 workflow。

观察唯一 job：

```text
observability-matrix-gate
```

---

# 五、必须验证的完整链路

真实 CI 必须经过：

```text
checkout
  ↓
Python setup
  ↓
requirements install
  ↓
PostgreSQL service
  ↓
pgvector extension
  ↓
init_db
  ↓
Matrix execution
  ↓
Baseline Gate
  ↓
CI Adapter
  ↓
exit code
```

不能只验证 Workflow YAML 能解析。

---

# 六、PostgreSQL 审计

确认：

```text
service image = pgvector/pgvector:pg16
```

确认：

```text
pgvector extension = available
```

确认：

```text
init_db = success
```

确认：

```text
DATABASE_URL
```

实际连接的是 CI PostgreSQL service，而不是外部数据库。

禁止出现：

```text
AWS
Azure
Neon
Supabase
生产 PostgreSQL
```

---

# 七、Database Residue

真实 CI Matrix 完成后确认：

```text
llm_usage_record
tool_execution_record
rag_execution_record
assistant_outcome_record
```

最终 residue：

```text
0
```

如果 residue 非零：

**CI 必须失败。**

不要修改 baseline 来接受 residue。

---

# 八、Baseline

确认真实 CI 使用：

```text
MATRIX_EXECUTION_BASELINE
```

中的冻结值。

不得发生：

```text
baseline refresh
baseline overwrite
baseline regeneration
```

重点检查：

```text
offline = 375/356/19
db = 180/180
matrix total = 555
status = PASS
residue = 0
```

如果真实 CI 的测试数量与本地不同：

不要立即修改 baseline。

先定位：

```text
Python version
dependency version
PostgreSQL version
pgvector
timezone
fixture
pytest discovery
environment variable
```

---

# 九、Exit Code

真实 CI：

### 正常情况

```text
Matrix Gate
Status: PASS
Exit code: 0
```

GitHub Actions：

```text
job = SUCCESS
```

### 如果人为制造 drift

不修改 baseline。

只验证：

```text
DRIFT
 ↓
exit 1
 ↓
GitHub Actions job FAILED
```

如果当前没有安全方式制造 drift：

**不要修改生产代码。**

可以只通过现有测试 fake 验证，不要求真实 CI 做 destructive drift。

---

# 十、LLM / Network

真实 CI 必须：

```text
DeepSeek calls = 0
LLM calls = 0
external API = 0
```

不允许：

```text
DEEPSEEK_API_KEY
SILICONFLOW_API_KEY
OPENAI_API_KEY
```

进入 Workflow。

Matrix Gate 是：

```text
evaluation infrastructure
```

不是：

```text
LLM evaluation
```

---

# 十一、首次失败处理

如果真实 CI 失败：

**不要立即修改代码。**

先分类：

### A. Workflow 配置问题

例如：

```text
Python setup
dependency installation
environment variable
PostgreSQL service
port
```

允许最小修复 Workflow。

### B. CI 环境差异

例如：

```text
pytest collection
timezone
locale
dependency version
```

先记录并判断是否属于真实环境问题。

### C. Matrix/Gate 真实失败

例如：

```text
DB suite != 180
residue != 0
baseline drift
```

停止。

不要修改 Gate / baseline / production code。

最终报告：

```text
CI failure:
Root cause:
Impact:
Production code changed: NO
```

---

# 十二、Workflow 不得吞失败

确认真实 Workflow 没有：

```yaml
continue-on-error: true
```

没有：

```bash
|| true
```

没有：

```yaml
if: failure()
```

来覆盖 Gate 失败。

---

# 十三、Workflow Security

确认：

```text
Secrets = 0
GitHub Token = 0
External API = 0
PR comment = 0
```

Workflow 不应该拥有不必要的：

```yaml
permissions:
```

如果当前没有显式权限配置，**不要为了本阶段新增复杂权限体系**。

---

# 十四、性能记录

只记录真实 CI 总耗时：

```text
workflow duration
matrix duration
```

不要进行性能优化。

如果明显失败于：

```text
timeout
```

先报告。

不要增加：

```text
retry
parallel matrix
cache
```

---

# 十五、测试

Step 98 测试只审计 Workflow：

```powershell
python -m pytest -q tests/test_github_actions_matrix_gate.py
```

然后：

```powershell
python -m pytest -q tests/test_matrix_ci_adapter.py tests/test_matrix_gate_cli.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

**不要因为 Step 98 再跑完整 `pytest -q`。**

---

# 十六、Workflow 静态审计

测试至少确认：

```text
唯一 workflow = observability-matrix-gate.yml
唯一 job = observability-matrix-gate
trigger = push + pull_request
runner = ubuntu-latest
python = 3.13
postgres = pgvector/pgvector:pg16
init_db = existing module
matrix command = python scripts/run_matrix_gate.py
```

确认没有：

```text
schedule
workflow_dispatch
release
deployment
repository_dispatch
```

---

# 十七、真实 CI 结果记录

新增：

```text
docs/evaluation/phase-3.12-step-98-github-actions-run-audit.md
```

记录：

```text
Run:
Commit:
Trigger:
Workflow:
Job:
Result:
```

然后：

```text
Python:
PostgreSQL:
pgvector:
init_db:
Matrix:
Gate:
Adapter:
Exit code:
```

---

# 十八、如果首次 CI PASS

记录：

```text
GitHub Actions = PASS
```

并确认：

```text
DB residue = 0
LLM = 0
Network = 0
Baseline unchanged
```

不要修改 Workflow 进行“优化”。

---

# 十九、如果首次 CI FAIL

记录：

```text
GitHub Actions = FAIL
```

然后只修复**确定属于 CI 配置层**的问题。

例如：

```text
DATABASE_URL host
PostgreSQL service health check
Python setup
dependency install
environment variable
```

禁止通过：

```text
修改 baseline
跳过测试
关闭 DB suite
```

来修复。

---

# 二十、Git Diff

最终检查：

```powershell
git status --short
git diff --stat
git diff -- backend
git diff -- .github
```

确认：

```text
生产业务逻辑 = 0
Matrix Gate = unchanged
Adapter = unchanged
Baseline = unchanged
DB schema = unchanged
Prompt = unchanged
LLM = 0
Secrets = 0
```

---

# 二十一、完成报告

严格按照：

```text
Phase 3.12 Step 98 COMPLETE

1. GitHub Actions Run
2. Workflow
3. PostgreSQL
4. pgvector
5. init_db
6. Matrix
7. Gate
8. Adapter
9. Exit Code
10. DB residue
11. LLM / Network
12. Baseline
13. Tests
14. compileall
15. Git Diff
16. CI failure / fix（如有）
17. 当前限制

Phase 3.12 Step 98 READY
Phase 3.12 Step 98 STOP
```

---

# 二十二、强制 STOP

Step 98 完成后立即停止。

不要进入：

```text
Step 99
Baseline Refresh
PR Comment
Dashboard
Telemetry
OpenTelemetry
Prometheus
```

等待下一步指令。
