# Phase 3.12 Step 109 — CI Governance Self-Consistency Audit

## 一、阶段目标

验证 Step 108 冻结的：

```text
CI_GOVERNANCE_CONTRACT
```

与当前仓库中的静态 Workflow / 测试 Contract 保持一致。

本阶段只做：

```text
Self-Consistency Audit
```

不访问 GitHub API。

不修改 GitHub 设置。

不修改 Workflow。

---

# 二、必须验证的关系

冻结事实：

```text
CI_GOVERNANCE_CONTRACT
        │
        ├── workflow_name
        ├── job_name
        ├── required_check
        ├── required_branch
        ├── merge_queue_enabled
        ├── merge_group_trigger
        └── decision
```

必须满足：

```text
workflow_name
    ==
Workflow name

job_name
    ==
Workflow job id

required_check
    ==
GitHub Actions job check name

required_branch
    ==
main

merge_queue_enabled
    ==
False

merge_group_trigger
    ==
False

decision
    ==
DEFER
```

---

# 三、严格范围

允许修改：

```text
tests/test_github_actions_matrix_gate.py
docs/evaluation/
```

禁止修改：

```text
.github/workflows/
backend/
Gate
Adapter
CLI
Baseline
Ruleset
GitHub Settings
```

禁止：

```text
GitHub API
PR
push
merge
Merge Queue
DeepSeek
DB
```

---

# 四、Step 1：Workflow Identity Audit

从：

```text
.github/workflows/observability-matrix-gate.yml
```

读取真实 YAML。

验证：

```text
workflow name
job id
job name
```

与：

```text
CI_GOVERNANCE_CONTRACT
```

完全一致。

特别检查：

```text
job id = observability-matrix-gate
job name = Observability Matrix Gate
```

---

# 五、Step 2：Trigger Audit

验证 Workflow 当前：

```text
push = present
pull_request = present
merge_group = absent
```

与：

```text
merge_group_trigger = False
```

一致。

不要把：

```text
future merge_group support
```

误判成当前 trigger。

---

# 六、Step 3：Branch Scope Audit

验证 Workflow 当前没有把：

```text
main
```

错误扩大为：

```text
*
all branches
refs/heads/*
```

注意：

Workflow trigger 与 Ruleset branch scope 是两个不同概念。

不要把它们混为同一个配置。

---

# 七、Step 4：Required Check Identity Audit

验证：

```text
CI_GOVERNANCE_CONTRACT["required_check"]
```

与 Workflow job name：

```text
Observability Matrix Gate
```

一致。

同时确认不存在第二套 CI Gate 名称。

禁止出现：

```text
CI Gate
Merge Gate
Observability Gate
Merge Queue Gate
```

---

# 八、Step 5：Ownership Boundary Audit

验证 Governance Contract 中没有出现：

```text
PASS
DRIFT
exit_code
MATRIX_EXECUTION_BASELINE
evaluate_matrix_baseline_gate
adapt_gate_result_to_exit_code
```

这些属于：

```text
Gate / Adapter
```

不是 Governance Contract。

同时验证 Workflow 中没有重新实现：

```text
PASS / DRIFT
baseline comparison
exit mapping
```

---

# 九、Step 6：Merge Queue Boundary Audit

验证：

```text
merge_queue_enabled = False
merge_group_trigger = False
decision = DEFER
```

三者保持一致。

并验证：

```text
decision == DEFER
```

不能推导成：

```text
future_enablement == True
```

Contract 只描述当前决策。

---

# 十、Step 7：Contract Immutability

验证：

```python
CI_GOVERNANCE_CONTRACT
```

不能被测试运行过程修改。

例如：

```python
before = dict(CI_GOVERNANCE_CONTRACT)

run_audit()

after = dict(CI_GOVERNANCE_CONTRACT)

assert before == after
```

---

# 十一、Step 8：No Dynamic GitHub Dependency

这是本 Step 很重要的一点。

CI Governance Self-Consistency Audit：

**不得依赖 GitHub API。**

不得出现：

```text
requests
httpx
urllib
github
PyGithub
GITHUB_TOKEN
GH_TOKEN
```

Audit 必须完全基于：

```text
local workflow
local contract
local tests
```

这样才能在离线环境稳定运行。

---

# 十二、Step 9：测试

继续复用：

```text
tests/test_github_actions_matrix_gate.py
```

不要创建第二个 CI Governance Framework。

增加约：

```text
6～10 tests
```

覆盖：

```text
Workflow identity
Job identity
Trigger identity
Required check identity
Branch scope
Merge Queue boundary
Ownership boundary
No dynamic GitHub dependency
Contract immutability
```

---

# 十三、Step 10：compileall

执行：

```powershell
python -m compileall -q backend tests scripts
```

要求：

```text
0 errors
```

---

# 十四、Step 11：Git Diff

确认：

```text
.github/workflows/
    0 modified

backend/
    0 modified

Gate:
    0 modified

Adapter:
    0 modified

CLI:
    0 modified

Baseline:
    0 modified
```

只允许：

```text
tests/test_github_actions_matrix_gate.py
docs/evaluation/phase-3.12-ci-governance-self-consistency.md
```

---

# 十五、最终报告

严格输出：

```text
【Phase 3.12 Step 109 COMPLETE】

1. Workflow Identity
2. Job Identity
3. Trigger Identity
4. Required Check Identity
5. Branch Scope
6. Gate Ownership
7. Workflow Ownership
8. Merge Queue Boundary
9. Dynamic GitHub Dependency
10. Contract Immutability
11. Tests
12. compileall
13. Git Diff
14. Network / DB / LLM
15. Conclusion

CI GOVERNANCE SELF-CONSISTENCY = PASS
```

如果发现任何不一致：

```text
CI GOVERNANCE SELF-CONSISTENCY = FAIL
```

只报告问题，不自动修复。

---

# 十六、硬停止

完成后：

**立即 STOP。**

不要进入：

```text
Step 110
Merge Queue
merge_group
PR Automation
Dashboard
Telemetry
OpenTelemetry
Prometheus
Langfuse
```

等待下一步指令。
