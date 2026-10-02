# Phase 3.12 Step 105 Cleanup — 清理 Required Check 阻断测试

## 一、目标

Step 105 已完成：

```text
Gate PASS              ✅
Gate FAIL              ✅
Required Check         ✅
PR Merge Blocking      ✅
```

现在只清理：

```text
PR #1
ci/step-105-required-check-test
failure probe
temporary docs
```

**不要进入 Step 106。**

---

# 二、严格禁止

本 Step 禁止：

```text
修改 Ruleset
修改 Required Check
修改 Workflow
修改 Gate
修改 Adapter
修改 CLI
修改 Baseline
修改 backend
修改业务代码
Merge PR #1
```

---

# 三、Step 1：关闭 PR #1

GitHub Web UI：

```text
Pull requests
→ #1
→ Close pull request
```

PR：

```text
https://github.com/shuang54/wms-ai-assistant/pull/1
```

**不要 Merge。**

GitHub 官方支持直接关闭 PR 而不合并。([docs.github.com](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/closing-a-pull-request))

---

# 四、Step 2：删除临时远程分支

关闭 PR 后，在 PR 页面底部：

```text
Delete branch
```

删除：

```text
ci/step-105-required-check-test
```

GitHub 要求关联开放 PR 的 branch 先关闭 PR，之后才能删除。([docs.github.com](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-branches-in-your-repository/deleting-and-restoring-branches-in-a-pull-request))

确认不要删除：

```text
main
```

---

# 五、Step 3：本地同步

让 CodeBuddy 执行：

```powershell
git fetch origin --prune
git switch main
git pull --ff-only origin main
```

---

# 六、Step 4：确认 Main 未污染

执行：

```powershell
git rev-parse HEAD
git rev-parse origin/main
```

预期：

```text
HEAD == origin/main
```

并且应该仍然是：

```text
4b8a19f468fb2ae40208cb6c9e878b2d4056000b
```

---

# 七、Step 5：检查临时文件

执行：

```powershell
git ls-tree -r HEAD --name-only | Select-String "step-105"
```

预期：

```text
无输出
```

特别确认不存在：

```text
tests/test_step_105_ci_failure_probe.py
docs/evaluation/phase-3.12-step-105-required-check-test.md
```

---

# 八、Step 6：工作区状态

执行：

```powershell
git status --short
```

目标：

```text
clean
```

如果存在之前任务说明副本：

```text
docs/decisions/Phase 3.12 Step 105 — ...
```

不要擅自删除或修改。

只报告。

---

# 九、Step 7：确认远程分支已删除

执行：

```powershell
git branch -r
```

确认：

```text
origin/ci/step-105-required-check-test
```

不存在。

如果 GitHub Web UI 已经删除，但本地 remote-tracking ref 仍存在：

```powershell
git fetch origin --prune
```

重新检查。

---

# 十、Step 8：验证 Ruleset 未变化

只读确认：

```text
Protect Main
Required Status Check:
Observability Matrix Gate
strict=false
enforcement=active
```

**不要修改任何设置。**

---

# 十一、测试

只运行：

```powershell
python -m pytest -q tests/test_github_actions_matrix_gate.py
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
full Matrix
```

---

# 十二、最终报告

严格输出：

```text
【Phase 3.12 Step 105 CLEANUP COMPLETE】

1. PR #1
- status:
- merged:
- closed:

2. Temporary Branch
- name:
- remote deleted:

3. Main
- HEAD:
- origin/main:
- equal:

4. Temporary Files
- failure probe:
- temporary docs:
- main tree clean:

5. Workspace
- status:

6. Ruleset
- Protect Main:
- Observability Matrix Gate:
- Required:
- strict:
- modified:

7. Tests
- github_actions_matrix_gate:
- compileall:

8. Production Code
- modified: 0

9. Workflow
- modified: 0

10. Final
- CLEANUP COMPLETE
```

---

# 十三、硬停止

完成后：

**立即 STOP。**

不要进入：

```text
Phase 3.12 Step 106
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
