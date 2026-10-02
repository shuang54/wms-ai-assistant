# Phase 3.12 Step 101：Repository Governance / Required Check Readiness

## 一、阶段目标

Step 100 已完成：

```text
Matrix Baseline
    ↓
Gate
    ↓
Adapter
    ↓
CLI
    ↓
GitHub Actions
```

并且真实 GitHub Actions 已经成功。

本阶段只回答一个问题：

> `Observability Matrix Gate` 是否已经具备成为 `main` 分支 Required Status Check 的条件？

**本阶段不修改 GitHub Repository Settings。**

---

# 二、严格范围

本阶段允许：

```text
GitHub API / GitHub Web 只读查询
tests/test_github_actions_matrix_gate.py
docs/evaluation/
docs/architecture.md
```

允许新增：

```text
docs/evaluation/phase-3.12-step-101-repository-governance-audit.md
```

如果现有测试足够，不新增测试文件。

---

# 三、绝对禁止

不要修改：

```text
.github/workflows/observability-matrix-gate.yml
backend/app/
scripts/run_matrix_gate.py
MatrixExecutionBaseline
MatrixBaselineGateResult
evaluate_matrix_baseline_gate()
adapt_gate_result_to_exit_code()
```

禁止：

```text
创建 Branch Protection
创建 Ruleset
启用 Required Status Check
修改 main protection
要求 PR review
Require branches up-to-date
Merge Queue
Auto Merge
PR Comment
GitHub App
GitHub Token
```

本阶段：

```text
READ ONLY
```

---

# 四、审计目标

检查：

```text
main
```

当前是否存在：

```text
Branch Protection
```

或者：

```text
Ruleset
```

---

# 五、Branch Protection 查询

尝试读取：

```text
GET /repos/{owner}/{repo}/branches/main/protection
```

如果：

```text
200
```

记录：

```text
Branch Protection = ENABLED
```

并读取：

```text
required_status_checks
required_pull_request_reviews
enforce_admins
restrictions
required_linear_history
allow_force_pushes
allow_deletions
```

---

# 六、如果返回 401 / 403

如果：

```text
401 Unauthorized
403 Forbidden
```

不要猜测。

明确记录：

```text
Branch Protection = NOT AUDITED
Reason = GitHub API authorization unavailable
```

不要因此认为：

```text
enabled
```

也不要认为：

```text
disabled
```

---

# 七、Ruleset 查询

如果当前凭据允许读取 Ruleset：

查询 repository rulesets。

重点检查：

```text
target = main
enforcement = active
```

以及是否包含：

```text
required_status_checks
```

GitHub Ruleset 支持对目标分支要求 status checks；Required Status Check 可以指定具体 check 名称。

---

# 八、Required Check 名称

确认真实 GitHub Check：

```text
Observability Matrix Gate
```

对应：

```text
job name:
observability-matrix-gate
```

GitHub Actions 的 required check 名称通常来自 job name，因此这里必须保持：

```text
observability-matrix-gate
```

唯一。

不要创建第二个同名 job。

GitHub 官方也特别提醒，保护分支要求 status check 时，job name 应保持唯一，否则可能造成 check 结果歧义。

---

# 九、最新成功 Evidence

读取最新 commit：

```text
510ab81
```

确认：

```text
Observability Matrix Gate
```

存在：

```text
completed
success
```

并确认：

```text
commit SHA == 510ab81
```

Required Status Check 必须针对最新 commit 成功，旧 commit 的成功不能代替最新 commit。

---

# 十、Push / Pull Request Trigger

确认 Workflow 当前：

```yaml
on:
  push:
  pull_request:
```

确认没有：

```text
workflow_dispatch only
schedule only
```

因为 GitHub Actions workflow job 要作为 Pull Request Required Check 被评估，需要使用 GitHub 支持的相关事件；`push` / `pull_request` 是有效触发方式。

---

# 十一、Merge Queue

只审计，不修改。

检查仓库当前是否使用：

```text
Merge Queue
```

如果没有：

```text
Merge Queue = NOT ENABLED
```

如果已经启用：

检查 Workflow 是否包含：

```yaml
merge_group:
```

GitHub 官方说明：如果 Required Check 被 Merge Queue 使用，Workflow 需要额外响应 `merge_group`，否则 Merge Queue 中可能不会产生该 Required Check。

如果当前没有 Merge Queue：

**不要新增 `merge_group`。**

---

# 十二、当前建议状态

根据 Step 100：

```text
Workflow CI readiness:
PASS

Latest Check:
PASS

Branch Protection:
NOT AUDITED

Required Status Check:
NOT ENABLED / NOT AUDITED
```

注意：

如果 API 无法读取 Repository Settings：

不要写：

```text
Required Status Check = disabled
```

只能写：

```text
NOT AUDITED
```

---

# 十三、Governance Readiness 判定

定义：

```text
CI_READINESS
```

而不是：

```text
REPOSITORY_PROTECTION
```

### READY

如果：

```text
Workflow PASS
Latest Check PASS
Required Check name stable
No duplicate job
No workflow bypass
```

则：

```text
CI Governance Readiness = READY
```

但：

```text
Repository Protection = NOT AUDITED
```

---

### NOT READY

如果发现：

```text
duplicate job
workflow bypass
latest commit missing check
unexpected check source
workflow only manual trigger
```

则：

```text
CI Governance Readiness = NOT READY
```

---

# 十四、不要修改 Branch Protection

即使判断：

```text
READY
```

也不要执行：

```text
enable required status check
```

本阶段只是：

```text
Audit
```

真正启用保护应该单独作为后续 Phase。

---

# 十五、测试

只运行与 Governance Audit 直接相关的测试：

```powershell
python -m pytest -q tests/test_github_actions_matrix_gate.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

不要重新运行：

```text
full pytest
DB Matrix
DeepSeek
```

因为 Step 99 已经完成真实 DB Matrix + GitHub Actions 验证。

---

# 十六、Documentation

新增：

```text
docs/evaluation/phase-3.12-step-101-repository-governance-audit.md
```

记录：

```text
1. Repository
2. Main Branch
3. Branch Protection
4. Ruleset
5. Required Status Check
6. Latest Successful Check
7. Workflow Trigger
8. Merge Queue
9. CI Governance Readiness
10. Repository Protection Status
11. Authorization Limitations
```

---

# 十七、Git Diff

确认：

```powershell
git status --short
git diff --stat
git diff -- backend
git diff -- .github
```

要求：

```text
backend = unchanged
.github = unchanged
Gate = unchanged
Adapter = unchanged
CLI = unchanged
Baseline = unchanged
```

允许：

```text
docs/
tests/
```

---

# 十八、最终报告

严格输出：

```text
Phase 3.12 Step 101 COMPLETE

1. Branch Protection
2. Ruleset
3. Required Status Check
4. Latest Check
5. Workflow Trigger
6. Merge Queue
7. CI Governance Readiness
8. Repository Protection Status
9. Tests
10. compileall
11. Git Diff
12. Authorization Limitations

Phase 3.12 Step 101 READY
Phase 3.12 Step 101 STOP
```

---

# 十九、硬停止

完成后立即停止。

不要进入：

```text
Step 102
Enable Branch Protection
Enable Required Status Check
Merge Queue
PR Automation
PR Comment
Dashboard
Telemetry
OpenTelemetry
Prometheus
Langfuse
```

等待下一步指令。
