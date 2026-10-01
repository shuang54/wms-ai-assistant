# Phase 3.12 Step 97：GitHub Actions 最小 CI 接入

## 一、阶段目标

将已经验证完成的：

```text
Regression Matrix
        ↓
Matrix Baseline Gate
        ↓
CI Adapter
        ↓
Local CLI
        ↓
exit code 0 / 1
```

正式接入：

```text
GitHub Actions
```

最终形成：

```text
Git Push / Pull Request
        ↓
GitHub Actions
        ↓
python scripts/run_matrix_gate.py
        ↓
PASS → exit 0 → CI success
DRIFT → exit 1 → CI failure
```

GitHub Actions 会根据步骤/Action 的退出码判断成功或失败：`0` 为 success，任何非零值为 failure。

---

# 二、严格范围

## 允许新增

```text
.github/workflows/observability-matrix-gate.yml
docs/evaluation/phase-3.12-step-97-github-actions.md
tests/test_github_actions_matrix_gate.py
```

如果当前项目已有 workflow 目录，则遵循现有结构。

---

# 三、禁止

本阶段禁止：

```text
Baseline Refresh
自动更新 Baseline
Dashboard
Telemetry
OpenTelemetry
Prometheus
Langfuse
Sentry
PR Comment
GitHub Annotation
Release
Deploy
```

禁止修改：

```text
MatrixExecutionSummary
MatrixExecutionBaseline
MatrixBaselineGateResult
evaluate_matrix_baseline_gate()
compare_matrix_execution_baseline()
adapt_gate_result_to_exit_code()
scripts/run_matrix_gate.py
```

禁止：

```text
RAG
Tool
T2SQL
Validator
Executor
Router
Orchestrator
Prompt
LLM Provider
```

不要修改数据库结构。

---

# 四、Workflow 最小职责

创建：

```text
.github/workflows/observability-matrix-gate.yml
```

只负责：

```text
Checkout
  ↓
Setup Python
  ↓
Install project dependencies
  ↓
Run Matrix Gate CLI
```

核心命令：

```powershell
python scripts/run_matrix_gate.py
```

不要在 YAML 中重新实现：

```text
PASS / DRIFT 判断
Baseline comparison
Exit code mapping
```

全部由项目现有代码完成。

---

# 五、触发条件

第一版只支持：

```yaml
on:
  pull_request:
  push:
```

不要增加：

```text
schedule
workflow_dispatch
release
deployment
repository_dispatch
```

本阶段保持最简单。

---

# 六、Runner

使用：

```yaml
runs-on: ubuntu-latest
```

原因：

项目 CLI 本身已经是：

```text
Python
```

并且 Step 96 已经证明本地 CLI 能够执行。

---

# 七、Python Setup

优先使用官方：

```text
actions/checkout
actions/setup-python
```

使用项目当前 Python 版本。

**先阅读项目实际 Python 版本配置。**

不要凭空选择：

```text
3.10
3.11
3.12
3.13
```

如果项目已有：

```text
pyproject.toml
.python-version
runtime.txt
```

优先复用。

---

# 八、依赖安装

先检查项目当前依赖管理方式：

```text
requirements.txt
pyproject.toml
poetry.lock
uv.lock
```

必须复用现有方式。

不要新增：

```text
pip install 某个随机包
```

不要因为 CI 而修改 production dependency。

如果项目使用：

```text
requirements.txt
```

则安装现有 requirements。

如果使用：

```text
pyproject.toml
```

则使用现有项目方式。

---

# 九、数据库环境

这是本阶段重点。

Step 96 的 CLI 会执行：

```text
Offline Matrix
+
DB Matrix
```

因此 GitHub Actions 必须提供：

```text
PostgreSQL + pgvector
```

但是：

### 不允许连接真实生产数据库。

必须使用：

```text
GitHub Actions ephemeral PostgreSQL
```

