# Phase 3.12 Step 108 — CI Governance Contract Freeze

## 一、阶段目标

冻结目前已经实际验证的 CI Governance Contract：

```text
Protect Main
    ↓
Required Check
    ↓
Observability Matrix Gate
    ↓
0 / 1
    ↓
PR Merge Blocking
```

同时冻结：

```text
Merge Queue = DEFER
```

本阶段只做：

```text
Contract
+
Regression Test
+
Architecture Documentation
```

不增加新的 CI 能力。

---

# 二、当前已验证事实

必须以当前仓库真实状态为准：

```text
Workflow:
push = YES
pull_request = YES
merge_group = NO

Required Check:
Observability Matrix Gate = YES

Ruleset:
Protect Main = active

Merge Queue:
NOT ENABLED

Decision:
DEFER
```

Step 105 已实际证明：

```text
Gate PASS
→ Required Check PASS
→ PR 可以满足检查

Gate FAIL
→ Required Check FAIL
→ PR Merge Blocking
```

GitHub Required Checks 必须在最新 commit 上成功后才能满足合并要求。

---

# 三、严格禁止

本阶段禁止：

```text
修改 .github/workflows/
修改 Ruleset
启用 Merge Queue
添加 merge_group
修改 Required Check
修改 Gate
修改 Adapter
修改 CLI
修改 Baseline
修改 backend
创建 PR
push
merge
```

禁止：

```text
full pytest
RUN_DB_TESTS
DeepSeek
GitHub Actions 实际运行
```

---

# 四、Step 1：冻结 CI Governance Contract

在现有：

```text
tests/test_github_actions_matrix_gate.py
```

中增加：

```python
CI_GOVERNANCE_CONTRACT
```

至少表达：

```text
workflow_name
job_name
required_check
required_branch
merge_queue_enabled
merge_group_trigger
decision
```

目标语义：

```text
workflow_name = "Observability Matrix Gate"

job_name = "observability-matrix-gate"

required_check = "Observability Matrix Gate"

required_branch = "main"

merge_queue_enabled = False

merge_group_trigger = False

decision = "DEFER"
```

不要加入动态 GitHub API 数据。

Contract 是：

> 当前架构事实的冻结描述。

---

# 五、Step 2：禁止 Contract 承诺未来启用 Merge Queue

增加测试：

```text
decision_does_not_promise_future_merge_queue
```

确保 Contract 不表达：

```text
will_enable
must_enable
enable_next
```

只表达：

```text
DEFER
```

---

# 六、Step 3：Required Check Identity

增加测试确保：

```text
workflow job name
Required Check name
Ruleset expected check
```

当前保持：

```text
Observability Matrix Gate
```

不要出现：

```text
Observability Gate
Matrix Gate
CI Gate
Merge Gate
```

等第二套名字。

---

# 七、Step 4：Branch Scope

冻结：

```text
refs/heads/main
```

Contract 必须明确：

```text
Required Check applies to main
```

不要把：

```text
main
```

扩大成：

```text
*
all branches
```

---

# 八、Step 5：Merge Queue Boundary

冻结：

```text
merge_queue_enabled = False
merge_group_trigger = False
decision = DEFER
```

并测试：

```text
Current CI remains valid without merge_group
```

以及：

```text
Future Merge Queue requires separate enablement
```

不要在本阶段修改 Workflow。

---

# 九、Step 6：Gate Ownership

确认 Governance Contract 不复制：

```text
PASS / DRIFT
```

判断逻辑。

保持：

```text
MatrixBaseline
      ↓
evaluate_matrix_baseline_gate()
      ↓
MatrixBaselineGateResult
      ↓
CI Adapter
      ↓
exit code
```

Governance Contract 只描述：

```text
谁负责
叫什么
保护什么
当前是否启用
```

不重新实现判断逻辑。

---

# 十、Step 7：Workflow Ownership

确认：

```text
Workflow
```

只负责：

```text
checkout
setup Python
install dependencies
init DB
run CLI
```

Workflow 不重新实现：

```text
baseline comparison
PASS/DRIFT
exit mapping
```

保持现状。

---

# 十一、Step 8：Required Check Ownership

冻结：

```text
GitHub Ruleset
    ↓
requires
    ↓
Observability Matrix Gate
```

而不是：

```text
Workflow 自己决定 required
```

不要把 GitHub Governance 和应用 Gate 混在一起。

---

# 十二、Step 9：Regression Tests

只修改：

```text
tests/test_github_actions_matrix_gate.py
```

增加约：

```text
5～8 tests
```

覆盖：

```text
CI Governance Contract
Required Check identity
Branch scope
Merge Queue DEFER
merge_group absent
Gate ownership
Workflow ownership
```

不要新建第二个 CI regression framework。

---

# 十三、Step 10：Documentation

新增：

```text
docs/evaluation/phase-3.12-ci-governance-contract.md
```

记录：

```text
Current CI:
push + pull_request

Required:
Observability Matrix Gate

Protected branch:
main

Merge Queue:
NOT ENABLED

merge_group:
ABSENT

Decision:
DEFER
```

并明确：

> 如果未来启用 Merge Queue，必须先单独完成 Workflow `merge_group` readiness，再启用 Merge Queue。

GitHub 官方要求 Merge Queue 场景下 Required Check workflow 监听 `merge_group`。

---

# 十四、Step 11：测试

只运行：

```powershell
python -m pytest -q tests/test_github_actions_matrix_gate.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

不要：

```text
full pytest
RUN_DB_TESTS
DeepSeek
GitHub Actions
```

---

# 十五、Step 12：Git Diff

确认：

```text
.github/workflows/
    0 modified

backend/
    0 modified

Gate
    0 modified

Adapter
    0 modified

CLI
    0 modified

Baseline
    0 modified
```

允许：

```text
tests/test_github_actions_matrix_gate.py
docs/evaluation/phase-3.12-ci-governance-contract.md
```

---

# 十六、最终报告

严格：

```text
【Phase 3.12 Step 108 COMPLETE】

1. CI Governance Contract
2. Workflow Identity
3. Required Check Identity
4. Branch Scope
5. Gate Ownership
6. Workflow Ownership
7. Merge Queue
8. merge_group
9. Tests
10. compileall
11. Git Diff
12. Network / DB / LLM
13. Conclusion

CURRENT CI GOVERNANCE = FROZEN
MERGE QUEUE = DEFER
```

---

# 十七、硬停止

完成后：

**立即 STOP。**

不要进入：

```text
Merge Queue Enablement
merge_group implementation
PR Automation
PR Comment
Dashboard
Telemetry
OpenTelemetry
Prometheus
Langfuse
```

等待下一步指令。
