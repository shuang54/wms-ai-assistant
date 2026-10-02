# Phase 3.12 Step 106 — Merge Queue / merge_group Readiness Audit

## 一、阶段目标

基于 Step 105 已经验证：

```text
Observability Matrix Gate
        ↓
Required Status Check
        ↓
PR Merge Blocking
```

本 Step 只回答一个问题：

> **如果未来启用 GitHub Merge Queue，当前 `Observability Matrix Gate` 是否具备正确的 `merge_group` 触发与检查条件？**

本阶段只做：

```text
Read-only Audit
```

**不启用 Merge Queue。**

**不修改 GitHub Ruleset。**

**不修改 Workflow。**

**不创建 PR。**

**不 push。**

---

# 二、重要背景

当前 Workflow：

```text
.github/workflows/observability-matrix-gate.yml
```

当前触发：

```yaml
on:
  push:
  pull_request:
```

当前没有：

```yaml
merge_group:
```

GitHub 官方说明：

如果 Required Check 被 Merge Queue 使用，GitHub Actions workflow 需要额外监听：

```yaml
merge_group:
```

否则进入 Merge Queue 后，Required Check 可能不会被触发，从而无法满足合并要求。

本阶段只确认当前状态，不修改它。

---

# 三、严格禁止

禁止：

```text
修改 .github/workflows/observability-matrix-gate.yml
修改 Protect Main Ruleset
启用 Merge Queue
创建 Merge Group
创建 PR
push
merge
修改 Required Check
修改 Gate
修改 Adapter
修改 CLI
修改 Baseline
修改 backend
```

禁止：

```text
full pytest
RUN_DB_TESTS
DeepSeek
真实 Merge Queue 操作
```

---

# 四、Step 1：读取 Workflow

检查：

```text
.github/workflows/observability-matrix-gate.yml
```

确认：

```text
workflow name
job name
on.push
on.pull_request
on.merge_group
```

特别判断：

```text
merge_group 是否存在
```

预期当前：

```text
push           = YES
pull_request   = YES
merge_group    = NO
```

---

# 五、Step 2：确认 Required Check

只读检查当前 GitHub Ruleset：

```text
Protect Main
```

确认：

```text
target:
refs/heads/main

enforcement:
active

required_status_checks:
Observability Matrix Gate
```

同时记录：

```text
integration_id
strict
```

不得修改。

---

# 六、Step 3：检查 Merge Queue 状态

通过 GitHub Web UI / 可用的只读 API 检查：

```text
Settings
→ Rules
→ Rulesets
→ Protect Main
```

确认当前是否启用了：

```text
Require merge queue
```

或者等价的：

```text
Merge queue
```

结果必须明确记录：

```text
ENABLED
```

或：

```text
NOT ENABLED
```

如果当前环境无法读取：

```text
NOT AUDITED
```

**不要猜。**

---

# 七、Step 4：检查 Workflow / Required Check 一致性

建立一个只读判断：

### 当前 Merge Queue 未启用 + Workflow 没有 merge_group

这是：

```text
CURRENTLY VALID
```

因为当前没有 Merge Queue。

但是：

```text
FUTURE MERGE_QUEUE_READY = NO
```

原因：

```text
Required Check
+
Merge Queue
+
Workflow lacks merge_group trigger
```

可能导致 Merge Queue 中 Required Check 不被报告。

---

# 八、Step 5：不要修改 Workflow

即使发现：

```text
merge_group = missing
```

也不要现在添加：

```yaml
merge_group:
```

本阶段只记录：

```text
Gap:
Workflow does not declare merge_group trigger.
```

下一阶段如果真的决定启用 Merge Queue，再单独修改。

---

# 九、Step 6：静态 Workflow Audit

继续使用现有：

```text
tests/test_github_actions_matrix_gate.py
```

如果已经有 Workflow parser / AST audit：

优先复用。

如果没有：

可以新增一个非常小的：

```text
tests/test_merge_group_readiness.py
```

但只有在现有测试基础设施无法完成检查时才新增。

检查：

```text
1. workflow exists
2. workflow has push
3. workflow has pull_request
4. workflow does not currently require merge_group
5. job name = observability-matrix-gate
6. workflow does not contain continue-on-error
7. workflow does not contain exit 0
8. workflow does not swallow failures
```

不要测试 GitHub API。

---

# 十、Step 7：Merge Queue Compatibility Contract

如果需要增加测试 Contract，只定义：

```python
MERGE_QUEUE_READINESS_CONTRACT
```

内容：

```text
Current Merge Queue:
NOT ENABLED / NOT AUDITED

Current Workflow:
push = required
pull_request = required
merge_group = absent

Required Check:
Observability Matrix Gate

Current readiness:
NOT READY FOR MERGE_QUEUE
```

注意：

这里的：

```text
NOT READY FOR MERGE_QUEUE
```

不是代码失败。

而是：

> 当前尚未配置 `merge_group` trigger。

---

# 十一、Step 8：测试

只运行相关测试：

```powershell
python -m pytest -q tests/test_github_actions_matrix_gate.py
```

如果新增了专门测试：

```powershell
python -m pytest -q tests/test_merge_group_readiness.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

不要运行：

```text
full pytest
RUN_DB_TESTS
DeepSeek
Matrix execution
GitHub Actions
```

---

# 十二、Step 9：Git Diff

执行：

```powershell
git status --short
git diff --stat
git diff
```

目标：

```text
backend:
0 modified

.github/workflows/:
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

如果新增了测试，只允许：

```text
tests/test_merge_group_readiness.py
```

以及必要的 evaluation/task documentation。

---

# 十三、最终报告

严格输出：

```text
【Phase 3.12 Step 106 COMPLETE】

1. Workflow
- name:
- job:
- push:
- pull_request:
- merge_group:

2. Required Check
- name:
- required:
- integration_id:
- strict:

3. Merge Queue
- status:
- audited:
- result:

4. Compatibility
- Current CI:
- Current Merge Queue:
- Future Merge Queue readiness:

5. Identified Gap
- ...

6. Tests
- github_actions_matrix_gate:
- merge_group_readiness:
- compileall:

7. Git Diff
- workflow modified:
- backend modified:
- gate modified:
- adapter modified:
- cli modified:
- baseline modified:

8. Network / DB / LLM
- network:
- DB:
- DeepSeek:

9. Conclusion
- CURRENT CI READY:
- MERGE_QUEUE READY:
```

---

# 十四、预期结论

如果当前 Workflow 确实没有：

```yaml
merge_group:
```

则预期：

```text
CURRENT CI READY = YES

MERGE_QUEUE READY = NO

Reason:
Required Check exists, but workflow has no merge_group trigger.
```

这是**发现配置缺口**，不是要求本阶段修复。

---

# 十五、硬停止

完成后：

**立即 STOP。**

不要：

```text
启用 Merge Queue
修改 Ruleset
修改 Workflow
添加 merge_group
创建 PR
Merge
PR Automation
PR Comment
Dashboard
Telemetry
OpenTelemetry
Prometheus
Langfuse
```

等待下一步指令。