或者项目已有 CI PostgreSQL 方案。

---

# 十、DB Service

如果项目现有测试已经定义 PostgreSQL service：

**直接复用。**

否则才允许在 workflow 中增加最小：

```yaml
services:
  postgres:
```

要求：

```text
PostgreSQL
pgvector
```

版本尽可能与项目当前开发/测试环境一致。

先检查项目当前 PostgreSQL / pgvector 版本。

不要自行升级数据库版本。

---

# 十一、数据库初始化

优先复用现有：

```text
tests fixtures
scripts/init_db.py
migration
```

不要重新创建第二套数据库初始化逻辑。

CI 中应该：

```text
PostgreSQL start
    ↓
existing DB initialization
    ↓
existing test schema
    ↓
run_matrix_gate.py
```

---

# 十二、Secrets

CI 不允许把：

```text
DeepSeek API Key
SiliconFlow API Key
database password
```

写入：

```text
workflow YAML
```

本阶段 Matrix Gate 本身要求：

```text
LLM = 0
Network = 0
```

因此：

**不要配置 DeepSeek API Key。**

---

# 十三、LLM Boundary

Workflow 必须明确：

```text
DeepSeek = 0
LLM = 0
```

不要因为项目启动环境而初始化真实 LLM。

如果现有 import 会读取：

```text
DEEPSEEK_API_KEY
```

必须确认：

```text
Matrix Gate path
```

不会真正调用 LLM。

如果发现 CLI 间接初始化 LLM：

**停止并报告，不要为了 CI 临时 mock 掉。**

---

# 十四、Network Boundary

CI 运行 Matrix Gate 时：

```text
Network calls = 0
```

不允许：

```text
DeepSeek
SiliconFlow
OpenAI
Embedding API
```

本阶段不是 LLM Evaluation。

---

# 十五、Workflow Step

核心 step：

```yaml
- name: Run observability matrix gate
  run: python scripts/run_matrix_gate.py
```

不要：

```yaml
continue-on-error: true
```

因为：

```text
DRIFT → exit 1
```

必须真正让 CI 失败。

GitHub Actions 默认会根据 shell 命令的非零退出码将 step 标记为失败。

---

# 十六、不要捕获失败

禁止：

```yaml
run: |
  python scripts/run_matrix_gate.py || true
```

禁止：

```yaml
continue-on-error: true
```

禁止：

```yaml
if: failure()
```

来吞掉 Gate failure。

本阶段：

```text
DRIFT
 ↓
exit 1
 ↓
workflow failure
```

这是预期行为。

---

# 十七、Workflow 输出

保持 Step 96 CLI 的现有输出：

```text
Matrix Gate
Status: PASS
Exit code: 0
```

或者：

```text
Matrix Gate
Status: DRIFT
Drifts:
- DB_EXECUTION_DRIFT
Exit code: 1
```

不要增加：

```text
JSON
HTML
Dashboard event
PR comment
```

---

# 十八、测试 Workflow 文件

新增：

```text
tests/test_github_actions_matrix_gate.py
```

这里不要真的访问 GitHub。

使用：

```text
YAML static audit
```

验证 workflow：

### 1

存在：

```text
.github/workflows/observability-matrix-gate.yml
```

### 2

触发：

```text
pull_request
push
```

### 3

存在：

```text
actions/checkout
actions/setup-python
```

### 4

存在：

```text
python scripts/run_matrix_gate.py
```

### 5

没有：

```text
continue-on-error
```

### 6

没有：

```text
|| true
```

### 7

没有：

```text
--refresh
--update-baseline
```

### 8

没有：

```text
DEEPSEEK_API_KEY
SILICONFLOW_API_KEY
```

### 9

没有：

```text
OpenAI
DeepSeek
SiliconFlow
```

### 10

没有：

```text
curl
wget
requests
httpx
```

等主动网络调用。

---

# 十九、Workflow Security

