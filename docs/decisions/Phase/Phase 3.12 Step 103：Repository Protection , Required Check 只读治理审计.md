继续 Phase 3.12。

当前状态：

```text
Phase 3.12 Step 102 = COMPLETE

origin/main:
4b8a19f468fb2ae40208cb6c9e878b2d4056000b

Observability Matrix Gate:
Run #4
36964609587
success

CI Governance Readiness:
READY

Branch Protection:
NOT AUDITED

Required Status Check:
NOT ENABLED / NOT AUDITED

Merge Queue:
NOT AUDITED
```

---

# Phase 3.12 Step 103：Repository Protection / Required Check 只读治理审计

## 一、唯一目标

只回答：

> 当前 GitHub Repository 是否已经真正启用了 Branch Protection / Ruleset / Required Status Check，以及 `Observability Matrix Gate` 是否已经成为 `main` 的 Required Check。

本阶段：

**只读审计。**

不要修改 GitHub Repository 设置。

---

# 二、严格禁止

禁止：

```text
创建 Branch Protection
修改 Branch Protection
创建 Ruleset
修改 Ruleset
启用 Required Status Check
删除 Required Status Check
启用 Merge Queue
修改 Merge Queue
启用 Auto Merge
修改 PR Review 设置
修改 Workflow
修改 Gate
修改 Adapter
修改 CLI
修改 Baseline
修改 backend
```

禁止任何：

```text
POST
PUT
PATCH
DELETE
```

GitHub 管理 API 写操作。

本阶段必须：

```text
READ ONLY
```

---

# 三、Repository

固定：

```text
owner = shuang54
repo  = wms-ai-assistant
branch = main
```

---

# 四、Step 1：Branch Protection

查询：

```text
GET /repos/shuang54/wms-ai-assistant/branches/main/protection
```

记录 HTTP 状态。

如果：

```text
200
```

读取：

```text
required_status_checks
required_pull_request_reviews
enforce_admins
restrictions
required_linear_history
allow_force_pushes
allow_deletions
required_conversation_resolution
```

重点：

```text
required_status_checks
```

检查：

```text
strict
contexts
checks
```

确认是否包含：

```text
Observability Matrix Gate
```

如果：

```text
401
403
404
```

必须：

```text
NOT AUDITED
```

绝对不要推断：

```text
enabled
disabled
```

---

# 五、Step 2：Required Status Check 专项查询

如果权限允许，查询：

```text
GET /repos/shuang54/wms-ai-assistant/branches/main/protection/required_status_checks
```

记录：

```text
HTTP status
strict
contexts
checks
```

重点寻找：

```text
Observability Matrix Gate
```

如果权限不足：

```text
NOT AUDITED
```

不要用历史 Check Success 代替 Required Check 配置。

必须区分：

```text
Check exists
```

和：

```text
Check is required
```

---

# 六、Step 3：Rulesets

查询：

```text
GET /repos/shuang54/wms-ai-assistant/rulesets
```

如果返回：

```text
[]
```

记录：

```text
visible rulesets = 0
```

如果存在 Ruleset：

逐个记录：

```text
id
name
target
enforcement
conditions
rules
```

重点检查：

```text
target = branch
main 是否被匹配
enforcement = active
```

以及 rules 中是否存在：

```text
required_status_checks
```

确认是否包含：

```text
Observability Matrix Gate
```

注意：

未认证/低权限读取的空 Ruleset 列表可能受权限影响。

所以：

```text
rulesets = []
```

不能单独证明：

```text
repository has no ruleset
```

---

# 七、Step 4：Branches Metadata

查询：

```text
GET /repos/shuang54/wms-ai-assistant/branches/main
```

记录：

```text
protected
protection
```

如果 API 返回：

```text
protected = true
```

记录为：

```text
Branch metadata reports protected = true
```

如果：

```text
protected = false
```

记录事实。

不要把这个字段扩大解释成完整的 Required Check 配置。

---

# 八、Step 5：最新 Commit

确认：

```text
GET /repos/shuang54/wms-ai-assistant/commits/main/check-runs
```

或者针对：

```text
4b8a19f468fb2ae40208cb6c9e878b2d4056000b
```

查询 Check Runs。

必须确认：

```text
head_sha = 4b8a19f468fb2ae40208cb6c9e878b2d4056000b
```