静态扫描 executable YAML content，确认不存在：

```text
api_key
authorization
password
database_url
```

不要对普通说明性 docstring / comments 做脆弱全文判断。

---

# 二十、Baseline Security

Workflow 中不得出现：

```text
MATRIX_EXECUTION_BASELINE =
```

不得：

```text
json.dump
write baseline
refresh baseline
update baseline
```

Baseline 继续只读。

---

# 二十一、CI Failure Semantics

明确冻结：

```text
PASS
 ↓
exit 0
 ↓
GitHub Actions success
```

```text
DRIFT
 ↓
exit 1
 ↓
GitHub Actions failure
```

GitHub 官方文档确认非零 exit code 会导致 action/check run failure。

---

# 二十二、不要实现 PR 评论

即使未来可以通过 GitHub API 发布：

```text
Drift summary
```

本阶段不要做。

不要增加：

```text
GITHUB_TOKEN
```

也不要调用：

```text
GitHub REST API
GitHub GraphQL API
gh CLI
```

---

# 二十三、不要做 Matrix 分层 CI

本阶段只有一个 Gate：

```text
observability-matrix-gate
```

不要拆：

```text
offline-gate
db-gate
rag-gate
tool-gate
t2s-gate
trace-gate
timeline-gate
```

Matrix 内部已经负责这些。

---

# 二十四、测试命令

先运行：

```powershell
python -m pytest -q tests/test_github_actions_matrix_gate.py
```

然后：

```powershell
python -m pytest -q tests/test_matrix_ci_adapter.py tests/test_matrix_gate_cli.py
```

然后：

```powershell
python -m pytest -q tests/test_assistant_trace_timeline_regression.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

---

# 二十五、本地 CI 等价验证

本地执行：

```powershell
python scripts/run_matrix_gate.py
```

要求：

```text
Status: PASS
Exit code: 0
```

并确认 shell：

```text
$LASTEXITCODE
```

等于：

```text
0
```

PowerShell：

```powershell
python scripts/run_matrix_gate.py
$LASTEXITCODE
```

---

# 二十六、Git Diff

检查：

```powershell
git status --short
git diff --stat
git diff -- backend
git diff -- .github
```

确认：

```text
□ 生产业务逻辑 = 0
□ Matrix Gate = unchanged
□ Adapter = unchanged
□ Baseline = unchanged
□ DB schema = unchanged
□ API = unchanged
□ Prompt = unchanged
□ LLM = 0
□ Secrets = 0
□ Dashboard = 0
□ Telemetry = 0
```

---

# 二十七、Documentation

新增：

```text
docs/evaluation/phase-3.12-step-97-github-actions.md
```

记录：

```text
Step 97

Pull Request / Push
        ↓
GitHub Actions
        ↓
PostgreSQL CI Service
        ↓
run_matrix_gate.py
        ↓
MatrixBaselineGateResult
        ↓
CI Adapter
        ↓
exit code
```

以及：

```text
PASS  → 0 → CI success
DRIFT → 1 → CI failure
```

明确：

```text
本阶段未实现：
- Baseline Refresh
- PR Comment
- Dashboard
- Telemetry
- OpenTelemetry
- Prometheus
```

---

# 二十八、完成报告

严格：

```text
Phase 3.12 Step 97 COMPLETE

1. Workflow
2. Trigger
3. Python
4. PostgreSQL
5. Matrix Gate
6. Adapter
7. Exit Code
8. LLM / Network
9. Secrets
10. Workflow Security
11. Tests
12. Local CI Equivalent
13. compileall
14. Git Diff
15. 当前限制

Phase 3.12 Step 97 READY
Phase 3.12 Step 97 STOP
```

---

# 二十九、强制 STOP

完成后立即停止。

不要进入：

```text
Step 98
Baseline Refresh
PR Comment
Dashboard
Telemetry
OpenTelemetry
Prometheus
```

等待下一步指令。