存在：

```text
Observability Matrix Gate
```

并且：

```text
status = completed
conclusion = success
```

这是：

```text
CI success evidence
```

不是：

```text
Required Check configuration evidence
```

---

# 九、Step 6：Workflow Identity

读取：

```text
.github/workflows/observability-matrix-gate.yml
```

确认：

```text
jobs:
  observability-matrix-gate:
```

job name：

```text
Observability Matrix Gate
```

保持：

```text
唯一
稳定
```

同时确认：

```text
push
pull_request
```

仍然存在。

---

# 十、Step 7：Merge Queue

只读查询仓库能够暴露的信息。

如果没有足够权限：

```text
Merge Queue = NOT AUDITED
```

不要根据：

```text
workflow 没有 merge_group
```

直接判断：

```text
Merge Queue disabled
```

只能记录：

```text
Workflow currently has no merge_group trigger.
```

如果未来 Merge Queue 被启用，而该 Workflow 又是 Required Check，需要单独评估 `merge_group` 触发。GitHub 官方文档明确指出，使用 merge queue 时，Required Check 对应 workflow 需要处理 `merge_group` 事件。

---

# 十一、Step 8：权限边界

明确记录：

```text
GitHub Token 是否存在
gh CLI 是否可用
Branch Protection API 是否可读
Ruleset API 是否可读
Merge Queue 是否可读
Check Runs 是否可读
```

如果：

```text
401 / 403
```

必须明确：

```text
NOT AUDITED
```

禁止猜测。

---

# 十二、Step 9：Governance 状态模型

最终分别给出：

```text
CI Health
Repository Protection
Required Status Check
Ruleset
Merge Queue
```

不要只输出一个：

```text
READY
```

例如：

```text
CI Health
= PASS

Repository Protection
= NOT AUDITED

Required Status Check
= NOT AUDITED

Ruleset
= NO VISIBLE RULESET / LIMITED VISIBILITY

Merge Queue
= NOT AUDITED
```

---

# 十三、测试

本阶段只修改：

```text
tests/test_github_actions_matrix_gate.py
```

如果已有测试文件足够，则不要新增文件。

新增测试最多覆盖：

```text
required check name stable
branch main identity
workflow/job identity
```

不要为了测试而创建 mock GitHub governance system。

---

# 十四、不要运行完整 Matrix

本阶段：

不要运行：

```text
pytest -q
RUN_DB_TESTS=1 pytest -q
```

不要重新运行：

```text
full DB Matrix
```

只运行相关静态测试：

```powershell
python -m pytest -q tests/test_github_actions_matrix_gate.py
```

以及：

```powershell
python -m compileall -q backend tests scripts
```

---

# 十五、Git Diff

检查：

```powershell
git status --short
git diff --stat
git diff -- backend .github
```

必须确认：

```text
backend = unchanged
.github = unchanged
Gate = unchanged
Adapter = unchanged
CLI = unchanged
Baseline = unchanged
```

---

# 十六、Evaluation 文档

新增：

```text
docs/evaluation/phase-3.12-step-103-repository-protection-audit.md
```

只记录：

```text
Branch Protection
Ruleset
Required Status Check
Latest CI Check
Workflow Identity
Merge Queue
Authorization Limitations
```

不要写：

```text
enabled
```

除非 API 有直接证据。

---

# 十七、最终报告

严格按照：

```text
Phase 3.12 Step 103 COMPLETE

1. Repository
2. Branch Protection
3. Required Status Check
4. Rulesets
5. main Branch Metadata
6. Latest Commit Check
7. Workflow Identity
8. Merge Queue
9. Authorization
10. Governance State
11. Tests
12. compileall
13. Git Diff
14. Repository Settings 是否修改
15. 当前限制

Phase 3.12 Step 103 READY
Phase 3.12 Step 103 STOP
```

如果发现任何 GitHub 配置已经存在：

只报告。

**不要修改。**

---

# 十八、硬停止

完成后：

**立即 STOP。**

不要进入：

```text
Step 104 Enable Required Status Check
Step 105 Branch Protection
Step 106 Merge Queue
PR Automation
PR Comment
Dashboard
Telemetry
OpenTelemetry
Prometheus
Langfuse
```

等待下一步明确指令。
